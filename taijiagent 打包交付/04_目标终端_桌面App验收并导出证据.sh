#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
TOOLS_DIR="$SCRIPT_DIR/验收工具"
OUTPUT_DIR="$SCRIPT_DIR/生成的安装包"
MANIFEST="$OUTPUT_DIR/taiji-package-manifest.json"
BUILD_MARKER="$OUTPUT_DIR/.build-success"
DRIVER="$TOOLS_DIR/run-installed-electron-acceptance.js"
ASSEMBLER="$TOOLS_DIR/assemble-target-evidence.py"
INSTALL_OBSERVER="$TOOLS_DIR/observe-single-deb-install.py"
MATRIX="$TOOLS_DIR/certification-matrix.json"
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
SINGLE_DEB_CUSTOMER_DIR="${TAIJI_SINGLE_DEB_CUSTOMER_DIR:-}"
INSTALL_OBSERVATION="${TAIJI_SINGLE_DEB_INSTALL_OBSERVATION:-}"
INSTALL_METHOD_ATTESTATION="${TAIJI_SINGLE_DEB_METHOD_ATTESTATION:-}"
GRAPHICAL_INSTALLER_EVIDENCE="${TAIJI_SINGLE_DEB_GRAPHICAL_INSTALLER_EVIDENCE:-}"
CERTIFICATION_CATEGORY_ID="${TAIJI_CERTIFICATION_CATEGORY_ID:-}"
ENVIRONMENT_RECORD="${TAIJI_LINUX_ENVIRONMENT_RECORD:-}"
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

validate_manifest_schema_v3() {
  [ -f "$MANIFEST" ] && [ ! -L "$MANIFEST" ] \
    || fail "当前目标验收缺少实体发布 manifest：$MANIFEST"
  [ -x /usr/bin/python3 ] \
    || fail "目标系统缺少 /usr/bin/python3，无法执行 v3 manifest 前置门禁"
  /usr/bin/python3 -B - "$MANIFEST" <<'PY' \
    || fail "当前目标验收只接受 taiji-package-manifest/v3"
import json
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
if type(data) is not dict or data.get("schema") != "taiji-package-manifest/v3":
    raise SystemExit("target acceptance requires taiji-package-manifest/v3")
PY
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
  [ -x /usr/bin/python3 ] || fail "目标系统缺少 /usr/bin/python3，无法验证安装前持续观察记录"
  require_regular_file "$MATRIX" "国产 Linux 认证类别矩阵"
  [ -n "$CERTIFICATION_CATEGORY_ID" ] || fail "请设置 TAIJI_CERTIFICATION_CATEGORY_ID，选择一个认证矩阵类别"
  printf '%s\n' "$CERTIFICATION_CATEGORY_ID" | grep -Eq '^[a-z0-9][a-z0-9-]{2,63}$' \
    || fail "TAIJI_CERTIFICATION_CATEGORY_ID 格式不合法"
  case "$SINGLE_DEB_CUSTOMER_DIR" in
    /*) ;;
    *) fail "TAIJI_SINGLE_DEB_CUSTOMER_DIR 必须是单一 DEB 客户目录的绝对路径" ;;
  esac
  [ -d "$SINGLE_DEB_CUSTOMER_DIR" ] && [ ! -L "$SINGLE_DEB_CUSTOMER_DIR" ] \
    || fail "单一 DEB 客户目录必须是实体目录：$SINGLE_DEB_CUSTOMER_DIR"
  case "$INSTALL_OBSERVATION" in
    /*) ;;
    *) fail "TAIJI_SINGLE_DEB_INSTALL_OBSERVATION 必须是绝对路径" ;;
  esac
  case "$INSTALL_METHOD_ATTESTATION" in
    /*) ;;
    *) fail "TAIJI_SINGLE_DEB_METHOD_ATTESTATION 必须是绝对路径" ;;
  esac
  case "$GRAPHICAL_INSTALLER_EVIDENCE" in
    /*) ;;
    *) fail "TAIJI_SINGLE_DEB_GRAPHICAL_INSTALLER_EVIDENCE 必须是绝对路径" ;;
  esac
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
  require_regular_file "$INSTALL_OBSERVER" "单 DEB 安装前观察器"
  require_regular_file "$VALIDATOR" "发布证据校验器"
  require_regular_file "$PUBLIC_KEY" "发布证据验签公钥"
  require_regular_file "$MANIFEST" "发布 manifest"
  require_regular_file "$BUILD_MARKER" "构建成功标记"
  require_regular_file "$INSTALL_OBSERVATION" "单 DEB 安装持续观察记录"
  if [ -z "$ENVIRONMENT_RECORD" ]; then
    ENVIRONMENT_RECORD="$(dirname "$INSTALL_OBSERVATION")/environment-evidence.json"
  fi
  case "$ENVIRONMENT_RECORD" in
    /*) ;;
    *) fail "TAIJI_LINUX_ENVIRONMENT_RECORD 必须是绝对路径" ;;
  esac
  require_regular_file "$ENVIRONMENT_RECORD" "单环境认证记录"
  require_regular_file "$INSTALL_METHOD_ATTESTATION" "桌面双击安装人工见证"
  require_regular_file "$GRAPHICAL_INSTALLER_EVIDENCE" "系统图形安装器证据截图"
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
if data.get("schema") != "taiji-package-manifest/v3":
    raise SystemExit("target acceptance requires taiji-package-manifest/v3")
fields = ("source_commit", "version", "deb_sha256", "electron_executable_sha256", "desktop_entry_sha256")
fields = fields + ("deb_basename", "compatibility_policy_id", "compatibility_policy_sha256")
for key in fields:
    value = data.get(key)
    if type(value) is not str or not value or any(character in value for character in "\r\n\t"):
        raise SystemExit(f"invalid manifest field: {key}")
if not re.fullmatch(r"[0-9a-f]{40}", data["source_commit"]):
    raise SystemExit("invalid source_commit")
if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", data["version"]):
    raise SystemExit("invalid version")
deb_name = data["deb_basename"]
if deb_name != f'taiji-agent_{data["version"]}_amd64.deb':
    raise SystemExit("manifest deb/version mismatch")
for key in ("deb_sha256", "electron_executable_sha256", "desktop_entry_sha256"):
    if not re.fullmatch(r"[0-9a-f]{64}", data[key]):
        raise SystemExit(f"invalid manifest hash: {key}")
for key in ("source_commit", "version"):
    print(data[key])
print(deb_name)
for key in ("deb_sha256", "electron_executable_sha256", "desktop_entry_sha256"):
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

validate_single_deb_install_facts() {
  local entry_count customer_count customer_sha256
  entry_count="$(find "$SINGLE_DEB_CUSTOMER_DIR" -mindepth 1 -maxdepth 1 -print | wc -l | tr -d ' ')"
  [ "$entry_count" = "1" ] \
    || fail "单一 DEB 客户目录必须且只能有一个安装文件，当前：$entry_count"
  customer_count="$(find "$SINGLE_DEB_CUSTOMER_DIR" -mindepth 1 -maxdepth 1 -type f -name '*.deb' | wc -l | tr -d ' ')"
  [ "$customer_count" = "1" ] \
    || fail "单一 DEB 客户目录的唯一条目必须是 DEB 普通文件"
  CUSTOMER_DEB="$(find "$SINGLE_DEB_CUSTOMER_DIR" -mindepth 1 -maxdepth 1 -type f -name '*.deb' -print)"
  require_regular_file "$CUSTOMER_DEB" "客户单一 DEB"
  [ "$(basename "$CUSTOMER_DEB")" = "$DEB_BASENAME" ] \
    || fail "客户单一 DEB 文件名必须与 manifest 完全一致：expected=$DEB_BASENAME"
  customer_sha256="$(sha256sum "$CUSTOMER_DEB" | awk '{print $1}')"
  [ "$customer_sha256" = "$EXPECTED_DEB_SHA256" ] \
    || fail "客户单一 DEB 与当前发布 DEB 不是同一制品"
}

validate_install_observation() {
  info "验证安装前启动、同机同启动周期、持续断网和 absent→installed 机器观察记录"
  /usr/bin/python3 -B "$INSTALL_OBSERVER" verify \
    --observation "$INSTALL_OBSERVATION" \
    --manifest "$MANIFEST" \
    --deb "$CUSTOMER_DEB" \
    --attestation "$INSTALL_METHOD_ATTESTATION" \
    --graphical-evidence "$GRAPHICAL_INSTALLER_EVIDENCE" \
    --challenge "$CHALLENGE" \
    --matrix "$MATRIX" \
    --category-id "$CERTIFICATION_CATEGORY_ID" \
    --environment-record "$ENVIRONMENT_RECORD" \
    || fail "单 DEB 安装观察记录或桌面双击人工见证无效"
}

validate_certification_category() {
  "$PYTHON_BIN" - "$MATRIX" "$CERTIFICATION_CATEGORY_ID" "$OS_ID" "$DESKTOP_ENVIRONMENT" <<'PY' || fail "认证矩阵类别与目标 OS/桌面不匹配"
import json
import sys
from pathlib import Path

matrix = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
category_id, os_id, desktop = sys.argv[2:]
items = matrix.get("positive_categories", []) + matrix.get("negative_boundaries", [])
category = next((item for item in items if item.get("id") == category_id), None)
if category is None:
    raise SystemExit("unknown certification category")
if category.get("kind") != "positive":
    raise SystemExit("target desktop acceptance only emits positive environment records")
if os_id not in category.get("os_ids", []):
    raise SystemExit("OS does not match category")
if not any(token.lower() in desktop.lower() for token in category.get("desktop_environments", [])):
    raise SystemExit("desktop does not match category")
PY
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
    --matrix "$MATRIX" \
    --category-id "$CERTIFICATION_CATEGORY_ID" \
    --timeout-ms "$TIMEOUT_MS"

  require_regular_file "$driver_output/driver-result.json" "桌面 App 验收驱动结果"
  require_regular_file "$driver_output/desktop-app.png" "桌面 App 验收截图"
  require_regular_file "$driver_output/taiji-support-bundle.json" "桌面 App 诊断导出"

  # Electron 验收可能运行数分钟；在组装器重新打开证据前再做一次当前
  # 机器、启动会话、DEB 和人工见证绑定校验，关闭长流程的替换窗口。
  validate_install_observation

  info "组装 challenge 绑定的目标终端证据"
  "$PYTHON_BIN" "$ASSEMBLER" \
    --driver-result "$driver_output/driver-result.json" \
    --screenshot "$driver_output/desktop-app.png" \
    --diagnostic "$driver_output/taiji-support-bundle.json" \
    --manifest "$MANIFEST" \
    --deb "$DEB" \
    --electron-executable "$ELECTRON_BIN" \
    --desktop-entry "$DESKTOP_ENTRY" \
    --install-observation "$INSTALL_OBSERVATION" \
    --install-method-attestation "$INSTALL_METHOD_ATTESTATION" \
    --graphical-installer-evidence "$GRAPHICAL_INSTALLER_EVIDENCE" \
    --release-artifacts-sha256 "$RELEASE_ARTIFACTS_SHA256" \
    --installed-package-version "$INSTALLED_PACKAGE_VERSION" \
    --challenge "$CHALLENGE" \
    --os-id "$OS_ID" \
    --os-version "$OS_VERSION" \
    --desktop-environment "$DESKTOP_ENVIRONMENT" \
    --matrix "$MATRIX" \
    --category-id "$CERTIFICATION_CATEGORY_ID" \
    --environment-record "$ENVIRONMENT_RECORD" \
    --output-dir "$TARGET_DIR"
  OUTPUT_CREATED=1

  info "对未签名目标证据执行完整发布绑定校验"
  "$PYTHON_BIN" - "$TARGET_DIR/target-verification.json" "$CERTIFICATION_CATEGORY_ID" <<'PY' || fail "canonical target evidence envelope is invalid"
import json
import sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if data.get("schema") != "taiji-linux-target-verification/v1":
    raise SystemExit("wrong canonical target evidence schema")
if data.get("category_id") != sys.argv[2]:
    raise SystemExit("canonical category binding mismatch")
if "CERTIFIED" in json.dumps(data, ensure_ascii=False):
    raise SystemExit("single target evidence must not self-claim CERTIFIED")
PY
}

main() {
  validate_manifest_schema_v3
  validate_platform
  validate_inputs
  read_os_identity
  validate_certification_category
  read_release_identity
  validate_single_deb_install_facts
  validate_install_observation
  compute_release_inventory
  run_desktop_acceptance
  SUCCESS=1
  ok "真实 Electron 桌面 App 目标终端验收证据已生成：$TARGET_DIR"
  printf '\n下一步：将整个 target-verification 目录复制回发布主机，使用离线发布私钥签名；目标终端不存放私钥。\n'
}

main "$@"
