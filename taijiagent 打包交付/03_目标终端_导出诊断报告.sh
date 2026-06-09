#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORT_DIR="$SCRIPT_DIR/诊断报告"
REPORT_FILE="$REPORT_DIR/taiji-agent-diagnose-$(date +%Y%m%d_%H%M%S).txt"

mkdir -p "$REPORT_DIR"

{
  echo "太极 Agent 诊断报告"
  echo "生成时间：$(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "交付目录：$SCRIPT_DIR"
  echo

  if command -v taiji-agent-diagnose >/dev/null 2>&1; then
    taiji-agent-diagnose
  elif [ -x /opt/taiji-agent/scripts/taiji-agent-diagnose ]; then
    TAIJI_AGENT_ROOT=/opt/taiji-agent TAIJI_AGENT_USE_USER_DIRS=1 /opt/taiji-agent/scripts/taiji-agent-diagnose
  else
    echo "[WARN] 未找到 taiji-agent-diagnose，可能尚未安装新版太极 Agent。"
    echo
    echo "==== Basic System ===="
    uname -a 2>&1 || true
    [ -f /etc/os-release ] && cat /etc/os-release 2>&1 || true
    echo
    echo "==== Package State ===="
    dpkg-query -W -f='${Package} ${Version} ${Architecture} ${db:Status-Abbrev}\n' taiji-agent 2>&1 || true
    echo
    echo "==== Delivery Files ===="
    find "$SCRIPT_DIR" -maxdepth 2 -type f | sort 2>&1 || true
  fi
} > "$REPORT_FILE" 2>&1

chmod 600 "$REPORT_FILE" 2>/dev/null || true

printf '[OK] 诊断报告已生成：%s\n' "$REPORT_FILE"
printf '如果后续需要排查，请把这个 txt 文件发回来。\n'
