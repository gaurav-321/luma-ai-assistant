#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
LOG_DIR="${PROJECT_DIR}/logs"
SERVICE_NAME="crew-personal-agents"
SERVICE_FILE="${HOME}/.config/systemd/user/${SERVICE_NAME}.service"
PID_FILE="${PROJECT_DIR}/.${SERVICE_NAME}.pid"

echo "[1/6] Project directory: ${PROJECT_DIR}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required but was not found."
  echo "Install it first (example: sudo apt-get install -y python3 python3-venv python3-pip)."
  exit 1
fi

mkdir -p "${LOG_DIR}"

echo "[2/6] Creating virtual environment"
python3 -m venv "${VENV_DIR}"

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

echo "[3/6] Installing dependencies"
python -m pip install --upgrade pip
python -m pip install -r "${PROJECT_DIR}/requirements.txt"

if [[ ! -f "${PROJECT_DIR}/.env" && -f "${PROJECT_DIR}/.env.example" ]]; then
  cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
  echo "[4/6] Created .env from .env.example (update values before production use)"
else
  echo "[4/6] .env already exists (or no template found)"
fi

echo "[5/5] Creating background service and starting it"
if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  mkdir -p "$(dirname "${SERVICE_FILE}")"
  cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Crew Personal Agents
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-${PROJECT_DIR}/.env
ExecStart=${VENV_DIR}/bin/python ${PROJECT_DIR}/start.py
Restart=always
RestartSec=5
StandardOutput=append:${LOG_DIR}/service.log
StandardError=append:${LOG_DIR}/service.err.log

[Install]
WantedBy=default.target
EOF

  systemctl --user daemon-reload
  systemctl --user enable --now "${SERVICE_NAME}.service"
  echo "Service active: ${SERVICE_NAME}.service"
  systemctl --user --no-pager --full status "${SERVICE_NAME}.service" | sed -n '1,12p'
else
  echo "systemd user service is not available. Falling back to nohup background process."
  if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" >/dev/null 2>&1; then
    echo "Background process already running with PID $(cat "${PID_FILE}")"
  else
    nohup "${VENV_DIR}/bin/python" "${PROJECT_DIR}/start.py" >> "${LOG_DIR}/background.log" 2>&1 &
    echo $! > "${PID_FILE}"
    echo "Started background process with PID $(cat "${PID_FILE}")"
  fi
fi

cat <<'EOF'

Setup complete.

Useful commands:
- Service logs (systemd): journalctl --user -u crew-personal-agents.service -f
- Service restart (systemd): systemctl --user restart crew-personal-agents.service
- Fallback logs (nohup): tail -f logs/background.log
EOF
