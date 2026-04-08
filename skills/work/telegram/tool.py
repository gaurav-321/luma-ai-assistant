from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.utils.config import default_chat_id
from skills.work.telegram import telegram_tools

OPERATIONS = {
    "create_topic": telegram_tools.create_topic,
    "list_topics": telegram_tools.list_topics,
    "delete_topic": telegram_tools.delete_topic,
    "send_to_topic": telegram_tools.send_to_topic,
}

EXTRA_ARG_BINDINGS = {
    "create_topic": ("chat_id", "db_path"),
    "list_topics": ("chat_id", "db_path"),
    "delete_topic": ("chat_id", "db_path"),
    "send_to_topic": ("chat_id",),
}


def _normalize_args(args: dict | str | None) -> dict[str, Any]:
    if args is None:
        return {}

    if isinstance(args, dict):
        return args

    if isinstance(args, str):
        text = args.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"args must be valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("args JSON must deserialize to an object.")
        return parsed

    raise ValueError("args must be a dict, JSON string, or null.")


def _ok(operation: str | None, result: Any) -> dict[str, Any]:
    return {
        "ok": True,
        "operation": operation,
        "result": result,
        "error": None,
    }


def _fail(operation: str | None, error: str, recovery_hint: str) -> dict[str, Any]:
    return {
        "ok": False,
        "operation": operation,
        "result": None,
        "error": error,
    }


def _bind_extra_args(operation: str, payload: dict[str, Any], extra_args: dict[str, Any]) -> None:
    for key in EXTRA_ARG_BINDINGS.get(operation, ()):
        if key in payload:
            continue
        if key not in extra_args:
            continue
        value = extra_args[key]
        if key == "chat_id" and value is not None:
            payload[key] = int(value)
        else:
            payload[key] = value


def _error_hint_for_error(operation: str | None, message: str) -> str:
    if not operation:
        return "Retry with operation=create_topic|list_topics|delete_topic|send_to_topic and include payload."

    if operation == "send_to_topic":
        return "Retry with payload.thread_id and payload.text. chat_id can come from extra_args."

    if operation == "create_topic":
        return "Retry with payload.name and a valid forum-enabled chat_id."

    if operation == "delete_topic":
        return "Use list_topics first to get a valid thread_id, then retry delete_topic."

    if operation == "list_topics":
        return "Retry with valid chat_id and db_path."

    if "Topic_id_invalid" in message:
        return "Use list_topics to fetch valid thread IDs, then retry."

    return "Retry with valid payload fields for this operation."


async def process_args(args: dict | str | None = None, extra_args: dict | None = None) -> dict[str, Any]:
    operation: str | None = None
    try:
        parsed_args = _normalize_args(args)
        extra_args = extra_args or {}

        operation = parsed_args.get("operation")
        payload = parsed_args.get("payload", {})

        if not operation:
            raise ValueError("operation is required.")

        if operation not in OPERATIONS:
            supported = ", ".join(sorted(OPERATIONS))
            raise ValueError(f"Unknown operation: {operation}. Supported: {supported}.")

        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object.")

        payload = dict(payload)
        _bind_extra_args(operation, payload, extra_args)

        func = OPERATIONS[operation]
        result = func(**payload)
        if asyncio.iscoroutine(result):
            result = await result

        return _ok(operation, result)

    except Exception as exc:
        message = str(exc)
        return _fail(operation, message, _error_hint_for_error(operation, message))


def main() -> None:
    demo_chat_id_raw = (default_chat_id or "").strip()
    try:
        demo_chat_id = int(demo_chat_id_raw) if demo_chat_id_raw else 0
    except Exception:
        demo_chat_id = demo_chat_id_raw or 0

    src_db = ROOT / "users" / "default" / "data.sqlite"
    test_root = Path.home() / ".codex" / "memories" / "skill_tool_tests"
    test_root.mkdir(parents=True, exist_ok=True)
    test_db = test_root / "telegram_tool_test.sqlite"
    with sqlite3.connect(src_db) as src_conn, sqlite3.connect(test_db) as dst_conn:
        src_conn.backup(dst_conn)

    cases = [
        {
            "name": "list_topics",
            "args": {"operation": "list_topics", "payload": {}},
            "extra_args": {"chat_id": demo_chat_id, "db_path": str(test_db)},
        },
        {
            "name": "send_to_topic",
            "args": {
                "operation": "send_to_topic",
                "payload": {"chat_id": demo_chat_id, "thread_id": 1, "text": "tool main test"},
            },
            "extra_args": {},
        },
        {
            "name": "create_topic",
            "args": {"operation": "create_topic", "payload": {"name": "tool-main-test-topic"}},
            "extra_args": {"chat_id": demo_chat_id, "db_path": str(test_db)},
        },
        {
            "name": "delete_topic",
            "args": {"operation": "delete_topic", "payload": {"thread_id": 1}},
            "extra_args": {"chat_id": demo_chat_id, "db_path": str(test_db)},
        },
    ]

    outputs: list[dict[str, Any]] = []
    for case in cases:
        result = asyncio.run(process_args(args=case["args"], extra_args=case["extra_args"]))
        outputs.append({"case": case["name"], "result": result})

    print(json.dumps({"db_path": str(test_db), "tests": outputs}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
