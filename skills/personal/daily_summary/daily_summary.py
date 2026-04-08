"""Repository helpers for markdown daily summaries."""

from __future__ import annotations

import sqlite3
from typing import Any


def upsert_daily_summary(
        conn: sqlite3.Connection,
        date: str,
        markdown_summary: str,
) -> dict[str, Any]:
    """Insert or update one daily summary and return write metadata."""
    existing = conn.execute(
        "SELECT id FROM daily_summary WHERE date = ?",
        (date,),
    ).fetchone()

    write_status = "created" if existing is None else "updated"

    with conn:
        cursor = conn.execute(
            """
            INSERT INTO daily_summary (date, markdown_summary)
            VALUES (?, ?) ON CONFLICT(date)
            DO
            UPDATE SET markdown_summary = excluded.markdown_summary
            """,
            (date, markdown_summary),
        )

    if existing is None:
        summary_id = int(cursor.lastrowid)
    else:
        summary_id = int(existing["id"])

    return {
        "summary_id": summary_id,
        "date": date,
        "write_status": write_status,
    }


def get_daily_summary(conn: sqlite3.Connection, date: str) -> dict[str, Any] | None:
    """Fetch one daily summary by date."""
    row = conn.execute(
        "SELECT * FROM daily_summary WHERE date = ?",
        (date,),
    ).fetchone()
    return dict(row) if row is not None else None
