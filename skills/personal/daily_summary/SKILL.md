---
name: daily-summary
description: >-
  Read and write daily summary records in the local SQLite assistant database.
  Fetch existing summaries for a date or upsert new markdown summaries.
  Use this skill for daily recap generation, storage, and retrieval flows.
license: Apache-2.0
compatibility: Requires Python agent runtime
metadata:
  author: gaurav
  version: "3.2"
---

# Daily Summary Skill

## When to Use
- Use to retrieve or store markdown day summaries by date.
- Use for reliable daily notes/reporting persistence.

## Procedure
1. Read current summary with `get_daily_summary`.
2. Upsert new markdown using `upsert_daily_summary`.
3. Re-read the same date to confirm persistence.

## Pitfalls
- Invalid date format.
- Empty markdown on upsert.
- Missing `db_path` in runtime context.

## Tool Usage
Main flow (detailed):

```python
run_skill_script(
  skill_name="daily-summary",
  script_name="tool.py",
  args={
    "operation": "upsert_daily_summary",
    "payload": {
      "date": "2026-04-08",
      "markdown_summary": "# Day Summary\n- Completed key tasks"
    }
  }
)
```

Other major operation (short form):
- `get_daily_summary`: `run_skill_script(..., args={"operation":"get_daily_summary","payload":{"date":"2026-04-08"}})`

## Verification
- Calls return `ok=true`.
- Read-after-write returns expected summary content.
