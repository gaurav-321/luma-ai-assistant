from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
SANDBOX_ROOT = ROOT / "sandbox"
DEFAULT_TIMEOUT_S = 30
MAX_LIST_ITEMS = 2000
_ACTIVE_WORKDIR: Path | None = None


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def _ensure_workdir() -> Path:
    global _ACTIVE_WORKDIR
    if _ACTIVE_WORKDIR is None:
        SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
        _ACTIVE_WORKDIR = Path(tempfile.mkdtemp(prefix="tempdir-", dir=SANDBOX_ROOT)).resolve()
    return _ACTIVE_WORKDIR


def _resolve_path(path: str | None, *, must_exist: bool = False) -> Path:
    raw = (path or ".").strip()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = (_ensure_workdir() / candidate).resolve()
    else:
        candidate = candidate.resolve()

    root_resolved = ROOT.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("Path must stay inside repository root.") from exc

    if must_exist and not candidate.exists():
        raise ValueError(f"Path does not exist: {candidate}")

    return candidate


def _decode(data: bytes | None) -> str:
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")


def init_local() -> dict[str, Any]:
    workdir = _ensure_workdir()
    return {
        "root": str(ROOT),
        "workdir": _rel(workdir),
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
    }


def fs_write(path: str, content: str, mode: str = "w") -> dict[str, Any]:
    if mode not in {"w", "a"}:
        raise ValueError("mode must be 'w' or 'a'.")
    target = _resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = content if isinstance(content, str) else str(content)
    with target.open(mode, encoding="utf-8") as fh:
        fh.write(payload)
    return {"path": _rel(target), "bytes": len(payload.encode("utf-8"))}


def fs_read(path: str) -> dict[str, Any]:
    target = _resolve_path(path, must_exist=True)
    if target.is_dir():
        raise ValueError("path points to a directory; use fs_list.")
    text = target.read_text(encoding="utf-8")
    return {
        "path": _rel(target),
        "content": text,
        "bytes": len(text.encode("utf-8")),
    }


def fs_list(path: str = ".", recursive: bool = False, include_hidden: bool = False) -> dict[str, Any]:
    base = _resolve_path(path, must_exist=True)
    if base.is_file():
        rel = _rel(base)
        return {"items": [{"path": rel, "type": "file", "size": base.stat().st_size}], "count": 1}

    iterator = base.rglob("*") if recursive else base.glob("*")
    items: list[dict[str, Any]] = []
    for entry in iterator:
        rel_path = _rel(entry)
        name = entry.name
        if not include_hidden and name.startswith("."):
            continue
        item_type = "dir" if entry.is_dir() else "file"
        item: dict[str, Any] = {"path": rel_path, "type": item_type}
        if item_type == "file":
            item["size"] = entry.stat().st_size
        items.append(item)
        if len(items) >= MAX_LIST_ITEMS:
            break

    return {"items": items, "count": len(items), "truncated": len(items) >= MAX_LIST_ITEMS}


def cmd_run(
        cmd: str | list[str],
        timeout_s: int = DEFAULT_TIMEOUT_S,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
) -> dict[str, Any]:
    if isinstance(cmd, str):
        if os.name == "nt":
            argv = ["powershell", "-NoProfile", "-Command", cmd]
        else:
            argv = ["bash", "-lc", cmd]
    elif isinstance(cmd, list) and cmd:
        argv = [str(part) for part in cmd]
    else:
        raise ValueError("cmd must be a non-empty string or list.")

    run_cwd = _resolve_path(cwd or ".", must_exist=True)
    proc_env = os.environ.copy()
    if env:
        proc_env.update({str(k): str(v) for k, v in env.items()})

    try:
        completed = subprocess.run(
            argv,
            cwd=str(run_cwd),
            env=proc_env,
            capture_output=True,
            timeout=int(timeout_s),
            check=False,
        )
        return {
            "stdout": _decode(completed.stdout),
            "stderr": _decode(completed.stderr),
            "returncode": int(completed.returncode),
            "timed_out": False,
            "cwd": _rel(run_cwd),
            "argv": argv,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "stdout": _decode(exc.stdout),
            "stderr": _decode(exc.stderr),
            "returncode": None,
            "timed_out": True,
            "cwd": _rel(run_cwd),
            "argv": argv,
        }


def py_run(
        code: str,
        args: list[str] | None = None,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
) -> dict[str, Any]:
    script = code if isinstance(code, str) else str(code)
    argv = [sys.executable, "-c", script, *(str(v) for v in (args or []))]
    return cmd_run(argv, timeout_s=timeout_s, cwd=cwd, env=env)
