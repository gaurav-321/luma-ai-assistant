"""
llm_tools_client.py

LLM-friendly client tools for your FastAPI mini-interpreter server.

Key upgrades for LLM tool-calling:
- Always returns a consistent JSON shape: {ok, session_id, stdout, stderr, returncode, timed_out, duration_ms, artifacts, error}
- Auto-creates a session if missing.
- Automatically chooses shell for cmd_run() when passed a string:
    - Linux/container: bash -lc
    - Windows host:   powershell (fallback to cmd)
- Fixes "W\x00i\x00n..." UTF-16 output by decoding when it looks like UTF-16LE.
- Adds convenience: get_session(), set_session(), reset_session()

Endpoints used:
- POST /sessions
- GET  /health
- POST /py/run
- POST /fs/write
- GET  /fs/read
- POST /fs/list
- POST /cmd/run
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

import requests

base_url: str = os.getenv("SANDBOX_BASE_URL", "http://127.0.0.1:1000")


# -------------------------
# Output decoding helpers
# -------------------------
def _maybe_decode_utf16le(text: str) -> str:
    """
    If text looks like UTF-16LE that got decoded as latin-1/utf-8 (with lots of \x00),
    convert it back to readable unicode.

    Example symptom: "W\x00i\x00n\x00d\x00o\x00w\x00s\x00 ..."
    """
    if not text:
        return text
    # Heuristic: many NULs relative to length
    nul_ratio = text.count("\x00") / max(1, len(text))
    if nul_ratio < 0.05:
        return text

    try:
        raw = text.encode("latin-1", errors="ignore")  # 1:1 mapping
        decoded = raw.decode("utf-16le", errors="ignore")
        # If decoding produced something meaningful, return it
        if decoded and sum(c.isprintable() for c in decoded) / max(1, len(decoded)) > 0.6:
            return decoded
        return text
    except Exception:
        return text


def _normalize_exec_result(
        data: Dict[str, Any],
        *,
        session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Ensure a stable schema for the LLM:
      ok, session_id, stdout, stderr, returncode, timed_out, duration_ms, artifacts, error
    """
    out = {
        "ok": bool(data.get("ok", data.get("returncode", 1) == 0)),
        "session_id": session_id,
        "stdout": _maybe_decode_utf16le(str(data.get("stdout", "") or "")),
        "stderr": _maybe_decode_utf16le(str(data.get("stderr", "") or "")),
        "returncode": data.get("returncode", None),
        "timed_out": bool(data.get("timed_out", False)),
        "duration_ms": data.get("duration_ms", None),
        "artifacts": data.get("artifacts", []),
        "error": None,
    }
    return out


def _normalize_fs_result(
        data: Dict[str, Any],
        *,
        session_id: Optional[str] = None,
) -> Dict[str, Any]:
    # Keep file endpoints consistent too
    out = {
        "ok": bool(data.get("ok", True)),
        "session_id": session_id,
        **data,
        "error": None,
    }
    # If it contains content, decode if needed
    if "content" in out:
        out["content"] = _maybe_decode_utf16le(str(out["content"] or ""))
    return out


def _normalize_error(e: Exception, *, session_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "ok": False,
        "session_id": session_id,
        "stdout": "",
        "stderr": "",
        "returncode": None,
        "timed_out": False,
        "duration_ms": None,
        "artifacts": [],
        "error": str(e),
    }


# -------------------------
# Low-level HTTP client
# -------------------------
@dataclass
class InterpreterClient:
    session_id: Optional[str] = None
    timeout_s: int = 60
    _http: requests.Session = field(default_factory=requests.Session)

    def _url(self, path: str) -> str:
        return base_url.rstrip("/") + path

    def _raise_for_error(self, r: requests.Response) -> None:
        if r.ok:
            return
        try:
            payload = r.json()
            detail = payload.get("detail", payload)
        except Exception:
            detail = r.text
        raise RuntimeError(f"HTTP {r.status_code}: {detail}")

    def health(self) -> Dict[str, Any]:
        r = self._http.get(self._url("/health"), timeout=self.timeout_s)
        self._raise_for_error(r)
        return r.json()

    def create_session(self) -> str:
        r = self._http.post(self._url("/sessions"), timeout=self.timeout_s)
        self._raise_for_error(r)
        data = r.json()
        self.session_id = data["session_id"]
        return self.session_id

    def ensure_session(self) -> str:
        if not self.session_id:
            return self.create_session()
        return self.session_id

    # -------- Python --------
    def py_run(
            self,
            code: str,
            *,
            args: Optional[List[str]] = None,
            stdin: Optional[str] = None,
            timeout_s: int = 30,
            env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        sid = self.ensure_session()
        payload = {
            "session_id": sid,
            "code": code,
            "args": args or [],
            "stdin": stdin,
            "timeout_s": timeout_s,
            "env": env,
        }
        r = self._http.post(self._url("/py/run"), json=payload, timeout=self.timeout_s)
        self._raise_for_error(r)
        return _normalize_exec_result(r.json(), session_id=sid)

    # -------- FS --------
    def fs_write(self, path: str, content: str, *, mode: str = "w") -> Dict[str, Any]:
        sid = self.ensure_session()
        payload = {"session_id": sid, "path": path, "content": content, "mode": mode}
        r = self._http.post(self._url("/fs/write"), json=payload, timeout=self.timeout_s)
        self._raise_for_error(r)
        return _normalize_fs_result(r.json(), session_id=sid)

    def fs_read(self, path: str) -> Dict[str, Any]:
        sid = self.ensure_session()
        r = self._http.get(self._url("/fs/read"), params={"session_id": sid, "path": path}, timeout=self.timeout_s)
        self._raise_for_error(r)
        return _normalize_fs_result(r.json(), session_id=sid)

    def fs_list(self, path: str = "", *, recursive: bool = False) -> Dict[str, Any]:
        sid = self.ensure_session()
        payload = {"session_id": sid, "path": path, "recursive": recursive}
        r = self._http.post(self._url("/fs/list"), json=payload, timeout=self.timeout_s)
        self._raise_for_error(r)
        return _normalize_fs_result(r.json(), session_id=sid)

    # -------- CMD --------
    def cmd_run(
            self,
            cmd: Union[str, List[str]],
            *,
            timeout_s: int = 30,
            stdin: Optional[str] = None,
            env: Optional[Dict[str, str]] = None,
            prefer_powershell_on_windows: bool = True,
    ) -> Dict[str, Any]:
        """
        LLM-friendly behavior:
        - If cmd is a string:
            - Linux: ["bash","-lc", cmd]
            - Windows: ["powershell","-NoProfile","-Command", cmd] (or ["cmd","/c", cmd])
        - If cmd is a list: sent as-is.
        """
        sid = self.ensure_session()

        if isinstance(cmd, str):
            if os.name == "nt":
                if prefer_powershell_on_windows:
                    argv = ["powershell", "-NoProfile", "-Command", cmd]
                else:
                    argv = ["cmd", "/c", cmd]
            else:
                argv = ["bash", "-lc", cmd]
        else:
            argv = cmd

        payload = {
            "session_id": sid,
            "cmd": argv,
            "timeout_s": timeout_s,
            "stdin": stdin,
            "env": env,
        }
        r = self._http.post(self._url("/cmd/run"), json=payload, timeout=self.timeout_s)
        self._raise_for_error(r)
        return _normalize_exec_result(r.json(), session_id=sid)


# -------------------------
# Tool-style functions (register these with your LLM / AutoGen)
# -------------------------
_client: Optional[InterpreterClient] = None


def init_llm_tools(
        session_id: Optional[str] = None,
        timeout_s: int = 60,
) -> Dict[str, Any]:
    """
    Initialize the global client. LLM should call this once at start.
    Returns stable JSON.
    """
    global _client
    try:
        _client = InterpreterClient(session_id=session_id, timeout_s=timeout_s)
        health = _client.health()
        sid = _client.ensure_session()
        return {
            "ok": True,
            "session_id": sid,
            "health": health,
            "error": None,
        }
    except Exception as e:
        return {"ok": False, "session_id": session_id, "health": None, "error": str(e)}


def new_session() -> Dict[str, Any]:
    """Create and switch to a new session (workspace)."""
    global _client
    try:
        if _client is None:
            _client = InterpreterClient()
        sid = _client.create_session()
        return {"ok": True, "session_id": sid, "error": None}
    except Exception as e:
        return {"ok": False, "session_id": getattr(_client, "session_id", None), "error": str(e)}


def get_session() -> Dict[str, Any]:
    """Return current session_id (creates one if missing)."""
    global _client
    try:
        if _client is None:
            _client = InterpreterClient()
        sid = _client.ensure_session()
        return {"ok": True, "session_id": sid, "error": None}
    except Exception as e:
        return {"ok": False, "session_id": None, "error": str(e)}


def set_session(session_id: str) -> Dict[str, Any]:
    """Force client to use a specific session_id (LLM can restore previous workspace)."""
    global _client
    try:
        if _client is None:
            _client = InterpreterClient()
        _client.session_id = session_id
        sid = _client.ensure_session()
        return {"ok": True, "session_id": sid, "error": None}
    except Exception as e:
        return {"ok": False, "session_id": session_id, "error": str(e)}


def py_run(code: str, args: Optional[List[str]] = None, stdin: Optional[str] = None, timeout_s: int = 30) -> Dict[
    str, Any]:
    """Run python code in the current session."""
    global _client
    try:
        if _client is None:
            init_llm_tools()
        return _client.py_run(code, args=args, stdin=stdin, timeout_s=timeout_s)
    except Exception as e:
        return _normalize_error(e, session_id=getattr(_client, "session_id", None))


def fs_write(path: str, content: str, mode: str = "w") -> Dict[str, Any]:
    """Write a file inside session workspace (relative path)."""
    global _client
    try:
        if _client is None:
            init_llm_tools()
        return _client.fs_write(path, content, mode=mode)
    except Exception as e:
        out = _normalize_error(e, session_id=getattr(_client, "session_id", None))
        # keep fs-like shape
        return {**out, "path": path}


def fs_read(path: str) -> Dict[str, Any]:
    """Read a file inside session workspace (relative path)."""
    global _client
    try:
        if _client is None:
            init_llm_tools()
        return _client.fs_read(path)
    except Exception as e:
        out = _normalize_error(e, session_id=getattr(_client, "session_id", None))
        return {**out, "path": path}


def fs_list(path: str = "", recursive: bool = False) -> Dict[str, Any]:
    """List files/dirs inside session workspace."""
    global _client
    try:
        if _client is None:
            init_llm_tools()
        return _client.fs_list(path, recursive=recursive)
    except Exception as e:
        out = _normalize_error(e, session_id=getattr(_client, "session_id", None))
        return {**out, "path": path, "items": []}


def cmd_run(cmd: Union[str, List[str]], timeout_s: int = 30, stdin: Optional[str] = None) -> Dict[str, Any]:
    if _client is None:
        init_llm_tools()

    # Convert string -> argv list BEFORE hitting server
    if isinstance(cmd, str):
        # since server is Docker/Linux, always use bash
        cmd = ["bash", "-lc", cmd]

    return _client.cmd_run(cmd, timeout_s=timeout_s, stdin=stdin)


# -------------------------
# Quick manual test
# -------------------------
if __name__ == "__main__":
    print(init_llm_tools("test"))

    print(fs_write("hello.txt", "hi from client\n"))
    print(fs_list("", recursive=False))
    print(fs_read("hello.txt"))

    print(py_run("print('python says hi'); x=21; print(x*2)"))

    # Recommended: use string so it auto-selects shell (bash on linux, powershell/cmd on windows)
    print(cmd_run("pwd && ls -la"))
