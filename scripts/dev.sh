#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -z "${PYTHON_BIN:-}" ] && [ -x "${ROOT_DIR}/backend/.venv/bin/python" ]; then
  PYTHON_BIN="${ROOT_DIR}/backend/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

cleanup() {
  if [ -n "${BACKEND_PID:-}" ]; then
    kill "${BACKEND_PID}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

"${PYTHON_BIN}" -m uvicorn app.main:app --reload --app-dir "${ROOT_DIR}/backend" --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

cd "${ROOT_DIR}/frontend"
npm run dev
