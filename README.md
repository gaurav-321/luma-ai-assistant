
This project provides a small tool interface for an isolated execution sandbox.  
It lets an agent run Python code, run shell commands, and read/write/list files through a local HTTP sandbox server.

## What This Project Does

- Exposes a single entry point in `tool.py` with operation-based routing.
- Implements an HTTP client in `sandbox_tools.py` for sandbox endpoints.
- Normalizes responses into a stable JSON shape so tool-calling systems can consume output reliably.
- Auto-manages sandbox sessions and includes helpers for switching/reusing sessions.

## How It Works

`tool.py` receives:

```json
{
  "operation": "cmd_run",
  "payload": {"cmd": "echo hi"}
}
```

It dispatches to the matching async function in `sandbox_tools.py`.

Supported operations:

- `init_llm_tools`: initialize client + health check + ensure session
- `py_run`: execute Python in current sandbox session
- `fs_write`: write a file in session workspace
- `fs_read`: read a file in session workspace
- `fs_list`: list files/directories in session workspace
- `cmd_run`: execute shell command in session workspace

The client talks to a sandbox server (default `http://127.0.0.1:1000`) using:

- `POST /sessions`
- `GET /health`
- `POST /py/run`
- `POST /fs/write`
- `GET /fs/read`
- `POST /fs/list`
- `POST /cmd/run`

## Linux Setup

Run:

```bash
chmod +x setup_linux.sh
./setup_linux.sh
```

The script:

1. Creates `.venv`
2. Installs `requests`
3. Verifies local module import paths

## Usage

Activate venv:

```bash
source .venv/bin/activate
```

Run built-in smoke tests:

```bash
python tool.py
```

Set custom sandbox URL if needed:

```bash
export SANDBOX_BASE_URL="http://127.0.0.1:1000"
```

## Notes

- `tool.py` inserts repository root into `sys.path` so `skills.work.sandbox` imports resolve.
- `sandbox_tools.py` normalizes command output and tries to fix garbled UTF-16LE output from some Windows command cases.
