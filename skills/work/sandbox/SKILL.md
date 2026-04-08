---
name: sandbox
description: >-
  Run shell commands, execute Python snippets, and read/write files directly in this local workspace.
  Uses local process execution only; no external sandbox API is required.
license: Apache-2.0
compatibility: Requires Python agent runtime
metadata:
  author: gaurav
  version: "1.0"
---

# Sandbox Skill

## When to Use
- Use for local shell commands, Python snippets, and workspace file operations.
- Use when you need direct local execution without any external API service.

## Procedure
1. Optionally run `init_local` to confirm runtime details.
2. Inspect files with `fs_list` and `fs_read`.
3. Apply edits with `fs_write`.
4. Validate with `py_run` and `cmd_run`.

## Pitfalls
- Passing paths outside the repository root.
- Running unsafe shell commands.
- Passing invalid JSON args shape.

## Tool Usage
Main flow (detailed):

```python
run_skill_script(
  skill_name="local_sandbox",
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
  skill_name="local_sandbox",
  script_name="tool.py",
  args={
    "operation": "fs_write",
    "payload": {"path": "notes/todo.md", "content": "# Tasks\n- [ ] verify\n"}
  }
)
```

Other major operations (short form):
- `init_local`: `run_skill_script(..., args={"operation":"init_local","payload":{}})`
- `py_run`: `run_skill_script(..., args={"operation":"py_run","payload":{"code":"print(2+2)"}})`
- `fs_read`: `run_skill_script(..., args={"operation":"fs_read","payload":{"path":"notes/todo.md"}})`
- `fs_list`: `run_skill_script(..., args={"operation":"fs_list","payload":{"path":".","recursive":False}})`

## Verification
- Each call returns `ok=true`.
- File content matches expected edits.
- Command/Python output confirms success.
