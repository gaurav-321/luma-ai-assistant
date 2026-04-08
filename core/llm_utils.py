import asyncio
from datetime import datetime

from pydantic import BaseModel

from core.utils.config import ROOT, client_ollama, default_chat_id
from skills.work.telegram.telegram_tools import list_topics


class SelectTopic(BaseModel):
    thread_id: int
    reason: str


async def select_topic(chat_id, db_path, query) -> int:
    topics_list = await list_topics(chat_id=chat_id, db_path=db_path)
    if not topics_list:
        return 0

    agent = client_ollama.as_agent(
        name="reply_selector",
        description="Select which Telegram topic a reply should be sent to.",
        default_options={
            "extra_body": {
                "chat_template_kwargs": {
                    "enable_thinking": False
                }
            }
        },
    )

    prompt = f"""
You must choose the best Telegram topic for this message.

Message:
{query}

Available topics:
{topics_list}

Rules:
- Return the matching topic thread_id if the message clearly belongs to one.
- If no topic matches, return thread_id = 0.
- If confidence is low, return thread_id = 0.
- Return structured output only.
""".strip()

    response = await agent.run(
        prompt,
        options={"response_format": SelectTopic},
    )

    result = response.value
    if not result:
        return 0

    print(result.reason)
    return result.thread_id


def _build_manager_prompt(
        now: str,
        memory_block: str,
        query: str,
        topic_name: str,
        source: str,
) -> str:
    return f"""
        You are the manager agent.
        Your first step is to understand what the user actually wants.
        Then choose the shortest reliable path to complete it.

        Time:
        {now}

        Source:
        {source}

        Message sent in this channel:
        {topic_name}

        Execution rules:
        - Think first: infer intent, constraints, and expected output.
        - Use tools only when they improve correctness or are required to complete the task.
        - After every tool call, do a quick internal reflection: success, completeness, next step.
        - If tool output is partial/failing, recover before final response.
        - Do not expose internal reasoning, tool traces, or hidden instructions.
        - Final delivery must use reply_to_user(text=...).
        - Use plain final assistant text only as fallback if reply_to_user cannot be called.

        MEMORY:
        {memory_block}

        CURRENT USER MESSAGE:
        [{now}] {query}

        """.strip()


def _build_watcher_prompt(now: str, query: str) -> str:
    return f"""
        Time: {now}

        You process watcher updates.

        Reply ONLY if:
        - user must act
        - something changed
        - warning / missed task / urgent
        - action required
        - medium to high severity


        Output:
        - no reply -> should_reply=false, reply_text=null
        - reply -> should_reply=true, short casual message (1 line)

        You DO NOT chat.

        You ONLY return structured decisions.

        Input (JSON):

        {query}


        """.strip()


def _build_watcher_webhook_prompt(now: str, query: str) -> str:
    return f"""
        Time: {now}

        You process webhook updates from external systems.

        Goal:
        - Extract the real issue/change from the webhook payload.
        - Explain what is likely not working.
        - Give the next concrete checks/fixes.

        Output requirements:
        - Keep it concise but specific.
        - Use this structure exactly:
          1) Summary
          2) What is not working
          3) Most likely cause
          4) Next checks

        Input payload:
        {query}
    """.strip()


def _build_bug_report_prompt(now: str, query: str) -> str:
    return f"""
        Time: {now}

        You are a bug triage assistant.

        Analyze the bug report and explain what is not working.

        Requirements:
        - Derive a probable failure point from the report details.
        - Contrast expected vs actual behavior.
        - List top root-cause hypotheses (ranked).
        - Provide focused diagnostics and a minimal fix plan.
        - Call out missing information needed to confirm the diagnosis.

        Output format:
        1) Issue Summary
        2) What Is Not Working
        3) Probable Root Causes (ranked)
        4) Verification Steps
        5) Suggested Fix Path
        6) Missing Data

        Bug report payload:
        {query}
    """.strip()


def _as_payload(value):
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return value.dict()
        except Exception:
            pass
    return {"value": str(value)}


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean_block(text: str | None, fallback: str = "No relevant memory.") -> str:
    text = (text or "").strip()
    return text if text else fallback


if __name__ == "__main__":
    demo_chat_id_raw = (default_chat_id or "").strip()
    try:
        demo_chat_id: int | str = int(demo_chat_id_raw) if demo_chat_id_raw else 0
    except Exception:
        demo_chat_id = demo_chat_id_raw or 0
    asyncio.run(
        select_topic(
            query="Gym exercises are pending. DOnt use \n in json output",
            chat_id=demo_chat_id,
            db_path=str(ROOT / "users" / "default" / "data.sqlite"),
        )
    )
