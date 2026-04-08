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

from skills.personal.add_food_log import food_log

OPERATIONS = {
    "add_food_log": food_log.add_food_log,
    "update_food_log": food_log.update_food_log,
    "delete_food_log": food_log.delete_food_log,
    "get_food_logs_by_date": food_log.get_food_logs_by_date,
    "get_total_calories_for_date": food_log.get_total_calories_for_date,
    "get_food_summary_for_date": food_log.get_food_summary_for_date,
    "get_food_day_report": food_log.get_food_day_report,
}

REQUIRED_FIELDS = {
    "add_food_log": ("food_name", "calories", "protein", "carbs", "fat", "quantity", "meal_type"),
    "update_food_log": ("id",),
    "delete_food_log": ("id",),
    "get_food_logs_by_date": ("date",),
    "get_total_calories_for_date": ("date",),
    "get_food_summary_for_date": ("date",),
    "get_food_day_report": ("date",),
}

INT_FIELDS = {
    "add_food_log": ("calories",),
    "update_food_log": ("id", "calories"),
    "delete_food_log": ("id",),
}

FLOAT_FIELDS = {
    "add_food_log": ("protein", "carbs", "fat"),
    "update_food_log": ("protein", "carbs", "fat"),
}

ALLOWED_FIELDS = {
    "add_food_log": (
        "food_name",
        "calories",
        "protein",
        "carbs",
        "fat",
        "quantity",
        "meal_type",
        "notes",
        "logged_at",
    ),
    "update_food_log": (
        "id",
        "food_name",
        "calories",
        "protein",
        "carbs",
        "fat",
        "quantity",
        "meal_type",
        "notes",
        "logged_at",
    ),
    "delete_food_log": ("id",),
    "get_food_logs_by_date": ("date",),
    "get_total_calories_for_date": ("date",),
    "get_food_summary_for_date": ("date",),
    "get_food_day_report": ("date",),
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


def _coerce_int(payload: dict[str, Any], key: str) -> None:
    if key not in payload or payload[key] is None:
        return

    value = payload[key]
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer.")

    try:
        payload[key] = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer.") from exc


def _coerce_float(payload: dict[str, Any], key: str) -> None:
    if key not in payload or payload[key] is None:
        return

    value = payload[key]
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a number.")

    try:
        payload[key] = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number.") from exc


def _validate_payload(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    for field in REQUIRED_FIELDS[operation]:
        if field not in payload or payload[field] is None or payload[field] == "":
            hint = "Use YYYY-MM-DD." if field == "date" else None
            raise ValueError(f"Missing required field: {field}." + (f" {hint}" if hint else ""))

    normalized: dict[str, Any] = {}
    for field in ALLOWED_FIELDS[operation]:
        if field in payload:
            normalized[field] = payload[field]

    for field in INT_FIELDS.get(operation, ()):
        _coerce_int(normalized, field)

    for field in FLOAT_FIELDS.get(operation, ()):
        _coerce_float(normalized, field)

    if operation == "add_food_log":
        normalized["food_name"] = _require_str(normalized, "food_name")
        normalized["quantity"] = _require_str(normalized, "quantity")
        normalized["meal_type"] = _require_str(normalized, "meal_type")

        notes = normalized.get("notes")
        if notes is not None:
            normalized["notes"] = str(notes).strip() or None

        logged_at = normalized.get("logged_at")
        if logged_at is not None:
            if not isinstance(logged_at, str) or not logged_at.strip():
                raise ValueError("logged_at must be a non-empty string when provided. Use YYYY-MM-DD HH:MM:SS.")
            normalized["logged_at"] = logged_at.strip()

    elif operation == "update_food_log":
        if normalized["id"] <= 0:
            raise ValueError("id must be a positive integer.")

        allowed_updates = {
            "food_name",
            "calories",
            "protein",
            "carbs",
            "fat",
            "quantity",
            "meal_type",
            "notes",
            "logged_at",
        }
        update_keys = [k for k in normalized.keys() if k in allowed_updates]
        if not update_keys:
            raise ValueError("update_food_log requires at least one field to update.")

        if "food_name" in normalized:
            normalized["food_name"] = _require_str(normalized, "food_name")
        if "quantity" in normalized:
            normalized["quantity"] = _require_str(normalized, "quantity")
        if "meal_type" in normalized:
            normalized["meal_type"] = _require_str(normalized, "meal_type")

        if "notes" in normalized:
            notes_value = normalized["notes"]
            normalized["notes"] = None if notes_value is None else (str(notes_value).strip() or None)

        if "logged_at" in normalized:
            logged_at = normalized["logged_at"]
            if not isinstance(logged_at, str) or not logged_at.strip():
                raise ValueError("logged_at must be a non-empty string when provided. Use YYYY-MM-DD HH:MM:SS.")
            normalized["logged_at"] = logged_at.strip()

    elif operation == "delete_food_log":
        if normalized["id"] <= 0:
            raise ValueError("id must be a positive integer.")

    else:
        normalized["date"] = _require_str(normalized, "date", hint="Use YYYY-MM-DD.")

    return normalized


def _format_result(operation: str, result: Any, payload: dict[str, Any]) -> dict[str, Any]:
    if operation == "add_food_log":
        return {"food_log_id": result}

    if operation == "update_food_log":
        return {"food_log_id": payload["id"], "affected_rows": result, "updated": result > 0}

    if operation == "delete_food_log":
        return {"food_log_id": payload["id"], "affected_rows": result, "deleted": result > 0}

    if operation == "get_food_logs_by_date":
        return {"food_logs": result, "count": len(result)}

    if operation == "get_total_calories_for_date":
        return {"total_calories": result}

    if operation == "get_food_summary_for_date":
        return {"summary": result}

    if operation == "get_food_day_report":
        return result

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
        return (
            "Retry with operation and payload. Supported: add_food_log, update_food_log, delete_food_log, "
            "get_food_logs_by_date, get_total_calories_for_date, get_food_summary_for_date, get_food_day_report."
        )
    if operation == "add_food_log":
        return "Retry with food_name, calories, protein, carbs, fat, quantity, and meal_type."
    if operation == "update_food_log":
        return "Retry with payload.id and at least one field to update."
    if operation == "delete_food_log":
        return "Retry with payload.id as a positive integer."
    return "Retry with payload.date in YYYY-MM-DD format."


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
            return _ok(operation, _format_result(operation, result, payload))

    except Exception as exc:
        return _fail(operation, str(exc), _error_hint_for_error(operation))


def main() -> None:
    src_db = ROOT / "users" / "default" / "data.sqlite"
    test_root = Path.home() / ".codex" / "memories" / "skill_tool_tests"
    test_root.mkdir(parents=True, exist_ok=True)
    test_db = test_root / "add_food_log_test.sqlite"
    with sqlite3.connect(src_db) as src_conn, sqlite3.connect(test_db) as dst_conn:
        src_conn.backup(dst_conn)

    cases = [
        {
            "name": "add_food_log",
            "args": {
                "operation": "add_food_log",
                "payload": {
                    "food_name": "Tool Test Oats",
                    "calories": 210,
                    "protein": 8.0,
                    "carbs": 36.0,
                    "fat": 4.0,
                    "quantity": "1 bowl",
                    "meal_type": "breakfast",
                },
            },
        },
        {"name": "get_food_logs_by_date",
         "args": {"operation": "get_food_logs_by_date", "payload": {"date": "2026-03-25"}}},
        {"name": "get_total_calories_for_date",
         "args": {"operation": "get_total_calories_for_date", "payload": {"date": "2026-03-25"}}},
        {"name": "get_food_summary_for_date",
         "args": {"operation": "get_food_summary_for_date", "payload": {"date": "2026-03-25"}}},
        {"name": "get_food_day_report",
         "args": {"operation": "get_food_day_report", "payload": {"date": "2026-03-25"}}},
    ]

    outputs: list[dict[str, Any]] = []
    for case in cases:
        result = asyncio.run(process_args(case["args"], extra_args={"db_path": str(test_db)}))
        outputs.append({"case": case["name"], "result": result})

    print(json.dumps({"db_path": str(test_db), "tests": outputs}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
