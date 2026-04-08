import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

ROOT_DIR = Path(__file__).parents[3]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def short(s: Any, n: int = 240) -> str:
    if s is None:
        return ""
    t = str(s).replace("\n", " ").strip()
    return t if len(t) <= n else (t[: n - 3] + "...")


@dataclass
class TraceEvent:
    trace_id: str
    ts: str
    type: str
    data: Dict[str, Any] = field(default_factory=dict)


class TraceLogger:
    """
    Collect structured events during a run.
    Can write JSONL to per-chat per-day file.
    """

    def __init__(self, trace_id: Optional[str] = None, base_dir: str = "data/traces"):
        self.trace_id = trace_id or uuid.uuid4().hex[:8]
        self.base_dir = os.path.join(ROOT_DIR, base_dir)
        self.events: List[TraceEvent] = []
        self._t0 = time.perf_counter()

    def add(self, event_type: str, **data: Any) -> None:
        self.events.append(
            TraceEvent(
                trace_id=self.trace_id,
                ts=utc_now_iso(),
                type=event_type,
                data=data,
            )
        )

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._t0) * 1000)

    def flush_jsonl(self, chat_id: str) -> str:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out_dir = os.path.join(self.base_dir, str(chat_id))
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"{day}.jsonl")

        with open(path, "a", encoding="utf-8") as f:
            for e in self.events:
                try:
                    payload = {
                        "trace": e.trace_id,
                        "ts": e.ts,
                        "type": e.type,
                        **(e.data or {}),
                    }
                    f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
                except Exception as ex:
                    # last resort: never fail the whole flush
                    f.write(json.dumps({
                        "trace": e.trace_id,
                        "ts": e.ts,
                        "type": "trace_logger_error",
                        "error": repr(ex),
                    }, ensure_ascii=False) + "\n")

            f.flush()
            os.fsync(f.fileno())

        return path


def traced_tool(logger: TraceLogger, tool_name: Optional[str] = None) -> Callable:
    """
    Decorator that logs tool_call / tool_result / tool_error with durations.
    """

    def deco(fn: Callable) -> Callable:
        name = tool_name or fn.__name__

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.perf_counter()
            logger.add(
                "tool_call",
                tool=name,
                args=short(args, 120),
                kwargs=short(kwargs, 200),
            )
            try:
                res = fn(*args, **kwargs)
                dt = int((time.perf_counter() - t0) * 1000)
                logger.add(
                    "tool_result",
                    tool=name,
                    ms=dt,
                    result=short(res, 240),
                )
                return res
            except Exception as e:
                dt = int((time.perf_counter() - t0) * 1000)
                logger.add(
                    "tool_error",
                    tool=name,
                    ms=dt,
                    error=repr(e),
                )
                raise

        return wrapper

    return deco


def create_logger(
        name: str = "app",
        log_file: str = "logs/app.log",
        level: int = logging.INFO,
        max_bytes: int = 5 * 1024 * 1024,
        backup_count: int = 3,
) -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
