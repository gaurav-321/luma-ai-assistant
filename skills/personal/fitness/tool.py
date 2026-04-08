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
from skills.personal.fitness.fitness_tools import mark_today_exercise_as_skip, send_today_exercises


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


def _error_hint_for_error(operation: str | None) -> str:
    if not operation:
        return (
            "Retry with operation=send_today_exercises or operation=mark_today_exercise_as_skip "
            "and provide the required extra_args."
        )
    if operation == "send_today_exercises":
        return "Retry with extra_args.chat_id and extra_args.db_path. Do not include payload.skip."
    if operation == "mark_today_exercise_as_skip":
        return "Retry with extra_args.db_path only. No payload is required."
    return "Retry with a supported operation and the required extra_args."


def _validate_empty_payload(payload: Any, operation: str) -> None:
    if payload is None:
        return
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object.")
    if "skip" in payload:
        raise ValueError("payload.skip is no longer supported. Use operation=mark_today_exercise_as_skip.")
    if payload:
        raise ValueError(f"{operation} does not accept payload fields.")


async def process_args(args: dict | str | None = None, extra_args: dict | None = None) -> dict[str, Any]:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError as exc:
            return _fail(None, f"Args string is not valid JSON: {exc}", "Send args as a JSON object.")

    parsed_args = args or {}
    if not isinstance(parsed_args, dict):
        return _fail(None, "args must be a JSON object.", "Send args as a JSON object.")
    operation = parsed_args.get("operation", "send_today_exercises")
    payload = parsed_args.get("payload", {})
    extra_args = extra_args or {}

    try:
        if not isinstance(extra_args, dict):
            raise ValueError("extra_args must be a JSON object.")

        if operation == "send_today_exercises":
            _validate_empty_payload(payload, operation)
            if "chat_id" not in extra_args or "db_path" not in extra_args:
                raise ValueError("extra_args.chat_id and extra_args.db_path are required.")

            result = await send_today_exercises(
                chat_id=extra_args["chat_id"],
                db_path=extra_args["db_path"],
            )
            return _ok(operation, result)

        if operation == "mark_today_exercise_as_skip":
            _validate_empty_payload(payload, operation)
            if "db_path" not in extra_args:
                raise ValueError("extra_args.db_path is required.")

            result = mark_today_exercise_as_skip(db_path=extra_args["db_path"])
            return _ok(operation, result)

        raise ValueError("Unsupported operation. Use 'send_today_exercises' or 'mark_today_exercise_as_skip'.")

    except Exception as exc:
        return _fail(operation, str(exc), _error_hint_for_error(operation))


def main() -> None:
    demo_chat_id_raw = (default_chat_id or "").strip()
    try:
        demo_chat_id = int(demo_chat_id_raw) if demo_chat_id_raw else 0
    except Exception:
        demo_chat_id = demo_chat_id_raw or 0

    src_db = ROOT / "users" / "default" / "data.sqlite"
    test_root = Path.home() / ".codex" / "memories" / "skill_tool_tests"
    test_root.mkdir(parents=True, exist_ok=True)
    test_db = test_root / "fitness_test.sqlite"
    with sqlite3.connect(src_db) as src_conn, sqlite3.connect(test_db) as dst_conn:
        src_conn.backup(dst_conn)

    cases = [
        {
            "name": "mark_today_exercise_as_skip",
            "args": {"operation": "mark_today_exercise_as_skip", "payload": {}},
            "extra_args": {"db_path": str(test_db)},
        },
        {
            "name": "send_today_exercises",
            "args": {"operation": "send_today_exercises", "payload": {}},
            "extra_args": {"db_path": str(test_db), "chat_id": demo_chat_id},
        },
    ]

    outputs: list[dict[str, Any]] = []
    for case in cases:
        result = asyncio.run(process_args(args=case["args"], extra_args=case["extra_args"]))
        outputs.append({"case": case["name"], "result": result})

    print(json.dumps({"db_path": str(test_db), "tests": outputs}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
