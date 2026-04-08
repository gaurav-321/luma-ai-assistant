"""Repository helpers for nutrition logging."""

from __future__ import annotations

import sqlite3
from typing import Any


def add_food_log(
        conn: sqlite3.Connection,
        food_name: str,
        calories: int,
        protein: float,
        carbs: float,
        fat: float,
        quantity: str,
        meal_type: str,
        notes: str | None = None,
        logged_at: str | None = None,
) -> int:
    """Insert one food log row and return its id."""
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO food_logs (food_name, calories, protein, carbs, fat,
                                   quantity, meal_type, logged_at, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?)
            """,
            (food_name, calories, protein, carbs, fat, quantity, meal_type, logged_at, notes),
        )
    return int(cursor.lastrowid)


def get_food_logs_by_date(conn: sqlite3.Connection, date: str) -> list[dict[str, Any]]:
    """Return all food logs on a given YYYY-MM-DD date."""
    rows = conn.execute(
        """
        SELECT *
        from food_logs
        WHERE DATE (logged_at) = ?
        ORDER BY logged_at ASC, id ASC
        """,
        (date,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_total_calories_for_date(conn: sqlite3.Connection, date: str) -> int:
    """Return total calories for the given date."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(calories), 0) AS total_calories
        from food_logs
        WHERE DATE (logged_at) = ?
        """,
        (date,),
    ).fetchone()
    return int(row["total_calories"])


def get_food_summary_for_date(conn: sqlite3.Connection, date: str) -> dict[str, float]:
    """Return macro totals for a specific date."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(calories), 0) AS calories,
               COALESCE(SUM(protein), 0)  AS protein,
               COALESCE(SUM(carbs), 0)    AS carbs,
               COALESCE(SUM(fat), 0)      AS fat
        from food_logs
        WHERE DATE (logged_at) = ?
        """,
        (date,),
    ).fetchone()
    return {
        "calories": float(row["calories"]),
        "protein": float(row["protein"]),
        "carbs": float(row["carbs"]),
        "fat": float(row["fat"]),
    }


def update_food_log(
        conn: sqlite3.Connection,
        id: int,
        **updates: Any,
) -> int:
    """Update one food log row by id and return affected row count."""
    allowed = {
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
    fields = [key for key in updates.keys() if key in allowed]
    if not fields:
        return 0

    set_clause = ", ".join(f"{field} = ?" for field in fields)
    params = [updates[field] for field in fields]
    params.append(id)

    with conn:
        cursor = conn.execute(
            f"UPDATE food_logs SET {set_clause} WHERE id = ?",
            tuple(params),
        )
    return int(cursor.rowcount)


def delete_food_log(conn: sqlite3.Connection, id: int) -> int:
    """Delete one food log row by id and return affected row count."""
    with conn:
        cursor = conn.execute(
            "DELETE FROM food_logs WHERE id = ?",
            (id,),
        )
    return int(cursor.rowcount)


def get_food_day_report(conn: sqlite3.Connection, date: str) -> dict[str, Any]:
    """Return one combined daily report (logs + total calories + macro summary)."""
    logs = get_food_logs_by_date(conn=conn, date=date)
    total_calories = get_total_calories_for_date(conn=conn, date=date)
    summary = get_food_summary_for_date(conn=conn, date=date)
    return {
        "date": date,
        "food_logs": logs,
        "count": len(logs),
        "total_calories": total_calories,
        "summary": summary,
    }
