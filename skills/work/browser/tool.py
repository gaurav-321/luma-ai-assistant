from __future__ import annotations

import json
import os
import re
from typing import Any
import uuid
import threading

import requests

DEFAULT_BASE_URL = os.getenv("BROWSER_BASE_URL", "http://127.0.0.1:9377")
DEFAULT_TIMEOUT_S = 45

STATE: dict[str, Any] = {
    "base_url": DEFAULT_BASE_URL,
    "user_id": "agent1",
    "session_key": "task1",
    "last_tab_id": None,
}
TASK_SESSIONS: dict[str, dict[str, Any]] = {}
TASK_SESSIONS_LOCK = threading.Lock()


def _ok(operation: str | None, result: Any) -> dict[str, Any]:
    return {"ok": True, "operation": operation, "result": result, "error": None}


def _fail(operation: str | None, error: str, recovery_hint: str) -> dict[str, Any]:
    return {"ok": False, "operation": operation, "result": None, "error": error}


def _normalize_args(args: dict | str | None) -> dict[str, Any]:
    if args is None:
        return {}
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        text = args.strip()
        if not text:
            return {}
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("args JSON must deserialize to an object.")
        return parsed
    raise ValueError("args must be a dict, JSON string, or null.")


def _pick(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload.get(key) is not None:
            return payload.get(key)
    return None


def _resolve_base_url(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> str:
    v = _pick(payload, "base_url", "host")
    if v:
        return str(v)

    if extra_args:
        x = _pick(extra_args, "base_url", "host", "camofox_base_url")
        if x:
            return str(x)

    return str(STATE.get("base_url") or DEFAULT_BASE_URL)


def _resolve_user_id(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> str:
    v = _pick(payload, "user_id", "userId")
    if v:
        return str(v)

    if extra_args:
        x = _pick(extra_args, "user_id", "userId")
        if x:
            return str(x)

    return str(STATE.get("user_id") or "agent1")


def _resolve_session_key(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> str:
    v = _pick(payload, "session_key", "sessionKey")
    if v:
        return str(v)

    if extra_args:
        x = _pick(extra_args, "session_key", "sessionKey")
        if x:
            return str(x)

    return str(STATE.get("session_key") or "task1")


def _resolve_tab_id(payload: dict[str, Any], *, required: bool = True) -> str | None:
    tab_id = _pick(payload, "tab_id", "tabId", "id")
    if tab_id:
        return str(tab_id)

    if STATE.get("last_tab_id"):
        return str(STATE["last_tab_id"])

    if required:
        raise ValueError("payload.tab_id is required (or create/open a tab first).")
    return None


def _task_key(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> str:
    v = _pick(payload, "task_id") or (extra_args or {}).get("task_id")
    if not v:
        return "default"
    return str(v)


def _session_ids_for_task(task_id: str) -> tuple[str, str]:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", task_id)[:24] or "default"
    user_id = f"hermes_{safe}_{uuid.uuid4().hex[:6]}"
    session_key = f"task_{safe}"
    return user_id, session_key


def _get_task_session(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    task_id = _task_key(payload, extra_args)
    with TASK_SESSIONS_LOCK:
        if task_id in TASK_SESSIONS:
            return TASK_SESSIONS[task_id]

        user_id, session_key = _session_ids_for_task(task_id)
        session = {
            "task_id": task_id,
            "user_id": user_id,
            "session_key": session_key,
            "tab_id": None,
        }
        TASK_SESSIONS[task_id] = session
        return session


def _get_task_tab_id(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> str:
    explicit = _pick(payload, "tab_id", "tabId")
    if explicit:
        return str(explicit)
    session = _get_task_session(payload, extra_args)
    tab_id = session.get("tab_id")
    if tab_id:
        return str(tab_id)
    raise ValueError("No active tab for this task_id. Call browser_navigate first.")


def _request(
    method: str,
    path: str,
    *,
    base_url: str,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    resp = requests.request(
        method=method.upper(),
        url=url,
        params=params,
        json=payload,
        timeout=timeout_s,
        headers=headers,
    )

    try:
        data = resp.json()
    except Exception:
        data = {"text": resp.text}

    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {data}")

    if isinstance(data, dict):
        return data
    return {"data": data}


def _timeout(payload: dict[str, Any]) -> int:
    return int(payload.get("timeout_s", DEFAULT_TIMEOUT_S))


def _set_state(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    if "base_url" in payload and payload["base_url"]:
        STATE["base_url"] = str(payload["base_url"])
    if "user_id" in payload and payload["user_id"]:
        STATE["user_id"] = str(payload["user_id"])
    if "session_key" in payload and payload["session_key"]:
        STATE["session_key"] = str(payload["session_key"])
    if "tab_id" in payload and payload["tab_id"]:
        STATE["last_tab_id"] = str(payload["tab_id"])

    if extra_args:
        if extra_args.get("user_id"):
            STATE["user_id"] = str(extra_args["user_id"])
        if extra_args.get("session_key"):
            STATE["session_key"] = str(extra_args["session_key"])
        if extra_args.get("base_url"):
            STATE["base_url"] = str(extra_args["base_url"])

    return dict(STATE)


def _get_state(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    del payload, extra_args
    return dict(STATE)


def _health(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    return _request("GET", "/health", base_url=_resolve_base_url(payload, extra_args), timeout_s=_timeout(payload))


def _start(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    return _request("POST", "/start", base_url=_resolve_base_url(payload, extra_args), timeout_s=_timeout(payload))


def _stop(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    headers = {}
    admin_key = _pick(payload, "admin_key")
    if admin_key:
        headers["Authorization"] = f"Bearer {admin_key}"
    return _request(
        "POST",
        "/stop",
        base_url=_resolve_base_url(payload, extra_args),
        timeout_s=_timeout(payload),
        headers=headers or None,
    )


def _create_tab(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    url = _pick(payload, "url")
    if not url:
        raise ValueError("payload.url is required")

    result = _request(
        "POST",
        "/tabs",
        base_url=_resolve_base_url(payload, extra_args),
        payload={
            "userId": _resolve_user_id(payload, extra_args),
            "sessionKey": _resolve_session_key(payload, extra_args),
            "url": str(url),
        },
        timeout_s=_timeout(payload),
    )
    tab_id = _pick(result, "tabId", "id")
    if tab_id:
        STATE["last_tab_id"] = str(tab_id)
    return result


def _list_tabs(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    return _request(
        "GET",
        "/tabs",
        base_url=_resolve_base_url(payload, extra_args),
        params={"userId": _resolve_user_id(payload, extra_args)},
        timeout_s=_timeout(payload),
    )


def _tab_stats(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    return _request(
        "GET",
        f"/tabs/{tab_id}/stats",
        base_url=_resolve_base_url(payload, extra_args),
        params={"userId": _resolve_user_id(payload, extra_args)},
        timeout_s=_timeout(payload),
    )


def _close_tab(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    result = _request(
        "DELETE",
        f"/tabs/{tab_id}",
        base_url=_resolve_base_url(payload, extra_args),
        params={"userId": _resolve_user_id(payload, extra_args)},
        timeout_s=_timeout(payload),
    )
    if str(STATE.get("last_tab_id") or "") == str(tab_id):
        STATE["last_tab_id"] = None
    return result


def _close_group(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    group_id = _pick(payload, "group_id", "groupId", "session_key", "sessionKey")
    if not group_id:
        group_id = _resolve_session_key(payload, extra_args)
    return _request(
        "DELETE",
        f"/tabs/group/{group_id}",
        base_url=_resolve_base_url(payload, extra_args),
        params={"userId": _resolve_user_id(payload, extra_args)},
        timeout_s=_timeout(payload),
    )


def _close_session(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    user_id = _resolve_user_id(payload, extra_args)
    result = _request(
        "DELETE",
        f"/sessions/{user_id}",
        base_url=_resolve_base_url(payload, extra_args),
        timeout_s=_timeout(payload),
    )
    STATE["last_tab_id"] = None
    return result


def _snapshot(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    params: dict[str, Any] = {"userId": _resolve_user_id(payload, extra_args)}
    if "include_screenshot" in payload:
        params["includeScreenshot"] = bool(payload.get("include_screenshot"))
    if "offset" in payload and payload.get("offset") is not None:
        params["offset"] = int(payload.get("offset"))
    result = _request(
        "GET",
        f"/tabs/{tab_id}/snapshot",
        base_url=_resolve_base_url(payload, extra_args),
        params=params,
        timeout_s=_timeout(payload),
    )
    STATE["last_tab_id"] = str(tab_id)
    return result


def _click(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    body: dict[str, Any] = {"userId": _resolve_user_id(payload, extra_args)}
    ref = _pick(payload, "ref")
    selector = _pick(payload, "selector")
    if ref:
        body["ref"] = str(ref)
    if selector:
        body["selector"] = str(selector)
    if "ref" not in body and "selector" not in body:
        raise ValueError("payload.ref or payload.selector is required")
    result = _request(
        "POST",
        f"/tabs/{tab_id}/click",
        base_url=_resolve_base_url(payload, extra_args),
        payload=body,
        timeout_s=_timeout(payload),
    )
    STATE["last_tab_id"] = str(tab_id)
    return result


def _type(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    text = _pick(payload, "text")
    if text is None:
        raise ValueError("payload.text is required")

    body: dict[str, Any] = {
        "userId": _resolve_user_id(payload, extra_args),
        "text": str(text),
        "pressEnter": bool(payload.get("press_enter", payload.get("pressEnter", False))),
    }
    ref = _pick(payload, "ref")
    selector = _pick(payload, "selector")
    if ref:
        body["ref"] = str(ref)
    if selector:
        body["selector"] = str(selector)
    if "ref" not in body and "selector" not in body:
        raise ValueError("payload.ref or payload.selector is required")

    result = _request(
        "POST",
        f"/tabs/{tab_id}/type",
        base_url=_resolve_base_url(payload, extra_args),
        payload=body,
        timeout_s=_timeout(payload),
    )
    STATE["last_tab_id"] = str(tab_id)
    return result


def _press(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    key = _pick(payload, "key")
    if not key:
        raise ValueError("payload.key is required")
    body = {"userId": _resolve_user_id(payload, extra_args), "key": str(key)}
    return _request(
        "POST",
        f"/tabs/{tab_id}/press",
        base_url=_resolve_base_url(payload, extra_args),
        payload=body,
        timeout_s=_timeout(payload),
    )


def _scroll(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    direction = _pick(payload, "direction") or "down"
    body = {"userId": _resolve_user_id(payload, extra_args), "direction": str(direction)}
    amount = _pick(payload, "amount")
    if amount is not None:
        body["amount"] = int(amount)
    return _request(
        "POST",
        f"/tabs/{tab_id}/scroll",
        base_url=_resolve_base_url(payload, extra_args),
        payload=body,
        timeout_s=_timeout(payload),
    )


def _navigate(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    body: dict[str, Any] = {"userId": _resolve_user_id(payload, extra_args)}
    url = _pick(payload, "url")
    macro = _pick(payload, "macro")
    query = _pick(payload, "query")
    if url:
        body["url"] = str(url)
    if macro:
        body["macro"] = str(macro)
    if query:
        body["query"] = str(query)
    if "url" not in body and "macro" not in body:
        raise ValueError("payload.url or payload.macro is required")
    result = _request(
        "POST",
        f"/tabs/{tab_id}/navigate",
        base_url=_resolve_base_url(payload, extra_args),
        payload=body,
        timeout_s=_timeout(payload),
    )
    STATE["last_tab_id"] = str(tab_id)
    return result


def _wait(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    body: dict[str, Any] = {"userId": _resolve_user_id(payload, extra_args)}
    selector = _pick(payload, "selector")
    timeout_ms = _pick(payload, "timeout_ms", "timeoutMs")
    if selector:
        body["selector"] = str(selector)
    if timeout_ms is not None:
        body["timeoutMs"] = int(timeout_ms)
    return _request(
        "POST",
        f"/tabs/{tab_id}/wait",
        base_url=_resolve_base_url(payload, extra_args),
        payload=body,
        timeout_s=_timeout(payload),
    )


def _links(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    return _request(
        "GET",
        f"/tabs/{tab_id}/links",
        base_url=_resolve_base_url(payload, extra_args),
        params={"userId": _resolve_user_id(payload, extra_args)},
        timeout_s=_timeout(payload),
    )


def _images(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    params: dict[str, Any] = {"userId": _resolve_user_id(payload, extra_args)}
    if "include_data" in payload:
        params["includeData"] = bool(payload.get("include_data"))
    if "max_bytes" in payload and payload.get("max_bytes") is not None:
        params["maxBytes"] = int(payload.get("max_bytes"))
    if "limit" in payload and payload.get("limit") is not None:
        params["limit"] = int(payload.get("limit"))
    return _request(
        "GET",
        f"/tabs/{tab_id}/images",
        base_url=_resolve_base_url(payload, extra_args),
        params=params,
        timeout_s=_timeout(payload),
    )


def _downloads(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    params: dict[str, Any] = {"userId": _resolve_user_id(payload, extra_args)}
    if "include_data" in payload:
        params["includeData"] = bool(payload.get("include_data"))
    if "consume" in payload:
        params["consume"] = bool(payload.get("consume"))
    if "max_bytes" in payload and payload.get("max_bytes") is not None:
        params["maxBytes"] = int(payload.get("max_bytes"))
    return _request(
        "GET",
        f"/tabs/{tab_id}/downloads",
        base_url=_resolve_base_url(payload, extra_args),
        params=params,
        timeout_s=_timeout(payload),
    )


def _screenshot(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    return _request(
        "GET",
        f"/tabs/{tab_id}/screenshot",
        base_url=_resolve_base_url(payload, extra_args),
        params={"userId": _resolve_user_id(payload, extra_args)},
        timeout_s=_timeout(payload),
    )


def _back(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    return _request(
        "POST",
        f"/tabs/{tab_id}/back",
        base_url=_resolve_base_url(payload, extra_args),
        payload={"userId": _resolve_user_id(payload, extra_args)},
        timeout_s=_timeout(payload),
    )


def _forward(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    return _request(
        "POST",
        f"/tabs/{tab_id}/forward",
        base_url=_resolve_base_url(payload, extra_args),
        payload={"userId": _resolve_user_id(payload, extra_args)},
        timeout_s=_timeout(payload),
    )


def _refresh(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    tab_id = _resolve_tab_id(payload)
    return _request(
        "POST",
        f"/tabs/{tab_id}/refresh",
        base_url=_resolve_base_url(payload, extra_args),
        payload={"userId": _resolve_user_id(payload, extra_args)},
        timeout_s=_timeout(payload),
    )


def _youtube_transcript(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    url = _pick(payload, "url")
    if not url:
        raise ValueError("payload.url is required")

    body: dict[str, Any] = {"url": str(url)}
    languages = _pick(payload, "languages")
    if languages is not None:
        body["languages"] = languages

    return _request(
        "POST",
        "/youtube/transcript",
        base_url=_resolve_base_url(payload, extra_args),
        payload=body,
        timeout_s=_timeout(payload),
    )


def _import_cookies(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    user_id = _resolve_user_id(payload, extra_args)
    cookies = _pick(payload, "cookies")
    if not isinstance(cookies, list) or not cookies:
        raise ValueError("payload.cookies must be a non-empty list")

    headers = {}
    api_key = _pick(payload, "api_key") or (extra_args or {}).get("api_key")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    return _request(
        "POST",
        f"/sessions/{user_id}/cookies",
        base_url=_resolve_base_url(payload, extra_args),
        payload={"cookies": cookies},
        timeout_s=_timeout(payload),
        headers=headers or None,
    )


# Convenience aliases for planner/agent simplicity.
def _open(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    return _create_tab(payload, extra_args)


def _go(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    return _navigate(payload, extra_args)


def _snap(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    return _snapshot(payload, extra_args)


def _tap(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    return _click(payload, extra_args)


def _enter(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    return _type(payload, extra_args)


# Hermes-style browser operations for easier planner workflows.
def _browser_navigate(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    url = _pick(payload, "url")
    if not url:
        raise ValueError("payload.url is required")

    session = _get_task_session(payload, extra_args)
    base_url = _resolve_base_url(payload, extra_args)
    timeout_s = _timeout(payload)

    if not session.get("tab_id"):
        created = _request(
            "POST",
            "/tabs",
            base_url=base_url,
            payload={
                "userId": session["user_id"],
                "sessionKey": session["session_key"],
                "url": str(url),
            },
            timeout_s=timeout_s,
        )
        session["tab_id"] = _pick(created, "tabId", "id")
    else:
        _request(
            "POST",
            f"/tabs/{session['tab_id']}/navigate",
            base_url=base_url,
            payload={"userId": session["user_id"], "url": str(url)},
            timeout_s=timeout_s,
        )

    STATE["last_tab_id"] = session["tab_id"]
    return {
        "success": True,
        "task_id": session["task_id"],
        "user_id": session["user_id"],
        "session_key": session["session_key"],
        "tab_id": session["tab_id"],
        "url": str(url),
    }


def _browser_snapshot(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    session = _get_task_session(payload, extra_args)
    tab_id = _get_task_tab_id(payload, extra_args)
    result = _request(
        "GET",
        f"/tabs/{tab_id}/snapshot",
        base_url=_resolve_base_url(payload, extra_args),
        params={"userId": session["user_id"]},
        timeout_s=_timeout(payload),
    )
    STATE["last_tab_id"] = tab_id
    session["tab_id"] = tab_id
    return {
        "success": True,
        "snapshot": result.get("snapshot", ""),
        "element_count": result.get("refsCount", 0),
        "url": result.get("url", ""),
        "task_id": session["task_id"],
        "tab_id": tab_id,
    }


def _browser_click(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    ref = _pick(payload, "ref")
    selector = _pick(payload, "selector")
    if not ref and not selector:
        raise ValueError("payload.ref or payload.selector is required")

    session = _get_task_session(payload, extra_args)
    tab_id = _get_task_tab_id(payload, extra_args)
    body: dict[str, Any] = {"userId": session["user_id"]}
    if ref:
        body["ref"] = str(ref).lstrip("@")
    if selector:
        body["selector"] = str(selector)

    result = _request(
        "POST",
        f"/tabs/{tab_id}/click",
        base_url=_resolve_base_url(payload, extra_args),
        payload=body,
        timeout_s=_timeout(payload),
    )
    STATE["last_tab_id"] = tab_id
    session["tab_id"] = tab_id
    return {"success": True, "task_id": session["task_id"], "tab_id": tab_id, "result": result}


def _browser_type(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    text = _pick(payload, "text")
    if text is None:
        raise ValueError("payload.text is required")
    ref = _pick(payload, "ref")
    selector = _pick(payload, "selector")
    if not ref and not selector:
        raise ValueError("payload.ref or payload.selector is required")

    session = _get_task_session(payload, extra_args)
    tab_id = _get_task_tab_id(payload, extra_args)
    body: dict[str, Any] = {"userId": session["user_id"], "text": str(text)}
    if ref:
        body["ref"] = str(ref).lstrip("@")
    if selector:
        body["selector"] = str(selector)
    if "press_enter" in payload or "pressEnter" in payload:
        body["pressEnter"] = bool(payload.get("press_enter", payload.get("pressEnter", False)))

    result = _request(
        "POST",
        f"/tabs/{tab_id}/type",
        base_url=_resolve_base_url(payload, extra_args),
        payload=body,
        timeout_s=_timeout(payload),
    )
    STATE["last_tab_id"] = tab_id
    session["tab_id"] = tab_id
    return {"success": True, "task_id": session["task_id"], "tab_id": tab_id, "result": result}


def _browser_scroll(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    session = _get_task_session(payload, extra_args)
    tab_id = _get_task_tab_id(payload, extra_args)
    direction = _pick(payload, "direction") or "down"
    result = _request(
        "POST",
        f"/tabs/{tab_id}/scroll",
        base_url=_resolve_base_url(payload, extra_args),
        payload={"userId": session["user_id"], "direction": str(direction)},
        timeout_s=_timeout(payload),
    )
    return {"success": True, "task_id": session["task_id"], "tab_id": tab_id, "result": result}


def _browser_press(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    key = _pick(payload, "key")
    if not key:
        raise ValueError("payload.key is required")
    session = _get_task_session(payload, extra_args)
    tab_id = _get_task_tab_id(payload, extra_args)
    result = _request(
        "POST",
        f"/tabs/{tab_id}/press",
        base_url=_resolve_base_url(payload, extra_args),
        payload={"userId": session["user_id"], "key": str(key)},
        timeout_s=_timeout(payload),
    )
    return {"success": True, "task_id": session["task_id"], "tab_id": tab_id, "result": result}


def _browser_close(payload: dict[str, Any], extra_args: dict[str, Any] | None) -> dict[str, Any]:
    session = _get_task_session(payload, extra_args)
    user_id = session["user_id"]
    _request(
        "DELETE",
        f"/sessions/{user_id}",
        base_url=_resolve_base_url(payload, extra_args),
        timeout_s=_timeout(payload),
    )
    with TASK_SESSIONS_LOCK:
        TASK_SESSIONS.pop(session["task_id"], None)
    if str(STATE.get("last_tab_id") or "") == str(session.get("tab_id") or ""):
        STATE["last_tab_id"] = None
    return {"success": True, "closed": True, "task_id": session["task_id"]}


OPERATIONS = {
    "set_state": _set_state,
    "get_state": _get_state,
    "health": _health,
    "start": _start,
    "stop": _stop,
    "create_tab": _create_tab,
    "list_tabs": _list_tabs,
    "tab_stats": _tab_stats,
    "close_tab": _close_tab,
    "close_group": _close_group,
    "close_session": _close_session,
    "snapshot": _snapshot,
    "click": _click,
    "type": _type,
    "press": _press,
    "scroll": _scroll,
    "navigate": _navigate,
    "wait": _wait,
    "links": _links,
    "images": _images,
    "downloads": _downloads,
    "screenshot": _screenshot,
    "back": _back,
    "forward": _forward,
    "refresh": _refresh,
    "youtube_transcript": _youtube_transcript,
    "import_cookies": _import_cookies,
    "open": _open,
    "go": _go,
    "snap": _snap,
    "tap": _tap,
    "enter": _enter,
    "browser_navigate": _browser_navigate,
    "browser_snapshot": _browser_snapshot,
    "browser_click": _browser_click,
    "browser_type": _browser_type,
    "browser_scroll": _browser_scroll,
    "browser_press": _browser_press,
    "browser_close": _browser_close,
}


def _error_hint_for_error(operation: str | None) -> str:
    if not operation:
        return "Retry with operation and payload."

    if operation in {"create_tab", "open"}:
        return "Provide payload.url. user_id/session_key can be omitted and auto-filled from state."

    if operation == "browser_navigate":
        return "Provide payload.url and optional payload.task_id. First call creates a tab, later calls reuse it."

    if operation in {"browser_snapshot", "browser_click", "browser_type", "browser_scroll", "browser_press", "browser_close"}:
        return "Provide payload.task_id (optional, default=default). Navigate first so the task has an active tab."

    if operation in {
        "snapshot",
        "snap",
        "navigate",
        "go",
        "click",
        "tap",
        "type",
        "enter",
        "press",
        "scroll",
        "wait",
        "links",
        "images",
        "downloads",
        "screenshot",
        "back",
        "forward",
        "refresh",
        "tab_stats",
        "close_tab",
    }:
        return "Provide payload.tab_id or create/open a tab first; last_tab_id is auto-used when available."

    if operation == "youtube_transcript":
        return "Provide payload.url and optional payload.languages."

    if operation == "import_cookies":
        return "Provide payload.cookies list and optional payload.api_key."

    return "Check payload fields for this operation and retry."


async def process_args(args: dict | str | None = None, extra_args: dict | None = None) -> dict[str, Any]:
    operation: str | None = None
    try:
        parsed = _normalize_args(args)
        operation = parsed.get("operation")
        payload = parsed.get("payload", {})

        if not operation:
            raise ValueError("operation is required")

        if operation not in OPERATIONS:
            supported = ", ".join(sorted(OPERATIONS.keys()))
            raise ValueError(f"Unknown operation: {operation}. Supported: {supported}.")

        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")

        result = OPERATIONS[operation](dict(payload), extra_args or {})
        return _ok(operation, result)

    except Exception as exc:
        return _fail(operation, str(exc), _error_hint_for_error(operation))


def main() -> None:
    import asyncio

    cases = [
        {"name": "set_state", "args": {"operation": "set_state", "payload": {"base_url": DEFAULT_BASE_URL}}},
        {"name": "health", "args": {"operation": "health", "payload": {}}},
        {"name": "get_state", "args": {"operation": "get_state", "payload": {}}},
    ]

    outputs = []
    for case in cases:
        result = asyncio.run(process_args(case["args"]))
        outputs.append({"case": case["name"], "result": result})

    print(json.dumps({"tests": outputs}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
