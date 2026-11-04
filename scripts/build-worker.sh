#!/usr/bin/env bash
# Build sendline-worker (one-folder) for Sendline Desktop on macOS.
set -euo pipefail

AUTOMATE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$AUTOMATE_ROOT"

VENV_PYTHON="$AUTOMATE_ROOT/venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Missing venv at $VENV_PYTHON — create venv and pip install -r requirements.txt" >&2
  exit 1
fi

GIT_SHA="$(git -C "$AUTOMATE_ROOT" rev-parse --short HEAD 2>/dev/null || true)"
if [[ -z "$GIT_SHA" ]]; then
  GIT_SHA="dev"
fi
export WORKER_VERSION="$GIT_SHA"
echo "WORKER_VERSION=$WORKER_VERSION"

"$VENV_PYTHON" -m pip install -q pyinstaller
"$VENV_PYTHON" -m PyInstaller --noconfirm --clean "$AUTOMATE_ROOT/sendline-worker.spec"

DIST_DIR="$AUTOMATE_ROOT/dist/sendline-worker"
echo "$WORKER_VERSION" >"$DIST_DIR/VERSION"
echo "Built: $DIST_DIR/sendline-worker"
echo "VERSION: $DIST_DIR/VERSION"
