import asyncio
import sqlite3
from datetime import datetime, timedelta, UTC
from pathlib import Path
from zoneinfo import ZoneInfo

from core.heartbeat import WorkItem, msg_queue
from core.utils.config import logger, ROOT, default_chat_id
from core.workers.main_worker import _build_scheduled_work_item, SCHEDULER_POLL_SECONDS


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _user_db_paths() -> list[Path]:
    db_path = ROOT / "users" / "default" / "data.sqlite"
    return [db_path] if db_path.is_file() else []


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    cursor = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
        (table_name,),
    )
    return cursor.fetchone() is not None


def _ensure_crontab_schema(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(crontab)").fetchall()}
    if "thread_id" not in cols:
        conn.execute("ALTER TABLE crontab ADD COLUMN thread_id INTEGER")
        conn.commit()


def _parse_cron_field(field: str, minimum: int, maximum: int) -> set[int]:
    values: set[int] = set()

    for part in field.split(","):
        part = part.strip()
        if not part:
            continue

        step = 1
        base = part
        if "/" in part:
            base, step_part = part.split("/", 1)
            step = int(step_part)
            if step <= 0:
                raise ValueError(f"Invalid cron step: {part}")

        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_str, end_str = base.split("-", 1)
            start, end = int(start_str), int(end_str)
        else:
            value = int(base)
            start = value
            end = value

        if start < minimum or end > maximum or start > end:
            raise ValueError(f"Cron field out of range: {part}")

        values.update(range(start, end + 1, step))

    if not values:
        raise ValueError("Cron field produced no values")

    return values


class CronSchedule:
    def __init__(self, expression: str) -> None:
        fields = expression.split()
        if len(fields) != 5:
            raise ValueError(f"Expected 5 cron fields, got {len(fields)} in '{expression}'")

        minute, hour, day, month, weekday = fields
        self.minutes = _parse_cron_field(minute, 0, 59)
        self.hours = _parse_cron_field(hour, 0, 23)
        self.days = _parse_cron_field(day, 1, 31)
        self.months = _parse_cron_field(month, 1, 12)
        weekday_values = _parse_cron_field(weekday.replace("7", "0"), 0, 6)
        self.weekdays = {0 if value == 7 else value for value in weekday_values}

    def matches(self, dt: datetime) -> bool:
        cron_weekday = (dt.weekday() + 1) % 7
        return (
                dt.minute in self.minutes
                and dt.hour in self.hours
                and dt.day in self.days
                and dt.month in self.months
                and cron_weekday in self.weekdays
        )

    def next_after(self, dt: datetime) -> datetime:
        candidate = dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
        deadline = candidate + timedelta(days=366)
        while candidate <= deadline:
            if self.matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        raise ValueError("Could not resolve next cron run within 366 days")


def _next_run_at(cron_expression: str, timezone_name: str, reference_utc: datetime) -> datetime:
    tz = ZoneInfo(timezone_name or "UTC")
    schedule = CronSchedule(cron_expression)
    local_reference = reference_utc.astimezone(tz)
    next_local = schedule.next_after(local_reference)
    return next_local.astimezone(UTC)


def _format_db_timestamp(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _parse_db_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _sync_due_schedules(now_utc: datetime) -> list[WorkItem]:
    due_items: list[WorkItem] = []

    for db_path in _user_db_paths():
        username = "default"
        chat_id = int(default_chat_id)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            if not _table_exists(conn, "crontab"):
                continue
            _ensure_crontab_schema(conn)

            rows = conn.execute(
                """
                SELECT id,
                       name,
                       task_prompt,
                       cron_expression,
                       timezone,
                       thread_id,
                       is_active,
                       last_run_at,
                       next_run_at
                FROM crontab
                WHERE is_active = 1
                ORDER BY id
                """
            ).fetchall()

            for row in rows:
                try:
                    timezone_name = row["timezone"] or "UTC"
                    last_run_at = _parse_db_timestamp(row["last_run_at"])
                    next_run_at = _parse_db_timestamp(row["next_run_at"])

                    if next_run_at is None:
                        reference = last_run_at or now_utc
                        next_run_at = _next_run_at(row["cron_expression"], timezone_name, reference)
                        conn.execute(
                            "UPDATE crontab SET next_run_at = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (_format_db_timestamp(next_run_at), row["id"]),
                        )
                        conn.commit()

                    if next_run_at > now_utc:
                        continue

                    next_after_due = _next_run_at(row["cron_expression"], timezone_name, now_utc)
                    conn.execute(
                        """
                        UPDATE crontab
                        SET last_run_at = ?,
                            next_run_at = ?,
                            updated_at  = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (
                            _format_db_timestamp(now_utc),
                            _format_db_timestamp(next_after_due),
                            row["id"],
                        ),
                    )
                    conn.commit()
                    due_items.append(_build_scheduled_work_item(username, chat_id, row))
                except Exception:
                    logger.exception(
                        "Failed to process cron row id=%s for user=%s",
                        row["id"],
                        username,
                    )
        finally:
            conn.close()

    return due_items


async def scheduler_loop() -> None:
    while True:
        try:
            now_utc = _utc_now()
            for work_item in _sync_due_schedules(now_utc):
                logger.info(
                    "Queued scheduled job id=%s user=%s",
                    work_item.metadata.get("schedule_id"),
                    work_item.username,
                )
                await msg_queue.put(work_item)
        except Exception:
            logger.exception("scheduler_loop iteration failed")

        await asyncio.sleep(SCHEDULER_POLL_SECONDS)
