---
name: sandbox
description: >-
  Run shell commands, execute Python snippets, and read/write files in the workspace.
  Use for implementation support, diagnostics, and controlled automation tasks.
license: Apache-2.0
compatibility: Requires Python agent runtime
metadata:
  author: gaurav
  version: "3.1"
---

# Sandbox Skill

## When to Use
- Use for shell commands, Python snippets, and workspace file read/write tasks.
- Use for implementation checks, quick diagnostics, and controlled automation.

## Procedure
1. Initialize with `init_llm_tools` if starting a fresh execution session.
2. Inspect file state via `fs_list` and `fs_read`.
3. Apply edits using `fs_write`.
4. Validate with `py_run` and `cmd_run`.
5. Re-read files and confirm expected output.

## Pitfalls
- Running unsafe shell commands.
- Writing to wrong path with `fs_write`.
- Passing invalid JSON args shape.

## Tool Usage
Main flow (detailed):

```python
run_skill_script(
  skill_name="sandbox",
  script_name="tool.py",
  args={
    "operation": "cmd_run",
    "payload": {"cmd": "rg -n TODO ."}
  }
)
```

Then file update:

```python
run_skill_script(
  skill_name="sandbox",
  script_name="tool.py",
  args={
    "operation": "fs_write",
    "payload": {"path": "notes/todo.md", "content": "# Tasks\n- [ ] verify\n"}
  }
)
```

Other major operations (short form):
- `init_llm_tools`: `run_skill_script(..., args={"operation":"init_llm_tools","payload":{}})`
- `py_run`: `run_skill_script(..., args={"operation":"py_run","payload":{"code":"print(2+2)"}})`
- `fs_read`: `run_skill_script(..., args={"operation":"fs_read","payload":{"path":"notes/todo.md"}})`
- `fs_list`: `run_skill_script(..., args={"operation":"fs_list","payload":{"path":".","recursive":False}})`

## Verification
- Each call returns `ok=true`.
- File content matches expected changes.
- Command/Python output confirms success.
