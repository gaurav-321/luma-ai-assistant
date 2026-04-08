from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict


def _ro_conn(db_path: str) -> sqlite3.Connection:
    path = Path(str(db_path)).resolve()
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def check(context: Dict[str, Any]) -> Dict[str, Any]:
    db_path = context.get("db_path", "data.sqlite")
    now_utc = datetime.fromisoformat(str(context.get("now_utc")).replace("Z", "+00:00"))
    in_24h = (now_utc + timedelta(hours=24)).isoformat()

    try:
        conn = _ro_conn(str(db_path))
        overdue = conn.execute(
            """
            SELECT id, title, priority, due_at
            FROM tasks
            WHERE status != 'completed' AND due_at IS NOT NULL AND datetime(due_at) <= datetime(?)
            ORDER BY priority ASC, datetime(due_at) ASC
                LIMIT 10
            """,
            (now_utc.isoformat(),),
        ).fetchall()

        upcoming = conn.execute(
            """
            SELECT id, title, priority, due_at
            FROM tasks
            WHERE status != 'completed' AND due_at IS NOT NULL
              AND datetime(due_at) > datetime(?)
              AND datetime(due_at) <= datetime(?)
            ORDER BY priority ASC, datetime(due_at) ASC
                LIMIT 10
            """,
            (now_utc.isoformat(), in_24h),
        ).fetchall()
        conn.close()
    except Exception as exc:
        msg = str(exc)
        if isinstance(exc, sqlite3.OperationalError) and "no such table" in msg.lower():
            return {
                "status": "ok",
                "title": "Urgent task monitor",
                "summary": "tasks table is not configured for this user DB.",
                "facts": {"missing_table": "tasks"},
                "severity": "low",
                "action_required": False,
                "dedupe_fields": {"missing_table": "tasks"},
            }
        return {
            "status": "error",
            "title": "Urgent tasks watcher failed",
            "summary": msg,
            "facts": {"error": msg},
            "severity": "high",
            "action_required": False,
            "dedupe_fields": {"error": msg},
        }

    overdue_count = len(overdue)
    upcoming_count = len(upcoming)
    action_required = overdue_count > 0 or upcoming_count > 0

    if overdue_count > 0:
        severity = "high"
        summary = f"{overdue_count} overdue task(s) need attention."
    elif upcoming_count > 0:
        severity = "medium"
        summary = f"{upcoming_count} task(s) due in next 24 hours."
    else:
        severity = "low"
        summary = "No urgent pending tasks right now."

    sample_titles = [r[1] for r in (overdue + upcoming)[:5]]

    return {
        "status": "ok",
        "title": "Urgent task monitor",
        "summary": summary,
        "facts": {
            "overdue_count": overdue_count,
            "due_next_24h_count": upcoming_count,
            "sample_titles": sample_titles,
        },
        "severity": severity,
        "action_required": action_required,
        "action_key": "resolve_urgent_tasks",
        "dedupe_fields": {
            "overdue": overdue_count,
            "upcoming": upcoming_count,
            "titles": sample_titles,
        },
    }
