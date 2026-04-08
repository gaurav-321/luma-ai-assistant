---
name: food-log-manager
description: >-
  Record, update, delete, and summarize daily food intake in local SQLite.
  Use this skill for meal logging, nutrition tracking, corrections, and daily recap generation.
license: Apache-2.0
compatibility: Requires Python agent runtime
metadata:
  author: gaurav
  version: "3.2"
---

# Food Log Manager Skill

## When to Use
- Use for nutrition logging, correction, and day-level food reports.
- Use for calories totals and macro summaries by date.

## Procedure
1. Add food entries with nutritional fields.
2. Retrieve date-wise logs and reports.
3. Update or delete incorrect entries.
4. Generate totals/summary for the day.

## Pitfalls
- Missing required nutrition fields.
- Invalid IDs for update/delete.
- Bad date formatting for day queries.

## Tool Usage
Main flow (detailed):

```python
run_skill_script(
  skill_name="food_log_manager",
  script_name="tool.py",
  args={
    "operation": "add_food_log",
    "payload": {
      "food_name": "Oats",
      "calories": 210,
      "protein": 8,
      "carbs": 36,
      "fat": 4,
      "quantity": "1 bowl",
      "meal_type": "breakfast"
    }
  }
)
```

Then retrieve logs:

```python
run_skill_script(
  skill_name="food_log_manager",
  script_name="tool.py",
  args={"operation":"get_food_logs_by_date","payload":{"date":"2026-04-08"}}
)
```

Other major operations (short form):
- `update_food_log`: `run_skill_script(..., args={"operation":"update_food_log","payload":{"id":12,"calories":240}})`
- `delete_food_log`: `run_skill_script(..., args={"operation":"delete_food_log","payload":{"id":12}})`
- `get_food_day_report`: `run_skill_script(..., args={"operation":"get_food_day_report","payload":{"date":"2026-04-08"}})`
- `get_total_calories_for_date`: `run_skill_script(..., args={"operation":"get_total_calories_for_date","payload":{"date":"2026-04-08"}})`

## Verification
- Calls return `ok=true`.
- Reports reflect latest add/update/delete operations.
