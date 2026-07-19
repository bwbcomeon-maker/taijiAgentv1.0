#!/usr/bin/env bash
# setup.sh — Automated setup for twozero MCP plugin for TouchDesigner
# Idempotent: safe to run multiple times.
set -euo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
OK="${GREEN}✔${NC}"; FAIL="${RED}✘${NC}"; WARN="${YELLOW}⚠${NC}"

TWOZERO_URL="https://www.404zero.com/pisang/twozero.tox"
TOX_PATH="$HOME/Downloads/twozero.tox"
HERMES_HOME_DIR="${HERMES_HOME:-$HOME/.hermes}"
HERMES_CFG="${HERMES_HOME_DIR}/config.yaml"
MCP_PORT=40404
MCP_ENDPOINT="http://localhost:${MCP_PORT}/mcp"

choose_hermes_python() {
    local script_dir repo_root candidate hermes_bin
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    repo_root="$(cd "${script_dir}/../../../.." && pwd)"
    hermes_bin="$(command -v hermes 2>/dev/null || true)"

    for candidate in \
        "${HERMES_PYTHON:-}" \
        "${repo_root}/.venv/bin/python" \
        "${repo_root}/venv/bin/python" \
        "${hermes_bin:+$(dirname "$hermes_bin")/python3}" \
        "$(command -v python3 2>/dev/null || true)"
    do
        if [[ -n "$candidate" && -x "$candidate" ]] \
          && "$candidate" -c 'import agent.provider_credentials' >/dev/null 2>&1
        then
            printf '%s\n' "$candidate"
            return
        fi
    done

    echo "A Hermes Python environment with agent.provider_credentials is required." >&2
    return 1
}

manual_steps=()

echo -e "\n${CYAN}═══ twozero MCP for TouchDesigner — Setup ═══${NC}\n"

# ── 1. Check if TouchDesigner is running ──
# Match on process *name* (not full cmdline) to avoid self-matching shells
# that happen to have "TouchDesigner" in their args. macOS and Linux pgrep
# both support -x for exact name match.
if pgrep -x TouchDesigner >/dev/null 2>&1 || pgrep -x TouchDesignerFTE >/dev/null 2>&1; then
    echo -e " ${OK} TouchDesigner is running"
    td_running=true
else
    echo -e " ${WARN} TouchDesigner is not running"
    td_running=false
fi

# ── 2. Ensure twozero.tox exists ──
if [[ -f "$TOX_PATH" ]]; then
    echo -e " ${OK} twozero.tox already exists at ${TOX_PATH}"
else
    echo -e " ${WARN} twozero.tox not found — downloading..."
    if curl -fSL -o "$TOX_PATH" "$TWOZERO_URL" 2>/dev/null; then
        echo -e " ${OK} Downloaded twozero.tox to ${TOX_PATH}"
    else
        echo -e " ${FAIL} Failed to download twozero.tox from ${TWOZERO_URL}"
        echo "       Please download manually and place at ${TOX_PATH}"
        manual_steps+=("Download twozero.tox from ${TWOZERO_URL} to ${TOX_PATH}")
    fi
fi

# ── 3. Ensure Hermes config has twozero_td MCP entry ──
if [[ ! -f "$HERMES_CFG" ]]; then
    echo -e " ${FAIL} Hermes config not found at ${HERMES_CFG}"
    manual_steps+=("Create ${HERMES_CFG} with twozero_td MCP server entry")
elif hermes_python="$(choose_hermes_python)" && update_status=$(
    "$hermes_python" - "$HERMES_CFG" "$MCP_ENDPOINT" 2>/dev/null <<'PY'
import sys
from pathlib import Path

from agent.provider_credentials import mutate_config_strict


config_path = Path(sys.argv[1])
endpoint = sys.argv[2]
state = {"added": False}


def update(config):
    servers = config.get("mcp_servers")
    if servers is None:
        servers = {}
        config["mcp_servers"] = servers
    elif not isinstance(servers, dict):
        raise ValueError("mcp_servers config must be a mapping")

    existing = servers.get("twozero_td")
    if existing is None:
        servers["twozero_td"] = {
            "url": endpoint,
            "timeout": 120,
            "connect_timeout": 60,
        }
        state["added"] = True
    elif not isinstance(existing, dict):
        raise ValueError("twozero_td config must be a mapping")


mutate_config_strict(update, config_path=config_path)
print("added" if state["added"] else "existing")
PY
); then
    if [[ "$update_status" == "added" ]]; then
        echo -e " ${OK} twozero_td MCP entry added to config"
        manual_steps+=("Restart Hermes session to pick up config change")
    else
        echo -e " ${OK} twozero_td MCP entry exists in Hermes config"
    fi
else
    echo -e " ${FAIL} Could not update config through the canonical Hermes writer"
    manual_steps+=("Add twozero_td MCP entry to ${HERMES_CFG} manually")
fi

# ── 4. Test if MCP port is responding ──
if nc -z 127.0.0.1 "$MCP_PORT" 2>/dev/null; then
    echo -e " ${OK} Port ${MCP_PORT} is open"

    # ── 5. Verify MCP endpoint responds ──
    resp=$(curl -s --max-time 3 "$MCP_ENDPOINT" 2>/dev/null || true)
    if [[ -n "$resp" ]]; then
        echo -e " ${OK} MCP endpoint responded at ${MCP_ENDPOINT}"
    else
        echo -e " ${WARN} Port open but MCP endpoint returned empty response"
        manual_steps+=("Verify MCP is enabled in twozero settings")
    fi
else
    echo -e " ${WARN} Port ${MCP_PORT} is not open"
    if [[ "$td_running" == true ]]; then
        manual_steps+=("In TD: drag twozero.tox into network editor → click Install")
        manual_steps+=("Enable MCP: twozero icon → Settings → mcp → 'auto start MCP' → Yes")
    else
        manual_steps+=("Launch TouchDesigner")
        manual_steps+=("Drag twozero.tox into the TD network editor and click Install")
        manual_steps+=("Enable MCP: twozero icon → Settings → mcp → 'auto start MCP' → Yes")
    fi
fi

# ── Status Report ──
echo -e "\n${CYAN}═══ Status Report ═══${NC}\n"

if [[ ${#manual_steps[@]} -eq 0 ]]; then
    echo -e " ${OK} ${GREEN}Fully configured! twozero MCP is ready to use.${NC}\n"
    exit 0
else
    echo -e " ${WARN} ${YELLOW}Manual steps remaining:${NC}\n"
    for i in "${!manual_steps[@]}"; do
        echo -e "   $((i+1)). ${manual_steps[$i]}"
    done
    echo ""
    exit 1
fi
