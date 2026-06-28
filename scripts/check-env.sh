#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "Project: ${ROOT_DIR}"

if command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  "${PYTHON_BIN}" - <<'PY'
import sys
version = sys.version_info
print(f"Python: {version.major}.{version.minor}.{version.micro}")
if version < (3, 10):
    raise SystemExit("Python 3.10+ is required.")
PY
else
  echo "Python: missing"
  exit 1
fi

if command -v node >/dev/null 2>&1; then
  node - <<'JS'
const version = process.versions.node.split('.').map(Number)
console.log(`Node: ${process.versions.node}`)
if (version[0] < 20 || (version[0] === 20 && version[1] < 19)) {
  process.exitCode = 1
  console.error('Node 20.19+ is required.')
}
JS
else
  echo "Node: missing"
  exit 1
fi

if command -v ffmpeg >/dev/null 2>&1; then
  echo "FFmpeg: $(command -v ffmpeg)"
else
  echo "FFmpeg: missing"
  exit 1
fi

echo "Environment looks ready."
