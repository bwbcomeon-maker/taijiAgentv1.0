#!/usr/bin/env bash
# Compatibility launcher for the Taiji Agent Electron desktop shell on macOS.
set -euo pipefail

REPO_DIR="/Users/bwb/Documents/工作/taiji-agentv1.0"
APP_DIR="$REPO_DIR/apps/taiji-desktop"
LAB_DIR="$REPO_DIR/hermes-local-lab"
APP_BUNDLE="$LAB_DIR/启动太极Agent桌面端.app"
LOG_DIR="$HOME/.local/state/taiji-agent/logs"
LOG_FILE="$LOG_DIR/taiji-desktop-launcher.log"

mkdir -p "$LOG_DIR"

{
  echo "========================================"
  echo "太极 Agent 桌面端兼容启动器"
  echo "========================================"
  echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "App : $APP_DIR"
  echo

  if [ -d "$APP_BUNDLE" ]; then
    echo "Opening app bundle: $APP_BUNDLE"
    open "$APP_BUNDLE"
    exit 0
  fi

  if ! command -v npm >/dev/null 2>&1; then
    echo "未找到 npm。请先安装 Node.js 20+。"
    exit 1
  fi

  cd "$APP_DIR"

  if [ ! -d node_modules/electron ]; then
    echo "首次启动：正在安装 Electron 依赖..."
    npm ci
  fi

  echo "正在打开太极 Agent 桌面端..."
  export TAIJI_AGENT_ROOT="$LAB_DIR"
  "$APP_DIR/node_modules/.bin/electron" "$APP_DIR" >/dev/null 2>&1 &
  disown
} >>"$LOG_FILE" 2>&1
