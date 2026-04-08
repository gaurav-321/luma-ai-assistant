"""Repository helpers for tasks, subtasks, and task updates."""

from __future__ import annotations

import sqlite3
from typing import Any


def _ensure_support_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subtasks
        (
            id
            INTEGER
            PRIMARY
            KEY
            AUTOINCREMENT,
            task_id
            INTEGER
            NOT
            NULL,
            title
            TEXT
            NOT
            NULL,
            created_at
            TEXT
            NOT
            NULL
            DEFAULT
            CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_updates
        (
            id
            INTEGER
            PRIMARY
            KEY
            AUTOINCREMENT,
            task_id
            INTEGER
            NOT
            NULL,
            update_text
            TEXT
            NOT
            NULL,
            created_at
            TEXT
            NOT
            NULL
            DEFAULT
            CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def create_task(
        conn: sqlite3.Connection,
        title: str,
        description: str,
        status: str = "pending",
        priority: int = 3,
        due_date: str | None = None,
) -> int:
    """Create a task and return its id."""
    _ensure_support_tables(conn)
    with conn:
        cursor = conn.execute(
            """
            INSERT INTO tasks (title, details, status, priority, due_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (title, description, status, priority, due_date),
        )
    return int(cursor.lastrowid)


def list_tasks(conn: sqlite3.Connection, status: str | None = None) -> list[dict[str, Any]]:
    """List tasks, optionally filtered by status."""
    _ensure_support_tables(conn)
    if status is None:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY priority ASC, created_at ASC, id ASC",
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT *
            FROM tasks
            WHERE status = ?
            ORDER BY priority ASC, created_at ASC, id ASC
            """,
            (status,),
        ).fetchall()
    return [dict(row) for row in rows]


def complete_task(
        conn: sqlite3.Connection,
        task_id: int,
        completed_at: str | None = None,
) -> int:
    """Mark a task as completed and return the affected row count."""
    _ensure_support_tables(conn)
    with conn:
        cursor = conn.execute(
            """
            UPDATE tasks
            SET status       = 'completed',
                completed_at = COALESCE(?, CURRENT_TIMESTAMP)
            WHERE id = ?
            """,
            (completed_at, task_id),
        )
    return cursor.rowcount


def create_subtask(conn: sqlite3.Connection, task_id: int, title: str) -> int:
    """Create a subtask for the given task."""
    _ensure_support_tables(conn)
    with conn:
        cursor = conn.execute(
            "INSERT INTO subtasks (task_id, title) VALUES (?, ?)",
            (task_id, title),
        )
    return int(cursor.lastrowid)


def list_subtasks(conn: sqlite3.Connection, task_id: int) -> list[dict[str, Any]]:
    """List subtasks for one task."""
    _ensure_support_tables(conn)
    rows = conn.execute(
        "SELECT * FROM subtasks WHERE task_id = ? ORDER BY id ASC",
        (task_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def add_task_update(conn: sqlite3.Connection, task_id: int, update_text: str) -> int:
    """Append an update row to a task."""
    _ensure_support_tables(conn)
    with conn:
        cursor = conn.execute(
            "INSERT INTO task_updates (task_id, update_text) VALUES (?, ?)",
            (task_id, update_text),
        )
    return int(cursor.lastrowid)


def list_task_updates(conn: sqlite3.Connection, task_id: int) -> list[dict[str, Any]]:
    """List updates for a task."""
    _ensure_support_tables(conn)
    rows = conn.execute(
        "SELECT * FROM task_updates WHERE task_id = ? ORDER BY created_at ASC, id ASC",
        (task_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_completed_tasks_for_date(
        conn: sqlite3.Connection,
        date: str,
) -> list[dict[str, Any]]:
    """Return tasks completed on the given YYYY-MM-DD date."""
    _ensure_support_tables(conn)
    rows = conn.execute(
        """
        SELECT *
        FROM tasks
        WHERE status = 'completed'
          AND DATE (completed_at) = ?
        ORDER BY completed_at ASC, id ASC
        """,
        (date,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_pending_tasks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return pending tasks."""
    _ensure_support_tables(conn)
    return list_tasks(conn, status="pending")
