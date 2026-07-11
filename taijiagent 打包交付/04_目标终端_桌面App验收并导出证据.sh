#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
TOOLS_DIR="$SCRIPT_DIR/验收工具"
OUTPUT_DIR="$SCRIPT_DIR/生成的安装包"
OFFLINE_REPO="$SCRIPT_DIR/离线依赖"
MANIFEST="$OUTPUT_DIR/taiji-package-manifest.json"
BUILD_MARKER="$OUTPUT_DIR/.build-success"
DRIVER="$TOOLS_DIR/run-installed-electron-acceptance.js"
ASSEMBLER="$TOOLS_DIR/assemble-target-evidence.py"
VALIDATOR="$TOOLS_DIR/validate-taiji-release-evidence.py"
PUBLIC_KEY="$TOOLS_DIR/signing-public.pem"
PUBLIC_KEY_FINGERPRINT="839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"
NODE_BIN="/opt/taiji-agent/runtime/node/bin/node"
PYTHON_BIN="/opt/taiji-agent/runtime/agent/venv/bin/python"
ELECTRON_BIN="/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron"
DESKTOP_ENTRY="/usr/share/applications/taiji-agent.desktop"
TARGET_DIR="${TAIJI_TARGET_VERIFICATION_DIR:-$SCRIPT_DIR/target-verification}"
CHALLENGE="${TAIJI_TARGET_ACCEPTANCE_CHALLENGE:-}"
TIMEOUT_MS="${TAIJI_TARGET_ACCEPTANCE_TIMEOUT_MS:-900000}"
WORK_ROOT=""
OUTPUT_CREATED=0
SUCCESS=0

export PYTHONDONTWRITEBYTECODE=1

ok() { printf '[OK] %s\n' "$*"; }
info() { printf '[INFO] %s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
require_cmd() { have "$1" || fail "缺少命令：$1"; }

cleanup() {
  local status="$?"
  trap - EXIT
  if [ -n "$WORK_ROOT" ] && [ -d "$WORK_ROOT" ] && [ ! -L "$WORK_ROOT" ]; then
    rm -rf -- "$WORK_ROOT" || true
  fi
  if [ "$SUCCESS" != "1" ] && [ "$OUTPUT_CREATED" = "1" ]; then
    rm -rf -- "$TARGET_DIR" || true
  fi
  exit "$status"
}
trap cleanup EXIT

require_regular_file() {
  local path="$1" label="$2" executable="${3:-0}" links
  [ -f "$path" ] && [ ! -L "$path" ] || fail "$label 必须是实体普通文件：$path"
  links="$(stat -c '%h' "$path")" || fail "无法读取 $label 链接数：$path"
  [ "$links" = "1" ] || fail "$label 不能是硬链接：$path"
  if [ "$executable" = "1" ]; then
    [ -x "$path" ] || fail "$label 不可执行：$path"
  fi
}

validate_platform() {
  [ "$(uname -s)" = "Linux" ] || fail "桌面 App 目标验收仅允许 Linux x86_64 目标终端"
  case "$(uname -m)" in
    x86_64|amd64) ;;
    *) fail "桌面 App 目标验收仅允许 x86_64/amd64，当前：$(uname -m)" ;;
  esac
  if [ "$EUID" -eq 0 ]; then
    fail "请在已登录图形桌面的普通用户终端执行，不要使用 sudo bash"
  fi
  if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    fail "未检测到 DISPLAY 或 WAYLAND_DISPLAY，不能进行真实 Electron 桌面 App 验收"
  fi
}

validate_inputs() {
  require_cmd stat
  require_cmd sha256sum
  require_cmd dpkg-query
  require_cmd dpkg-deb
  require_cmd mktemp
  require_cmd env
  printf '%s\n' "$CHALLENGE" | grep -Eq '^[0-9a-f]{64,128}$' \
    || fail "请设置 64-128 位小写十六进制 TAIJI_TARGET_ACCEPTANCE_CHALLENGE"
  printf '%s\n' "$TIMEOUT_MS" | grep -Eq '^[0-9]+$' \
    || fail "TAIJI_TARGET_ACCEPTANCE_TIMEOUT_MS 必须是整数"
  case "$TARGET_DIR" in
    /*) ;;
    *) fail "TAIJI_TARGET_VERIFICATION_DIR 必须是绝对路径：$TARGET_DIR" ;;
  esac
  if [ -e "$TARGET_DIR" ] || [ -L "$TARGET_DIR" ]; then
    fail "证据输出目录已存在，拒绝覆盖：$TARGET_DIR"
  fi
  [ -d "$(dirname "$TARGET_DIR")" ] && [ ! -L "$(dirname "$TARGET_DIR")" ] \
    || fail "证据输出父目录必须是已存在的实体目录：$(dirname "$TARGET_DIR")"
  require_regular_file "$NODE_BIN" "安装态 Node" 1
  require_regular_file "$PYTHON_BIN" "安装态 Python" 1
  require_regular_file "$ELECTRON_BIN" "安装态 Electron" 1
  require_regular_file "$DESKTOP_ENTRY" "安装态 desktop entry"
  require_regular_file "$DRIVER" "桌面 App 验收驱动"
  require_regular_file "$ASSEMBLER" "目标证据组装器"
  require_regular_file "$VALIDATOR" "发布证据校验器"
  require_regular_file "$PUBLIC_KEY" "发布证据验签公钥"
  require_regular_file "$MANIFEST" "发布 manifest"
  require_regular_file "$BUILD_MARKER" "构建成功标记"
  require_regular_file "$OFFLINE_REPO/Packages" "离线仓库 Packages"
  require_regular_file "$OFFLINE_REPO/Packages.gz" "离线仓库 Packages.gz"
}

read_os_identity() {
  local values
  values="$($PYTHON_BIN - /etc/os-release <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
fields = {}
for raw in path.read_text(encoding="utf-8").splitlines():
    if not raw or raw.lstrip().startswith("#") or "=" not in raw:
        continue
    key, value = raw.split("=", 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    fields[key] = value
os_id = fields.get("ID", "").strip().lower()
version = (fields.get("VERSION_ID") or fields.get("VERSION") or "").strip()
if not re.fullmatch(r"[a-z0-9._-]{2,32}", os_id):
    raise SystemExit("invalid os id")
if not version or len(version) > 128 or any(character in version for character in "\r\n\t"):
    raise SystemExit("invalid os version")
print(os_id)
print(version)
PY
)" || fail "无法安全读取 /etc/os-release"
  OS_ID="$(printf '%s\n' "$values" | sed -n '1p')"
  OS_VERSION="$(printf '%s\n' "$values" | sed -n '2p')"
  case "$OS_ID" in
    kylin|uos|openkylin) ;;
    *) fail "目标桌面验收只接受 Kylin/UOS/openKylin，当前 ID=$OS_ID" ;;
  esac
  DESKTOP_ENVIRONMENT="${XDG_CURRENT_DESKTOP:-${DESKTOP_SESSION:-}}"
  [ -n "$DESKTOP_ENVIRONMENT" ] || fail "无法识别当前桌面环境"
  case "$DESKTOP_ENVIRONMENT" in
    *$'\n'*|*$'\r'*|*$'\t'*) fail "桌面环境标识包含非法换行或制表符" ;;
  esac
  [ "${#DESKTOP_ENVIRONMENT}" -le 128 ] || fail "桌面环境标识过长"
}

read_release_identity() {
  local values package_status package_version deb_count
  values="$($PYTHON_BIN - "$MANIFEST" <<'PY'
import json
import re
import sys
from pathlib import Path

def strict(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate manifest key: {key}")
        result[key] = value
    return result

data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"), object_pairs_hook=strict)
fields = ("source_commit", "version", "deb", "deb_sha256", "electron_executable_sha256", "desktop_entry_sha256")
for key in fields:
    value = data.get(key)
    if type(value) is not str or not value or any(character in value for character in "\r\n\t"):
        raise SystemExit(f"invalid manifest field: {key}")
if not re.fullmatch(r"[0-9a-f]{7,40}", data["source_commit"]):
    raise SystemExit("invalid source_commit")
if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", data["version"]):
    raise SystemExit("invalid version")
if data["deb"] != f'taiji-agent_{data["version"]}_amd64.deb':
    raise SystemExit("manifest deb/version mismatch")
for key in ("deb_sha256", "electron_executable_sha256", "desktop_entry_sha256"):
    if not re.fullmatch(r"[0-9a-f]{64}", data[key]):
        raise SystemExit(f"invalid manifest hash: {key}")
for key in fields:
    print(data[key])
PY
)" || fail "发布 manifest 字段不合法"
  SOURCE_COMMIT="$(printf '%s\n' "$values" | sed -n '1p')"
  VERSION="$(printf '%s\n' "$values" | sed -n '2p')"
  DEB_BASENAME="$(printf '%s\n' "$values" | sed -n '3p')"
  EXPECTED_DEB_SHA256="$(printf '%s\n' "$values" | sed -n '4p')"
  EXPECTED_ELECTRON_SHA256="$(printf '%s\n' "$values" | sed -n '5p')"
  EXPECTED_DESKTOP_SHA256="$(printf '%s\n' "$values" | sed -n '6p')"
  DEB="$OUTPUT_DIR/$DEB_BASENAME"
  CHECKSUM="${DEB}.sha256"
  SOURCE_ARCHIVE="$SCRIPT_DIR/taiji-agentv1.0-kylin-build-src-$SOURCE_COMMIT.tar.gz"
  require_regular_file "$DEB" "当前 DEB"
  require_regular_file "$CHECKSUM" "DEB SHA256 sidecar"
  require_regular_file "$SOURCE_ARCHIVE" "当前源码包"
  deb_count="$(find "$OUTPUT_DIR" -maxdepth 1 -type f -name 'taiji-agent_*_amd64.deb' | wc -l | tr -d ' ')"
  [ "$deb_count" = "1" ] || fail "生成的安装包/必须且只能有一个 amd64 DEB，当前：$deb_count"
  [ "$(sha256sum "$DEB" | awk '{print $1}')" = "$EXPECTED_DEB_SHA256" ] \
    || fail "当前 DEB 摘要与 manifest 不一致"
  [ "$(sha256sum "$ELECTRON_BIN" | awk '{print $1}')" = "$EXPECTED_ELECTRON_SHA256" ] \
    || fail "安装态 Electron 摘要与 manifest 不一致"
  [ "$(sha256sum "$DESKTOP_ENTRY" | awk '{print $1}')" = "$EXPECTED_DESKTOP_SHA256" ] \
    || fail "安装态 desktop entry 摘要与 manifest 不一致"
  package_status="$(dpkg-query -W -f='${Status}' taiji-agent 2>/dev/null || true)"
  package_version="$(dpkg-query -W -f='${Version}' taiji-agent 2>/dev/null || true)"
  [ "$package_status" = "install ok installed" ] || fail "taiji-agent 未处于已安装状态"
  [ "$package_version" = "$VERSION" ] || fail "安装态版本与 manifest 不一致：installed=$package_version manifest=$VERSION"
  [ "$(dpkg-deb -f "$DEB" Version)" = "$VERSION" ] || fail "DEB 控制字段 Version 与 manifest 不一致"
  INSTALLED_PACKAGE_VERSION="$package_version"
}

compute_private_machine_fingerprint() {
  local machine_id_file machine_id
  machine_id_file="/etc/machine-id"
  [ -r "$machine_id_file" ] || machine_id_file="/var/lib/dbus/machine-id"
  [ -r "$machine_id_file" ] || fail "无法读取系统机器标识用于隐私化摘要"
  machine_id="$(tr -d '[:space:]' < "$machine_id_file")"
  printf '%s\n' "$machine_id" | grep -Eq '^[0-9A-Fa-f-]{16,128}$' \
    || fail "系统机器标识格式异常"
  machine_fingerprint_sha256="$(printf '%s\0%s\0%s\0' "$CHALLENGE" "$OS_ID" "$machine_id" | sha256sum | awk '{print $1}')"
  unset machine_id
}

compute_release_inventory() {
  RELEASE_ARTIFACTS_SHA256="$($PYTHON_BIN - "$VALIDATOR" "$SCRIPT_DIR" <<'PY'
import importlib.util
import sys
from pathlib import Path

validator_path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("taiji_release_evidence_validator", validator_path)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load validator")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(module.delivery_inventory_sha256(Path(sys.argv[2])))
PY
)" || fail "交付目录清单无法通过同捆 validator 计算"
  printf '%s\n' "$RELEASE_ARTIFACTS_SHA256" | grep -Eq '^[0-9a-f]{64}$' \
    || fail "交付目录清单摘要不合法"
}

run_desktop_acceptance() {
  local session_id driver_output
  session_id="$($PYTHON_BIN -c 'import secrets; print(secrets.token_hex(16))')"
  printf '%s\n' "$session_id" | grep -Eq '^[0-9a-f]{32}$' || fail "无法生成验收会话 ID"
  WORK_ROOT="$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/taiji-target-acceptance.XXXXXX")"
  [ -d "$WORK_ROOT" ] && [ ! -L "$WORK_ROOT" ] || fail "无法创建安全验收临时目录"
  chmod 0700 "$WORK_ROOT"
  driver_output="$WORK_ROOT/driver"

  info "执行安装态桌面原生校验与 Electron smoke"
  env \
    -u TAIJI_AGENT_AGENT_DIR \
    -u TAIJI_AGENT_WEBUI_DIR \
    -u TAIJI_AGENT_PYTHON \
    -u TAIJI_WEBUI_PYTHON \
    -u TAIJI_WEBUI_AGENT_DIR \
    -u TAIJI_AGENT_RUNTIME_ENV \
    -u TAIJI_WEBUI_CHAT_BACKEND \
    -u TAIJI_RUNTIME_HOME \
    -u TAIJI_WORKSPACE \
    -u TAIJI_AGENT_CONFIG_DIR \
    -u TAIJI_AGENT_DATA_DIR \
    -u TAIJI_AGENT_STATE_DIR \
    -u TAIJI_AGENT_LOG_DIR \
    -u TAIJI_AGENT_TMP_DIR \
    -u TAIJI_DESKTOP_USER_DATA_DIR \
    -u TAIJI_STATE_DIR \
    -u HERMES_HOME \
    -u HERMES_CONFIG_PATH \
    -u HERMES_CONFIG \
    -u HERMES_ENV \
    -u HERMES_WEBUI_AGENT_DIR \
    -u HERMES_WEBUI_PYTHON \
    -u PYTHONPATH \
    -u PYTHONHOME \
    -u ELECTRON_RUN_AS_NODE \
    -u NODE_OPTIONS \
    TAIJI_AGENT_ROOT="/opt/taiji-agent" \
    TAIJI_AGENT_USE_USER_DIRS="1" \
    TAIJI_VERIFY_DESKTOP_SMOKE="1" \
    /opt/taiji-agent/bin/taiji-native-verify

  info "启动真实 Electron App，验收附件、真实模型回复、诊断导出与关窗退出"
  "$NODE_BIN" "$DRIVER" \
    --electron "$ELECTRON_BIN" \
    --app-dir "/opt/taiji-agent/apps/taiji-desktop" \
    --output-dir "$driver_output" \
    --session-id "$session_id" \
    --challenge "$CHALLENGE" \
    --timeout-ms "$TIMEOUT_MS"

  require_regular_file "$driver_output/driver-result.json" "桌面 App 验收驱动结果"
  require_regular_file "$driver_output/desktop-app.png" "桌面 App 验收截图"
  require_regular_file "$driver_output/taiji-support-bundle.json" "桌面 App 诊断导出"

  info "组装 challenge 绑定的目标终端证据"
  "$PYTHON_BIN" "$ASSEMBLER" \
    --driver-result "$driver_output/driver-result.json" \
    --screenshot "$driver_output/desktop-app.png" \
    --diagnostic "$driver_output/taiji-support-bundle.json" \
    --manifest "$MANIFEST" \
    --deb "$DEB" \
    --electron-executable "$ELECTRON_BIN" \
    --desktop-entry "$DESKTOP_ENTRY" \
    --release-artifacts-sha256 "$RELEASE_ARTIFACTS_SHA256" \
    --machine-fingerprint-sha256 "$machine_fingerprint_sha256" \
    --installed-package-version "$INSTALLED_PACKAGE_VERSION" \
    --challenge "$CHALLENGE" \
    --os-id "$OS_ID" \
    --os-version "$OS_VERSION" \
    --desktop-environment "$DESKTOP_ENVIRONMENT" \
    --output-dir "$TARGET_DIR"
  OUTPUT_CREATED=1

  info "对未签名目标证据执行完整发布绑定校验"
  "$PYTHON_BIN" "$VALIDATOR" target \
    --evidence "$TARGET_DIR/target-verification.json" \
    --source-commit "$SOURCE_COMMIT" \
    --deb "$DEB" \
    --checksum "$CHECKSUM" \
    --manifest "$MANIFEST" \
    --build-marker "$BUILD_MARKER" \
    --source-archive "$SOURCE_ARCHIVE" \
    --packages "$OFFLINE_REPO/Packages" \
    --packages-gz "$OFFLINE_REPO/Packages.gz" \
    --delivery-dir "$SCRIPT_DIR" \
    --attestation-public-key "$PUBLIC_KEY" \
    --attestation-public-key-fingerprint "$PUBLIC_KEY_FINGERPRINT" \
    --challenge "$CHALLENGE" \
    --pre-sign
}

main() {
  validate_platform
  validate_inputs
  read_os_identity
  read_release_identity
  compute_private_machine_fingerprint
  compute_release_inventory
  run_desktop_acceptance
  SUCCESS=1
  ok "真实 Electron 桌面 App 目标终端验收证据已生成：$TARGET_DIR"
  printf '\n下一步：将整个 target-verification 目录复制回发布主机，使用离线发布私钥签名；目标终端不存放私钥。\n'
}

main "$@"
