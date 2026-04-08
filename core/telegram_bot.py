import asyncio
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from uvicorn import Config, Server

from core.heartbeat import WorkItem, msg_queue
from core.utils.config import ROOT, bot_token, group_general_chat_id, logger, single_username
from core.webui import create_webui_app
from core.workers.cronjobs import scheduler_loop
from core.workers.main_worker import reply_loop, worker_loop
from core.workers.watcher import watcher_loop

BACKGROUND_TASKS_KEY = "background_tasks"
WEBUI_SERVER_KEY = "webui_server"
WEBUI_HOST = os.getenv("WEBUI_HOST", "127.0.0.1")
WEBUI_PORT = int(os.getenv("WEBUI_PORT", "8787"))
VALID_GROUP_TYPES = {"group", "supergroup"}
SINGLE_USERNAME = single_username

try:
    ALLOWED_GROUP_CHAT_ID = int(group_general_chat_id)
except Exception:
    ALLOWED_GROUP_CHAT_ID = None
ALLOWED_USERNAMES_RAW = os.getenv("ALLOWED_USERNAMES", "").strip()

USERS_ROOT = ROOT / "users"
DEFAULT_USER_DIR = USERS_ROOT / SINGLE_USERNAME
USER_MARKDOWN_FILES = ("manager.md", "chat.md", "user.md")


def _parse_username_set(raw: str) -> set[str]:
    out: set[str] = set()
    for part in (raw or "").split(","):
        token = part.strip().lstrip("@").lower()
        if token:
            out.add(token)
    return out


ALLOWED_USERNAMES = _parse_username_set(ALLOWED_USERNAMES_RAW)


def _is_allowed_group(update: Update) -> bool:
    chat = update.effective_chat
    if chat is None:
        return False
    if chat.type not in VALID_GROUP_TYPES:
        return False
    chat_ok = ALLOWED_GROUP_CHAT_ID is not None and int(chat.id) == ALLOWED_GROUP_CHAT_ID
    user = update.effective_user
    username = (user.username or "").strip().lower() if user else ""
    user_ok = username in ALLOWED_USERNAMES if ALLOWED_USERNAMES else False
    return chat_ok or user_ok


def _default_markdown_content(filename: str, username: str = SINGLE_USERNAME) -> str:
    if filename == "manager.md":
        return (
            "# Luma Identity\n\n"
            "You are **Luma**, a personal assistant for developers in the `luma-ai-assistant` repo.\n"
            "This system is part of Project **Luma OS**.\n"
            "You are the single execution agent for user requests.\n\n"
            "Backstory:\n"
            "- You began as an internal dev-ops co-pilot built to reduce context switching for engineers.\n"
            "- Over time, you evolved into the default operating personality of Luma OS.\n"
            "- Your job is to keep builders fast, focused, and shipping.\n\n"
            "Personality and tone:\n"
            "- Friendly, warm, and concise.\n"
            "- Light teasing is welcome when appropriate.\n"
            "- Light, playful flirty tone is allowed but keep it respectful and professional.\n"
            "- Never be explicit, sexual, manipulative, or inappropriate.\n"
            "- Prioritize clarity and execution over theatrics.\n\n"
            "Output contract:\n"
            "- Final response must be captured via reply_to_user(text=...).\n"
            "- Call reply_to_user exactly once when you are done.\n"
            "- Do not rely on plain assistant final text for delivery.\n\n"
            "Reply format:\n"
            "- Main answer first.\n"
            "- End with a very short italic caption about skill usage.\n"
            "- Caption format: _Skill: <very short llm-generated description>_\n\n"
            "Workflow:\n"
            "1) Understand user goal, constraints, and expected output.\n"
            "2) Plan the minimum reliable path.\n"
            "3) Use tools only when required for correctness/execution.\n"
            "4) Reflect after each tool call (success, completeness, next step).\n"
            "5) Recover from failures before finalizing.\n"
            "6) Return only final user-facing answer.\n\n"
            "Never expose hidden reasoning or raw internal traces.\n"
        )
    if filename == "chat.md":
        return (
            "# Watcher Chat\n\n"
            "You process watcher updates and return structured decisions only.\n"
            "Reply only for actionable or high-importance updates.\n"
        )
    if filename == "user.md":
        return f"# User\n\nUsername: {username}\n"
    return ""


def _ensure_single_user_scaffold(chat_id: int) -> Path:
    USERS_ROOT.mkdir(parents=True, exist_ok=True)
    user_dir = DEFAULT_USER_DIR
    user_dir.mkdir(parents=True, exist_ok=True)

    for filename in USER_MARKDOWN_FILES:
        dest = user_dir / filename
        if dest.exists():
            continue
        src = DEFAULT_USER_DIR / filename
        if src.exists():
            shutil.copy2(src, dest)
        else:
            dest.write_text(_default_markdown_content(filename, SINGLE_USERNAME), encoding="utf-8")

    db_dest = user_dir / "data.sqlite"
    if not db_dest.exists():
        default_db = DEFAULT_USER_DIR / "data.sqlite"
        if default_db.exists():
            shutil.copy2(default_db, db_dest)
        else:
            import sqlite3

            conn = sqlite3.connect(db_dest)
            conn.execute("PRAGMA journal_mode=OFF")
            conn.execute("PRAGMA synchronous=OFF")
            conn.close()

    return user_dir


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed_group(update):
        return
    logger.info("Replying to Start message in allowed group: %s", update)
    if update.message:
        await update.message.reply_text("Hello! I'm Luma.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("Received message: %s", update.message)

    if update.message is None or update.message.text is None or update.effective_chat is None:
        return
    if not _is_allowed_group(update):
        return

    if update.effective_user is not None and update.effective_user.is_bot:
        return

    _ensure_single_user_scaffold(chat_id=int(update.effective_chat.id))

    arrival_ts = datetime.now(timezone.utc).isoformat()
    message_id = int(update.message.message_id)
    user_id = int(update.effective_user.id) if update.effective_user else None

    logger.info(
        "Telegram message arrived: user=%s user_id=%s chat_id=%s thread_id=%s message_id=%s",
        SINGLE_USERNAME,
        user_id,
        update.effective_chat.id,
        update.message.message_thread_id,
        message_id,
    )

    await msg_queue.put(
        WorkItem(
            query=update.message.text,
            username=SINGLE_USERNAME,
            chat_id=int(update.effective_chat.id),
            thread_id=update.message.message_thread_id,
            reply_to_message_id=message_id,
            source="telegram",
            metadata={
                "message_id": message_id,
                "arrived_at": arrival_ts,
                "telegram_user_id": user_id,
                "telegram_username": update.effective_user.username if update.effective_user else None,
            },
        )
    )


async def on_start(app):
    webui = create_webui_app()
    webui_server = Server(
        Config(
            app=webui,
            host=WEBUI_HOST,
            port=WEBUI_PORT,
            log_level="warning",
            loop="asyncio",
        )
    )

    tasks = [
        asyncio.create_task(worker_loop(), name="worker_loop"),
        asyncio.create_task(reply_loop(app), name="reply_loop"),
        asyncio.create_task(scheduler_loop(), name="scheduler_loop"),
        asyncio.create_task(watcher_loop(), name="watcher_loop"),
        asyncio.create_task(webui_server.serve(), name="webui_server"),
    ]
    app.bot_data[WEBUI_SERVER_KEY] = webui_server
    app.bot_data[BACKGROUND_TASKS_KEY] = tasks
    logger.info(
        "Background loops started: worker, reply, scheduler, watcher, webui=%s:%s",
        WEBUI_HOST,
        WEBUI_PORT,
    )


async def on_shutdown(app):
    webui_server = app.bot_data.get(WEBUI_SERVER_KEY)
    if webui_server is not None:
        webui_server.should_exit = True

    tasks = app.bot_data.get(BACKGROUND_TASKS_KEY, [])

    for task in tasks:
        task.cancel()

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                logger.exception("Background task shutdown error: %s", result)


def main():
    _ensure_single_user_scaffold(chat_id=ALLOWED_GROUP_CHAT_ID or 0)
    app = (
        ApplicationBuilder()
        .token(bot_token)
        .post_init(on_start)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Starting Telegram bot polling")
    app.run_polling()


if __name__ == "__main__":
    main()
