from __future__ import annotations

import asyncio
import traceback
from typing import Callable

from core.agent_builder import AgentBuilder
from core.llm_utils import (
    _as_payload,
    _build_bug_report_prompt,
    _build_manager_prompt,
    _build_watcher_prompt,
    _build_watcher_webhook_prompt,
    _clean_block,
    _now_str,
)
from core.memory_helpers import QdrantSemanticProvider
from core.utils.config import ROOT, default_chat_id, single_username
from core.utils.models import WatcherDecision
from core.webui import debug_hub
from skills.work.telegram.telegram_tools import get_topics_with_names

SINGLE_USERNAME = single_username


def _build_memory(username: str, chat_id: int | str, topic_id: str):
    return QdrantSemanticProvider(
        chat_id_memory=f"default_{chat_id}_{topic_id}"
    )


async def _run_manager(chat_id: int | str, username: str, topic_name: str, prompt: str, think=True,
                       allow_skills=True, trace_id: str | None = None) -> dict:
    debug_hub.record_stage(
        trace_id,
        "manager_invoke",
        label="manager.md",
        payload={"prompt": prompt, "topic": topic_name},
        source_node="manager_agent",
        target_node="manager",
    )
    manager = AgentBuilder(chat_id,
                           username,
                           topic_name,
                           agent_md_file="manager.md",
                           think=think,
                           allow_skills=allow_skills,
                           trace_id=trace_id,
                           debug_agent_name="manager")
    final_text = await manager.run_query(prompt, stream=True)
    debug_hub.record_stage(
        trace_id,
        "manager_result",
        label=(final_text or "")[:160],
        payload={"final_text": final_text},
        source_node="manager",
        target_node="manager_result",
    )
    return manager, (final_text or "").strip()


async def _run_watcher_agent(
        chat_id: int | str,
        username: str,
        topic_name: str,
        query: str,
        trace_id: str | None = None,
) -> WatcherDecision:
    prompt = _build_watcher_prompt(_now_str(), query)
    debug_hub.record_stage(
        trace_id,
        "watcher_invoke",
        label="chat.md structured",
        payload={"prompt": prompt, "topic": topic_name},
        source_node="watcher_agent",
        target_node="watcher",
    )

    watcher_agent = AgentBuilder(
        chat_id,
        username,
        topic_name,
        agent_md_file="chat.md",
        debug_agent_name="watcher",
        response_format=WatcherDecision,
        think=False,
        allow_skills=False,
        trace_id=trace_id,
    )

    response = await watcher_agent.run_query(
        prompt
    )

    if isinstance(response, WatcherDecision):
        debug_hub.record_stage(
            trace_id,
            "watcher_result",
            label=f"should_reply={response.should_reply}",
            payload={"decision": _as_payload(response)},
            source_node="watcher",
            target_node="watcher_result",
        )
        return response

    if isinstance(response, dict):
        debug_hub.record_stage(
            trace_id,
            "watcher_result",
            label=f"should_reply={bool(response.get('should_reply'))}",
            payload={"decision": response},
            source_node="watcher",
            target_node="watcher_result",
        )
        return WatcherDecision(**response)

    debug_hub.record_stage(
        trace_id,
        "watcher_result",
        label="fallback_no_reply",
        payload={"raw_response": _as_payload(response)},
        source_node="watcher",
        target_node="watcher_result",
    )
    return WatcherDecision(should_reply=False, reply_text="")


async def _run_report_agent(
        chat_id: int | str,
        username: str,
        topic_name: str,
        query: str,
        trace_id: str | None,
        stage_name: str,
        prompt_builder: Callable[[str, str], str],
) -> str:
    prompt = prompt_builder(_now_str(), query)
    debug_hub.record_stage(
        trace_id,
        f"{stage_name}_invoke",
        label="manager.md report analysis",
        payload={"prompt": prompt, "topic": topic_name},
        source_node=f"{stage_name}_agent",
        target_node=stage_name,
    )
    _, reply_text = await _run_manager(
        chat_id=chat_id,
        username=username,
        topic_name=topic_name,
        prompt=prompt,
        think=True,
        allow_skills=False,
        trace_id=trace_id,
    )
    debug_hub.record_stage(
        trace_id,
        f"{stage_name}_result",
        label=reply_text[:180],
        payload={"reply_text": reply_text},
        source_node=stage_name,
        target_node=f"{stage_name}_result",
    )
    return reply_text.strip()


async def process_message(
        query: str,
        username: str,
        chat_id: int | str,
        origin_topic: int | None = None,
        source: str = "telegram",
):
    username = SINGLE_USERNAME
    args = {
        "query": query,
        "username": username,
        "chat_id": chat_id,
        "source": source,
        "topic_id": origin_topic,
    }
    run_id = debug_hub.start_run(**args)


    def _finish(reply: str, thread: int | None, status: str = "completed", error: str | None = None):
        debug_hub.end_run(run_id, status=status, reply_text=reply, error=error)
        return reply, thread

    try:
        print("\n========== AGENT DEBUG START ==========\n")
        print(f"source={source}, origin_topic_id={origin_topic}")
        db_path = ROOT / "users" / username / "data.sqlite"
        print(db_path)
        all_topics = get_topics_with_names(db_path=db_path, chat_id=chat_id)
        topic_name = [x for x in all_topics if x.get("thread_id") == origin_topic]
        if len(topic_name) > 0:
            topic_name = topic_name[0]
        else:
            topic_name = "General Chat"
        stage_args = {
            "run_id": run_id,
            "stage": "process_started",
            "label": f"source={source}",
            "payload": {
                "query": query,
                "source": source,
                "chat_id": chat_id,
                "origin_topic": origin_topic,
            },
        }
        debug_hub.record_stage(**stage_args)

        now = _now_str()

        is_watcher = source in {"watcher", "watcher_schedule"}
        is_watcher_webhook = source == "watcher_webhook"
        is_bug_report = source == "bug_report"
        is_background = source in {"scheduler", "watcher", "watcher_schedule", "watcher_webhook", "bug_report"}

        manager_think = True
        memory_mode = "24h" if is_background else "8h"
        memory_limit = 10 if is_background else 5

        debug_hub.record_stage(
            run_id,
            "memory_lookup",
            label=f"mode={memory_mode} limit={memory_limit}",
            payload={"mode": memory_mode, "limit": memory_limit, "query": query},
        )

        semantic_memory = _build_memory(username, chat_id, origin_topic)
        memory_context_raw = await semantic_memory.get_memory_chats(
            query,
            mode=memory_mode,
            limit=memory_limit,
        )
        memory_context = _clean_block(memory_context_raw)

        debug_hub.record_stage(
            run_id,
            "memory_ready",
            payload={"memory_context": memory_context},
        )

        if is_watcher:
            debug_hub.record_stage(run_id, "watcher_agent", payload={"query": query})
            watcher_result = await _run_watcher_agent(
                chat_id, username, origin_topic, query, trace_id=run_id
            )
            print(watcher_result)

            if not watcher_result.should_reply or len(watcher_result.reply_text.strip()) <= 2:
                debug_hub.record_stage(
                    run_id,
                    "watcher_no_reply",
                    label=watcher_result.reason or "No watcher reply required",
                    payload={"decision": _as_payload(watcher_result)},
                )
                return _finish("", None)

            reply_text = watcher_result.reply_text.strip()
            print(f"WATCHER RESULT: {reply_text}")
            return _finish(reply_text, None)

        if is_watcher_webhook:
            debug_hub.record_stage(run_id, "watcher_webhook_agent", payload={"query": query})
            reply_text = await _run_report_agent(
                chat_id=chat_id,
                username=username,
                topic_name=str(topic_name),
                query=query,
                trace_id=run_id,
                stage_name="watcher_webhook",
                prompt_builder=_build_watcher_webhook_prompt,
            )
            return _finish(reply_text, None)

        if is_bug_report:
            debug_hub.record_stage(run_id, "bug_report_agent", payload={"query": query})
            reply_text = await _run_report_agent(
                chat_id=chat_id,
                username=username,
                topic_name=str(topic_name),
                query=query,
                trace_id=run_id,
                stage_name="bug_report",
                prompt_builder=_build_bug_report_prompt,
            )
            return _finish(reply_text, None)

        debug_hub.record_stage(
            run_id,
            "manager_agent",
            payload={
                "mode": "manager_direct",
                "topic": topic_name,
                "source": source,
                "memory_included": bool(memory_context.strip()),
            },
        )

        manager_prompt = _build_manager_prompt(
            now=now,
            memory_block=memory_context,
            query=query,
            topic_name=str(topic_name),
            source=source,
        )

        manager, reply_text = await _run_manager(
            chat_id,
            username,
            origin_topic,
            manager_prompt,
            think=manager_think,
            trace_id=run_id,
        )

        captured_text = manager.get_captured_reply()
        if captured_text:
            reply_text = captured_text

        if len(reply_text.strip()) <= 5:
            return _finish("I couldn't produce a usable final response.", None)

        if not is_watcher:
            semantic_memory.add_memory([{"role": "user", "content": query}])
            semantic_memory.add_memory([{"role": "assistant", "content": reply_text}])

        return _finish(reply_text, None)

    except Exception as e:
        print("\n========== ERROR ==========")
        print(str(e))
        traceback.print_exc()
        debug_hub.record_stage(run_id, "error", label=str(e), kind="error")
        return _finish(
            f"I hit an internal error while processing the request: {e}",
            None,
            status="error",
            error=str(e),
        )


if __name__ == "__main__":
    demo_chat_id_raw = (default_chat_id or "").strip()
    try:
        demo_chat_id: int | str = int(demo_chat_id_raw) if demo_chat_id_raw else 0
    except Exception:
        demo_chat_id = demo_chat_id_raw or 0
    asyncio.run(
        process_message(
            query="Hi",
            chat_id=demo_chat_id,
            username="default",
            origin_topic=None,
            source="telegram",
        )
    )
