---
name: fitness
description: >-
  Send today's workout plan to a Telegram chat from the configured workout database.
  Mark today's workout explicitly as skipped when the user cannot perform it.
  Use this skill to control workout-day messaging and skip-state updates.
license: Apache-2.0
compatibility: Requires Python agent runtime
metadata:
  author: gaurav
  version: "2.6"
---

# Fitness Skill

## When to Use
- Use to send today?s exercise plan or mark today as skipped.
- Use for daily fitness nudges and schedule handling.

## Procedure
1. Call `send_today_exercises` to publish today's workout.
2. If needed, call `mark_today_exercise_as_skip`.
3. Re-run send operation to confirm expected behavior.

## Pitfalls
- Supplying unsupported payload fields.
- Missing required runtime context (`chat_id`, `db_path`).
- Using deprecated `payload.skip` behavior.

## Tool Usage
Main flow (detailed):

```python
run_skill_script(
  skill_name="fitness",
  script_name="tool.py",
  args={"operation": "send_today_exercises", "payload": {}}
)
```

Other major operation (short form):
- `mark_today_exercise_as_skip`: `run_skill_script(..., args={"operation":"mark_today_exercise_as_skip","payload":{}})`

## Verification
- Calls return `ok=true`.
- Output reflects expected send/skip state for today.
