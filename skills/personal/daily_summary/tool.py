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

from skills.personal.daily_summary import daily_summary

OPERATIONS = {
    "get_daily_summary": daily_summary.get_daily_summary,
    "upsert_daily_summary": daily_summary.upsert_daily_summary,
}

REQUIRED_FIELDS = {
    "get_daily_summary": ("date",),
    "upsert_daily_summary": ("date", "markdown_summary"),
}


def _supported_operations() -> str:
    return ", ".join(sorted(OPERATIONS))


def _require_payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raise ValueError("payload must be a JSON object.")


def _require_db_path(extra_args: dict[str, Any] | None) -> str:
    db_path = (extra_args or {}).get("db_path")
    if not isinstance(db_path, str) or not db_path.strip():
        raise ValueError("extra_args.db_path is required.")
    return db_path


def _require_str(payload: dict[str, Any], key: str, *, hint: str | None = None) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        message = f"Missing required field: {key}."
        if hint:
            message = f"{message} {hint}"
        raise ValueError(message)
    return value.strip()


def _validate_payload(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    for field in REQUIRED_FIELDS[operation]:
        if field not in payload or payload[field] is None or payload[field] == "":
            hint = "Use YYYY-MM-DD." if field == "date" else None
            raise ValueError(f"Missing required field: {field}." + (f" {hint}" if hint else ""))

    normalized: dict[str, Any] = {}
    for field in REQUIRED_FIELDS[operation]:
        normalized[field] = payload[field]

    normalized["date"] = _require_str(normalized, "date", hint="Use YYYY-MM-DD.")
    if operation == "upsert_daily_summary":
        normalized["markdown_summary"] = _require_str(normalized, "markdown_summary")

    return normalized


def _format_result(operation: str, result: Any) -> dict[str, Any]:
    if operation == "get_daily_summary":
        return {
            "summary": result,
            "found": result is not None,
        }

    if operation == "upsert_daily_summary":
        if isinstance(result, dict):
            return result
        return {"summary_id": result, "write_status": "unknown"}

    return {"value": result}


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
        return "Retry with operation=get_daily_summary|upsert_daily_summary and payload.date."
    if operation == "get_daily_summary":
        return "Retry with payload.date in YYYY-MM-DD format."
    return "Retry with payload.date (YYYY-MM-DD) and payload.markdown_summary."


async def process_args(args: dict | str | None = None, extra_args: dict = None) -> dict[str, Any]:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError as exc:
            return _fail(None, f"Args string is not valid JSON: {exc}", "Send args as a JSON object.")

    parsed_args = args or {}
    operation = parsed_args.get("operation")
    payload = parsed_args.get("payload", {})

    try:
        if not operation:
            raise ValueError(f"operation is required. Supported operations: {_supported_operations()}.")

        if operation not in OPERATIONS:
            raise ValueError(f"Unknown operation: {operation}. Supported operations: {_supported_operations()}.")

        payload = _require_payload_dict(payload)
        payload = _validate_payload(operation, payload)
        db_path = _require_db_path(extra_args)

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            result = OPERATIONS[operation](conn=conn, **payload)
            return _ok(operation, _format_result(operation, result))

    except Exception as exc:
        return _fail(operation, str(exc), _error_hint_for_error(operation))


def main() -> None:
    src_db = ROOT / "users" / "default" / "data.sqlite"
    test_root = Path.home() / ".codex" / "memories" / "skill_tool_tests"
    test_root.mkdir(parents=True, exist_ok=True)
    test_db = test_root / "daily_summary_test.sqlite"
    with sqlite3.connect(src_db) as src_conn, sqlite3.connect(test_db) as dst_conn:
        src_conn.backup(dst_conn)

    cases = [
        {
            "name": "upsert_daily_summary",
            "args": {
                "operation": "upsert_daily_summary",
                "payload": {"date": "2026-03-25", "markdown_summary": "Tool test summary"},
            },
        },
        {"name": "get_daily_summary", "args": {"operation": "get_daily_summary", "payload": {"date": "2026-03-25"}}},
    ]

    outputs: list[dict[str, Any]] = []
    for case in cases:
        result = asyncio.run(process_args(case["args"], extra_args={"db_path": str(test_db)}))
        outputs.append({"case": case["name"], "result": result})

    print(json.dumps({"db_path": str(test_db), "tests": outputs}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
