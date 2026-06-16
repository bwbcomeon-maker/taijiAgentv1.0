#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="$SCRIPT_DIR/清空对话记录日志"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/clear-chat-$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

pause_before_exit() {
  local code=$?
  echo
  if [ "$code" -eq 0 ]; then
    echo "[完成] 对话记录清理完成。日志：$LOG_FILE"
  else
    echo "[失败] 对话记录清理失败。日志：$LOG_FILE"
  fi
  if [ -t 0 ]; then
    echo
    read -r -p "按回车关闭窗口..." _
  fi
  exit "$code"
}
trap pause_before_exit EXIT

if [ "${EUID:-$(id -u)}" -eq 0 ]; then
  echo "[FAIL] 请不要用 root/sudo 运行本工具。"
  echo "       直接用当前登录用户双击运行即可，否则会清理 root 用户的数据而不是你的对话。"
  exit 1
fi

APP_ROOT="/opt/taiji-agent"
XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
XDG_STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
TAIJI_DATA_DIR="$XDG_DATA_HOME/taiji-agent"
TAIJI_STATE_DIR="$XDG_STATE_HOME/taiji-agent"
TAIJI_RUNTIME_HOME="$TAIJI_DATA_DIR/runtime-home"
WEBUI_DIR="$TAIJI_RUNTIME_HOME/webui"
WEBUI_SESSIONS_DIR="$WEBUI_DIR/sessions"
WEBUI_ATTACHMENTS_DIR="$WEBUI_DIR/attachments"
AGENT_SESSIONS_DIR="$TAIJI_RUNTIME_HOME/sessions"
STATE_DB="$TAIJI_RUNTIME_HOME/state.db"
BACKUP_DIR="$HOME/taiji-agent-会话备份-$(date +%Y%m%d_%H%M%S)"

count_files() {
  local dir="$1"
  if [ -d "$dir" ]; then
    find "$dir" -mindepth 1 -maxdepth 1 2>/dev/null | wc -l | tr -d ' '
  else
    printf '0'
  fi
}

echo "太极 Agent 对话记录清理工具"
echo "当前用户：$USER"
echo "本机运行数据目录：$TAIJI_RUNTIME_HOME"
echo
echo "本工具会清理："
echo "  - WebUI 对话 JSON：$WEBUI_SESSIONS_DIR"
echo "  - 聊天上传附件：$WEBUI_ATTACHMENTS_DIR"
echo "  - Agent 会话文件：$AGENT_SESSIONS_DIR"
echo "  - state.db 里的 sessions/messages 会话记录"
echo
echo "本工具不会清理："
echo "  - 模型配置和 API Key"
echo "  - 工作区文件：$TAIJI_DATA_DIR/workspace"
echo "  - 生成图片缓存"
echo "  - 安装目录：$APP_ROOT"
echo

if [ ! -d "$TAIJI_RUNTIME_HOME" ]; then
  echo "[FAIL] 没找到太极 Agent 用户数据目录：$TAIJI_RUNTIME_HOME"
  echo "       请确认你是在安装太极 Agent 的同一个登录用户下运行。"
  exit 1
fi

echo "[1/5] 停止当前太极 Agent 进程..."
if [ -x "$APP_ROOT/scripts/stop-all.sh" ]; then
  TAIJI_AGENT_ROOT="$APP_ROOT" TAIJI_AGENT_USE_USER_DIRS=1 "$APP_ROOT/scripts/stop-all.sh" || true
fi
if pgrep -u "$USER" -f "$APP_ROOT" >/dev/null 2>&1; then
  pkill -TERM -u "$USER" -f "$APP_ROOT" 2>/dev/null || true
  sleep 2
fi
if pgrep -u "$USER" -f "$APP_ROOT" >/dev/null 2>&1; then
  pkill -KILL -u "$USER" -f "$APP_ROOT" 2>/dev/null || true
fi

echo "[2/5] 备份当前会话数据到：$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
[ -d "$WEBUI_SESSIONS_DIR" ] && cp -a "$WEBUI_SESSIONS_DIR" "$BACKUP_DIR/webui-sessions" || true
[ -d "$WEBUI_ATTACHMENTS_DIR" ] && cp -a "$WEBUI_ATTACHMENTS_DIR" "$BACKUP_DIR/webui-attachments" || true
[ -d "$AGENT_SESSIONS_DIR" ] && cp -a "$AGENT_SESSIONS_DIR" "$BACKUP_DIR/agent-sessions" || true
for f in "$STATE_DB" "$STATE_DB-wal" "$STATE_DB-shm" "$STATE_DB-journal"; do
  [ -f "$f" ] && cp -a "$f" "$BACKUP_DIR/" || true
done

echo "[3/5] 清理文件型会话记录..."
mkdir -p "$WEBUI_SESSIONS_DIR" "$WEBUI_ATTACHMENTS_DIR" "$AGENT_SESSIONS_DIR"
find "$WEBUI_SESSIONS_DIR" -maxdepth 1 -type f \( -name '*.json' -o -name '*.json.bak' -o -name '*.tmp.*' -o -name '_index.json' \) -delete 2>/dev/null || true
rm -rf "$WEBUI_ATTACHMENTS_DIR"
mkdir -p "$WEBUI_ATTACHMENTS_DIR"
find "$AGENT_SESSIONS_DIR" -maxdepth 1 -type f \( -name 'session_*.json' -o -name 'sessions.json' -o -name '*.tmp.*' \) -delete 2>/dev/null || true

echo "[4/5] 清理 state.db 里的会话表..."
python3 - "$STATE_DB" <<'PY'
import sqlite3
import sys
from pathlib import Path

db = Path(sys.argv[1]).expanduser()
if not db.exists():
    print("state.db 不存在，跳过")
    raise SystemExit(0)

conn = sqlite3.connect(str(db))
try:
    cur = conn.cursor()
    tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    deleted = []
    if "messages" in tables:
        cur.execute("DELETE FROM messages")
        deleted.append("messages")
    if "sessions" in tables:
        cur.execute("DELETE FROM sessions")
        deleted.append("sessions")
    if "state_meta" in tables:
        cur.execute("DELETE FROM state_meta WHERE key LIKE 'goal:%'")
        deleted.append("state_meta goal:*")
    conn.commit()
    try:
        cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.DatabaseError:
        pass
    try:
        cur.execute("VACUUM")
        conn.commit()
    except sqlite3.DatabaseError as exc:
        print(f"VACUUM 跳过：{exc}")
    print("已清理表：" + (", ".join(deleted) if deleted else "无匹配表"))
finally:
    conn.close()
PY

echo "[5/5] 复查剩余会话文件数量..."
echo "WebUI sessions 剩余项：$(count_files "$WEBUI_SESSIONS_DIR")"
echo "WebUI attachments 剩余项：$(count_files "$WEBUI_ATTACHMENTS_DIR")"
echo "Agent sessions 剩余项：$(count_files "$AGENT_SESSIONS_DIR")"
echo
echo "备份目录：$BACKUP_DIR"
echo "现在可以重新从开始菜单打开：太极 Agent"
