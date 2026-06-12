#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ELECTRON_BIN="$REPO_ROOT/apps/taiji-desktop/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron"

if [ ! -x "$ELECTRON_BIN" ]; then
  echo "Electron runtime not found. Run npm ci inside apps/taiji-desktop first." >&2
  exit 1
fi

exec "$ELECTRON_BIN" "$SCRIPT_DIR"
