from __future__ import annotations

import sqlite3
from io import BytesIO
from pathlib import Path

from telegram import InputFile
from telegram.error import BadRequest
from telegram.ext import Application
from telegramify_markdown import ContentType, telegramify

from core.heartbeat import ReplyItem, WorkItem, msg_queue, reply_queue
from core.llm import process_message
from core.llm_utils import select_topic
from core.utils.config import ROOT, group_general_chat_id, logger
from core.webui import debug_hub

SCHEDULER_POLL_SECONDS = 30
DEFAULT_USERNAME = "default"
DEFAULT_SCHEDULE_CHAT_ID = int(group_general_chat_id)


async def _resolve_background_topic_dest(work_item: WorkItem, reply_text: str) -> int | None:
    if work_item.source not in {"scheduler", "watcher", "watcher_schedule", "watcher_webhook", "bug_report"}:
        return work_item.thread_id
    if work_item.thread_id is not None:
        return work_item.thread_id
    if not reply_text.strip():
        return None

    db_path = Path(ROOT) / "users" / DEFAULT_USERNAME / "data.sqlite"
    topic_query = f"{work_item.query}\n\nProposed reply:\n{reply_text}".strip()
    selected = await select_topic(chat_id=work_item.chat_id, db_path=str(db_path), query=topic_query)
    return selected if selected != 0 else None


async def worker_loop() -> None:
    print("worker loop")
    while True:
        work_item = await msg_queue.get()
        print(work_item)
        try:
            debug_hub.push_global_event(
                "work_item_dequeued",
                {
                    "source": work_item.source,
                    "username": work_item.username,
                    "chat_id": work_item.chat_id,
                },
            )
            logger.info("Processing %s work item for user=%s", work_item.source, work_item.username)
            reply_text, _ = await process_message(
                query=work_item.query,
                username=DEFAULT_USERNAME,
                chat_id=work_item.chat_id,
                origin_topic=work_item.thread_id,
                source=work_item.source,
            )
            if work_item.source in {"watcher", "watcher_schedule"} and not reply_text:
                debug_hub.push_global_event(
                    "watcher_no_reply",
                    {"username": work_item.username, "chat_id": work_item.chat_id},
                )
                continue

            if work_item.source == "telegram":
                # Telegram messages should stay in the same source topic/thread.
                thread_id = work_item.thread_id
            elif work_item.source in {"scheduler", "watcher", "watcher_schedule", "watcher_webhook", "bug_report"}:
                thread_id = await _resolve_background_topic_dest(work_item, reply_text)
            else:
                thread_id = work_item.thread_id

            await reply_queue.put(
                ReplyItem(
                    chat_id=work_item.chat_id,
                    text=reply_text,
                    thread_id=thread_id,
                    reply_to_message_id=work_item.reply_to_message_id,
                    source=work_item.source,
                    metadata=work_item.metadata,
                )
            )
            debug_hub.push_global_event(
                "reply_enqueued",
                {
                    "source": work_item.source,
                    "chat_id": work_item.chat_id,
                    "thread_id": thread_id,
                },
            )
        except Exception as e:
            print(e)
            logger.exception("worker_loop failed for %s", work_item)
            debug_hub.push_global_event(
                "worker_error",
                {
                    "source": work_item.source,
                    "username": work_item.username,
                    "error": str(e),
                },
            )
            await reply_queue.put(
                ReplyItem(
                    chat_id=work_item.chat_id,
                    text="I hit an internal error while processing the request.",
                    thread_id=work_item.thread_id,
                    reply_to_message_id=work_item.reply_to_message_id,
                    source=work_item.source,
                    metadata=work_item.metadata,
                )
            )
        finally:
            msg_queue.task_done()


async def reply_loop(app: Application) -> None:
    while True:
        reply = await reply_queue.get()
        try:
            logger.info("Sending reply to chat=%s source=%s", reply.chat_id, reply.source)
            results = await telegramify(reply.text, max_message_length=4090)
            reply_to_id = reply.reply_to_message_id if reply.source == "telegram" else None
            for item in results:
                try:
                    if item.content_type == ContentType.TEXT:
                        await app.bot.send_message(
                            chat_id=reply.chat_id,
                            text=item.text,
                            entities=[e.to_dict() for e in item.entities],
                            message_thread_id=reply.thread_id,
                            reply_to_message_id=reply_to_id,
                        )
                    elif item.content_type == ContentType.PHOTO:
                        await app.bot.send_photo(
                            chat_id=reply.chat_id,
                            photo=(item.file_name, item.file_data),
                            caption=item.caption_text or None,
                            caption_entities=[e.to_dict() for e in item.caption_entities] or None,
                            message_thread_id=reply.thread_id,
                            reply_to_message_id=reply_to_id,
                        )
                    elif item.content_type == ContentType.FILE:
                        bio = BytesIO(item.file_data)
                        bio.name = item.file_name
                        await app.bot.send_document(
                            chat_id=reply.chat_id,
                            document=InputFile(bio, filename=item.file_name),
                            caption=item.caption_text or None,
                            caption_entities=[e.to_dict() for e in item.caption_entities] or None,
                            message_thread_id=reply.thread_id,
                            reply_to_message_id=reply_to_id,
                        )
                except BadRequest as exc:
                    if "Message thread not found" not in str(exc):
                        raise
                    logger.warning(
                        "Invalid thread_id=%s for chat=%s; retrying without thread",
                        reply.thread_id,
                        reply.chat_id,
                    )
                    if item.content_type == ContentType.TEXT:
                        await app.bot.send_message(
                            chat_id=reply.chat_id,
                            text=item.text,
                            entities=[e.to_dict() for e in item.entities],
                            reply_to_message_id=reply_to_id,
                        )
                    elif item.content_type == ContentType.PHOTO:
                        await app.bot.send_photo(
                            chat_id=reply.chat_id,
                            photo=(item.file_name, item.file_data),
                            caption=item.caption_text or None,
                            caption_entities=[e.to_dict() for e in item.caption_entities] or None,
                            reply_to_message_id=reply_to_id,
                        )
                    elif item.content_type == ContentType.FILE:
                        bio = BytesIO(item.file_data)
                        bio.name = item.file_name
                        await app.bot.send_document(
                            chat_id=reply.chat_id,
                            document=InputFile(bio, filename=item.file_name),
                            caption=item.caption_text or None,
                            caption_entities=[e.to_dict() for e in item.caption_entities] or None,
                            reply_to_message_id=reply_to_id,
                        )
                # Reply to source message only once; subsequent chunks continue in same thread normally.
                reply_to_id = None
        except Exception:
            logger.exception("reply_loop failed for %s", reply)
        finally:
            reply_queue.task_done()


def _build_scheduled_work_item(username: str, chat_id: int, job_row: sqlite3.Row) -> WorkItem:
    cron_name = str(job_row["name"]).strip() or f"job-{job_row['id']}"
    prompt = str(job_row["task_prompt"]).strip()
    tagged_prompt = (
        f"[Scheduled task: {cron_name}]\n\n"
        "Execution context:\n"
        "- This request was triggered by scheduler.\n"
        "- Complete the task end-to-end and return a concise final user update.\n"
        "- Use tools/skills only when required for correctness.\n\n"
        "Skill routing hints:\n"
        "- `todo_lists`: task lists, pending/completed summaries, status updates.\n"
        "- `daily-summary`: write/read date-based markdown summaries.\n"
        "- `crontab-scheduler`: inspect/update schedule metadata and activation.\n"
        "- `researcher`: external research when local data is insufficient.\n"
        "- `telegram`: topic management or explicit topic-targeted sends.\n"
        "- `sandbox`: shell/python/filesystem operations for validation/automation.\n\n"
        "Task objective:\n"
        f"{prompt}"
    )
    row_thread_id = job_row["thread_id"] if "thread_id" in job_row.keys() else None
    try:
        row_thread_id = int(row_thread_id) if row_thread_id is not None else None
    except Exception:
        row_thread_id = None

    return WorkItem(
        query=tagged_prompt,
        username=DEFAULT_USERNAME,
        chat_id=chat_id,
        thread_id=row_thread_id,
        source="scheduler",
        metadata={"schedule_id": job_row["id"], "schedule_name": cron_name},
    )
