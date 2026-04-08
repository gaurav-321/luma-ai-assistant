---
name: browser
description: >-
  Automate websites end-to-end: open pages, inspect elements, click, type, navigate, and verify outcomes.
  Supports session continuity, snapshots, screenshots, and page interaction workflows.
license: Apache-2.0
metadata:
  author: gaurav
  version: "6.0"
---

# Browser Skill

## When to Use
- Use for interactive website tasks: navigation, form filling, clicking, validation snapshots, and page-state checks.
- Use for multi-step workflows that need session continuity via `task_id`.

## Procedure
1. Navigate with `browser_navigate` using a stable `task_id`.
2. Read refs using `browser_snapshot`.
3. Interact via `browser_type` and `browser_click`.
4. Re-snapshot after each important action.
5. Confirm completion from final snapshot.

## Pitfalls
- Reusing stale refs after page updates.
- Changing `task_id` mid-flow and losing tab context.
- Using selectors when refs are available.

## Tool Usage
Main flow (detailed):

```python
run_skill_script(
  skill_name="browser",
  script_name="tool.py",
  args={
    "operation": "browser_navigate",
    "payload": {
      "url": "https://example.com/signup",
      "task_id": "signup_task"
    }
  }
)
```

Then snapshot + interact:

```python
run_skill_script(
  skill_name="browser",
  script_name="tool.py",
  args={"operation": "browser_snapshot", "payload": {"task_id": "signup_task"}}
)
```

Other major operations (short form):
- `browser_type`: `run_skill_script(..., args={"operation":"browser_type","payload":{"task_id":"signup_task","ref":"@e3","text":"john@example.com"}})`
- `browser_click`: `run_skill_script(..., args={"operation":"browser_click","payload":{"task_id":"signup_task","ref":"@e8"}})`
- `browser_press`: `run_skill_script(..., args={"operation":"browser_press","payload":{"task_id":"signup_task","key":"Enter"}})`
- `browser_scroll`: `run_skill_script(..., args={"operation":"browser_scroll","payload":{"task_id":"signup_task","direction":"down"}})`
- `browser_close`: `run_skill_script(..., args={"operation":"browser_close","payload":{"task_id":"signup_task"}})`

## Verification
- Each call returns `ok=true`.
- Final snapshot shows expected success state.
- Session closes successfully when cleanup is needed.
