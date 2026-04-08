# AGENTS.md

## Short Version (Editable Fast)

Goal: build a reliable watcher framework where function results are queued, prompt text is generated for an LLM, and
reminders are tracked with escalation.

Key decisions:

* `watcher.py` does **not** call any LLM.
* `watcher.py` must include a cron-check function that adds due jobs to a global queue.
* Prompt text is drafted by framework code and passed to external tools you will build.
* `settings.yaml` must be highly customizable, with safe defaults when fields are missing.
* Reminder logic must handle:
    * action-needed tasks (example: log food/calories) with missed-count tracking and escalation.
    * scheduled info notifications (example: YouTube stats) without "work not done" penalty.

Core folder shape:

* `skills/heartbeat/runner.py` - orchestration
* `skills/heartbeat/db.py` - SQLite schema + upserts
* `skills/heartbeat/registry.py` - watcher discovery + settings/default merge
* `skills/heartbeat/prompt_drafter.py` - build prompt payload only
* `skills/heartbeat/watchers/<name>/watcher.py`
* `skills/heartbeat/watchers/<name>/settings.yaml`

Main flow:

* startup scans watcher folders
* settings are loaded + default values are applied
* DB rows are created/updated
* cron schedule is synced
* due watchers are pushed into global queue
* runner executes watcher `main()`
* state hash and decision records are saved
* reminders are created/updated based on watcher type + rules
* prompt is drafted for external LLM tools

---

## Full Implementation Guide For Codex

## 1) Purpose

This module is for:

* lifestyle reminders
* server reminders
* public project status
* docker health
* system tasks and urgent tasks

Reliability requirements:

* defaults must always work
* every run must be traceable in DB
* reminders should not be noisy
* repeated misses on action tasks should be counted and escalated

## 2) Watcher Contract (`watcher.py`)

Every watcher module must expose:

```python
from typing import Any, Dict, List


def check_crontables_and_enqueue(now_ts: str, scheduler_rows: List[dict], global_queue: list) -> int:
    """
    Finds due schedule rows for this watcher and appends queue items to global_queue.
    Returns number of enqueued jobs.
    """


def main(context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes watcher logic and returns structured result.
    No LLM calls here.
    """
```

`main()` return shape (minimum):

```json
{
  "status": "ok",
  "title": "Daily calories check",
  "summary": "Calories not logged today",
  "facts": {
    "logged_today": false
  },
  "severity": "medium",
  "action_required": true,
  "action_key": "log_calories_today",
  "dedupe_fields": {
    "date": "2026-03-24",
    "logged_today": false
  }
}
```

Rules:

* `action_required=true` means task completion must be tracked.
* `action_key` identifies the pending task for reminder lifecycle.
* `dedupe_fields` is used to compute stable change hashes.

## 3) Settings Contract (`settings.yaml`)

All watchers support these fields. Missing values must use defaults.

```yaml
watcher_key: "calories_daily"
enabled: true
watcher_type: "action_task"   # action_task | scheduled_notifier
cron: "0 20 * * *"
timezone: "Asia/Calcutta"
priority: 3

notify:
  enabled: true
  min_severity: "low"         # low | medium | high | critical
  cooldown_minutes: 180
  max_notifications_per_day: 6
  only_on_change: true
  quiet_hours:
    enabled: false
    start: "23:00"
    end: "07:00"
    timezone: "Asia/Calcutta"

reminder:
  repeat_until_done: true
  repeat_every_minutes: 120
  max_repeats: 8
  escalation:
    enabled: true
    step_after_misses: [ 2, 4, 7 ]
    severity_by_step:
      "2": "high"
      "4": "critical"
      "7": "critical"

prompt:
  language: "en"
  tone: "direct"
  max_chars: 1200
  include_sections: [ "what_happened", "why_important", "what_to_do_next" ]
  importance_rubric: |
    Important if it affects health, security, uptime, deadlines, or money.
  user_template: |
    Watcher: {watcher_key}
    Type: {watcher_type}
    Title: {title}
    Summary: {summary}
    Severity: {severity}
    Changed: {changed}
    Action required: {action_required}
    Miss count: {miss_count}
    Facts: {facts_json}
    Tell me if this should notify now.
```

Default values (must apply when missing):

```yaml
enabled: true
watcher_type: "scheduled_notifier"
cron: "0 * * * *"
timezone: "UTC"
priority: 5
notify.enabled: true
notify.min_severity: "medium"
notify.cooldown_minutes: 120
notify.max_notifications_per_day: 10
notify.only_on_change: true
notify.quiet_hours.enabled: false
reminder.repeat_until_done: false
reminder.repeat_every_minutes: 180
reminder.max_repeats: 5
reminder.escalation.enabled: true
prompt.language: "en"
prompt.tone: "direct"
prompt.max_chars: 1000
prompt.include_sections: [ "what_happened", "what_to_do_next" ]
prompt.importance_rubric: "Important if user action is needed or impact is high."
```

## 4) SQLite Tables

Use these tables:

1. `watcher_registry`
2. `watcher_schedule`
3. `watcher_runs`
4. `watcher_state`
5. `watcher_queue`
6. `reminder_state`
7. `reminder_events`

Suggested SQL:

```sql
CREATE TABLE IF NOT EXISTS watcher_registry
(
    watcher_key
    TEXT
    PRIMARY
    KEY,
    watcher_name
    TEXT
    NOT
    NULL,
    watcher_type
    TEXT
    NOT
    NULL,
    enabled
    INTEGER
    NOT
    NULL
    DEFAULT
    1,
    settings_yaml
    TEXT
    NOT
    NULL,
    updated_at
    TEXT
    NOT
    NULL
);

CREATE TABLE IF NOT EXISTS watcher_schedule
(
    watcher_key
    TEXT
    PRIMARY
    KEY,
    cron_expr
    TEXT
    NOT
    NULL,
    timezone
    TEXT
    NOT
    NULL,
    next_run_at
    TEXT,
    enabled
    INTEGER
    NOT
    NULL
    DEFAULT
    1,
    updated_at
    TEXT
    NOT
    NULL
);

CREATE TABLE IF NOT EXISTS watcher_queue
(
    queue_id
    INTEGER
    PRIMARY
    KEY
    AUTOINCREMENT,
    watcher_key
    TEXT
    NOT
    NULL,
    scheduled_for
    TEXT
    NOT
    NULL,
    status
    TEXT
    NOT
    NULL
    DEFAULT
    'queued', -- queued|running|done|failed
    created_at
    TEXT
    NOT
    NULL,
    started_at
    TEXT,
    finished_at
    TEXT
);

CREATE TABLE IF NOT EXISTS watcher_runs
(
    run_id
    INTEGER
    PRIMARY
    KEY
    AUTOINCREMENT,
    watcher_key
    TEXT
    NOT
    NULL,
    triggered_at
    TEXT
    NOT
    NULL,
    completed_at
    TEXT,
    run_status
    TEXT
    NOT
    NULL, -- ok|failed
    result_json
    TEXT,
    result_hash
    TEXT,
    changed
    INTEGER
    NOT
    NULL
    DEFAULT
    1,
    severity
    TEXT,
    action_required
    INTEGER
    NOT
    NULL
    DEFAULT
    0,
    action_key
    TEXT,
    error_text
    TEXT
);

CREATE TABLE IF NOT EXISTS watcher_state
(
    watcher_key
    TEXT
    PRIMARY
    KEY,
    last_hash
    TEXT,
    last_result_json
    TEXT,
    last_severity
    TEXT,
    last_notified_at
    TEXT,
    notify_count_today
    INTEGER
    NOT
    NULL
    DEFAULT
    0,
    notify_day
    TEXT
);

CREATE TABLE IF NOT EXISTS reminder_state
(
    reminder_id
    INTEGER
    PRIMARY
    KEY
    AUTOINCREMENT,
    watcher_key
    TEXT
    NOT
    NULL,
    action_key
    TEXT
    NOT
    NULL,
    open
    INTEGER
    NOT
    NULL
    DEFAULT
    1,
    first_detected_at
    TEXT
    NOT
    NULL,
    last_reminded_at
    TEXT,
    remind_count
    INTEGER
    NOT
    NULL
    DEFAULT
    0,
    miss_count
    INTEGER
    NOT
    NULL
    DEFAULT
    0,
    resolved_at
    TEXT,
    UNIQUE
(
    watcher_key,
    action_key,
    open
)
    );

CREATE TABLE IF NOT EXISTS reminder_events
(
    event_id
    INTEGER
    PRIMARY
    KEY
    AUTOINCREMENT,
    reminder_id
    INTEGER
    NOT
    NULL,
    watcher_key
    TEXT
    NOT
    NULL,
    action_key
    TEXT
    NOT
    NULL,
    event_type
    TEXT
    NOT
    NULL, -- created|reminded|escalated|resolved|skipped
    reason
    TEXT,
    severity
    TEXT,
    created_at
    TEXT
    NOT
    NULL
);
```

## 5) Queue + Scheduler Behavior

Startup:

* discover watchers in `skills/heartbeat/watchers/*`
* load `settings.yaml`
* apply defaults
* upsert `watcher_registry` and `watcher_schedule`

Tick behavior:

* scheduler fetches due `watcher_schedule` rows
* for each watcher, call `check_crontables_and_enqueue(...)`
* queue row inserted in `watcher_queue`
* runner pops oldest `queued` by `scheduled_for`, then `priority`

## 6) Prompt Drafting (No LLM Call Here)

Create `prompt_drafter.py` with:

```python
from typing import Dict, Any


def build_prompt_payload(settings: Dict[str, Any], result: Dict[str, Any], decision_ctx: Dict[str, Any]) -> Dict[
    str, str]:
    """
    Returns {"system_prompt": "...", "user_prompt": "..."}.
    This module does not call any LLM.
    """
```

Include in prompt payload:

* watcher identity
* watcher type
* current result summary
* changed/not-changed
* reminder miss count
* rubric text from `settings.prompt.importance_rubric`
* explicit ask: notify now, snooze, or skip

## 7) Reminder Behavior Rules

For `watcher_type=action_task`:

* if `action_required=true` and action still open, create/update `reminder_state`
* each reminder increments `remind_count`
* if still unresolved at next cycle, increment `miss_count`
* when `miss_count` reaches escalation thresholds, write `escalated` event and raise severity
* reminder stops only when resolved signal is received

For `watcher_type=scheduled_notifier`:

* no `miss_count` penalty
* send on change/schedule based on `notify` rules
* no repeat-until-done logic unless explicitly enabled

## 8) Notify Gate Logic

A notification candidate is sent only if all pass:

* watcher enabled
* notify enabled
* severity >= `min_severity`
* not in cooldown window
* daily cap not exceeded
* if `only_on_change=true`, hash changed
* quiet-hours policy allows send

## 9) Hashing Rule

Hash should prefer `dedupe_fields`.
Fallback: hash canonical JSON of (`title`, `summary`, `facts`, `severity`, `action_required`, `action_key`).

## 10) Minimal Implementation Order

1. `db.py` schema + upserts
2. `registry.py` scan/load/default merge
3. `watcher.py` contract + `check_crontables_and_enqueue`
4. `runner.py` queue pop + run + state hash + reminder updates
5. `prompt_drafter.py` output prompt payload only
6. one sample watcher each:
    * action task: calories logging
    * scheduled notifier: youtube stats

## 11) End-to-End Sample Watchers

Action task example:

* watcher key: `calories_daily`
* if no calorie log today, `action_required=true`, `action_key=log_calories_YYYY-MM-DD`
* reminder repeats and miss count escalates

Scheduled notifier example:

* watcher key: `youtube_daily_stats`
* run once daily, summarize views/subscribers delta
* no open-task reminder unless custom rule requests it
