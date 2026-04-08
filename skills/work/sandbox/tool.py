from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skills.work.sandbox import local_sandbox_tools


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


OPERATIONS = {
    "init_local": local_sandbox_tools.init_local,
    "py_run": local_sandbox_tools.py_run,
    "fs_write": local_sandbox_tools.fs_write,
    "fs_read": local_sandbox_tools.fs_read,
    "fs_list": local_sandbox_tools.fs_list,
    "cmd_run": local_sandbox_tools.cmd_run,
}


def _error_hint_for_error(operation: str | None) -> str:
    if not operation:
        return "Retry with operation and payload."
    if operation in {"fs_read", "fs_write", "fs_list"}:
        return "Retry with a repository-relative payload.path."
    if operation in {"py_run", "cmd_run"}:
        return "Retry with valid payload.code or payload.cmd."
    return "Check payload fields for this operation and retry."


async def process_args(args: dict | str | None = None, extra_args: dict | None = None) -> dict[str, Any]:
    del extra_args
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

        if operation in {"py_run", "fs_write"}:
            field = "code" if operation == "py_run" else "content"
            value = payload.get(field)
            if isinstance(value, list):
                payload[field] = "\n".join(str(v) for v in value)

        result = OPERATIONS[operation](**payload)
        return _ok(operation, result)

    except Exception as exc:
        return _fail(operation, str(exc), _error_hint_for_error(operation))


def main() -> None:
    import asyncio

    cases = [
        {"name": "init_local", "args": {"operation": "init_local", "payload": {}}},
        {"name": "fs_write", "args": {"operation": "fs_write", "payload": {"path": "tmp_local_sandbox_test.txt", "content": "hello"}}},
        {"name": "fs_read", "args": {"operation": "fs_read", "payload": {"path": "tmp_local_sandbox_test.txt"}}},
        {"name": "py_run", "args": {"operation": "py_run", "payload": {"code": "print(2+2)"}}},
        {"name": "cmd_run", "args": {"operation": "cmd_run", "payload": {"cmd": "echo hi"}}},
    ]

    outputs = []
    for case in cases:
        result = asyncio.run(process_args(case["args"]))
        outputs.append({"case": case["name"], "result": result})

    print(json.dumps({"tests": outputs}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
