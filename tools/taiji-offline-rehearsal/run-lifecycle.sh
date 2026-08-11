#!/usr/bin/env bash
set -Eeuo pipefail

READ_ONLY_DELIVERY="/delivery-ro"
EVIDENCE_DIR="/evidence"
WORK_ROOT="/work"
WORK_DELIVERY="$WORK_ROOT/delivery"
REHEARSAL_USER="rehearsal"
REHEARSAL_HOME="/home/$REHEARSAL_USER"
SESSION_BASENAME="offline-install-rehearsal-session.json"
LIFECYCLE_BASENAME="offline-install-rehearsal-lifecycle.json"
EXPECTED_REHEARSAL_FIXTURE_ID="kylin-os-release-v1"
REHEARSAL_ENVIRONMENT="container-kylin-policy-fixture-v1"

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
  [ "$runtime_id" = "ubuntu" ] || fail "离线演练容器系统不是 ubuntu：${runtime_id:-missing}"
  [ "$runtime_version" = "20.04" ] || fail "离线演练容器版本不是 20.04：${runtime_version:-missing}"
}

verify_runtime_network_none() {
  local active_links global_addresses non_loopback_routes
  active_links="$(
    ip -o link show up \
      | awk -F ': ' '$2 !~ /^lo(@|$)/ { print $2 }' \
      | LC_ALL=C sort \
      | tr '\n' ' '
  )"
  [ -z "$active_links" ] || fail "--network none 容器仍存在启用的非 loopback 链路：$active_links"

  global_addresses="$(ip -o addr show scope global | tr '\n' ' ')"
  [ -z "$global_addresses" ] || fail "--network none 容器仍存在全局 IP 地址：$global_addresses"

  non_loopback_routes="$(
    ip -o route show table all \
      | awk '$0 !~ / dev lo( |$)/ { print }' \
      | tr '\n' ' '
  )"
  [ -z "$non_loopback_routes" ] || fail "--network none 容器仍存在非 loopback route：$non_loopback_routes"
}

ensure_local_hostname_resolution() {
  local current_hostname
  current_hostname="$(hostname)"
  [[ "$current_hostname" =~ ^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$ ]] || fail "容器 hostname 格式不合法"
  if ! awk -v expected="$current_hostname" '
    { for (field = 2; field <= NF; field += 1) if ($field == expected) found = 1 }
    END { exit(found ? 0 : 1) }
  ' /etc/hosts; then
    printf '127.0.1.1\t%s\n' "$current_hostname" >> /etc/hosts \
      || fail "无法为容器 hostname 写入本地解析"
  fi
  getent hosts "$current_hostname" >/dev/null 2>&1 || fail "容器 hostname 无法在本地解析"
}

activate_kylin_policy_fixture() {
  local fixture_target="/usr/lib/os-release"
  local fixture_tmp=""
  local fixture_metadata=""
  local fixture_id=""

  [ "${TAIJI_REHEARSAL_FIXTURE_ID:-}" = "$EXPECTED_REHEARSAL_FIXTURE_ID" ] \
    || fail "离线演练 policy fixture identity 不匹配"
  [ -f "$fixture_target" ] && [ ! -L "$fixture_target" ] \
    || fail "Ubuntu 基线缺少可信 /usr/lib/os-release"

  fixture_tmp="$(mktemp /usr/lib/.taiji-os-release.XXXXXX)" \
    || fail "无法创建 policy fixture os-release 临时文件"
  if ! {
    printf '%s\n' \
      'ID=kylin' \
      'NAME="Kylin policy fixture (not a target OS)"' \
      'VERSION_ID="V10-policy-fixture"' \
      'PRETTY_NAME="Kylin policy fixture on Ubuntu 20.04"' > "$fixture_tmp" \
      && chown 0:0 -- "$fixture_tmp" \
      && chmod 0644 -- "$fixture_tmp" \
      && mv -f -- "$fixture_tmp" "$fixture_target"; \
  }; then
    rm -f -- "$fixture_tmp" 2>/dev/null || true
    fail "无法激活 Kylin policy fixture os-release"
  fi

  rm -f -- /etc/os-release
  ln -s ../usr/lib/os-release /etc/os-release
  chown -h 0:0 -- /etc/os-release
  install -d -o 0 -g 0 -m 0755 /usr/share/xsessions

  fixture_metadata="$(stat -Lc '%u:%a:%h' -- "$fixture_target" 2>/dev/null || true)"
  [ "$fixture_metadata" = "0:644:1" ] \
    || fail "policy fixture os-release owner/mode/link-count 不可信：${fixture_metadata:-missing}"
  [ -L /etc/os-release ] \
    && [ "$(readlink -- /etc/os-release)" = "../usr/lib/os-release" ] \
    || fail "policy fixture /etc/os-release 不是 canonical symlink"
  fixture_id="$(awk -F= '$1 == "ID" {print $2; exit}' /etc/os-release)"
  [ "$fixture_id" = "kylin" ] || fail "policy fixture OS ID 激活失败"
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

record_step() {
  printf '%s\n' "$1" >> "$WORK_ROOT/steps.txt"
}

record_package_action() {
  local command_line="$1" package="$2"
  printf '%s\t%s\tnone\n' "$command_line" "$package" >> "$WORK_ROOT/package-actions.tsv"
}

record_receipt() {
  local operation="$1" result="$2" state="$3" transaction_id="$4"
  python3 - "$WORK_ROOT/receipts.jsonl" "$operation" "$result" "$state" "$transaction_id" <<'PY'
import json
import os
import pathlib
import sys

target, operation, result, state, transaction_id = sys.argv[1:]
payload = {
    "operation": operation,
    "result": result,
    "state": state,
    "transaction_id": transaction_id,
    "deb_sha256": os.environ["TAIJI_EXPECTED_DEB_SHA256"],
    "compatibility_policy_id": os.environ["TAIJI_COMPATIBILITY_POLICY_ID"],
    "compatibility_policy_sha256": os.environ["TAIJI_COMPATIBILITY_POLICY_SHA256"],
    "network": "none",
}
with pathlib.Path(target).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
PY
}

data_manifest() {
  python3 - "$REHEARSAL_HOME" <<'PY'
import hashlib
import json
import pathlib
import sys

home = pathlib.Path(sys.argv[1])
files = []
for root_name in (".config/taiji-agent", ".local/share/taiji-agent", ".local/state/taiji-agent"):
    root = home / root_name
    if not root.exists():
        continue
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        files.append({
            "relative": path.relative_to(home).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": path.stat().st_size,
        })
payload = {"schema": "taiji.offline-user-data-manifest/v1", "files": files}
canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
print(hashlib.sha256(canonical).hexdigest())
PY
}

seed_user_data() {
  sudo -H -u "$REHEARSAL_USER" env HOME="$REHEARSAL_HOME" python3 - <<'PY'
import pathlib
import sqlite3

home = pathlib.Path.home()
files = {
    ".config/taiji-agent/settings.json": '{"locale":"zh-CN","rehearsal":true}\n',
    ".config/taiji-agent/licenses/active-license.jwt": "offline-rehearsal-license\n",
    ".config/taiji-agent/license-device.json": '{"device_id":"offline-rehearsal-device"}\n',
    ".local/state/taiji-agent/license-state.json": '{"status":"active"}\n',
    ".local/state/taiji-agent/anti-rollback.json": '{"version":1}\n',
    ".local/share/taiji-agent/sessions/session-001.json": '{"id":"session-001"}\n',
    ".local/share/taiji-agent/attachments/attachment-001.txt": "attachment\n",
    ".local/share/taiji-agent/workspace/README.md": "offline workspace\n",
    ".local/share/taiji-agent/skills/rehearsal.skill": "skill\n",
    ".local/share/taiji-agent/docx-engine-v2/installed/manifest.json": '{"template":"fixture"}\n',
}
for relative, content in files.items():
    path = home / relative
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
database = home / ".local/state/taiji-agent/state.db"
database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
with sqlite3.connect(database) as connection:
    connection.execute("CREATE TABLE IF NOT EXISTS rehearsal_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute(
        "INSERT OR REPLACE INTO rehearsal_state(key, value) VALUES (?, ?)",
        ("seed", "n-minus-one"),
    )
    connection.commit()
PY
}

dpkg_install() {
  local package="$1"
  # All package actions use bytes already in the read-only delivery; no download/update path exists.
  dpkg --install --force-confold -- "$package"
  record_package_action "dpkg --install" "$package"
}

dpkg_remove() {
  dpkg --remove --force-depends taiji-agent
  record_package_action "dpkg --remove" "taiji-agent"
}

dpkg_purge() {
  dpkg --purge --force-depends taiji-agent
  record_package_action "dpkg --purge" "taiji-agent"
}

transaction_upgrade() {
  local candidate="$1" previous="$2" signature="$3" injected="$4"
  TAIJI_TX_CANDIDATE="$candidate" \
    TAIJI_TX_PREVIOUS="$previous" \
    TAIJI_TX_SIGNATURE="$signature" \
    TAIJI_TX_INJECTED="$injected" \
    TAIJI_TX_ROOT="$WORK_ROOT/transactions" \
    TAIJI_TX_USER="$REHEARSAL_USER" \
    python3 - "$WORK_DELIVERY/.rehearsal-inputs/upgrade_transaction.py" <<'PY'
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

helper_path = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("taiji_rehearsal_upgrade_transaction", helper_path)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load upgrade_transaction.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
account = module.resolve_account(os.environ["TAIJI_TX_USER"])
transaction = module.UpgradeTransaction.create(
    os.environ["TAIJI_TX_ROOT"], account=account, operation="upgrade"
)
candidate = pathlib.Path(os.environ["TAIJI_TX_CANDIDATE"])
previous = pathlib.Path(os.environ["TAIJI_TX_PREVIOUS"])
signature = pathlib.Path(os.environ["TAIJI_TX_SIGNATURE"])
injected = os.environ["TAIJI_TX_INJECTED"] == "1"
original = pathlib.Path("/var/lib/dpkg/info/taiji-agent.postinst")
diverted = pathlib.Path("/var/lib/dpkg/info/taiji-agent.postinst.taiji-rehearsal-original")

def dpkg_install(package: pathlib.Path) -> bool:
    completed = subprocess.run(
        ["dpkg", "--install", "--force-confold", "--", str(package)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    print(completed.stdout, end="", file=sys.stderr)
    with pathlib.Path("/work/package-actions.tsv").open("a", encoding="utf-8") as handle:
        handle.write(f"dpkg --install\t{package}\tnone\n")
    return completed.returncode == 0

def disable_injection() -> None:
    if injected:
        try:
            original.unlink()
        except FileNotFoundError:
            pass
        subprocess.run(
            ["dpkg-divert", "--remove", "--rename", "--divert", str(diverted), str(original)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False,
        )

def verify_package() -> bool:
    status = subprocess.run(
        ["dpkg-query", "-W", "-f=${Status}", "taiji-agent"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False,
    )
    if status.stdout.strip() != "install ok installed":
        return False
    verifier = pathlib.Path("/opt/taiji-agent/bin/taiji-native-verify")
    if not verifier.is_file() or not verifier.stat().st_mode & 0o111:
        return False
    native = subprocess.run(
        ["sudo", "-H", "-u", os.environ["TAIJI_TX_USER"], "env",
         f"HOME=/home/{os.environ['TAIJI_TX_USER']}",
         "TAIJI_AGENT_USE_USER_DIRS=1", str(verifier)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False,
    )
    if native.returncode != 0:
        return False
    cli = subprocess.run(
        ["sudo", "-H", "-u", os.environ["TAIJI_TX_USER"], "env",
         f"HOME=/home/{os.environ['TAIJI_TX_USER']}", "taiji", "--help"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False,
    )
    return cli.returncode == 0

result = transaction.run_upgrade(
    candidate_deb=candidate,
    previous_deb=previous,
    previous_sha256=os.environ["TAIJI_EXPECTED_PREVIOUS_DEB_SHA256"],
    previous_signature=signature,
    stop_fn=lambda: True,
    install_fn=dpkg_install,
    verify_fn=verify_package,
    rollback_install_fn=lambda package: (disable_injection() or dpkg_install(package)),
)
print(json.dumps(result, sort_keys=True, separators=(",", ":")))
PY
}

verify_journal_resume() {
  python3 - "$WORK_DELIVERY/.rehearsal-inputs/upgrade_transaction.py" "$WORK_ROOT/transactions" <<'PY'
import importlib.util
import json
import pathlib
import sys

helper_path = pathlib.Path(sys.argv[1])
root = pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("taiji_rehearsal_upgrade_transaction_resume", helper_path)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load transaction helper for journal resume")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
account = module.resolve_account("rehearsal")
for journal_path in sorted(root.glob("*/journal.json")):
    transaction = module.UpgradeTransaction.resume_for_account(journal_path, account)
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    state = transaction.state
    if state not in module.TERMINAL_STATES:
        raise SystemExit(f"partial journal was left unresolved: {journal_path}")
    history = journal.get("history")
    if not isinstance(history, list) or not history or history[-1].get("state") != state:
        raise SystemExit(f"journal history is not resumable: {journal_path}")
    if state == "committed" and any(entry.get("state") == "manual_recovery_required" for entry in history):
        raise SystemExit(f"partial journal was incorrectly treated as committed: {journal_path}")
print("journal-resume-check=passed")
PY
}

power_loss_resume_check() {
  TAIJI_POWER_LOSS_RESULT="$WORK_ROOT/power-loss-check" \
    python3 - "$WORK_DELIVERY/.rehearsal-inputs/upgrade_transaction.py" <<'PY'
import importlib.util
import os
import pathlib
import shutil
import sys

helper_path = pathlib.Path(sys.argv[1])
root = pathlib.Path(os.environ["TAIJI_POWER_LOSS_RESULT"])
if root.exists():
    shutil.rmtree(root)
spec = importlib.util.spec_from_file_location("taiji_rehearsal_power_loss", helper_path)
if spec is None or spec.loader is None:
    raise SystemExit("cannot load transaction helper for power-loss check")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
try:
    account = module.resolve_account("rehearsal")
    transaction = module.UpgradeTransaction.create(root, account=account, operation="fresh_install")
    transaction.transition("trusted_staging")
    journal = transaction.journal_path
    payload = journal.read_bytes()
    journal.write_bytes(payload[: max(1, len(payload) // 3)])
    try:
        module.UpgradeTransaction.resume_for_account(journal, account)
    except Exception as exc:
        print("manual_recovery_required")
    else:
        raise SystemExit("torn journal was incorrectly accepted as resumable")
finally:
    if root.exists():
        shutil.rmtree(root)
PY
}

expanded_lifecycle() {
  local candidate="$WORK_DELIVERY/生成的安装包/$TAIJI_EXPECTED_DEB_BASENAME"
  local previous="$WORK_DELIVERY/$TAIJI_PREVIOUS_DEB_RELATIVE"
  local previous_checksum="$previous.sha256"
  local previous_signature="$WORK_ROOT/previous.deb.sig"
  [ -f "$candidate" ] || fail "工作交付目录缺少 candidate DEB：$candidate"
  [ -f "$previous" ] || fail "工作交付目录缺少 previous DEB：$previous"
  local actual_candidate_sha actual_previous_sha actual_candidate_version actual_previous_version
  actual_candidate_sha="$(sha256sum -- "$candidate" | awk '{print $1}')"
  [ "$actual_candidate_sha" = "$TAIJI_EXPECTED_DEB_SHA256" ] || fail "candidate DEB 字节 SHA256 发生变化"
  actual_previous_sha="$(sha256sum -- "$previous" | awk '{print $1}')"
  [ "$actual_previous_sha" = "$TAIJI_EXPECTED_PREVIOUS_DEB_SHA256" ] || fail "previous DEB SHA256 不一致"
  actual_candidate_version="$(dpkg-deb -f "$candidate" Version)" \
    || fail "无法读取 candidate DEB 的 control Version"
  [ "$actual_candidate_version" = "$TAIJI_EXPECTED_CANDIDATE_VERSION" ] \
    || fail "candidate DEB control Version 与正式候选版本不一致"
  actual_previous_version="$(dpkg-deb -f "$previous" Version)" \
    || fail "无法读取 previous DEB 的 control Version"
  [ "$actual_previous_version" = "$TAIJI_EXPECTED_PREVIOUS_VERSION" ] \
    || fail "previous DEB control Version 与声明版本不一致"
  grep -Eq "${TAIJI_EXPECTED_PREVIOUS_DEB_SHA256}[[:space:]]+\*?${TAIJI_EXPECTED_PREVIOUS_DEB_BASENAME}" "$previous_checksum" \
    || fail "previous DEB SHA256 sidecar 未绑定"
  printf 'offline rehearsal previous signature\n' > "$previous_signature"
  chmod 0600 "$previous_signature"
  : > "$WORK_ROOT/steps.txt"
  : > "$WORK_ROOT/receipts.jsonl"
  : > "$WORK_ROOT/package-actions.tsv"

  record_step "fresh_install_n"
  dpkg_install "$candidate"
  verify_installed
  record_receipt "fresh_install" "installed" "committed" "fresh-n"

  record_step "same_version_reinstall_n"
  dpkg_install "$candidate"
  verify_installed
  record_receipt "reinstall" "reinstalled" "committed" "reinstall-n"

  record_step "seed_n_minus_one"
  dpkg_purge
  dpkg_install "$previous"
  seed_user_data
  local before_upgrade
  before_upgrade="$(data_manifest)"

  record_step "upgrade_n_minus_one_to_n"
  local success_result success_transaction success_state
  success_result="$(transaction_upgrade "$candidate" "$previous" "$previous_signature" 0)"
  success_transaction="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["transaction_id"])' "$success_result")"
  success_state="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["state"])' "$success_result")"
  [ "$success_state" = "committed" ] || fail "N-1→N transaction 未提交：$success_result"
  verify_installed
  record_receipt "upgrade" "upgraded" "$success_state" "$success_transaction"
  record_step "data_manifest_after_upgrade"
  local after_upgrade
  after_upgrade="$(data_manifest)"
  [ "$after_upgrade" = "$before_upgrade" ] || fail "升级后用户数据 manifest 不一致"

  # candidate DEB 字节必须保持同一 SHA256；dpkg-divert 只转移 maintainer script。
  record_step "inject_postinst_failure_same_candidate"
  dpkg_install "$previous"
  local postinst="/var/lib/dpkg/info/taiji-agent.postinst"
  local diverted="/var/lib/dpkg/info/taiji-agent.postinst.taiji-rehearsal-original"
  dpkg-divert --add --rename --divert "$diverted" "$postinst" >/dev/null
  printf '#!/bin/sh\nexit 73\n' > "$postinst"
  chmod 0755 "$postinst"
  local failed_result failed_transaction failed_state
  failed_result="$(transaction_upgrade "$candidate" "$previous" "$previous_signature" 1)"
  failed_transaction="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["transaction_id"])' "$failed_result")"
  failed_state="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["state"])' "$failed_result")"
  [ "$failed_state" = "rolled_back" ] || fail "postinst 失败未自动回滚：$failed_result"

  record_step "automatic_rollback_to_n_minus_one"
  local after_rollback
  after_rollback="$(data_manifest)"
  [ "$after_rollback" = "$before_upgrade" ] || fail "自动回滚后用户数据 manifest 不一致"
  record_receipt "rollback" "rolled_back" "$failed_state" "$failed_transaction"
  [ "$actual_candidate_sha" = "$(sha256sum -- "$candidate" | awk '{print $1}')" ] \
    || fail "注入失败后 candidate DEB 字节发生变化"

  record_step "upgrade_n_again"
  local second_result second_transaction second_state
  second_result="$(transaction_upgrade "$candidate" "$previous" "$previous_signature" 0)"
  second_transaction="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["transaction_id"])' "$second_result")"
  second_state="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["state"])' "$second_result")"
  [ "$second_state" = "committed" ] || fail "解除注入后再次升级未提交：$second_result"
  verify_installed
  record_receipt "upgrade_again" "upgraded" "$second_state" "$second_transaction"

  record_step "remove_preserves_user_data"
  dpkg_remove
  local after_remove
  after_remove="$(data_manifest)"
  [ "$after_remove" = "$before_upgrade" ] || fail "ordinary remove 未保留用户数据"

  record_step "purge_clears_root_state_only"
  dpkg_purge
  verify_purged
  local after_purge
  after_purge="$(data_manifest)"
  [ "$after_purge" = "$before_upgrade" ] || fail "purge 清理了用户数据"
  verify_journal_resume >/dev/null
  export TAIJI_POWER_LOSS_CHECK="$(power_loss_resume_check)"
  [ "$TAIJI_POWER_LOSS_CHECK" = "manual_recovery_required" ] || fail "power-loss partial journal 未进入 manual_recovery_required"

  export TAIJI_LIFECYCLE_STEPS="$(paste -sd, "$WORK_ROOT/steps.txt")"
  export TAIJI_DATA_BEFORE="$before_upgrade"
  export TAIJI_DATA_AFTER="$after_upgrade"
  export TAIJI_DATA_ROLLBACK="$after_rollback"
  export TAIJI_DATA_REMOVE="$after_remove"
  export TAIJI_DATA_PURGE="$after_purge"
  export TAIJI_SUCCESS_TRANSACTION="$success_transaction"
  export TAIJI_FAILED_TRANSACTION="$failed_transaction"
  export TAIJI_SECOND_TRANSACTION="$second_transaction"
}

write_evidence() {
  local generated_at_utc rehearsal_session_id
  generated_at_utc="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  rehearsal_session_id="$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')"
  export generated_at_utc rehearsal_session_id
  export TAIJI_REHEARSAL_OS_ID="ubuntu"
  export TAIJI_REHEARSAL_OS_VERSION="20.04"
  python3 - "$EVIDENCE_DIR/$SESSION_BASENAME" "$EVIDENCE_DIR/$LIFECYCLE_BASENAME" <<'PY'
import json
import os
import pathlib
import tempfile

session_path = pathlib.Path(os.sys.argv[1])
lifecycle_path = pathlib.Path(os.sys.argv[2])
steps = os.environ.get("TAIJI_LIFECYCLE_STEPS", "").split(",") if os.environ.get("TAIJI_LIFECYCLE_STEPS") else []
receipts = []
receipt_path = pathlib.Path("/work/receipts.jsonl")
if receipt_path.exists():
    receipts = [json.loads(line) for line in receipt_path.read_text(encoding="utf-8").splitlines() if line]
for receipt in receipts:
    receipt["deb_sha256"] = os.environ["TAIJI_EXPECTED_DEB_SHA256"]
    receipt["compatibility_policy_id"] = os.environ.get("TAIJI_COMPATIBILITY_POLICY_ID", "legacy-v2")
    receipt["compatibility_policy_sha256"] = os.environ.get("TAIJI_COMPATIBILITY_POLICY_SHA256", "")
session = {
    "schema": "taiji.offline-install-rehearsal.v1",
    "generated_at_utc": os.environ["generated_at_utc"],
    "rehearsal_session_id": os.environ["rehearsal_session_id"],
    "challenge_nonce": os.environ["TAIJI_OFFLINE_REHEARSAL_CHALLENGE"],
    "source_commit": os.environ["TAIJI_EXPECTED_SOURCE_COMMIT"],
    "deb_basename": os.environ["TAIJI_EXPECTED_DEB_BASENAME"],
    "deb_sha256": os.environ["TAIJI_EXPECTED_DEB_SHA256"],
    "platform": "linux/amd64",
    "environment": os.environ["TAIJI_REHEARSAL_ENVIRONMENT"],
    "os_id": os.environ["TAIJI_REHEARSAL_OS_ID"],
    "os_version": os.environ["TAIJI_REHEARSAL_OS_VERSION"],
    "network": "none",
    "checks": {"install": True, "uninstall": True, "reinstall": True},
    "desktop_app_verified": False,
    "target_verified": False,
}
lifecycle = dict(session)
lifecycle.update({
    "previous_deb_basename": os.environ.get("TAIJI_EXPECTED_PREVIOUS_DEB_BASENAME", ""),
    "previous_deb_sha256": os.environ.get("TAIJI_EXPECTED_PREVIOUS_DEB_SHA256", ""),
    "previous_version": os.environ.get("TAIJI_EXPECTED_PREVIOUS_VERSION", ""),
    "steps": steps,
    "receipts": receipts,
    "data_manifests": {
        "before_upgrade": os.environ.get("TAIJI_DATA_BEFORE", ""),
        "after_upgrade": os.environ.get("TAIJI_DATA_AFTER", ""),
        "after_rollback": os.environ.get("TAIJI_DATA_ROLLBACK", ""),
        "after_remove": os.environ.get("TAIJI_DATA_REMOVE", ""),
        "after_purge": os.environ.get("TAIJI_DATA_PURGE", ""),
    },
    "compatibility_policy_id": os.environ.get("TAIJI_COMPATIBILITY_POLICY_ID", "legacy-v2"),
    "compatibility_policy_sha256": os.environ.get("TAIJI_COMPATIBILITY_POLICY_SHA256", ""),
    "journal": {
        "upgrade_transaction_id": os.environ.get("TAIJI_SUCCESS_TRANSACTION", ""),
        "rollback_transaction_id": os.environ.get("TAIJI_FAILED_TRANSACTION", ""),
        "second_upgrade_transaction_id": os.environ.get("TAIJI_SECOND_TRANSACTION", ""),
        "resume": "partial journal is never committed; manual_recovery_required is explicit",
        "power_loss_resume_checked": True,
        "partial_journal_treated_as_committed": False,
        "partial_journal_result": os.environ.get("TAIJI_POWER_LOSS_CHECK", "not-run"),
        "manual_recovery_required": False,
    },
})
action_path = pathlib.Path("/work/package-actions.tsv")
lifecycle["package_actions"] = []
if action_path.exists():
    for line in action_path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        command, package, _network = line.split("\t", 2)
        lifecycle["package_actions"].append({
            "command": command,
            "package": package,
            "network": "none",
            "download": False,
        })

def write_atomic(target, payload):
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
write_atomic(session_path, session)
write_atomic(lifecycle_path, lifecycle)
PY
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
require_env TAIJI_REHEARSAL_FIXTURE_ID
[[ "$TAIJI_OFFLINE_REHEARSAL_CHALLENGE" =~ ^[0-9a-f]{64,128}$ ]] || fail "challenge 格式不合法"
[[ "$TAIJI_EXPECTED_SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail "source commit 格式不合法"
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
activate_kylin_policy_fixture
export TAIJI_REHEARSAL_ENVIRONMENT="$REHEARSAL_ENVIRONMENT"

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

if [ "${TAIJI_REHEARSAL_EXPANDED:-0}" = "1" ]; then
  require_env TAIJI_EXPECTED_CANDIDATE_VERSION
  require_env TAIJI_EXPECTED_PREVIOUS_DEB_BASENAME
  require_env TAIJI_EXPECTED_PREVIOUS_DEB_SHA256
  require_env TAIJI_EXPECTED_PREVIOUS_VERSION
  require_env TAIJI_PREVIOUS_DEB_RELATIVE
  require_env TAIJI_COMPATIBILITY_POLICY_ID
  require_env TAIJI_COMPATIBILITY_POLICY_SHA256
  require_env TAIJI_TRANSACTION_HELPER_RELATIVE
  require_env TAIJI_TRANSACTION_CONTRACT_RELATIVE
  [[ "$TAIJI_EXPECTED_PREVIOUS_DEB_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "previous DEB SHA256 格式不合法"
  [[ "$TAIJI_EXPECTED_DEB_BASENAME" == "taiji-agent_${TAIJI_EXPECTED_CANDIDATE_VERSION}_amd64.deb" ]] \
    || fail "candidate DEB basename 与 version 不一致"
  [[ "$TAIJI_EXPECTED_PREVIOUS_DEB_BASENAME" == "taiji-agent_${TAIJI_EXPECTED_PREVIOUS_VERSION}_amd64.deb" ]] \
    || fail "previous DEB basename 与 version 不一致"
  [ -f "$WORK_DELIVERY/$TAIJI_TRANSACTION_HELPER_RELATIVE" ] || fail "缺少 Task8 upgrade_transaction.py"
  [ -f "$WORK_DELIVERY/$TAIJI_TRANSACTION_CONTRACT_RELATIVE" ] || fail "缺少 Task8 upgrade-data-contract.json"
  expanded_lifecycle
else
  # Historical v2 compatibility markers: release-check callers still use this path.
  installer="$WORK_DELIVERY/02_目标终端_安装并验证.sh"
  [ -f "$installer" ] || fail "交付目录缺少 02_目标终端_安装并验证.sh"
  deb_path="$WORK_DELIVERY/生成的安装包/$TAIJI_EXPECTED_DEB_BASENAME"
  [ -f "$deb_path" ] || fail "交付目录缺少预期 DEB：$TAIJI_EXPECTED_DEB_BASENAME"
  actual_deb_sha="$(sha256sum -- "$deb_path" | awk '{print $1}')"
  [ "$actual_deb_sha" = "$TAIJI_EXPECTED_DEB_SHA256" ] || fail "容器内 DEB SHA256 与宿主预检值不一致"
  sudo -H -u "$REHEARSAL_USER" env \
    HOME="$REHEARSAL_HOME" \
    ONLINE_OK=0 \
    TAIJI_ADMISSION_MODE=certification \
    TAIJI_CERTIFICATION_CHALLENGE="$TAIJI_OFFLINE_REHEARSAL_CHALLENGE" \
    TAIJI_ALLOW_HEADLESS_REHEARSAL=1 \
    bash "$installer"
  verify_installed
  dpkg_purge
  verify_purged
  # apt-get purge -y taiji-agent is intentionally not used; local dpkg is mandatory.
  sudo -H -u "$REHEARSAL_USER" env \
    HOME="$REHEARSAL_HOME" \
    ONLINE_OK=0 \
    TAIJI_ADMISSION_MODE=certification \
    TAIJI_CERTIFICATION_CHALLENGE="$TAIJI_OFFLINE_REHEARSAL_CHALLENGE" \
    TAIJI_ALLOW_HEADLESS_REHEARSAL=1 \
    bash "$installer"
  verify_installed
fi

write_evidence
printf 'offline-rehearsal-lifecycle-complete\t%s\n' "$EVIDENCE_DIR/$SESSION_BASENAME"
