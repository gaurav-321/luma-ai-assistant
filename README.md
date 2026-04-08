# Crew Personal Agents

This repository runs a Telegram-first personal AI assistant with persistent memory, pluggable skills, scheduled tasks, watcher automations, and a local debug web UI.

## Purpose

This project is built to act as a day-to-day personal assistant for a single user/group, including:

- Personal task reminders and scheduled follow-ups.
- Food logging and calorie tracking workflows.
- Exercise/fitness message generation and workout nudges.
- Bug report collection, summarization, and notifier-style updates.
- Watcher-based monitoring that sends alerts/notifications when conditions match.

## What This Project Does

- Receives messages from an allowed Telegram group/user and processes them through an agent pipeline.
- Uses a manager-style LLM agent that can call local skills (tools) under `skills/`.
- Stores and retrieves semantic memory via Qdrant + mem0 for better context across conversations.
- Runs background automation loops:
  - scheduler jobs (`core/workers/cronjobs.py`)
  - watcher checks and webhooks (`core/workers/watcher.py`, `watcher/`)
- Publishes a FastAPI debug UI for health and run/event tracing (`core/webui.py`).
- Keeps per-user state and prompts under `users/<username>/` (SQLite + markdown profiles).

## Core Architecture

- Entry point: `start.py` -> `core/telegram_bot.py`
- Runtime loops:
  - `worker_loop`: executes queued work via `process_message`
  - `reply_loop`: sends formatted responses back to Telegram
  - `scheduler_loop`: triggers cron-style agent tasks
  - `watcher_loop`: executes watcher checks and queues decisions
  - `webui_server`: serves FastAPI debug UI
- Agent orchestration: `core/llm.py` + `core/agent_builder.py`
- Skills:
  - `skills/work/*` for operational tools (researcher, telegram, sandbox, browser, reddit)
  - `skills/personal/*` for personal workflows (todo, food log, fitness, daily summary, scheduler)

## Run Locally

1. Create env file and set secrets:

```powershell
Copy-Item .env.example .env
```

Required at minimum:
- `TELEGRAM_BOT_TOKEN`
- `GROUP_GENERAL_CHAT_ID`

2. Install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

3. Start the bot + background services:

```powershell
python start.py
```

4. Debug UI:
- `http://127.0.0.1:8787`
- `http://127.0.0.1:8787/health`

## Docker

Use `DOCKER.md` for full instructions, or quick start:

```powershell
docker compose build
docker compose up -d
```

Default exposed port: `8787`.

## Repository Layout

- `core/`: bot runtime, orchestration, workers, web UI
- `skills/`: tool/skill implementations used by agents
- `watcher/`: watcher configs, checks, and watcher state DB
- `users/`: per-user prompts/config/state (`data.sqlite`)
- `scripts/`: state backup/restore helpers
- `extra_skills/`: additional/experimental skills
