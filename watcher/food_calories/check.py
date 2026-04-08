from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


def _ro_conn(db_path: str) -> sqlite3.Connection:
    path = Path(str(db_path)).resolve()
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def check(context: Dict[str, Any]) -> Dict[str, Any]:
    db_path = context.get("db_path", "data.sqlite")
    settings = context.get("settings", {})
    target = int((settings.get("targets") or {}).get("daily_calories", 2200))
    today = str(context.get("today_local") or datetime.utcnow().date().isoformat())

    total = 0
    count = 0
    try:
        conn = _ro_conn(str(db_path))
        row = conn.execute(
            """
            SELECT COALESCE(SUM(calories), 0), COUNT(*)
            FROM food_logs
            WHERE date (COALESCE (logged_at, created_at)) = date (?)
            """,
            (today,),
        ).fetchone()
        if row:
            total = int(row[0] or 0)
            count = int(row[1] or 0)
        conn.close()
    except Exception as exc:
        msg = str(exc)
        if isinstance(exc, sqlite3.OperationalError) and "no such table" in msg.lower():
            return {
                "status": "ok",
                "title": "Food and calories",
                "summary": "food_logs table is not configured for this user DB.",
                "facts": {"missing_table": "food_logs"},
                "severity": "low",
                "action_required": False,
                "dedupe_fields": {"date": today, "missing_table": "food_logs"},
            }
        return {
            "status": "error",
            "title": "Food calories watcher failed",
            "summary": msg,
            "facts": {"error": msg},
            "severity": "high",
            "action_required": False,
            "dedupe_fields": {"date": today, "error": msg},
        }

    action_required = count == 0
    severity = "medium" if action_required else "low"
    summary = "No food logs today yet." if action_required else f"{count} food log(s), {total} kcal logged today."

    if count > 0 and total < int(target * 0.5):
        severity = "medium"
        action_required = True
        summary = f"Only {total} kcal logged today (target {target})."

    return {
        "status": "ok",
        "title": "Food and calories",
        "summary": summary,
        "facts": {
            "date": today,
            "meal_logs": count,
            "total_calories": total,
            "target_calories": target,
        },
        "severity": severity,
        "action_required": action_required,
        "action_key": f"food_log_{today}",
        "dedupe_fields": {"date": today, "count": count, "total": total},
    }
