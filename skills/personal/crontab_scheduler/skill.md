---
name: crontab-scheduler
description: >-
  Manage recurring scheduled jobs stored in the local `crontab` SQLite table.
  Create jobs, update prompts, toggle activation, and inspect active schedules.
  Use this skill for reliable recurring automation metadata management.
license: Apache-2.0
compatibility: Requires Python agent runtime
metadata:
  author: gaurav
  version: "3.1"
---

# Crontab Scheduler Skill

## When to Use
- Use to manage recurring jobs in `crontab` and control schedule metadata.
- Use when scheduled jobs must target a specific thread via `thread_id`.

## Procedure
1. Inspect jobs with `list_jobs`.
2. Create new jobs with cron/timezone and optional `thread_id`.
3. Update prompts/active state as needed.
4. Set or clear thread routing with `set_job_thread_id`.
5. Re-list jobs to confirm final configuration.

## Pitfalls
- Invalid cron expressions.
- Wrong `id` causing no-op updates.
- Unsafe SQL in `query_whitelisted`.
- Vague `task_prompt` leads to weak scheduler outputs. Always define required skills and output criteria.

## Tool Usage
Recommended `task_prompt` structure (use this inside `create_job`):

```text
Objective:
- <exact goal>

Required skills:
- Primary: <skill_name>
- Secondary (optional): <skill_name>

Workflow:
1. <step>
2. <step>
3. <step>

Output criteria:
- Must include: <required fields/sections>
- Must avoid: <what not to include>
- Success condition: <how completion is verified>

Fallback:
- If data missing, do <recovery step>.
```

Main flow (detailed):

```python
run_skill_script(
  skill_name="crontab-scheduler",
  script_name="tool.py",
  args={
    "operation": "create_job",
    "payload": {
      "name": "Daily Digest",
      "task_prompt": "Objective:\\n- Summarize today's actionable work items.\\n\\nRequired skills:\\n- Primary: todo_lists\\n- Secondary: daily-summary\\n\\nWorkflow:\\n1. Get pending tasks with todo_lists.\\n2. Group by priority and due date.\\n3. Write concise markdown summary to daily-summary for today's date.\\n\\nOutput criteria:\\n- Must include: total pending count, top 3 urgent tasks, blocked tasks.\\n- Must avoid: raw tool traces or internal reasoning.\\n- Success condition: summary persisted and readable for today's date.\\n\\nFallback:\\n- If no tasks found, output 'No pending tasks' and still write summary entry.",
      "cron_expression": "0 8 * * *",
      "timezone": "Asia/Calcutta",
      "thread_id": 1043,
      "is_active": True
    }
  }
)
```

Then verify jobs:

```python
run_skill_script(
  skill_name="crontab-scheduler",
  script_name="tool.py",
  args={"operation": "list_jobs", "payload": {}}
)
```

Other major operations (short form):
- `update_task_prompt`: `run_skill_script(..., args={"operation":"update_task_prompt","payload":{"id":12,"task_prompt":"Updated prompt"}})`
- `set_job_active`: `run_skill_script(..., args={"operation":"set_job_active","payload":{"id":12,"is_active":False}})`
- `set_job_thread_id`: `run_skill_script(..., args={"operation":"set_job_thread_id","payload":{"id":12,"thread_id":1043}})`
- `query_whitelisted`: `run_skill_script(..., args={"operation":"query_whitelisted","payload":{"query":"SELECT id,name,thread_id FROM crontab WHERE id=?","params":[12]}})`

## Verification
- Each call returns `ok=true`.
- `list_jobs` shows expected cron + `thread_id` + active status.
- Update operations return `write_status=updated`.
