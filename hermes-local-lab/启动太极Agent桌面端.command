#!/usr/bin/env bash
# Double-click launcher for the Taiji Agent Electron desktop shell on macOS.
set -euo pipefail

REPO_DIR="/Users/bwb/Documents/工作/taiji-agentv1.0"
APP_DIR="$REPO_DIR/apps/taiji-desktop"
LOG_DIR="$HOME/.local/state/taiji-agent/logs"
LOG_FILE="$LOG_DIR/taiji-desktop-launcher.log"

mkdir -p "$LOG_DIR"

{
  echo "========================================"
  echo " 太极 Agent 桌面端启动器"
  echo "========================================"
  echo "Time: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "App : $APP_DIR"
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

  echo "正在打开太极 Agent 桌面端..."
  export TAIJI_AGENT_ROOT="$REPO_DIR/hermes-local-lab"
  npm start
} 2>&1 | tee -a "$LOG_FILE"
