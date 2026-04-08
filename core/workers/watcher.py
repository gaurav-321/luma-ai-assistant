import asyncio
import importlib.util
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from core.heartbeat import WorkItem, msg_queue
from core.utils.config import ROOT, default_chat_id
from core.webui import debug_hub

SINGLE_USER_ID = "default"

watcher_modules = [
    x
    for x in os.listdir(ROOT / "watcher")
    if Path(os.path.join(ROOT, "watcher", x, "check.py")).exists()
]

DATA_DIR = ROOT / "watcher" / "data"
WATCHER_DB_PATH = DATA_DIR / "watcher.db"
DEFAULT_CRON_EXPR = "0 * * * *"
WORKER_POLL_SECONDS = 20
MIN_REMINDER_REPEAT_MINUTES = 360


def _init_watcher_db(db_path: Path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS watcher_runs
        (
            user_id
            TEXT
            NOT
            NULL,
            watcher
            TEXT
            NOT
            NULL,
            last_run_at
            TEXT
            NOT
            NULL,
            last_run_day
            TEXT
            NOT
            NULL,
            runs_today
            INTEGER
            NOT
            NULL
            DEFAULT
            0,
            last_status
            TEXT,
            last_title
            TEXT,
            last_summary
            TEXT,
            last_severity
            TEXT,
            action_required
            INTEGER
            NOT
            NULL
            DEFAULT
            0,
            last_facts_json
            TEXT,
            updated_at
            TEXT
            NOT
            NULL,
            PRIMARY
            KEY
        (
            user_id,
            watcher
        )
            )
        """
    )

    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(watcher_runs)").fetchall()}
    if "last_run_day" not in existing_cols:
        conn.execute("ALTER TABLE watcher_runs ADD COLUMN last_run_day TEXT")
    if "runs_today" not in existing_cols:
        conn.execute("ALTER TABLE watcher_runs ADD COLUMN runs_today INTEGER NOT NULL DEFAULT 0")

    conn.execute(
        """
        UPDATE watcher_runs
        SET last_run_day = COALESCE(last_run_day, substr(last_run_at, 1, 10)),
            runs_today   = CASE WHEN runs_today <= 0 THEN 1 ELSE runs_today END
        """
    )
    conn.commit()
    return conn


def _upsert_watcher_run(
        conn: sqlite3.Connection,
        user_id: str,
        watcher: str,
        run_at: str,
        run_day: str,
        result: dict,
):
    facts_json = json.dumps(result.get("facts", {}), ensure_ascii=True, sort_keys=True)
    conn.execute(
        """
        INSERT INTO watcher_runs (user_id, watcher, last_run_at, last_status, last_title, last_summary,
                                  last_severity, action_required, last_facts_json, last_run_day, runs_today, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?) ON CONFLICT(user_id, watcher) DO
        UPDATE SET
            last_run_at = excluded.last_run_at,
            last_status = excluded.last_status,
            last_title = excluded.last_title,
            last_summary = excluded.last_summary,
            last_severity = excluded.last_severity,
            action_required = excluded.action_required,
            last_facts_json = excluded.last_facts_json,
            last_run_day = excluded.last_run_day,
            runs_today = CASE
            WHEN watcher_runs.last_run_day = excluded.last_run_day THEN watcher_runs.runs_today + 1
            ELSE 1
        END
        ,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            watcher,
            run_at,
            result.get("status"),
            result.get("title"),
            result.get("summary"),
            result.get("severity"),
            1 if result.get("action_required") else 0,
            facts_json,
            run_day,
            run_at,
        ),
    )


def _load_watcher_module(watcher_module: str):
    module_path = ROOT / "watcher" / watcher_module / "check.py"
    spec = importlib.util.spec_from_file_location(f"{watcher_module}_check", module_path)
    module = importlib.util.module_from_spec(spec)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load watcher module: {module_path}")
    spec.loader.exec_module(module)
    return module


def _load_settings(watcher_module: str):
    settings_path = ROOT / "watcher" / watcher_module / "settings.yaml"
    if not settings_path.exists():
        settings_path = ROOT / "watcher" / watcher_module / "settings.yml"
    with open(settings_path, "r", encoding="utf-8") as fh:
        settings = yaml.load(fh, Loader=yaml.SafeLoader) or {}
    reminder = settings.get("reminder")
    if not isinstance(reminder, dict):
        reminder = {}
        settings["reminder"] = reminder

    repeat_raw = reminder.get("repeat_every_minutes", MIN_REMINDER_REPEAT_MINUTES)
    try:
        repeat_minutes = int(repeat_raw)
    except Exception:
        repeat_minutes = MIN_REMINDER_REPEAT_MINUTES
    reminder["repeat_every_minutes"] = max(repeat_minutes, MIN_REMINDER_REPEAT_MINUTES)
    return settings


def _safe_timezone(tz_name: str):
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def _normalize_cron_expr(cron_expr: str) -> str:
    candidate = str(cron_expr or DEFAULT_CRON_EXPR).strip()
    if len(candidate.split()) != 5:
        return DEFAULT_CRON_EXPR
    return candidate


def _match_cron_field(value: int, field: str, min_value: int, max_value: int) -> bool:
    for token in field.split(","):
        token = token.strip()
        if not token:
            continue
        if token == "*":
            return True
        if token.startswith("*/"):
            step_text = token[2:]
            if step_text.isdigit():
                step = int(step_text)
                if step > 0 and value % step == 0:
                    return True
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            if start_text.isdigit() and end_text.isdigit():
                start = int(start_text)
                end = int(end_text)
                if min_value <= start <= max_value and min_value <= end <= max_value and start <= value <= end:
                    return True
            continue
        if token.isdigit():
            number = int(token)
            if min_value <= number <= max_value and number == value:
                return True
    return False


def _cron_matches(local_dt: datetime, cron_expr: str) -> bool:
    minute_field, hour_field, day_field, month_field, dow_field = _normalize_cron_expr(cron_expr).split()
    cron_dow = (local_dt.weekday() + 1) % 7
    return (
            _match_cron_field(local_dt.minute, minute_field, 0, 59)
            and _match_cron_field(local_dt.hour, hour_field, 0, 23)
            and _match_cron_field(local_dt.day, day_field, 1, 31)
            and _match_cron_field(local_dt.month, month_field, 1, 12)
            and _match_cron_field(cron_dow, dow_field.replace("7", "0"), 0, 6)
    )


def _next_run_utc(cron_expr: str, tz_name: str, from_utc: datetime) -> datetime:
    tz = _safe_timezone(tz_name)
    local_candidate = from_utc.astimezone(tz).replace(second=0, microsecond=0) + timedelta(minutes=1)

    for _ in range(60 * 24 * 370):
        if _cron_matches(local_candidate, cron_expr):
            return local_candidate.astimezone(timezone.utc)
        local_candidate += timedelta(minutes=1)

    return from_utc + timedelta(hours=1)


def _watcher_allowed_for_user(settings: dict, user_id: str) -> bool:
    controller = settings.get("controller") or {}
    scope = str(controller.get("scope") or "all").strip().lower()
    allowed_users = controller.get("users") or []

    if scope == "users":
        return user_id in allowed_users
    return True


def _format_value_lines(payload: dict) -> list[str]:
    lines: list[str] = []
    for key, value in (payload or {}).items():
        if isinstance(value, (dict, list)):
            pretty_value = json.dumps(value, ensure_ascii=True, sort_keys=True)
        else:
            pretty_value = str(value)
        lines.append(f"- **{key}**: {pretty_value}")
    if not lines:
        lines.append("- No values were returned.")
    return lines


def build_query_prompt(
        current_time: str,
        check_result: dict,
        extracted_data: dict,
) -> str:
    return json.dumps({
        "time": current_time,
        "check": check_result,
        "data": extracted_data,
    }, ensure_ascii=False)


def _load_single_user_chat_id() -> int:
    return int(default_chat_id)


async def _execute_watcher(conn: sqlite3.Connection, user_id: str, watcher_module: str, settings: dict,
                           now_utc: datetime):
    debug_hub.push_global_event(
        "watcher_execute_start",
        {"user": user_id, "watcher": watcher_module, "ts": now_utc.isoformat()},
    )
    module = _load_watcher_module(watcher_module)
    tz = _safe_timezone(settings.get("timezone", "UTC"))
    today_local = now_utc.astimezone(tz).date().isoformat()
    db_path = ROOT / "users" / user_id / "data.sqlite"

    try:
        check_result = module.check(
            {
                "settings": settings,
                "db_path": str(db_path),
                "now_utc": now_utc.isoformat(),
                "today_local": today_local,
                "user": user_id,
            }
        )
        if not isinstance(check_result, dict):
            check_result = {
                "status": "error",
                "title": f"{watcher_module} returned invalid result",
                "summary": "Watcher check returned non-dict response.",
                "facts": {"raw_type": str(type(check_result))},
                "severity": "high",
                "action_required": False,
            }
    except Exception as exc:
        check_result = {
            "status": "error",
            "title": f"{watcher_module} execution failed",
            "summary": str(exc),
            "facts": {"error": str(exc)},
            "severity": "high",
            "action_required": False,
        }

    extracted_data = check_result.get("facts", {})
    _upsert_watcher_run(
        conn=conn,
        user_id=user_id,
        watcher=watcher_module,
        run_at=now_utc.isoformat(),
        run_day=today_local,
        result=check_result,
    )

    prompt_markdown = build_query_prompt(
        current_time=now_utc.isoformat(),
        check_result=check_result,
        extracted_data=extracted_data,
    )

    chat_id = _load_single_user_chat_id()
    configured_thread_id = settings.get("thread_id")
    try:
        configured_thread_id = int(configured_thread_id) if configured_thread_id is not None else None
    except Exception:
        configured_thread_id = None

    task = WorkItem(
        query=prompt_markdown,
        username=user_id,
        chat_id=chat_id,
        thread_id=configured_thread_id,
        source="watcher_schedule",
        metadata={"schedule_name": watcher_module, "trigger": "schedule"},
    )
    await msg_queue.put(task)
    debug_hub.push_global_event(
        "watcher_enqueued",
        {
            "user": user_id,
            "watcher": watcher_module,
            "status": check_result.get("status"),
            "severity": check_result.get("severity"),
            "action_required": bool(check_result.get("action_required")),
        },
    )


async def run_watchers_once():
    now_utc = datetime.now(timezone.utc)
    conn = _init_watcher_db(WATCHER_DB_PATH)

    try:
        for watcher_module in watcher_modules:
            settings = _load_settings(watcher_module)
            if not settings.get("enabled", True):
                continue
            if not _watcher_allowed_for_user(settings, SINGLE_USER_ID):
                continue
            await _execute_watcher(conn, SINGLE_USER_ID, watcher_module, settings, now_utc)
        conn.commit()
    finally:
        conn.close()


async def watcher_loop(poll_seconds: int = WORKER_POLL_SECONDS):
    schedule_map: dict[str, datetime] = {}
    conn = _init_watcher_db(WATCHER_DB_PATH)
    try:
        while True:
            now_utc = datetime.now(timezone.utc)

            for watcher_module in watcher_modules:
                settings = _load_settings(watcher_module)
                if not settings.get("enabled", True):
                    continue
                if not _watcher_allowed_for_user(settings, SINGLE_USER_ID):
                    continue

                cron_expr = _normalize_cron_expr(settings.get("cron"))
                tz_name = settings.get("timezone", "UTC")
                key = watcher_module

                if key not in schedule_map:
                    schedule_map[key] = _next_run_utc(cron_expr, tz_name, now_utc - timedelta(minutes=1))

                if now_utc >= schedule_map[key]:
                    await _execute_watcher(conn, SINGLE_USER_ID, watcher_module, settings, now_utc)
                    schedule_map[key] = _next_run_utc(cron_expr, tz_name, now_utc)
                    # await asyncio.sleep(10)
            conn.commit()
            await asyncio.sleep(poll_seconds)
    finally:
        conn.close()


if __name__ == "__main__":
    asyncio.run(watcher_loop())
