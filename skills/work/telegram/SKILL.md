---
name: telegram
description: >-
  Manage Telegram forum topics and send messages to topic threads.
  Supports creating, listing, deleting topics, and thread-targeted notifications.
license: Apache-2.0
compatibility: Requires Python agent runtime
metadata:
  author: gaurav
  version: "2.0"
---

# Telegram Skill

## When to Use
- Use for forum-topic creation, listing, deletion, and topic-thread messaging.
- Use when automating Telegram thread-level communication.

## Procedure
1. Discover valid thread IDs with `list_topics`.
2. Create/delete topics if needed.
3. Send messages with `send_to_topic` using `chat_id` + `thread_id`.
4. Re-list topics to confirm state after mutations.

## Pitfalls
- Missing `chat_id` binding.
- Invalid or stale `thread_id`.
- Skipping `list_topics` before send/delete.

## Tool Usage
Main flow (detailed):

```python
run_skill_script(
  skill_name="telegram",
  script_name="tool.py",
  args={
    "operation": "send_to_topic",
    "payload": {
      "chat_id": -1001234567890,
      "thread_id": 42,
      "text": "Daily summary is ready."
    }
  }
)
```

Then topic discovery:

```python
run_skill_script(
  skill_name="telegram",
  script_name="tool.py",
  args={
    "operation": "list_topics",
    "payload": {"chat_id": -1001234567890}
  }
)
```

Other major operations (short form):
- `create_topic`: `run_skill_script(..., args={"operation":"create_topic","payload":{"chat_id":-1001234567890,"name":"Ops Alerts"}})`
- `delete_topic`: `run_skill_script(..., args={"operation":"delete_topic","payload":{"chat_id":-1001234567890,"thread_id":42}})`

## Verification
- Each call returns `ok=true`.
- Topic/message targets match expected `chat_id` and `thread_id`.
- Post-operation topic list reflects expected changes.
