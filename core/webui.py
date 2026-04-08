from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from core.heartbeat import WorkItem, msg_queue
from core.utils.config import logger, single_username

SINGLE_USERNAME = single_username


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short_text(value: Any, limit: int = 220) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


class DebugHub:
    def __init__(self, max_runs: int = 120, max_events_per_run: int = 300):
        self.max_runs = max_runs
        self.max_events_per_run = max_events_per_run
        self._lock = threading.Lock()
        self._runs: dict[str, dict[str, Any]] = {}
        self._run_order: list[str] = []
        self._global_events: list[dict[str, Any]] = []

    def start_run(
            self,
            *,
            query: str,
            username: str,
            chat_id: int | str,
            source: str,
            topic_id: int | None,
    ) -> str:
        run_id = uuid.uuid4().hex[:12]
        now = _now_iso()
        source_node = f"{source}_input"
        with self._lock:
            self._runs[run_id] = {
                "run_id": run_id,
                "created_at": now,
                "ended_at": None,
                "status": "running",
                "query": _short_text(query, 500),
                "username": username,
                "chat_id": chat_id,
                "topic_id": topic_id,
                "source": source,
                "reply_preview": "",
                "error": None,
                "events": [],
                "flow": {
                    "nodes": [source_node, "process_message"],
                    "edges": [
                        {
                            "from": source_node,
                            "to": "process_message",
                            "label": "instigated",
                            "kind": "instigation",
                            "ts": now,
                        }
                    ],
                    "last_node": "process_message",
                },
            }
            self._run_order.insert(0, run_id)
            self._trim_runs_locked()

        self.push_global_event(
            "process_message_started",
            {"run_id": run_id, "source": source, "username": username},
        )
        return run_id

    def record_stage(
            self,
            run_id: str | None,
            stage: str,
            *,
            label: str = "",
            kind: str = "stage",
            payload: dict[str, Any] | None = None,
            source_node: str | None = None,
            target_node: str | None = None,
    ) -> None:
        if not run_id:
            return

        now = _now_iso()
        payload = self._safe_value(payload or {})
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return

            flow = run["flow"]
            src = source_node or flow.get("last_node") or "process_message"
            dst = target_node or stage
            nodes = flow["nodes"]
            if src not in nodes:
                nodes.append(src)
            if dst not in nodes:
                nodes.append(dst)

            flow["edges"].append(
                {
                    "from": src,
                    "to": dst,
                    "label": label,
                    "kind": kind,
                    "ts": now,
                }
            )
            flow["last_node"] = dst

            run["events"].append(
                {
                    "ts": now,
                    "type": kind,
                    "stage": stage,
                    "label": label,
                    "payload": payload,
                }
            )
            if len(run["events"]) > self.max_events_per_run:
                run["events"] = run["events"][-self.max_events_per_run:]
            if len(flow["edges"]) > self.max_events_per_run:
                flow["edges"] = flow["edges"][-self.max_events_per_run:]
                flow["nodes"] = self._nodes_from_edges(flow["edges"])

    def record_stream_content(self, run_id: str | None, content: dict[str, Any]) -> None:
        if not run_id:
            return

        content = self._safe_value(content)
        ctype = str(content.get("type") or "unknown")
        agent_name = str(content.get("agent_name") or "agent")
        label = ""
        stage = f"{agent_name}:stream:{ctype}"

        if ctype == "function_call":
            tool_name = content.get("name") or "tool"
            stage = f"{agent_name}:tool_call:{tool_name}"
            label = f"args: {_short_text(content.get('arguments', ''), 180)}"
        elif ctype == "function_result":
            stage = f"{agent_name}:tool_result"
            label = _short_text(content.get("result", ""), 180)
        elif ctype in {"text", "text_reasoning"}:
            stage = f"{agent_name}:assistant_text" if ctype == "text" else f"{agent_name}:assistant_reasoning"
            label = _short_text(content.get("text", ""), 180)
        elif ctype == "update_event":
            event_type = content.get("event_type") or "update"
            if str(event_type) == "agent_response_update":
                return
            source = content.get("source")
            target = content.get("target")
            stage = f"{agent_name}:event:{event_type}"
            pieces = []
            if source or target:
                pieces.append(f"{source or '?'} -> {target or '?'}")
            if content.get("summary"):
                pieces.append(str(content.get("summary")))
            label = " | ".join(pieces)

        self.record_stage(
            run_id,
            stage,
            label=label,
            kind=f"stream_{ctype}",
            payload={"content": content, "agent": agent_name},
        )

    def end_run(
            self,
            run_id: str | None,
            *,
            status: str,
            reply_text: str = "",
            error: str | None = None,
    ) -> None:
        if not run_id:
            return
        with self._lock:
            run = self._runs.get(run_id)
            if not run:
                return
            run["status"] = status
            run["ended_at"] = _now_iso()
            run["reply_preview"] = _short_text(reply_text, 240)
            run["error"] = error
            run["flow"]["last_node"] = "done"
            if "done" not in run["flow"]["nodes"]:
                run["flow"]["nodes"].append("done")
            run["flow"]["edges"].append(
                {
                    "from": run["flow"]["edges"][-1]["to"] if run["flow"]["edges"] else "process_message",
                    "to": "done",
                    "label": status,
                    "kind": "completion",
                    "ts": _now_iso(),
                }
            )

        self.push_global_event(
            "process_message_finished",
            {"run_id": run_id, "status": status, "error": error},
        )

    def push_global_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        with self._lock:
            self._global_events.append(
                {
                    "ts": _now_iso(),
                    "type": event_type,
                    "payload": payload or {},
                }
            )
            if len(self._global_events) > 400:
                self._global_events = self._global_events[-400:]

    def ingest_external_event(
            self,
            *,
            run_id: str | None,
            event_type: str,
            message: str = "",
            source: str | None = None,
            target: str | None = None,
            payload: dict[str, Any] | None = None,
    ) -> str:
        selected_run_id = run_id
        if not selected_run_id:
            selected_run_id = self.start_run(
                query=message or event_type,
                username="api",
                chat_id="api",
                source="api",
                topic_id=None,
            )

        self.record_stage(
            selected_run_id,
            stage=target or event_type,
            label=message,
            kind=event_type,
            payload=payload or {},
            source_node=source or "api",
            target_node=target or event_type,
        )
        return selected_run_id

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            runs = [self._to_client_run(self._runs[run_id]) for run_id in self._run_order if run_id in self._runs]
            events = list(self._global_events)
        return {"runs": runs, "events": events, "server_time": _now_iso()}

    def _to_client_run(self, run: dict[str, Any]) -> dict[str, Any]:
        copy_run = dict(run)
        copy_flow = dict(run["flow"])
        copy_flow.pop("last_node", None)
        copy_run["flow"] = copy_flow
        return copy_run

    def _trim_runs_locked(self) -> None:
        if len(self._run_order) <= self.max_runs:
            return
        to_remove = self._run_order[self.max_runs:]
        self._run_order = self._run_order[: self.max_runs]
        for run_id in to_remove:
            self._runs.pop(run_id, None)

    @staticmethod
    def _nodes_from_edges(edges: list[dict[str, Any]]) -> list[str]:
        ordered: list[str] = []
        for edge in edges:
            src = edge.get("from")
            dst = edge.get("to")
            if src and src not in ordered:
                ordered.append(src)
            if dst and dst not in ordered:
                ordered.append(dst)
        return ordered

    @classmethod
    def _safe_value(cls, value: Any, depth: int = 0) -> Any:
        if depth >= 4:
            return _short_text(value, 400)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return _short_text(value, 2000)
        if isinstance(value, list):
            out = [cls._safe_value(v, depth + 1) for v in value[:40]]
            if len(value) > 40:
                out.append(f"... ({len(value) - 40} more)")
            return out
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for idx, (k, v) in enumerate(value.items()):
                if idx >= 40:
                    out["__truncated__"] = f"{len(value) - 40} more keys"
                    break
                out[str(k)] = cls._safe_value(v, depth + 1)
            return out

        for method_name in ("model_dump", "to_dict", "dict"):
            method = getattr(value, method_name, None)
            if callable(method):
                try:
                    data = method()
                    return cls._safe_value(data, depth + 1)
                except Exception:
                    pass

        if hasattr(value, "__dict__"):
            try:
                return cls._safe_value(dict(vars(value)), depth + 1)
            except Exception:
                pass

        return _short_text(repr(value), 500)


debug_hub = DebugHub()


class ExternalEventPayload(BaseModel):
    run_id: str | None = None
    event_type: str = "external_event"
    message: str = ""
    source: str | None = None
    target: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class WatcherUpdatePayload(BaseModel):
    query: str
    username: str = SINGLE_USERNAME
    chat_id: int
    thread_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BugReportPayload(BaseModel):
    report: str
    username: str = SINGLE_USERNAME
    chat_id: int
    thread_id: int | None = None
    title: str | None = None
    severity: str | None = None
    component: str | None = None
    expected_behavior: str | None = None
    actual_behavior: str | None = None
    repro_steps: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _build_bug_report_query(payload: BugReportPayload) -> str:
    body: dict[str, Any] = {
        "title": (payload.title or "").strip(),
        "severity": (payload.severity or "").strip(),
        "component": (payload.component or "").strip(),
        "report": payload.report.strip(),
        "expected_behavior": (payload.expected_behavior or "").strip(),
        "actual_behavior": (payload.actual_behavior or "").strip(),
        "repro_steps": [x for x in payload.repro_steps if str(x).strip()],
        "metadata": payload.metadata,
    }
    return json.dumps(body, ensure_ascii=False)


WEBUI_TEMPLATE_PATH = Path(__file__).resolve().parent / "static" / "debug_ui.html"

WEBUI_FALLBACK_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Agent Debug UI</title>
  <style>
    body { margin: 0; font-family: Arial, sans-serif; background: #0f172a; color: #e2e8f0; }
    .wrap { min-height: 100vh; display: grid; place-items: center; padding: 24px; }
    .card { max-width: 720px; width: 100%; border: 1px solid #334155; border-radius: 16px; padding: 24px; background: rgba(15, 23, 42, 0.88); }
    h1 { margin: 0 0 12px; font-size: 24px; }
    p { line-height: 1.5; color: #cbd5e1; }
    code { color: #93c5fd; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Agent Debug UI</h1>
      <p>The standalone debug UI file is missing.</p>
      <p>Expected path: <code>core/static/debug_ui.html</code></p>
    </div>
  </div>
</body>
</html>
"""


def _load_webui_html() -> str:
    try:
        if WEBUI_TEMPLATE_PATH.is_file():
            return WEBUI_TEMPLATE_PATH.read_text(encoding="utf-8")
    except OSError:
        pass
    return WEBUI_FALLBACK_HTML


def create_webui_app() -> FastAPI:
    app = FastAPI(title="Agent Debug WebUI", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    async def ui_index() -> HTMLResponse:
        return HTMLResponse(_load_webui_html())

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "ts": _now_iso()}

    @app.get("/api/state")
    async def api_state() -> dict[str, Any]:
        return debug_hub.snapshot()

    @app.post("/api/events")
    async def api_events(payload: ExternalEventPayload) -> dict[str, Any]:
        run_id = debug_hub.ingest_external_event(
            run_id=payload.run_id,
            event_type=payload.event_type,
            message=payload.message,
            source=payload.source,
            target=payload.target,
            payload=payload.payload,
        )
        return {"ok": True, "run_id": run_id}

    @app.post("/api/watcher/update")
    async def api_watcher_update(payload: WatcherUpdatePayload) -> dict[str, Any]:
        await msg_queue.put(
            WorkItem(
                query=payload.query,
                username=SINGLE_USERNAME,
                chat_id=payload.chat_id,
                thread_id=payload.thread_id,
                source="watcher_webhook",
                metadata=payload.metadata,
            )
        )
        run_id = debug_hub.start_run(
            query=payload.query,
            username=SINGLE_USERNAME,
            chat_id=payload.chat_id,
            source="watcher_webhook_api",
            topic_id=payload.thread_id,
        )
        debug_hub.record_stage(
            run_id,
            stage="watcher_webhook_queue",
            label="Watcher webhook update enqueued",
            kind="watcher_webhook_update",
            source_node="api",
            target_node="msg_queue",
            payload={"metadata": payload.metadata},
        )
        logger.info("Watcher update queued via API for user=%s", SINGLE_USERNAME)
        return {"ok": True, "queued": True, "run_id": run_id}

    @app.post("/api/watcher/bug_report")
    @app.post("/api/bug_report")
    async def api_watcher_bug_report(payload: BugReportPayload) -> dict[str, Any]:
        query = _build_bug_report_query(payload)
        await msg_queue.put(
            WorkItem(
                query=query,
                username=SINGLE_USERNAME,
                chat_id=payload.chat_id,
                thread_id=payload.thread_id,
                source="bug_report",
                metadata=payload.metadata,
            )
        )
        run_id = debug_hub.start_run(
            query=query,
            username=SINGLE_USERNAME,
            chat_id=payload.chat_id,
            source="bug_report_api",
            topic_id=payload.thread_id,
        )
        debug_hub.record_stage(
            run_id,
            stage="bug_report_queue",
            label="Bug report webhook enqueued",
            kind="bug_report_update",
            source_node="api",
            target_node="msg_queue",
            payload={
                "title": payload.title,
                "severity": payload.severity,
                "component": payload.component,
                "metadata": payload.metadata,
            },
        )
        logger.info("Bug report queued via API for user=%s", SINGLE_USERNAME)
        return {"ok": True, "queued": True, "run_id": run_id}

    return app
