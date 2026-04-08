# Crew Personal Agents

Telegram-first multi-agent runtime with worker queues, scheduler jobs, watchers, and skill execution.
This repository runs in **single-user mode** using the fixed profile at `users/default`.

## Setup

1. Create virtual environment and install dependencies.
2. Copy `.env.example` to `.env`.
3. Fill required values in `.env`:
- `TELEGRAM_BOT_TOKEN`
- `GROUP_GENERAL_CHAT_ID` (primary allowed/delivery group chat)
- Access control:
  - `ALLOWED_USERNAMES` (comma-separated usernames without `@`)
  - Incoming messages are accepted if group chat ID matches `GROUP_GENERAL_CHAT_ID` or username matches.
- `OLLAMA_*` and `QDRANT_*` values for your infra

PowerShell quick start:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python start.py
```

## Environment And Secrets

- All operational credentials and host settings must stay in `.env`.
- `.env` is git-ignored by default.
- Do not hard-code tokens/keys in Python files.

## Git Upload Checklist

Before pushing:

1. Ensure `.env` is not staged.
2. Ensure runtime files are not staged (`*.sqlite`, logs, `__pycache__`).
3. Confirm only code/docs/config template changes are committed.

Useful command:

```powershell
git status --short
```

## Backup And Restore

Important runtime data is usually:

- `.env`
- `users\default\data.sqlite`
- `watcher\data\watcher.db`

### Backup (PowerShell)

```powershell
.\scripts\backup_state.ps1
```

### Restore (PowerShell)

```powershell
.\scripts\restore_state.ps1 -BackupDir "backups\YYYYMMDD_HHMMSS"
```

After restore, restart the app:

```powershell
python start.py
```
