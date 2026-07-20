#!/usr/bin/env bash
# Compatibility launcher for the Taiji Agent Electron desktop shell on macOS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
LAB_DIR="$SCRIPT_DIR"
REPO_DIR="$(cd "$LAB_DIR/.." && pwd -P)"
APP_DIR="$REPO_DIR/apps/taiji-desktop"
ELECTRON_BIN="$APP_DIR/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron"
LOG_DIR="$HOME/.local/state/taiji-agent/logs"
LOG_FILE="$LOG_DIR/taiji-desktop-launcher.log"
SOURCE_GATE="$REPO_DIR/scripts/check-clean-worktree.sh"
TAIJI_SOURCE_MODE="${TAIJI_SOURCE_MODE:-formal}"

"$SOURCE_GATE" \
  --mode "$TAIJI_SOURCE_MODE" \
  --repo-root "$REPO_DIR" \
  --source-root "$REPO_DIR"

mkdir -p "$LOG_DIR"

TAIJI_SOURCE_ROOT="$REPO_DIR"
SOURCE_INSTANCE_ID="$(
  printf '%s' "$TAIJI_SOURCE_ROOT" \
    | /usr/bin/shasum -a 256 \
    | /usr/bin/awk '{print substr($1, 1, 16)}'
)"
if [ -z "${TAIJI_DESKTOP_USER_DATA_DIR:-}" ]; then
  TAIJI_DESKTOP_USER_DATA_DIR="$HOME/.local/share/taiji-agent/source-instances/$SOURCE_INSTANCE_ID/electron-user-data"
fi
mkdir -p "$TAIJI_DESKTOP_USER_DATA_DIR"
TAIJI_SOURCE_COMMIT="unknown"
TAIJI_SOURCE_DIRTY="unknown"
if [ -x /usr/bin/git ] && /usr/bin/git -C "$REPO_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  TAIJI_SOURCE_COMMIT="$(/usr/bin/git -C "$REPO_DIR" rev-parse HEAD)"
  source_status="$(/usr/bin/git -C "$REPO_DIR" status --porcelain=v1 --untracked-files=normal)"
  if [ -n "$source_status" ]; then
    TAIJI_SOURCE_DIRTY="1"
  else
    TAIJI_SOURCE_DIRTY="0"
  fi
  unset source_status
fi
export TAIJI_AGENT_ROOT="$LAB_DIR"
export TAIJI_SOURCE_ROOT
export TAIJI_SOURCE_COMMIT
export TAIJI_SOURCE_DIRTY
export TAIJI_DESKTOP_USER_DATA_DIR

{
  echo "========================================"
  echo "太极 Agent 桌面端兼容启动器"
  echo "========================================"
  echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "App : $APP_DIR"
  echo "Root: $TAIJI_SOURCE_ROOT"
  echo "Commit: $TAIJI_SOURCE_COMMIT"
  echo "Dirty: $TAIJI_SOURCE_DIRTY"
  echo "Instance: $SOURCE_INSTANCE_ID"
  echo "Electron data: $TAIJI_DESKTOP_USER_DATA_DIR"
  echo

  if ! command -v npm >/dev/null 2>&1; then
    echo "未找到 npm。请先安装 Node.js 20+。"
    exit 1
  fi

  cd "$APP_DIR"

  if [ ! -d node_modules/electron ]; then
    echo "首次启动：正在安装 Electron 依赖..."
    npm ci
  fi

  if [ ! -x "$ELECTRON_BIN" ]; then
    echo "Electron 启动文件不存在：$ELECTRON_BIN" >&2
    exit 1
  fi

  echo "正在打开太极 Agent 桌面端..."
  "$ELECTRON_BIN" "$APP_DIR" >>"$LOG_FILE" 2>&1 &
  disown
} >>"$LOG_FILE" 2>&1
