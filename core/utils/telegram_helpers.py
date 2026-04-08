import html
import json
import re
from typing import Any, Optional


def escape_html(s: Any) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def is_url(s: str) -> bool:
    s = (s or "").strip()
    return s.startswith("http://") or s.startswith("https://")


def mk_link(url: str, text: str) -> str:
    # Telegram HTML: <a href="...">text</a>
    return f'<a href="{escape_html(url)}">{escape_html(text)}</a>'


def pretty_type(ex_type: str) -> str:
    # collapse whitespace/newlines
    return " ".join(str(ex_type).split()).strip()


def pretty_name(name: str) -> str:
    return " ".join(str(name).split()).strip()


def fmt_sets_reps(ws: str, reps: str) -> str:
    # use × not x
    return f"{ws} × {reps}"


DATA_OPEN = "[[DATA]]"
DATA_CLOSE = "[[/DATA]]"
DATA_RE = re.compile(r"\[\[DATA\]\](.*?)\[\[/DATA\]\]", re.DOTALL)


def format_tip(tip: str) -> str:
    t = tip.strip()
    t = re.sub(r"^\*\*.*?\*\*:\s*", "", t).strip()
    t = escape_html(t)
    t = re.sub(r"\*\*(.*?)\*\*", r"<b>\1</b>", t)  # **bold**
    t = re.sub(r"\*(.*?)\*", r"<b>\1</b>", t)  # *bold*
    return t


def extract_data_from_message(text: str) -> Optional[dict]:
    if not text:
        return None
    m = DATA_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1).strip())
    except Exception:
        return None
