from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3]

root_str = str(ROOT)
if root_str not in sys.path:
    sys.path.insert(0, root_str)

FORBIDDEN_SQL_KEYWORDS = {
    "drop",
    "delete",
    "alter",
    "truncate",
    "attach",
    "detach",
    "vacuum",
    "pragma",
}


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


def _rows_to_dicts(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
    columns = [description[0] for description in cursor.description or []]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _normalize_params(value: Any) -> list[Any] | dict[str, Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return value
    raise ValueError("params must be a list, dict, or null.")


def _parse_args(args: dict | str | None) -> dict[str, Any]:
    if args is None:
        parsed: dict[str, Any] = {}
    elif isinstance(args, str):
        parsed = json.loads(args)
    elif isinstance(args, dict):
        parsed = args
    else:
        raise ValueError("args must be a dict, JSON string, or null.")

    if not isinstance(parsed, dict):
        raise ValueError("args must deserialize to a dictionary.")

    if "operation" not in parsed and "query" in parsed:
        parsed = {
            "operation": "query_whitelisted",
            "payload": {
                "query": parsed.get("query"),
                "params": parsed.get("params"),
            },
        }

    return parsed


def _require_payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raise ValueError("payload must be a JSON object.")


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing required field: {key}.")
    return value.strip()


def _require_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer.") from exc


def _query_type(query: str) -> str:
    return query.lstrip().split(None, 1)[0].upper() if query.strip() else ""


def _ensure_crontab_schema(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(crontab)").fetchall()}
    if "thread_id" not in cols:
        conn.execute("ALTER TABLE crontab ADD COLUMN thread_id INTEGER")
        conn.commit()


def _ensure_whitelisted_query(query: str) -> None:
    if ";" in query.strip().rstrip(";"):
        raise ValueError("Only one SQL statement is allowed.")

    lower = query.lower()
    if "crontab" not in lower:
        raise ValueError("Only queries targeting the crontab table are allowed.")

    for keyword in FORBIDDEN_SQL_KEYWORDS:
        if re.search(rf"\b{keyword}\b", lower):
            raise ValueError(f"Query contains forbidden keyword: {keyword}.")

    kind = _query_type(query)
    if kind not in {"SELECT", "INSERT", "UPDATE"}:
        raise ValueError("Only SELECT, INSERT, and UPDATE are allowed in query_whitelisted mode.")


def _execute_read(conn: sqlite3.Connection, query: str, params: list[Any] | dict[str, Any]) -> dict[str, Any]:
    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = _rows_to_dicts(cursor)
    return {
        "query_type": _query_type(query),
        "row_count": len(rows),
        "rows": rows,
    }


def _execute_write(conn: sqlite3.Connection, query: str, params: list[Any] | dict[str, Any]) -> dict[str, Any]:
    cursor = conn.cursor()
    cursor.execute(query, params)
    conn.commit()
    return {
        "query_type": _query_type(query),
        "affected_rows": cursor.rowcount,
        "lastrowid": cursor.lastrowid,
    }


def _op_list_jobs(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    active_only = payload.get("active_only")
    if active_only is None:
        rows = conn.execute(
            "SELECT id, name, cron_expression, timezone, thread_id, is_active, next_run_at, updated_at FROM crontab ORDER BY id ASC"
        ).fetchall()
    else:
        active_int = 1 if bool(active_only) else 0
        rows = conn.execute(
            """
            SELECT id, name, cron_expression, timezone, thread_id, is_active, next_run_at, updated_at
            FROM crontab
            WHERE is_active = ?
            ORDER BY id ASC
            """,
            (active_int,),
        ).fetchall()
    items = [dict(row) for row in rows]
    return {"jobs": items, "count": len(items)}


def _op_create_job(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    name = _require_str(payload, "name")
    task_prompt = _require_str(payload, "task_prompt")
    cron_expression = _require_str(payload, "cron_expression")
    timezone = str(payload.get("timezone", "UTC")).strip() or "UTC"
    is_active = 1 if bool(payload.get("is_active", True)) else 0
    thread_id = payload.get("thread_id")
    if thread_id is not None:
        thread_id = _require_int(payload, "thread_id")

    cursor = conn.execute(
        """
        INSERT INTO crontab (name, task_prompt, cron_expression, timezone, thread_id, is_active)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, task_prompt, cron_expression, timezone, thread_id, is_active),
    )
    conn.commit()
    return {
        "job_id": int(cursor.lastrowid),
        "write_status": "created",
    }


def _op_update_task_prompt(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _require_int(payload, "id")
    task_prompt = _require_str(payload, "task_prompt")
    cursor = conn.execute(
        """
        UPDATE crontab
        SET task_prompt = ?,
            updated_at  = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (task_prompt, job_id),
    )
    conn.commit()
    return {
        "job_id": job_id,
        "affected_rows": cursor.rowcount,
        "write_status": "updated" if cursor.rowcount > 0 else "not_found",
    }


def _op_set_job_active(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _require_int(payload, "id")
    is_active = 1 if bool(payload.get("is_active", True)) else 0
    cursor = conn.execute(
        """
        UPDATE crontab
        SET is_active  = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (is_active, job_id),
    )
    conn.commit()
    return {
        "job_id": job_id,
        "is_active": is_active,
        "affected_rows": cursor.rowcount,
        "write_status": "updated" if cursor.rowcount > 0 else "not_found",
    }


def _op_set_job_thread_id(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    job_id = _require_int(payload, "id")
    thread_id = payload.get("thread_id")
    if thread_id is None:
        value = None
    else:
        value = _require_int(payload, "thread_id")

    cursor = conn.execute(
        """
        UPDATE crontab
        SET thread_id  = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (value, job_id),
    )
    conn.commit()
    return {
        "job_id": job_id,
        "thread_id": value,
        "affected_rows": cursor.rowcount,
        "write_status": "updated" if cursor.rowcount > 0 else "not_found",
    }


def _op_query_whitelisted(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    query = _require_str(payload, "query")
    params = _normalize_params(payload.get("params"))
    _ensure_whitelisted_query(query)
    if _query_type(query) == "SELECT":
        return _execute_read(conn, query, params)
    return _execute_write(conn, query, params)


OPERATIONS: dict[str, Callable[[sqlite3.Connection, dict[str, Any]], dict[str, Any]]] = {
    "list_jobs": _op_list_jobs,
    "create_job": _op_create_job,
    "update_task_prompt": _op_update_task_prompt,
    "set_job_active": _op_set_job_active,
    "set_job_thread_id": _op_set_job_thread_id,
    "query_whitelisted": _op_query_whitelisted,
}


def _supported_operations() -> str:
    return ", ".join(sorted(OPERATIONS))


def _error_hint_for_error(operation: str | None) -> str:
    if not operation:
        return "Retry with operation=list_jobs|create_job|update_task_prompt|set_job_active|set_job_thread_id|query_whitelisted."
    if operation == "query_whitelisted":
        return "Use one safe SELECT/INSERT/UPDATE statement on crontab with parameter placeholders."
    if operation == "create_job":
        return "Provide payload.name, payload.task_prompt, and payload.cron_expression."
    if operation in {"update_task_prompt", "set_job_active", "set_job_thread_id"}:
        return "Provide payload.id and required update fields, then retry."
    return "Retry with a supported operation and valid payload."


async def process_args(args: dict | str | None = None, extra_args: dict | None = None) -> dict[str, Any]:
    operation: str | None = None
    try:
        parsed_args = _parse_args(args)
        operation = parsed_args.get("operation")
        payload = _require_payload_dict(parsed_args.get("payload", {}))

        if not operation:
            raise ValueError(f"operation is required. Supported operations: {_supported_operations()}.")
        if operation not in OPERATIONS:
            raise ValueError(f"Unknown operation: {operation}. Supported operations: {_supported_operations()}.")

        if extra_args is None or "db_path" not in extra_args:
            raise ValueError("extra_args.db_path is required.")
        db_path = str(extra_args["db_path"])
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            _ensure_crontab_schema(conn)
            result = OPERATIONS[operation](conn, payload)
            return _ok(operation, result)

    except Exception as exc:
        return _fail(operation, str(exc), _error_hint_for_error(operation))


def main() -> None:
    src_db = ROOT / "users" / "default" / "data.sqlite"
    test_root = Path.home() / ".codex" / "memories" / "skill_tool_tests"
    test_root.mkdir(parents=True, exist_ok=True)
    test_db = test_root / "crontab_scheduler_test.sqlite"
    with sqlite3.connect(src_db) as src_conn, sqlite3.connect(test_db) as dst_conn:
        src_conn.backup(dst_conn)

    with sqlite3.connect(test_db) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crontab
            (
                id
                INTEGER
                PRIMARY
                KEY
                AUTOINCREMENT,
                name
                TEXT
                NOT
                NULL,
                task_prompt
                TEXT
                NOT
                NULL,
                cron_expression
                TEXT
                NOT
                NULL,
                timezone
                TEXT
                NOT
                NULL
                DEFAULT
                'UTC',
                thread_id
                INTEGER,
                is_active
                INTEGER
                NOT
                NULL
                DEFAULT
                1,
                last_run_at
                DATETIME,
                next_run_at
                DATETIME,
                created_at
                DATETIME
                NOT
                NULL
                DEFAULT
                CURRENT_TIMESTAMP,
                updated_at
                DATETIME
                NOT
                NULL
                DEFAULT
                CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
        cols = {row[1] for row in conn.execute("PRAGMA table_info(crontab)").fetchall()}
        if "thread_id" not in cols:
            conn.execute("ALTER TABLE crontab ADD COLUMN thread_id INTEGER")
            conn.commit()

    import asyncio

    outputs: list[dict[str, Any]] = []

    list_result = asyncio.run(process_args({"operation": "list_jobs", "payload": {}}, {"db_path": str(test_db)}))
    outputs.append({"case": "list_jobs", "result": list_result})

    create_result = asyncio.run(
        process_args(
            {
                "operation": "create_job",
                "payload": {
                    "name": "tool-main-test-job",
                    "task_prompt": "say hello from test",
                    "cron_expression": "*/15 * * * *",
                    "timezone": "UTC",
                    "thread_id": 1043,
                    "is_active": True,
                },
            },
            {"db_path": str(test_db)},
        )
    )
    outputs.append({"case": "create_job", "result": create_result})

    created_id = (((create_result or {}).get("result") or {}).get("job_id")) if isinstance(create_result,
                                                                                           dict) else None
    if isinstance(created_id, int):
        update_result = asyncio.run(
            process_args(
                {"operation": "update_task_prompt",
                 "payload": {"id": created_id, "task_prompt": "updated prompt from main test"}},
                {"db_path": str(test_db)},
            )
        )
        outputs.append({"case": "update_task_prompt", "result": update_result})

        active_result = asyncio.run(
            process_args(
                {"operation": "set_job_active", "payload": {"id": created_id, "is_active": False}},
                {"db_path": str(test_db)},
            )
        )
        outputs.append({"case": "set_job_active", "result": active_result})

        thread_result = asyncio.run(
            process_args(
                {"operation": "set_job_thread_id", "payload": {"id": created_id, "thread_id": 1043}},
                {"db_path": str(test_db)},
            )
        )
        outputs.append({"case": "set_job_thread_id", "result": thread_result})

        query_result = asyncio.run(
            process_args(
                {
                    "operation": "query_whitelisted",
                    "payload": {
                        "query": "SELECT id, name, is_active FROM crontab WHERE id = ?",
                        "params": [created_id],
                    },
                },
                {"db_path": str(test_db)},
            )
        )
        outputs.append({"case": "query_whitelisted", "result": query_result})

    print(json.dumps({"db_path": str(test_db), "tests": outputs}, ensure_ascii=True))


if __name__ == "__main__":
    main()
