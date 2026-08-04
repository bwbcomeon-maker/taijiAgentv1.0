#!/usr/bin/env bash
# Compatibility launcher for the Taiji Agent Electron desktop shell on macOS.
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
LAB_DIR="$SCRIPT_DIR"
REPO_DIR="$(cd "$LAB_DIR/.." && pwd -P)"
APP_DIR="$REPO_DIR/apps/taiji-desktop"
ELECTRON_BIN="$APP_DIR/node_modules/electron/dist/Electron.app/Contents/MacOS/Electron"
SOURCE_GATE="$REPO_DIR/scripts/check-clean-worktree.sh"
TAIJI_SOURCE_ROOT="$REPO_DIR"
if [ -z "${TAIJI_AGENT_PYTHON:-}" ]; then
  if [ -x "$LAB_DIR/sources/hermes-agent/venv/bin/python" ]; then
    TAIJI_AGENT_PYTHON="$LAB_DIR/sources/hermes-agent/venv/bin/python"
  else
    TAIJI_AGENT_PYTHON="$LAB_DIR/sources/hermes-agent/.venv/bin/python"
  fi
fi
TAIJI_WEBUI_PYTHON="${TAIJI_WEBUI_PYTHON:-$TAIJI_AGENT_PYTHON}"
SOURCE_INSTANCE_ID="$(
  printf '%s' "$TAIJI_SOURCE_ROOT" \
    | /usr/bin/shasum -a 256 \
    | /usr/bin/awk '{print substr($1, 1, 16)}'
)"
XDG_STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state/taiji-agent/source-instances/$SOURCE_INSTANCE_ID}"
TAIJI_DESKTOP_USER_DATA_DIR="${TAIJI_DESKTOP_USER_DATA_DIR:-$HOME/.local/share/taiji-agent/source-instances/$SOURCE_INSTANCE_ID/electron-user-data}"
TAIJI_RUNTIME_HOME="${TAIJI_RUNTIME_HOME:-$HOME/.local/share/taiji-agent/source-instances/$SOURCE_INSTANCE_ID/runtime-home}"
TAIJI_WORKSPACE="${TAIJI_WORKSPACE:-$HOME/.local/share/taiji-agent/source-instances/$SOURCE_INSTANCE_ID/workspace}"
TAIJI_AGENT_TMP_DIR="${TAIJI_AGENT_TMP_DIR:-$HOME/.local/state/taiji-agent/source-instances/$SOURCE_INSTANCE_ID/tmp}"
LOG_DIR="$XDG_STATE_HOME/taiji-agent/logs"
LOG_FILE="$LOG_DIR/taiji-desktop-launcher.log"

unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE
unset GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES

if [ -z "${TAIJI_SOURCE_MODE:-}" ]; then
  if [ -d "$REPO_DIR/.git" ]; then
    TAIJI_SOURCE_MODE="formal"
  elif [ -f "$REPO_DIR/.git" ]; then
    TAIJI_SOURCE_MODE="development"
  else
    echo "无法识别当前源码目录：$REPO_DIR" >&2
    exit 1
  fi
fi

mkdir -p \
  "$LOG_DIR" \
  "$TAIJI_DESKTOP_USER_DATA_DIR" \
  "$TAIJI_RUNTIME_HOME" \
  "$TAIJI_WORKSPACE" \
  "$TAIJI_AGENT_TMP_DIR"

if ! /bin/bash "$SOURCE_GATE" \
  --mode "$TAIJI_SOURCE_MODE" \
  --repo-root "$REPO_DIR" \
  --source-root "$REPO_DIR"; then
  echo "当前源码来源校验失败，未启动太极 Agent。日志：$LOG_FILE" >&2
  exit 1
fi

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
export TAIJI_SOURCE_MODE
export TAIJI_SOURCE_COMMIT
export TAIJI_SOURCE_DIRTY
export TAIJI_DESKTOP_USER_DATA_DIR
export XDG_STATE_HOME
export TAIJI_RUNTIME_HOME
export TAIJI_WORKSPACE
export TAIJI_AGENT_TMP_DIR
export TAIJI_AGENT_PYTHON
export TAIJI_WEBUI_PYTHON
export TMPDIR="$TAIJI_AGENT_TMP_DIR"
export TMP="$TAIJI_AGENT_TMP_DIR"
export TEMP="$TAIJI_AGENT_TMP_DIR"

{
  echo "========================================"
  echo "太极 Agent 桌面端兼容启动器"
  echo "========================================"
  echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "App : $APP_DIR"
  echo "Root: $TAIJI_SOURCE_ROOT"
  echo "Source mode: $TAIJI_SOURCE_MODE"
  echo "Commit: $TAIJI_SOURCE_COMMIT"
  echo "Dirty: $TAIJI_SOURCE_DIRTY"
  echo "Instance: $SOURCE_INSTANCE_ID"
  echo "Electron data: $TAIJI_DESKTOP_USER_DATA_DIR"
  echo "State home: $XDG_STATE_HOME"
  echo "Runtime home: $TAIJI_RUNTIME_HOME"
  echo "Workspace: $TAIJI_WORKSPACE"
  echo "Temporary data: $TAIJI_AGENT_TMP_DIR"
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
  /usr/bin/nohup "$ELECTRON_BIN" "$APP_DIR" >>"$LOG_FILE" 2>&1 &
  disown
} >>"$LOG_FILE" 2>&1

exit 0
