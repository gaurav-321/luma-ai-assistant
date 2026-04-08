"""
single-file fitness module:
- reads from local SQLite database (table: workout)
- determines "today" as (last_completed_day + 1) using raw SQL
- builds ONE Telegram message per exercise
- embeds secret payload in text as: <tg-spoiler>{...}</tg-spoiler>
- sends messages to Telegram (NO reply/callback handling here)
- provides a dedicated skip operation that only updates the database
"""
import asyncio
import hashlib
import json
import re
import sqlite3
import statistics
import traceback
from typing import Any, Dict, List, Union

from telegram import Bot

# your project config
from core.utils.config import ROOT, bot_token, default_chat_id
from core.utils.telegram_helpers import *


# -----------------------------
# Config
# -----------------------------


# -----------------------------
# Helpers
# -----------------------------
def _clean(x: Any) -> str:
    if x is None:
        return "-"
    s = str(x).strip()
    return s if s else "-"


def _day_num(day_str: str) -> int:
    m = re.search(r"\d+", str(day_str))
    return int(m.group()) if m else 0


def _hash8(s: str) -> str:
    return hashlib.sha1(str(s).encode("utf-8")).hexdigest()[:8]


def _quote_ident(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _exercise_key(name: Any) -> str:
    return " ".join(str(name or "").split()).casefold()


def _set_columns(conn: sqlite3.Connection) -> List[str]:
    cols = [row[1] for row in conn.execute("PRAGMA table_info(workout)").fetchall()]
    indexed: List[tuple[int, str]] = []
    for col in cols:
        m = re.fullmatch(r"set\s+(\d+)", str(col).strip(), flags=re.IGNORECASE)
        if m:
            indexed.append((int(m.group(1)), col))
    indexed.sort(key=lambda x: x[0])
    return [col for _, col in indexed]


def _decode_weight(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    # Handle both comma-decimal (12,5) and dot-decimal (12.5) formats.
    normalized = s.replace(" ", "")
    if "," in normalized and "." not in normalized:
        normalized = normalized.replace(",", ".")
    elif "," in normalized and "." in normalized:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", "")

    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", normalized):
        return None

    try:
        return float(normalized)
    except ValueError:
        return None


def _fmt_weight(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:g}"


def _collect_exercise_set_stats(
        conn: sqlite3.Connection,
        exercise_names: List[Any],
        set_cols: List[str],
) -> Dict[str, Dict[str, Dict[str, float | None]]]:
    keys = {_exercise_key(name) for name in exercise_names if _exercise_key(name)}
    if not keys or not set_cols:
        return {}

    quoted_set_cols = ", ".join(_quote_ident(col) for col in set_cols)
    sql = f'SELECT "Exercise", day, {quoted_set_cols} FROM workout WHERE "Exercise" IS NOT NULL'
    rows = conn.execute(sql).fetchall()

    prepared_rows: List[tuple[int, int, str, sqlite3.Row]] = []
    for idx, row in enumerate(rows):
        key = _exercise_key(row["Exercise"])
        if key in keys:
            prepared_rows.append((_day_num(row["day"]), idx, key, row))

    prepared_rows.sort(key=lambda item: (item[0], item[1]))

    acc: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for _, _, key, row in prepared_rows:
        per_ex = acc.setdefault(key, {col: {"values": [], "last": None} for col in set_cols})
        for col in set_cols:
            value = _decode_weight(row[col])
            if value is None:
                continue
            per_ex[col]["values"].append(value)
            per_ex[col]["last"] = value

    out: Dict[str, Dict[str, Dict[str, float | None]]] = {}
    for key, per_ex in acc.items():
        out[key] = {}
        for col in set_cols:
            values: List[float] = per_ex[col]["values"]
            median = statistics.median(values) if values else None
            out[key][col] = {
                "median": median,
                "last": per_ex[col]["last"],
            }
    return out


# -----------------------------
# SQL-Based Schedule Logic
# -----------------------------
def _ensure_skip_column(conn: sqlite3.Connection) -> None:
    cols = [row[1].lower() for row in conn.execute("PRAGMA table_info(workout)").fetchall()]
    if "skip" not in cols:
        conn.execute('ALTER TABLE workout ADD COLUMN "skip" INTEGER NOT NULL DEFAULT 0')


def _get_last_completed_day_num(conn: sqlite3.Connection) -> int:
    last_day_query = """
                     WITH DayStats AS (SELECT CAST(REPLACE(day, 'Day ', '') AS INTEGER)                                    as day_num,
                                              COUNT(*)                                                                     as total_exercises,
                                              SUM(CASE WHEN "set 1" IS NOT NULL AND TRIM("set 1") != '' THEN 1 ELSE 0 END) as completed_exercises,
                                              MAX(COALESCE(skip, 0))                                                       as skipped
                                       FROM workout
                                       WHERE day LIKE 'Day %'
                     GROUP BY day
                         )
                     SELECT MAX(day_num) as last_completed
                     FROM DayStats
                     WHERE skipped = 1
                        OR (total_exercises - completed_exercises) <= 3; \
                     """
    cursor = conn.cursor()
    cursor.execute(last_day_query)
    row = cursor.fetchone()
    return row["last_completed"] if row and row["last_completed"] is not None else 0


def _get_today_day_str(conn: sqlite3.Connection) -> str:
    last_completed = _get_last_completed_day_num(conn)
    return f"Day {last_completed + 1}"


def load_today_exercises(db_path: str) -> Dict[str, Any]:
    """
    Finds the last completed day using raw SQL and returns the exercises for the next day.
    A day is "completed" if it has 3 or fewer empty 'set 1' fields or skip=1.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        _ensure_skip_column(conn)
        day_str = _get_today_day_str(conn)

        exercises_query = """
                          SELECT *
                          FROM workout
                          WHERE day = ? \
                          """
        cursor.execute(exercises_query, (day_str,))
        rows = cursor.fetchall()

        exercises = [dict(r) for r in rows]
        set_cols = _set_columns(conn)
        stats_by_ex = _collect_exercise_set_stats(
            conn=conn,
            exercise_names=[ex.get("Exercise") for ex in exercises],
            set_cols=set_cols,
        )

        for ex in exercises:
            ex["_set_columns"] = set_cols
            ex["_set_stats"] = stats_by_ex.get(_exercise_key(ex.get("Exercise")), {})

        if not exercises:
            return {"day": day_str, "exercise_type": "-", "exercises": []}

        # Clean up the exercise type string from the first row
        ex_type = str(exercises[0].get("Exercise Type", "-"))
        ex_type = " ".join(ex_type.split())

        return {
            "day": day_str,
            "exercise_type": ex_type,
            "exercises": exercises,
        }


def mark_today_exercise_as_skip(db_path: str) -> Dict[str, Any]:
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            _ensure_skip_column(conn)
            day_str = _get_today_day_str(conn)

            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) AS row_count FROM workout WHERE day = ?", (day_str,))
            row = cursor.fetchone()
            row_count = int(row["row_count"]) if row and row["row_count"] is not None else 0

            if row_count == 0:
                return {
                    "status": "empty",
                    "reason": "no_workout_for_day",
                    "day": day_str,
                    "updated_rows": 0,
                    "message": f"No workout rows found for {day_str}.",
                }

            with conn:
                conn.execute('UPDATE workout SET skip = 1 WHERE day = ?', (day_str,))

            return {
                "status": "skipped",
                "day": day_str,
                "updated_rows": row_count,
                "message": f"Marked {row_count} workout rows for {day_str} as skipped.",
            }
    except Exception as e:
        return {
            "status": "error",
            "reason": "skip_failed",
            "message": str(e),
            "traceback": traceback.format_exc(),
        }


# -----------------------------
# Build ONE Telegram message per exercise
# -----------------------------
def _escape_html(s: str) -> str:
    # Telegram HTML parse mode needs escaping for user/data text
    import html
    return html.escape(s or "", quote=True)


def format_exercise_message(
        day_str: str,
        ex_type: str,
        ex: Dict[str, Any],
        ex_idx: int
) -> str:
    """
    Output rules (Telegram HTML):
    - No emojis
    - First line: Day (bold) + type (italic)
    - Next line: Exercise title ONLY (bold)
    - Blank line
    - Next line: sets×reps | rest | rpe
    - Next line immediately: Main Exercise (clickable)
    - Blank line
    - Alternates: each on new line, clickable if URL exists
    - Append <tg-spoiler>{"i":<ex_idx>}</tg-spoiler> (raw)
    """

    ex_type_one = pretty_type(ex_type)

    name = pretty_name(_clean(ex.get("Exercise")))
    ws = _clean(ex.get("Working Sets"))
    reps = _clean(ex.get("Reps"))
    rest = _clean(ex.get("Rest"))
    rpe1 = _clean(ex.get("Early Set RPE"))
    rpe2 = _clean(ex.get("Last Set RPE"))
    set_cols = ex.get("_set_columns") or []
    set_stats = ex.get("_set_stats") or {}

    demo = _clean(ex.get("Exercise_link"))

    sub1 = pretty_name(_clean(ex.get("Substitution Option 1")))
    sub2 = pretty_name(_clean(ex.get("Substitution Option 2")))
    sub1_link = _clean(ex.get("Substitution Option 1_link"))
    sub2_link = _clean(ex.get("Substitution Option 2_link"))

    parts: List[str] = []

    # 1) Day + type
    parts.append(f"<b>{_escape_html(day_str)}</b> <i>{_escape_html(ex_type_one)}</i>")

    # 2) Exercise title only
    parts.append(f"<b>{_escape_html(name)}</b>")

    # 3) Targets line
    target = fmt_sets_reps(ws, reps)  # e.g. "3 × 10-12"
    parts.append(
        f"<code>{_escape_html(target)}</code>"
        f"  |  rest <code>{_escape_html(rest)}</code>"
        f"  |  rpe <code>{_escape_html(rpe1)}→{_escape_html(rpe2)}</code>"
    )

    # 4) Historical set stats (median + recent) per set column
    for set_col in set_cols:
        stats = set_stats.get(set_col, {})
        median_value = _fmt_weight(stats.get("median"))
        last_value = _fmt_weight(stats.get("last"))
        label = str(set_col).title()
        parts.append(
            f"{_escape_html(label)} "
            f"median <code>{_escape_html(median_value)}</code> | "
            f"last <code>{_escape_html(last_value)}</code>"
        )

    # 5) Main Exercise link
    if demo != "-" and is_url(demo):
        parts.append(f"{mk_link(demo, 'Main Exercise')}")
    elif demo != "-":
        parts.append(_escape_html(demo))

    # 6) Blank line
    parts.append("")

    # 7) Alternates (each on new line, clickable if possible)
    alt_lines: List[str] = []
    if sub1 != "-" and sub1:
        if sub1_link != "-" and is_url(sub1_link):
            alt_lines.append(mk_link(sub1_link, sub1))
        else:
            alt_lines.append(_escape_html(sub1))

    if sub2 != "-" and sub2:
        if sub2_link != "-" and is_url(sub2_link):
            alt_lines.append(mk_link(sub2_link, sub2))
        else:
            alt_lines.append(_escape_html(sub2))

    if alt_lines:
        parts.append("<b>Alternates</b>")
        parts.extend(alt_lines)

    base_html = "\n".join([p for p in parts if p is not None]).strip()

    # Minimal secret data (index only)
    payload = {
        "i": int(ex_idx),
        "d": int(_day_num(day_str)),
        "k": _hash8(name),
    }

    blob = json.dumps(payload, separators=(",", ":"))

    return f"{base_html}\n\n<tg-spoiler>{blob}</tg-spoiler>"


async def build_today_exercise_messages(db_path: str) -> List[str]:
    payload = load_today_exercises(db_path)
    day_str = payload["day"]
    ex_type = payload["exercise_type"]
    exercises = payload["exercises"]

    if not exercises:
        return [f"no workout found for {day_str}."]

    messages: List[str] = []
    for idx, ex in enumerate(exercises):
        messages.append(format_exercise_message(day_str, ex_type, ex, idx))

    return messages


# -----------------------------
# Telegram sending (sender-only)
# -----------------------------
async def send_messages_to_telegram(
        messages: List[str],
        chat_id: Union[str, int],
        *,
        disable_web_preview: bool = True,
) -> List[int]:
    """
    Returns list of Telegram message_ids sent.
    Requires python-telegram-bot v20+ (async).
    """

    bot = Bot(token=bot_token)
    sent_ids: List[int] = []

    for msg in messages:
        # Telegram text limit is 4096; truncate safely if needed.
        if len(msg) > 4096:
            msg = msg[:4000] + "\n\n…(truncated)"

        res = await bot.send_message(
            chat_id=chat_id,
            message_thread_id=1041,
            text=msg,
            parse_mode="HTML",
            disable_web_page_preview=disable_web_preview,
        )
        sent_ids.append(int(res.message_id))

    return sent_ids


# -----------------------------
# MAIN
# -----------------------------
async def send_today_exercises(db_path: str, chat_id: Union[int, str]) -> dict[str, Any]:
    try:
        messages = await build_today_exercise_messages(db_path)

        # Check if the list returned a "no workout found" string
        if messages and "no workout found" in messages[0].lower():
            day_match = re.search(r"(Day\s+\d+)", messages[0], re.IGNORECASE)
            day = day_match.group(1) if day_match else "Day 1"
            return {
                "status": "empty",
                "reason": "no_workout_for_day",
                "day": day,
                "message": messages[0],
            }

        if not bot_token or not chat_id:
            raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or GROUP_GENERAL_CHAT_ID env vars.")

        # Keep numeric if possible
        try:
            chat_id_cast: Union[int, str] = int(chat_id)
        except Exception:
            chat_id_cast = chat_id

        sent_ids = await send_messages_to_telegram(messages, chat_id_cast)
        return {
            "status": "sent",
            "sent_count": len(sent_ids),
            "message_ids": sent_ids,
            "message": f"{len(sent_ids)} exercises were successfully sent to Telegram. Task Completed.",
        }
    except Exception as e:
        return {
            "status": "error",
            "reason": "send_failed",
            "message": str(e),
            "traceback": traceback.format_exc(),
        }


def main() -> None:
    demo_chat_id_raw = (default_chat_id or "").strip()
    try:
        demo_chat_id: int | str = int(demo_chat_id_raw) if demo_chat_id_raw else 0
    except Exception:
        demo_chat_id = demo_chat_id_raw or 0

    asyncio.run(
        send_today_exercises(
            chat_id=demo_chat_id,
            db_path=str(ROOT / "users" / "default" / "data.sqlite"),
        )
    )


if __name__ == "__main__":
    main()
