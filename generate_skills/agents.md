# How To Create Skills

Use this guide when adding a new skill under `skills/personal` or `skills/work`.

## Goal

Create skills that are easy to route, safe to execute, and predictable in output.

## 1) Pick Scope And Name

Use one clear skill name in snake_case, for example:

- `todo_lists`
- `food_log_manager`
- `browser`

Place it in exactly one scope:

- `skills/personal/<skill_name>/`
- `skills/work/<skill_name>/`

## 2) Create Required Files

Minimum files:

- `skills/<scope>/<skill_name>/SKILL.md`
- `skills/<scope>/<skill_name>/tool.py`

Optional helper modules are allowed:

- `skills/<scope>/<skill_name>/*.py` (for DB logic, API clients, formatters)

## 3) Write `SKILL.md`

Your `SKILL.md` must match actual `tool.py` behavior.

Required sections:

1. `name: <skill_name>` (frontmatter if used by existing pattern)
2. Purpose (1-2 lines)
3. Operations list (exact operation names)
4. Input contract (`args` structure)
5. Required `extra_args` (like `db_path`, `chat_id`, `username`)
6. Response contract (success + failure shape)
7. Minimal examples (one per major operation)

Keep it short and concrete. Avoid vague language.

## 4) Implement `tool.py`

Use one entrypoint:

```python
async def process_args(args: dict | str | None = None, extra_args: dict | None = None) -> dict[str, Any]:
    ...
```

Rules:

- Accept `args` as dict or JSON string
- Normalize early; fail fast on malformed input
- Validate `operation` strictly
- Validate payload fields before calling business logic
- Use injected `extra_args` instead of hardcoded paths/IDs/tokens
- Return JSON-safe dicts only

Preferred response shape:

```json
{
  "ok": true,
  "operation": "operation_name",
  "result": {}
}
```

```json
{
  "ok": false,
  "operation": "operation_name",
  "error": "clear actionable message"
}
```

## 5) Keep Env And Secrets Clean

- Never hardcode bot tokens, API keys, private host IPs, or chat IDs
- Read from environment via config modules
- Use `extra_args` for runtime context (`chat_id`, `db_path`, `current_topic`)

## 6) Add Local Smoke Tests In `main()`

Inside `tool.py`, include a simple `main()` that:

- Creates/uses a safe local test DB copy when needed
- Runs 1-3 representative operations
- Prints machine-readable JSON results

This keeps manual verification fast.

## 7) Validation Checklist

Before finalizing a skill:

- `SKILL.md` operations match `tool.py` operations exactly
- Error messages include correction hints
- No hardcoded secrets/IDs/infra endpoints
- `process_args` handles missing/invalid payload safely
- Output shape is consistent across all operations

## Reference Pattern

Use these existing skills as implementation references:

- `skills/personal/todo_lists/`
- `skills/personal/food_log_manager/`
- `skills/work/telegram/`
- `skills/work/browser/`
