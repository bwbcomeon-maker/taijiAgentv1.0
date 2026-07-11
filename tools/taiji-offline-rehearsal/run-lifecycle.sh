#!/usr/bin/env bash
set -Eeuo pipefail

READ_ONLY_DELIVERY="/delivery-ro"
EVIDENCE_DIR="/evidence"
WORK_ROOT="/work"
WORK_DELIVERY="$WORK_ROOT/delivery"
REHEARSAL_USER="rehearsal"
REHEARSAL_HOME="/home/$REHEARSAL_USER"
SESSION_BASENAME="offline-install-rehearsal-session.json"

fail() {
  printf 'offline-rehearsal-lifecycle-failed\t%s\n' "$*" >&2
  exit 1
}

require_env() {
  local name="$1"
  [ -n "${!name:-}" ] || fail "缺少环境变量：$name"
}

verify_runtime_baseline() {
  local runtime_id runtime_version
  [ -r /etc/os-release ] || fail "容器缺少可读的 /etc/os-release"
  # shellcheck disable=SC1091
  source /etc/os-release
  runtime_id="${ID:-}"
  runtime_version="${VERSION_ID:-}"
  [ "$runtime_id" = "ubuntu" ] \
    || fail "离线演练容器系统不是 ubuntu：${runtime_id:-missing}"
  [ "$runtime_version" = "20.04" ] \
    || fail "离线演练容器版本不是 20.04：${runtime_version:-missing}"
}

verify_runtime_network_none() {
  local active_links global_addresses non_loopback_routes
  active_links="$(
    ip -o link show up \
      | awk -F ': ' '$2 !~ /^lo(@|$)/ { print $2 }' \
      | LC_ALL=C sort \
      | tr '\n' ' '
  )"
  [ -z "$active_links" ] \
    || fail "--network none 容器仍存在启用的非 loopback 链路：$active_links"

  global_addresses="$(ip -o addr show scope global | tr '\n' ' ')"
  [ -z "$global_addresses" ] \
    || fail "--network none 容器仍存在全局 IP 地址：$global_addresses"

  non_loopback_routes="$(
    ip -o route show table all \
      | awk '$0 !~ / dev lo( |$)/ { print }' \
      | tr '\n' ' '
  )"
  [ -z "$non_loopback_routes" ] \
    || fail "--network none 容器仍存在非 loopback route：$non_loopback_routes"
}

ensure_local_hostname_resolution() {
  local current_hostname
  current_hostname="$(hostname)"
  [[ "$current_hostname" =~ ^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$ ]] \
    || fail "容器 hostname 格式不合法"

  if ! awk -v expected="$current_hostname" '
    {
      for (field = 2; field <= NF; field += 1) {
        if ($field == expected) {
          found = 1
        }
      }
    }
    END { exit(found ? 0 : 1) }
  ' /etc/hosts; then
    printf '127.0.1.1\t%s\n' "$current_hostname" >> /etc/hosts \
      || fail "无法为容器 hostname 写入本地解析"
  fi

  getent hosts "$current_hostname" >/dev/null 2>&1 \
    || fail "容器 hostname 无法在本地解析"
}

verify_installed() {
  local status
  status="$(dpkg-query -W -f='${Status}' taiji-agent 2>/dev/null || true)"
  [ "$status" = "install ok installed" ] || fail "taiji-agent 未处于 installed 状态：${status:-missing}"
  [ -x /opt/taiji-agent/bin/taiji-native-verify ] || fail "安装态 native verifier 不存在"
  sudo -H -u "$REHEARSAL_USER" env \
    HOME="$REHEARSAL_HOME" \
    TAIJI_AGENT_USE_USER_DIRS=1 \
    /opt/taiji-agent/bin/taiji-native-verify
  sudo -H -u "$REHEARSAL_USER" env HOME="$REHEARSAL_HOME" taiji --help >/dev/null
}

verify_purged() {
  if dpkg-query -W -f='${Status}' taiji-agent >/dev/null 2>&1; then
    fail "purge 后仍存在 taiji-agent dpkg 状态"
  fi
  [ ! -e /opt/taiji-agent ] || fail "purge 后仍存在 /opt/taiji-agent"
  [ ! -e /usr/bin/taiji ] || fail "purge 后仍存在 /usr/bin/taiji"
  [ ! -e /usr/bin/taiji-agent ] || fail "purge 后仍存在 /usr/bin/taiji-agent"
  [ ! -e /usr/share/applications/taiji-agent.desktop ] || fail "purge 后仍存在桌面入口"
}

[ "$EUID" -eq 0 ] || fail "生命周期入口必须以容器 root 启动，再局部降权运行目标安装脚本"
[ -d "$READ_ONLY_DELIVERY" ] || fail "缺少只读交付目录挂载：$READ_ONLY_DELIVERY"
[ -d "$EVIDENCE_DIR" ] || fail "缺少证据输出目录挂载：$EVIDENCE_DIR"
if touch "$READ_ONLY_DELIVERY/.taiji-rehearsal-write-probe" >/dev/null 2>&1; then
  rm -f "$READ_ONLY_DELIVERY/.taiji-rehearsal-write-probe"
  fail "交付目录挂载不是只读"
fi

require_env TAIJI_OFFLINE_REHEARSAL_CHALLENGE
require_env TAIJI_EXPECTED_SOURCE_COMMIT
require_env TAIJI_EXPECTED_DEB_BASENAME
require_env TAIJI_EXPECTED_DEB_SHA256
[[ "$TAIJI_OFFLINE_REHEARSAL_CHALLENGE" =~ ^[0-9a-f]{64,128}$ ]] || fail "challenge 格式不合法"
[[ "$TAIJI_EXPECTED_SOURCE_COMMIT" =~ ^[0-9a-f]{7,40}$ ]] || fail "source commit 格式不合法"
[[ "$TAIJI_EXPECTED_DEB_BASENAME" =~ ^taiji-agent_[A-Za-z0-9.+:~_-]+_amd64\.deb$ ]] || fail "DEB basename 不合法"
[[ "$TAIJI_EXPECTED_DEB_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "DEB SHA256 格式不合法"

[ "$(dpkg --print-architecture)" = "amd64" ] || fail "容器 dpkg architecture 不是 amd64"
case "$(uname -m)" in
  x86_64|amd64) ;;
  *) fail "容器 kernel architecture 不是 x86_64/amd64：$(uname -m)" ;;
esac
verify_runtime_baseline
verify_runtime_network_none
ensure_local_hostname_resolution

for secret_name in \
  OPENAI_API_KEY ANTHROPIC_API_KEY GOOGLE_API_KEY GEMINI_API_KEY \
  DEEPSEEK_API_KEY OPENROUTER_API_KEY TAIJI_LICENSE_SOURCE \
  TAIJI_RELEASE_PRIVATE_KEY; do
  [ -z "${!secret_name:-}" ] || fail "容器不允许注入密钥变量：$secret_name"
done

if dpkg-query -W -f='${Status}' taiji-agent >/dev/null 2>&1; then
  fail "基线镜像已预装 taiji-agent，不是干净演练环境"
fi

rm -rf "$WORK_DELIVERY"
install -d -m 0755 "$WORK_ROOT" "$WORK_DELIVERY"
cp -a -- "$READ_ONLY_DELIVERY/." "$WORK_DELIVERY/"
chown -R "$REHEARSAL_USER:$REHEARSAL_USER" "$WORK_DELIVERY"

installer="$WORK_DELIVERY/02_目标终端_安装并验证.sh"
[ -f "$installer" ] || fail "交付目录缺少 02_目标终端_安装并验证.sh"
deb_path="$WORK_DELIVERY/生成的安装包/$TAIJI_EXPECTED_DEB_BASENAME"
[ -f "$deb_path" ] || fail "交付目录缺少预期 DEB：$TAIJI_EXPECTED_DEB_BASENAME"
actual_deb_sha="$(sha256sum -- "$deb_path" | awk '{print $1}')"
[ "$actual_deb_sha" = "$TAIJI_EXPECTED_DEB_SHA256" ] || fail "容器内 DEB SHA256 与宿主预检值不一致"

sudo -H -u "$REHEARSAL_USER" env \
  HOME="$REHEARSAL_HOME" \
  ONLINE_OK=0 \
  TAIJI_ALLOW_HEADLESS_REHEARSAL=1 \
  bash "$installer"
verify_installed

env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get purge -y taiji-agent
verify_purged

sudo -H -u "$REHEARSAL_USER" env \
  HOME="$REHEARSAL_HOME" \
  ONLINE_OK=0 \
  TAIJI_ALLOW_HEADLESS_REHEARSAL=1 \
  bash "$installer"
verify_installed

generated_at_utc="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
rehearsal_session_id="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
export generated_at_utc rehearsal_session_id
export TAIJI_REHEARSAL_OS_ID="ubuntu"
export TAIJI_REHEARSAL_OS_VERSION="20.04"

python3 - "$EVIDENCE_DIR/$SESSION_BASENAME" <<'PY'
import json
import os
import pathlib
import tempfile


target = pathlib.Path(os.sys.argv[1])
payload = {
    "schema": "taiji.offline-install-rehearsal.v1",
    "generated_at_utc": os.environ["generated_at_utc"],
    "rehearsal_session_id": os.environ["rehearsal_session_id"],
    "challenge_nonce": os.environ["TAIJI_OFFLINE_REHEARSAL_CHALLENGE"],
    "source_commit": os.environ["TAIJI_EXPECTED_SOURCE_COMMIT"],
    "deb_basename": os.environ["TAIJI_EXPECTED_DEB_BASENAME"],
    "deb_sha256": os.environ["TAIJI_EXPECTED_DEB_SHA256"],
    "platform": "linux/amd64",
    "environment": "container",
    "os_id": os.environ["TAIJI_REHEARSAL_OS_ID"],
    "os_version": os.environ["TAIJI_REHEARSAL_OS_VERSION"],
    "network": "none",
    "checks": {"install": True, "uninstall": True, "reinstall": True},
    "desktop_app_verified": False,
    "target_verified": False,
}
descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary_name, 0o644)
    os.replace(temporary_name, target)
finally:
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
PY

printf 'offline-rehearsal-lifecycle-complete\t%s\n' "$EVIDENCE_DIR/$SESSION_BASENAME"
