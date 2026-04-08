---
name: todo-lists
description: >-
  Manage to-do records in the local SQLite assistant database using named operations.
  Create tasks, update status, track subtasks, and append progress updates.
  Use this skill for task data management only, not for executing real-world work.
license: Apache-2.0
compatibility: Requires Python agent runtime
metadata:
  author: gaurav
  version: "3.0"
---

# Todo Lists Skill

## When to Use
- Use for task planning, subtask tracking, and progress updates.
- Use for pending/completed task retrieval and daily status checks.

## Procedure
1. Create task with title/priority.
2. Add subtasks and progress updates.
3. Complete tasks when done.
4. Query pending/completed and task history views.

## Pitfalls
- Missing `task_id` on task-linked operations.
- Missing required text fields.
- Invalid date in completed-by-date queries.

## Tool Usage
Main flow (detailed):

```python
run_skill_script(
  skill_name="todo_lists",
  script_name="tool.py",
  args={
    "operation": "create_task",
    "payload": {
      "title": "Prepare release notes",
      "description": "Summarize changes",
      "priority": 2,
      "due_date": "2026-04-09"
    }
  }
)
```

Then list tasks:

```python
run_skill_script(
  skill_name="todo_lists",
  script_name="tool.py",
  args={"operation":"list_tasks","payload":{}}
)
```

Other major operations (short form):
- `complete_task`: `run_skill_script(..., args={"operation":"complete_task","payload":{"task_id":1}})`
- `create_subtask`: `run_skill_script(..., args={"operation":"create_subtask","payload":{"task_id":1,"title":"Draft v1"}})`
- `add_task_update`: `run_skill_script(..., args={"operation":"add_task_update","payload":{"task_id":1,"update_text":"Blocked on review"}})`
- `get_pending_tasks`: `run_skill_script(..., args={"operation":"get_pending_tasks","payload":{}})`

## Verification
- Calls return `ok=true`.
- Task/subtask/update counts and statuses match expected workflow.
