# Watcher Check Log

---

## Watcher `tasks_urgent` for user `pythoneer99`

- module: `tasks_urgent`
- type: `action_task`
- cron: `*/15 * * * *`
- timezone: `Asia/Calcutta`

### Skill Description

- purpose: Track overdue and near-due tasks
- when_to_inform: Inform quickly for overdue tasks; notify for next-24h due tasks
- extra_instructions: Prioritize task IDs and due time in message

### Check Result

```json
{
  "action_key": "resolve_urgent_tasks",
  "action_required": false,
  "dedupe_fields": {
    "overdue": 0,
    "titles": [],
    "upcoming": 0
  },
  "facts": {
    "due_next_24h_count": 0,
    "overdue_count": 0,
    "sample_titles": []
  },
  "severity": "low",
  "status": "ok",
  "summary": "No urgent pending tasks right now.",
  "title": "Urgent task monitor"
}
```

### Extracted Data

```json
{
  "due_next_24h_count": 0,
  "overdue_count": 0,
  "sample_titles": []
}
```

### LLM Task

Analyze this watcher update and reply in markdown with:

1. `notify_now`: yes or no
2. `reason`: short reason
3. `message`: short user-facing text

---

## Watcher `tasks_urgent` for user `pythoneer99`

- module: `tasks_urgent`
- type: `action_task`
- cron: `*/15 * * * *`
- timezone: `Asia/Calcutta`

### Skill Description

- purpose: Track overdue and near-due tasks
- when_to_inform: Inform quickly for overdue tasks; notify for next-24h due tasks
- extra_instructions: Prioritize task IDs and due time in message

### Check Result

```json
{
  "action_key": "resolve_urgent_tasks",
  "action_required": false,
  "dedupe_fields": {
    "overdue": 0,
    "titles": [],
    "upcoming": 0
  },
  "facts": {
    "due_next_24h_count": 0,
    "overdue_count": 0,
    "sample_titles": []
  },
  "severity": "low",
  "status": "ok",
  "summary": "No urgent pending tasks right now.",
  "title": "Urgent task monitor"
}
```

### Extracted Data

```json
{
  "due_next_24h_count": 0,
  "overdue_count": 0,
  "sample_titles": []
}
```

### LLM Task

Analyze this watcher update and reply in markdown with:

1. `notify_now`: yes or no
2. `reason`: short reason
3. `message`: short user-facing text

---

## Watcher `food_calories` for user `pythoneer99`

- module: `food_calories`
- type: `action_task`
- cron: `0 * * * *`
- timezone: `Asia/Calcutta`

### Skill Description

- purpose: Track food logging and calorie progress
- when_to_inform: Inform when no meal logs exist or calories are far below target
- extra_instructions: Keep reminder short and include next meal action

### Check Result

```json
{
  "action_key": "food_log_2026-03-24",
  "action_required": true,
  "dedupe_fields": {
    "count": 0,
    "date": "2026-03-24",
    "total": 0
  },
  "facts": {
    "date": "2026-03-24",
    "meal_logs": 0,
    "target_calories": 2200,
    "total_calories": 0
  },
  "severity": "medium",
  "status": "ok",
  "summary": "No food logs today yet.",
  "title": "Food and calories"
}
```

### Extracted Data

```json
{
  "date": "2026-03-24",
  "meal_logs": 0,
  "target_calories": 2200,
  "total_calories": 0
}
```

### LLM Task

Analyze this watcher update and reply in markdown with:

1. `notify_now`: yes or no
2. `reason`: short reason
3. `message`: short user-facing text

---

## Watcher `tasks_urgent` for user `pythoneer99`

- module: `tasks_urgent`
- type: `action_task`
- cron: `*/15 * * * *`
- timezone: `Asia/Calcutta`

### Skill Description

- purpose: Track overdue and near-due tasks
- when_to_inform: Inform quickly for overdue tasks; notify for next-24h due tasks
- extra_instructions: Prioritize task IDs and due time in message

### Check Result

```json
{
  "action_key": "resolve_urgent_tasks",
  "action_required": false,
  "dedupe_fields": {
    "overdue": 0,
    "titles": [],
    "upcoming": 0
  },
  "facts": {
    "due_next_24h_count": 0,
    "overdue_count": 0,
    "sample_titles": []
  },
  "severity": "low",
  "status": "ok",
  "summary": "No urgent pending tasks right now.",
  "title": "Urgent task monitor"
}
```

### Extracted Data

```json
{
  "due_next_24h_count": 0,
  "overdue_count": 0,
  "sample_titles": []
}
```

### LLM Task

Analyze this watcher update and reply in markdown with:

1. `notify_now`: yes or no
2. `reason`: short reason
3. `message`: short user-facing text
