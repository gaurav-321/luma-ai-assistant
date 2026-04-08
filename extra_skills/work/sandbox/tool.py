import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skills.work.sandbox import local_sandbox_tools
import json


async def init_llm_tools(payload: dict):
    return local_sandbox_tools.init_llm_tools(**payload)


async def py_run(payload: dict):
    # Safely convert a list of strings into a single multi-line string
    code = payload.get("code")
    if isinstance(code, list):
        payload["code"] = "\n".join(code)

    return local_sandbox_tools.py_run(**payload)


async def fs_write(payload: dict):
    # Do the exact same thing for file writing
    content = payload.get("content")
    if isinstance(content, list):
        payload["content"] = "\n".join(content)

    return local_sandbox_tools.fs_write(**payload)


async def fs_read(payload: dict):
    return local_sandbox_tools.fs_read(**payload)


async def fs_list(payload: dict):
    return local_sandbox_tools.fs_list(**payload)


async def cmd_run(payload: dict):
    return local_sandbox_tools.cmd_run(**payload)


OPERATIONS = {
    "init_llm_tools": init_llm_tools,
    "py_run": py_run,
    "fs_write": fs_write,
    "fs_read": fs_read,
    "fs_list": fs_list,
    "cmd_run": cmd_run,
}


def _ok(operation: str | None, result):
    return {
        "ok": True,
        "operation": operation,
        "result": result,
        "error": None,
    }


def _fail(operation: str | None, error: str, recovery_hint: str):
    return {
        "ok": False,
        "operation": operation,
        "result": None,
        "error": error,
    }


async def process_args(args: dict | str | None = None, extra_args: dict = None):
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError as exc:
            return _fail(None, f"Args string is not valid JSON: {exc}", "Send args as a JSON object.")

    args = args or {}

    operation = args.get("operation")
    payload = args.get("payload", {})

    if operation not in OPERATIONS:
        return _fail(
            operation,
            f"Unknown operation: {operation}",
            "Retry with one of: init_llm_tools, py_run, fs_write, fs_read, fs_list, cmd_run.",
        )

    try:
        result = await OPERATIONS[operation](payload)
        return _ok(operation, result)

    except Exception as exc:
        return _fail(operation, str(exc), "Check payload fields for this operation and retry.")


def main():
    import asyncio

    cases = [
        {"name": "init_llm_tools", "args": {"operation": "init_llm_tools", "payload": {}}},
        {"name": "fs_list", "args": {"operation": "fs_list", "payload": {}}},
        {"name": "fs_write", "args": {"operation": "fs_write", "payload": {"path": "tmp_sandbox_tool_test.txt",
                                                                           "content": "hello from sandbox tool"}}},
        {"name": "fs_read", "args": {"operation": "fs_read", "payload": {"path": "tmp_sandbox_tool_test.txt"}}},
        {"name": "py_run", "args": {"operation": "py_run", "payload": {"code": "print(2 + 2)"}}},
        {"name": "cmd_run", "args": {"operation": "cmd_run", "payload": {"cmd": "echo hi"}}},
    ]

    outputs = []
    for case in cases:
        result = asyncio.run(process_args(case["args"]))
        outputs.append({"case": case["name"], "result": result})

    print(json.dumps({"tests": outputs}, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
