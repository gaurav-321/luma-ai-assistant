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

from skills.personal.todo_lists import tasks

OPERATIONS = {
    "create_task": tasks.create_task,
    "list_tasks": tasks.list_tasks,
    "complete_task": tasks.complete_task,
    "create_subtask": tasks.create_subtask,
    "list_subtasks": tasks.list_subtasks,
    "add_task_update": tasks.add_task_update,
    "list_task_updates": tasks.list_task_updates,
    "get_completed_tasks_for_date": tasks.get_completed_tasks_for_date,
    "get_pending_tasks": tasks.get_pending_tasks,
}

REQUIRED_FIELDS = {
    "create_task": ("title",),
    "list_tasks": (),
    "complete_task": ("task_id",),
    "create_subtask": ("task_id", "title"),
    "list_subtasks": ("task_id",),
    "add_task_update": ("task_id", "update_text"),
    "list_task_updates": ("task_id",),
    "get_completed_tasks_for_date": ("date",),
    "get_pending_tasks": (),
}

INT_FIELDS = {
    "create_task": ("priority",),
    "list_tasks": (),
    "complete_task": ("task_id",),
    "create_subtask": ("task_id",),
    "list_subtasks": ("task_id",),
    "add_task_update": ("task_id",),
    "list_task_updates": ("task_id",),
    "get_completed_tasks_for_date": (),
    "get_pending_tasks": (),
}

ALLOWED_FIELDS = {
    "create_task": ("title", "description", "status", "priority", "due_date"),
    "list_tasks": ("status",),
    "complete_task": ("task_id", "completed_at"),
    "create_subtask": ("task_id", "title"),
    "list_subtasks": ("task_id",),
    "add_task_update": ("task_id", "update_text"),
    "list_task_updates": ("task_id",),
    "get_completed_tasks_for_date": ("date",),
    "get_pending_tasks": (),
}


def _supported_operations() -> str:
    return ", ".join(sorted(OPERATIONS))


def _require_payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raise ValueError("payload must be a JSON object.")


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required field: {key}.")
    return value.strip()


def _require_db_path(extra_args: dict[str, Any] | None) -> str:
    db_path = (extra_args or {}).get("db_path")
    if not isinstance(db_path, str) or not db_path.strip():
        raise ValueError("extra_args.db_path is required.")
    return db_path


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


def _validate_payload(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
    for field in REQUIRED_FIELDS[operation]:
        if field not in payload or payload[field] is None or payload[field] == "":
            raise ValueError(f"Missing required field: {field}.")

    normalized: dict[str, Any] = {}
    for field in ALLOWED_FIELDS[operation]:
        if field in payload:
            normalized[field] = payload[field]

    for field in INT_FIELDS[operation]:
        _coerce_int(normalized, field)

    if operation == "create_task":
        normalized["title"] = _require_str(normalized, "title")
        normalized["description"] = str(normalized.get("description", "")).strip()
        normalized["status"] = str(normalized.get("status", "pending")).strip() or "pending"
        normalized["priority"] = normalized.get("priority", 3)
        due_date = normalized.get("due_date")
        if due_date is not None and not isinstance(due_date, str):
            raise ValueError("due_date must be a string in YYYY-MM-DD format when provided.")

    elif operation == "list_tasks":
        status = normalized.get("status")
        if status is not None:
            if not isinstance(status, str) or not status.strip():
                raise ValueError("status must be a non-empty string when provided.")
            normalized["status"] = status.strip()

    elif operation == "complete_task":
        completed_at = normalized.get("completed_at")
        if completed_at is not None and (not isinstance(completed_at, str) or not completed_at.strip()):
            raise ValueError("completed_at must be a non-empty string when provided.")

    elif operation == "create_subtask":
        normalized["title"] = _require_str(normalized, "title")

    elif operation == "add_task_update":
        normalized["update_text"] = _require_str(normalized, "update_text")

    elif operation == "get_completed_tasks_for_date":
        date = normalized.get("date")
        if not isinstance(date, str) or not date.strip():
            raise ValueError("Missing required field: date. Use YYYY-MM-DD.")
        normalized["date"] = date.strip()

    return normalized


def _format_result(operation: str, result: Any) -> dict[str, Any]:
    if operation == "create_task":
        return {"task_id": result}
    if operation == "list_tasks":
        return {"tasks": result, "count": len(result)}
    if operation == "complete_task":
        return {"affected_rows": result}
    if operation == "create_subtask":
        return {"subtask_id": result}
    if operation == "list_subtasks":
        return {"subtasks": result, "count": len(result)}
    if operation == "add_task_update":
        return {"task_update_id": result}
    if operation == "list_task_updates":
        return {"task_updates": result, "count": len(result)}
    if operation == "get_completed_tasks_for_date":
        return {"tasks": result, "count": len(result)}
    if operation == "get_pending_tasks":
        return {"tasks": result, "count": len(result)}
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
        return "Retry with operation and payload. Supported operations are listed in the tool schema."
    if operation in {"complete_task", "create_subtask", "list_subtasks", "add_task_update", "list_task_updates"}:
        return "Retry with a valid payload.task_id."
    if operation == "create_task":
        return "Retry with payload.title and optional description/priority/due_date."
    if operation == "get_completed_tasks_for_date":
        return "Retry with payload.date in YYYY-MM-DD format."
    return "Retry with valid payload for this operation."


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
    test_db = test_root / "todo_lists_test.sqlite"
    with sqlite3.connect(src_db) as src_conn, sqlite3.connect(test_db) as dst_conn:
        src_conn.backup(dst_conn)

    cases = [
        {"name": "create_task", "args": {"operation": "create_task",
                                         "payload": {"title": "Tool test task", "description": "task from tool main",
                                                     "priority": 2}}},
        {"name": "list_tasks", "args": {"operation": "list_tasks", "payload": {}}},
        {"name": "complete_task", "args": {"operation": "complete_task", "payload": {"task_id": 1}}},
        {"name": "create_subtask",
         "args": {"operation": "create_subtask", "payload": {"task_id": 1, "title": "Tool subtask"}}},
        {"name": "list_subtasks", "args": {"operation": "list_subtasks", "payload": {"task_id": 1}}},
        {"name": "add_task_update",
         "args": {"operation": "add_task_update", "payload": {"task_id": 1, "update_text": "Tool update"}}},
        {"name": "list_task_updates", "args": {"operation": "list_task_updates", "payload": {"task_id": 1}}},
        {"name": "get_completed_tasks_for_date",
         "args": {"operation": "get_completed_tasks_for_date", "payload": {"date": "2026-03-25"}}},
        {"name": "get_pending_tasks", "args": {"operation": "get_pending_tasks", "payload": {}}},
    ]

    outputs: list[dict[str, Any]] = []
    for case in cases:
        result = asyncio.run(process_args(case["args"], extra_args={"db_path": str(test_db)}))
        outputs.append({"case": case["name"], "result": result})

    print(json.dumps({"db_path": str(test_db), "tests": outputs}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
