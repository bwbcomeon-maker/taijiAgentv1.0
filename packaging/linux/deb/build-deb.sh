#!/usr/bin/env bash
# Build the single policy-bound offline Taiji Agent amd64 DEB.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
LAB_DIR="$REPO_ROOT/hermes-local-lab"
SOURCE_AGENT_DIR="$LAB_DIR/sources/hermes-agent"
SOURCE_WEB_DIR="$LAB_DIR/sources/hermes-webui"
APP_DIR="$REPO_ROOT/apps/taiji-desktop"
ELECTRON_BIN="$APP_DIR/node_modules/electron/dist/electron"
DESKTOP_FILE="$REPO_ROOT/packaging/linux/taiji-agent.desktop"
APPSTREAM_FILE="$REPO_ROOT/packaging/linux/taiji-agent.metainfo.xml"
ICON_VALIDATOR="$REPO_ROOT/packaging/linux/validate_icon_assets.py"
DEFAULT_CONFIG="$LAB_DIR/config/taiji-default-config.yaml"
VERSION_FILE="$REPO_ROOT/VERSION"
POLICY_FILE="$REPO_ROOT/packaging/linux/compatibility-policy.json"
POLICY_HELPER="$REPO_ROOT/packaging/linux/compatibility_policy.py"
PAYLOAD_CONTRACT="$REPO_ROOT/packaging/linux/payload-contract.json"
PAYLOAD_VERIFIER="$REPO_ROOT/packaging/linux/verify-payload.py"
RUNTIME_STAGER="$REPO_ROOT/packaging/linux/stage-runtime-components.py"
PYTHON_RUNTIME_STAGER="$REPO_ROOT/packaging/linux/stage-python-runtime.py"
ELECTRON_RUNTIME_STAGER="$REPO_ROOT/packaging/linux/stage-electron-runtime.py"
DESKTOP_JS_STAGER="$REPO_ROOT/packaging/linux/stage-desktop-js-closure.js"
PRIVATE_LIB_STAGER="$REPO_ROOT/packaging/linux/stage-private-libraries.py"
ELF_AUDITOR="$REPO_ROOT/packaging/linux/audit-elf-closure.py"
PREINST_RENDERER="$SCRIPT_DIR/render-preinst.py"
TRUSTED_GIT="$REPO_ROOT/scripts/taiji-trusted-git"
PACKAGED_NODE_ROOT="${TAIJI_PACKAGED_NODE_ROOT:-}"
PRIVATE_LIBRARY_SYSROOT="${TAIJI_PRIVATE_LIBRARY_SYSROOT:-/usr/lib/x86_64-linux-gnu}"
SOURCE_COMMIT="${TAIJI_SOURCE_COMMIT:-}"
ELECTRON_ARCHIVE="${TAIJI_ELECTRON_ARCHIVE:-}"
PACKAGED_NODE_VERSION="22.23.1"
ISSUER_PUBLIC_KEY_FINGERPRINT="2dcff4f2b5e6f7a5e7e3f730e2f4446ad3265964431f614de7550265f7628b35"

[ -f "$VERSION_FILE" ] || { echo "Missing product VERSION: $VERSION_FILE" >&2; exit 1; }
VERSION="$(tr -d '\r\n' < "$VERSION_FILE")"
printf '%s\n' "$VERSION" | grep -Eq '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' || {
  echo "Invalid product VERSION: $VERSION" >&2
  exit 1
}
if [ -n "${TAIJI_AGENT_VERSION:-}" ] && [ "$TAIJI_AGENT_VERSION" != "$VERSION" ]; then
  echo "TAIJI_AGENT_VERSION must match root VERSION ($VERSION)" >&2
  exit 1
fi

ARCH="amd64"
BUILD_ROOT="$REPO_ROOT/runtime/package-build/deb"
PKG_ROOT="$BUILD_ROOT/root"
INSTALL_ROOT="$PKG_ROOT/opt/taiji-agent"
AGENT_RUNTIME="$INSTALL_ROOT/runtime/agent"
WEB_RUNTIME="$INSTALL_ROOT/runtime/web"
DESKTOP_RUNTIME="$INSTALL_ROOT/apps/taiji-desktop"
OUT_DIR="$REPO_ROOT/packages/麒麟操作系统安装包"
OUT_DEB="$OUT_DIR/taiji-agent_${VERSION}_${ARCH}.deb"
ARCHIVE_DIR="$OUT_DIR/旧版本归档"
POLICY_INSTALL_PATH="$INSTALL_ROOT/resources/linux-compatibility-policy.json"
LAUNCH_MANIFEST_PATH="$INSTALL_ROOT/resources/taiji-release-manifest.json"
ABI_REPORT_PATH="$INSTALL_ROOT/resources/elf-abi-audit.json"
ABI_BUILD_REPORT="$BUILD_ROOT/elf-abi-audit.json"
PRIVATE_STAGE_REPORT="$BUILD_ROOT/private-library-stage.json"
MANIFEST_PATH="$OUT_DIR/taiji-package-manifest.json"
ICON_SET_SHA256=""
POLICY_SHA256=""
POLICY_ID=""
TAIJI_PACKAGE_NAME=""
TAIJI_PACKAGE_ARCHITECTURE=""
TAIJI_PACKAGE_MAINTAINER=""
TAIJI_DEBIAN_DEPENDS=""
TAIJI_GLIBC_MIN=""

fail() { echo "$*" >&2; exit 1; }
warn() { echo "Warning: $*" >&2; }
require_cmd() { command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"; }

load_policy_contract() {
  [ -f "$POLICY_FILE" ] && [ ! -L "$POLICY_FILE" ] || fail "Missing compatibility policy: $POLICY_FILE"
  [ -f "$POLICY_HELPER" ] && [ ! -L "$POLICY_HELPER" ] || fail "Missing compatibility policy helper: $POLICY_HELPER"
  eval "$(python3 "$POLICY_HELPER" validate --policy "$POLICY_FILE" --print-shell)"
  POLICY_SHA256="$TAIJI_POLICY_SHA256"
  POLICY_ID="$TAIJI_POLICY_ID"
  TAIJI_PACKAGE_NAME="$TAIJI_PACKAGE_NAME"
  TAIJI_PACKAGE_ARCHITECTURE="$TAIJI_PACKAGE_ARCHITECTURE"
  TAIJI_PACKAGE_MAINTAINER="$TAIJI_PACKAGE_MAINTAINER"
  TAIJI_DEBIAN_DEPENDS="$TAIJI_DEBIAN_DEPENDS"
  TAIJI_GLIBC_MIN="$TAIJI_GLIBC_MIN"
  [ "$TAIJI_PACKAGE_NAME" = "taiji-agent" ] || fail "unexpected package name from policy"
  [ "$TAIJI_PACKAGE_ARCHITECTURE" = "amd64" ] || fail "unexpected package architecture from policy"
  [ "$POLICY_ID" = "taiji-linux-amd64-deb-v1" ] || fail "unexpected compatibility policy id"
}

resolve_source_commit() {
  local git_commit
  [ -x "$TRUSTED_GIT" ] && [ ! -L "$TRUSTED_GIT" ] || fail "Missing trusted Git boundary: $TRUSTED_GIT"
  git_commit="$("$TRUSTED_GIT" -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || true)"
  if [ -n "$git_commit" ]; then
    if [ -n "$SOURCE_COMMIT" ] && [ "$SOURCE_COMMIT" != "$git_commit" ]; then
      fail "TAIJI_SOURCE_COMMIT does not exactly match source Git HEAD"
    fi
    SOURCE_COMMIT="$git_commit"
  fi
  printf '%s\n' "$SOURCE_COMMIT" | grep -Eq '^[0-9a-f]{40}$' || fail "A full source commit is required"
}

validate_build_host_glibc() {
  local build_glibc
  build_glibc="$(ldd --version 2>&1 | awk 'NR == 1 { print }' | grep -Eo '[0-9]+(\.[0-9]+)+' | tail -n 1)"
  [ -n "$build_glibc" ] || fail "Cannot determine Linux build-host glibc version"
  dpkg --compare-versions "$build_glibc" le "$TAIJI_GLIBC_MIN" || fail "Build-host glibc $build_glibc exceeds policy $TAIJI_GLIBC_MIN"
}

validate_desktop_entry() {
  local desktop="$1"
  if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$desktop"
  else
    warn "desktop-file-validate not found; using structural checks"
    grep -qx 'Type=Application' "$desktop" || fail "Desktop entry missing Type=Application"
    grep -qx 'Name=太极 Agent' "$desktop" || fail "Desktop entry missing expected Name"
    grep -qx 'Exec=/usr/bin/taiji-agent' "$desktop" || fail "Desktop entry missing expected Exec"
    grep -qx 'Icon=taiji-agent' "$desktop" || fail "Desktop entry missing expected Icon"
    grep -qx 'Terminal=false' "$desktop" || fail "Desktop entry must not require a terminal"
    grep -qx 'StartupWMClass=taiji-agent' "$desktop" || fail "Desktop entry missing StartupWMClass"
    grep -qx 'X-GNOME-WMClass=taiji-agent' "$desktop" || fail "Desktop entry missing X-GNOME-WMClass"
  fi
}

validate_appstream_metadata() {
  [ -f "$APPSTREAM_FILE" ] && [ ! -L "$APPSTREAM_FILE" ] || fail "Missing AppStream metadata: $APPSTREAM_FILE"
  grep -q '<id>taiji-agent.desktop</id>' "$APPSTREAM_FILE" || fail "AppStream metadata has wrong desktop id"
  grep -q '<launchable type="desktop-id">taiji-agent.desktop</launchable>' "$APPSTREAM_FILE" || fail "AppStream metadata has no desktop launchable"
  grep -q '<icon type="cached" width="512" height="512">taiji-agent</icon>' "$APPSTREAM_FILE" || fail "AppStream metadata has no 512px cached icon"
}

verify_linux_electron_runtime() {
  [ -x "$ELECTRON_BIN" ] || fail "Missing Linux Electron runtime"
  local electron_file ldd_output
  electron_file="$(file "$ELECTRON_BIN")"
  case "$electron_file" in *ELF*64-bit*x86-64*|*ELF*64-bit*X86-64*|*ELF*64-bit*80386*) ;; *) fail "Electron runtime is not Linux x86_64: $electron_file" ;; esac
  if ! ldd_output="$(ldd "$ELECTRON_BIN" 2>&1)"; then
    fail "Cannot inspect Electron runtime shared-library output"
  fi
  if printf '%s\n' "$ldd_output" | grep -F 'not found' >/dev/null; then
    fail "Electron runtime has missing shared libraries"
  else
    [ "$?" -eq 1 ] || fail "Cannot inspect Electron runtime shared-library output"
  fi
}

validate_packaged_config_template() {
  [ -f "$DEFAULT_CONFIG" ] || fail "Missing packaged default config template: $DEFAULT_CONFIG"
  "$SOURCE_AGENT_PYTHON" - "$DEFAULT_CONFIG" <<'PY'
import sys
from pathlib import Path
import yaml
path = Path(sys.argv[1])
data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
markers = ("api_key", "apikey", "token", "secret", "password", "private_key", "wechat", "weixin", "corpsecret")
def scan(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            if any(marker in str(key).lower() for marker in markers):
                raise SystemExit(f"sensitive key in packaged default config: {prefix}{key}")
            scan(child, f"{prefix}{key}.")
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            scan(child, f"{prefix}{idx}.")
    elif isinstance(value, str) and "BEGIN " in value and "PRIVATE KEY" in value:
        raise SystemExit(f"private key shaped value in packaged default config: {prefix.rstrip('.')}")
scan(data)
for parent, key in (("model", "provider"), ("model", "default"), ("webui", "feature_visibility")):
    if not isinstance(data.get(parent), dict) or key not in data[parent]:
        raise SystemExit(f"missing {parent}.{key} in packaged default config")
PY
}

compile_sourceless_python() {
  local target="$1" python_bin="$2"
  find "$target" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  "$python_bin" -m compileall -q -b "$target"
  find "$target" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  find "$target" -type f -name '*.py' ! -path '*/venv/*' -delete
}

rename_internal_agent_modules() {
  [ -d "$AGENT_RUNTIME/hermes_cli" ] && mv "$AGENT_RUNTIME/hermes_cli" "$AGENT_RUNTIME/taiji_cli" || true
  local source target
  for source in "$AGENT_RUNTIME"/hermes_*.py; do
    [ -e "$source" ] || continue
    target="$AGENT_RUNTIME/taiji_$(basename "$source" | sed 's/^hermes_//')"
    mv "$source" "$target"
  done
  [ -f "$AGENT_RUNTIME/agent/transports/hermes_tools_mcp_server.py" ] && mv "$AGENT_RUNTIME/agent/transports/hermes_tools_mcp_server.py" "$AGENT_RUNTIME/agent/transports/taiji_tools_mcp_server.py" || true
  [ -f "$AGENT_RUNTIME/agent/transports/hermes_tools_profile_env.py" ] && mv "$AGENT_RUNTIME/agent/transports/hermes_tools_profile_env.py" "$AGENT_RUNTIME/agent/transports/taiji_tools_profile_env.py" || true
  rm -rf "$AGENT_RUNTIME/hermes" "$AGENT_RUNTIME/hermes-agent" "$AGENT_RUNTIME/hermes_agent.egg-info" "$AGENT_RUNTIME/.hermes" "$AGENT_RUNTIME/setup-hermes.sh" "$AGENT_RUNTIME/HERMES.md" "$AGENT_RUNTIME/hermes-already-has-routines.md"
}

rewrite_product_text_tokens() {
  local target="$1"
  find "$target" -type f ! -path '*/venv/*' \( -name '*.py' -o -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.json' -o -name '*.yaml' -o -name '*.yml' -o -name '*.toml' -o -name '*.txt' -o -name '*.md' \) -print0 | xargs -0 -r perl -pi -e 's/HERMES_/TAIJI_/g; s/HERMES/TAIJI/g; s/Hermes/Taiji/g; s/hermes/taiji/g'
}

write_packaged_webui_version() {
  local base digest
  base="${TAIJI_WEBUI_VERSION:-}"
  if [ -z "$base" ] && command -v git >/dev/null 2>&1; then
    base="$("$TRUSTED_GIT" -C "$SOURCE_WEB_DIR" describe --tags --always 2>/dev/null || true)"
  fi
  [ -n "$base" ] || base="taiji-webui"
  digest="$(find "$WEB_RUNTIME" -type f ! -name '_version.txt' ! -name '*.pyc' -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print substr($1,1,12)}')"
  mkdir -p "$WEB_RUNTIME/api"
  printf '%s-pkg.%s\n' "$base" "$digest" > "$WEB_RUNTIME/api/_version.txt"
}

write_installed_runtime_profile() {
  cat > "$AGENT_RUNTIME/taiji-runtime-profile.json" <<'PROFILE'
{
  "schema_version": "taiji-runtime-profile/v1",
  "profile": "installed-production"
}
PROFILE
  chmod 0644 "$AGENT_RUNTIME/taiji-runtime-profile.json"
}

stage_python_runtime() {
  mkdir -p "$AGENT_RUNTIME" "$WEB_RUNTIME"
  rsync -a --exclude '.git' --exclude '.github' --exclude '.DS_Store' --exclude '._*' --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' --exclude '.env.example' --exclude '.env.docker.example' --exclude '*.example' --exclude '.envrc' --exclude '.dockerignore' --exclude '.gitattributes' --exclude '.gitignore' --exclude '.hadolint.yaml' --exclude '.mailmap' --exclude 'license.jwt' --exclude '*.jwt' --exclude '.pytest_cache' --exclude '.playwright-mcp' --exclude 'docs' --exclude 'tests' --exclude 'website' --exclude 'articles' --exclude 'demos' --exclude 'datagen-config-examples' --exclude 'docker' --exclude 'nix' --exclude 'packaging' --exclude 'plugins/hermes-achievements' --exclude 'plugins/kanban/systemd' --exclude 'plugins/security-guidance' --exclude 'skills' --exclude 'scripts' --exclude 'optional-skills' --exclude 'optional-mcps' --exclude 'locales' --exclude 'ui-tui' --exclude 'web' --exclude 'venv' --exclude 'Dockerfile' --exclude 'docker-compose*' --exclude 'flake.*' --exclude 'MANIFEST.in' --exclude 'uv.lock' --exclude 'package*.json' --exclude 'pyproject.toml' --exclude 'LICENSE' --exclude '*.md' "$SOURCE_AGENT_DIR"/ "$AGENT_RUNTIME"/
  rename_internal_agent_modules
  rewrite_product_text_tokens "$AGENT_RUNTIME"
  python3 "$PYTHON_RUNTIME_STAGER" --source-venv "$SOURCE_AGENT_DIR/venv" --destination "$AGENT_RUNTIME/venv" --require-linux-x86-64 --smoke-import yaml --smoke-import fastapi --smoke-import uvicorn --smoke-import httpx --smoke-import pydantic
  assert_no_development_distributions
  rsync -a --exclude '.git' --exclude '.DS_Store' --exclude '._*' --exclude '__pycache__' --exclude '*.pyc' --exclude '.env' --exclude '.env.example' --exclude '.env.docker.example' --exclude '*.example' --exclude 'docs' --exclude 'reports' --exclude 'scripts' --exclude 'ctl.sh' --exclude 'start.sh' --exclude 'docker_init.bash' --exclude 'docker*' --exclude '*compose*' --exclude 'start.ps1' --exclude 'eslint*' --exclude 'tests' --exclude 'node_modules' --exclude 'Dockerfile' --exclude 'package*.json' --exclude 'pyproject.toml' --exclude 'LICENSE' --exclude '*.md' "$SOURCE_WEB_DIR"/ "$WEB_RUNTIME"/
  rewrite_product_text_tokens "$WEB_RUNTIME"
  write_packaged_webui_version
  write_installed_runtime_profile
  compile_sourceless_python "$AGENT_RUNTIME" "$SOURCE_AGENT_PYTHON"
  compile_sourceless_python "$WEB_RUNTIME" "$SOURCE_AGENT_PYTHON"
}

assert_no_development_distributions() {
  if ! python3 - "$AGENT_RUNTIME/venv" <<'PY'
import re
import sys
from email.parser import Parser
from pathlib import Path

runtime = Path(sys.argv[1])
site_packages_roots = sorted(runtime.glob("lib/python*/site-packages"))
if not site_packages_roots:
    raise SystemExit(f"staged Python site-packages is missing under {runtime}")

forbidden = {
    "debugpy",
    "pytest",
    "pytest-asyncio",
    "pytest-timeout",
    "ruff",
    "ty",
}
found = []
metadata_count = 0
for site_packages in site_packages_roots:
    for metadata_dir in sorted(site_packages.glob("*.dist-info")):
        if metadata_dir.is_symlink() or not metadata_dir.is_dir():
            raise SystemExit(f"invalid staged distribution metadata directory: {metadata_dir}")
        metadata_path = metadata_dir / "METADATA"
        if metadata_path.is_symlink() or not metadata_path.is_file():
            raise SystemExit(f"staged distribution metadata is missing: {metadata_path}")
        metadata_count += 1
        metadata = Parser().parsestr(metadata_path.read_text(encoding="utf-8"))
        name = metadata.get("Name")
        if not name:
            raise SystemExit(f"staged distribution has no Name metadata: {metadata_path}")
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        if normalized in forbidden:
            found.append(f"{name} ({metadata_dir.name})")

if metadata_count == 0:
    raise SystemExit(f"no staged Python distribution metadata found under {runtime}")
if found:
    raise SystemExit("forbidden development distributions in staged runtime: " + ", ".join(found))
PY
  then
    fail "Staged Python development-distribution gate failed"
  fi
}

scan_private_key_material() {
  local secret_path private_list private_path grep_status
  if secret_path="$(find "$INSTALL_ROOT" \( -name '.env' -o -name '*.jwt' -o -name 'id_rsa' -o -name 'id_ed25519' -o -name '*.key' \) -print -quit)"; then
    :
  else
    fail "Cannot scan package tree for secret-shaped files"
  fi
  [ -z "$secret_path" ] || fail "Package tree contains secret-shaped files: $secret_path"

  private_list="$(mktemp "${TMPDIR:-/tmp}/taiji-private-key-scan.XXXXXX")" \
    || fail "Cannot allocate private-key scan list"
  if ! find "$INSTALL_ROOT" -type f \( -name '*.pem' -o -name '*.crt' -o -name '*.cer' \) -print0 > "$private_list"; then
    rm -f -- "$private_list"
    fail "Cannot enumerate package public-key files"
  fi
  while IFS= read -r -d '' private_path; do
    if grep -Iq 'BEGIN .*PRIVATE KEY' -- "$private_path"; then
      rm -f -- "$private_list"
      fail "Package tree contains private key material: $private_path"
    else
      grep_status=$?
      if [ "$grep_status" -ne 1 ]; then
        rm -f -- "$private_list"
        fail "Cannot inspect package key file: $private_path"
      fi
    fi
  done < "$private_list"
  rm -f -- "$private_list"
}

scan_product_privacy() {
  local name_hit privacy_list privacy_path grep_status
  if name_hit="$(find "$INSTALL_ROOT" -path "$INSTALL_ROOT/licenses" -prune -o -path "$INSTALL_ROOT/licenses/*" -prune -o -path "$AGENT_RUNTIME/venv/lib*" -prune -o -iname '*hermes*' -print -quit)"; then
    :
  else
    fail "Cannot scan package tree for legacy product names"
  fi
  [ -z "$name_hit" ] || fail "Package tree contains legacy product names in visible paths: $name_hit"

  privacy_list="$(mktemp "${TMPDIR:-/tmp}/taiji-privacy-scan.XXXXXX")" \
    || fail "Cannot allocate privacy scan list"
  if ! find "$INSTALL_ROOT" -path "$INSTALL_ROOT/licenses" -prune -o -path "$INSTALL_ROOT/licenses/*" -prune -o -path "$AGENT_RUNTIME/venv/lib*" -prune -o -type f ! -name '*.pyc' ! -name '*.so' ! -name '*.png' ! -name '*.jpg' ! -name '*.jpeg' ! -name '*.gif' -print0 > "$privacy_list"; then
    rm -f -- "$privacy_list"
    fail "Cannot enumerate package text files for privacy scan"
  fi
  while IFS= read -r -d '' privacy_path; do
    if grep -I -n -E 'hermes|Hermes|HERMES_|hermes_cli|hermes-agent|hermes-webui|hermes-home' -- "$privacy_path" >/dev/null; then
      rm -f -- "$privacy_list"
      fail "Package tree contains legacy product names in text files: $privacy_path"
    else
      grep_status=$?
      if [ "$grep_status" -ne 1 ]; then
        rm -f -- "$privacy_list"
        fail "Cannot inspect package text file: $privacy_path"
      fi
    fi
  done < "$privacy_list"
  rm -f -- "$privacy_list"
}

scan_package_tree() {
  local forbidden_path
  if forbidden_path="$(find "$PKG_ROOT" \( -name '.DS_Store' -o -name '._*' -o -name '__pycache__' \) -print -quit)"; then
    :
  else
    fail "Cannot scan package tree for forbidden cache metadata"
  fi
  [ -z "$forbidden_path" ] || fail "Package tree contains forbidden cache metadata: $forbidden_path"
  scan_private_key_material
  scan_product_privacy
}

scan_webui_offline_assets() {
  local static_dir="$SOURCE_WEB_DIR/static" required missing="" cdn_hits
  [ -d "$static_dir" ] || fail "Missing WebUI static directory: $static_dir"
  if cdn_hits="$(grep -RInE 'cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com' "$static_dir" --include='*.html' --include='*.js' --include='*.css' --include='*.mjs')"; then
    [ -z "$cdn_hits" ] || fail "WebUI static assets still depend on CDN"
  else
    [ "$?" -eq 1 ] || fail "Cannot scan WebUI static assets for CDN references"
  fi
  for required in "vendor/xterm/5.3.0/xterm.css" "vendor/xterm/5.3.0/xterm.js" "vendor/xterm-addon-fit/0.8.0/xterm-addon-fit.js" "vendor/xterm-addon-web-links/0.9.0/xterm-addon-web-links.js" "vendor/prismjs/1.29.0/themes/prism-tomorrow.min.css" "vendor/prismjs/1.29.0/themes/prism.min.css" "vendor/prismjs/1.29.0/prism.min.js" "vendor/pdfjs-dist/4.9.155/pdf.min.mjs" "vendor/pdfjs-dist/4.9.155/pdf.worker.min.mjs" "vendor/mermaid/10.9.3/mermaid.min.js"; do
    [ -f "$static_dir/$required" ] || missing="$missing$static_dir/$required"$'\n'
  done
  [ -z "$missing" ] || { printf '%s' "$missing" >&2; fail "WebUI offline vendor assets are incomplete"; }
}

archive_old_packages() {
  mkdir -p "$ARCHIVE_DIR"
  find "$OUT_DIR" -maxdepth 1 -type f \( -name 'taiji-agent_*_amd64.deb' -o -name 'taiji-agent_*_amd64.deb.sha256' \) ! -name "$(basename "$OUT_DEB")" ! -name "$(basename "$OUT_DEB").sha256" -exec mv {} "$ARCHIVE_DIR"/ \;
}

scan_deb_release_artifact() {
  dpkg-deb -I "$OUT_DEB" >/dev/null
  dpkg-deb -c "$OUT_DEB" >/dev/null
  local marker strings_dump
  strings_dump="$(mktemp "${TMPDIR:-/tmp}/taiji-deb-strings.XXXXXX")" \
    || fail "Cannot allocate DEB metadata scan buffer"
  if ! strings "$OUT_DEB" > "$strings_dump"; then
    rm -f -- "$strings_dump"
    fail "Cannot inspect DEB archive metadata"
  fi
  for marker in LIBARCHIVE.xattr com.apple.provenance PaxHeaders SCHILY.xattr; do
    if grep -F "$marker" "$strings_dump" >/dev/null; then
      rm -f -- "$strings_dump"
      fail "DEB contains forbidden archive metadata marker: $marker"
    else
      [ "$?" -eq 1 ] || {
        rm -f -- "$strings_dump"
        fail "Cannot inspect DEB archive metadata marker: $marker"
      }
    fi
  done
  rm -f -- "$strings_dump"
}

audit_deb_payload() {
  local contents="$BUILD_ROOT/deb-contents.txt" audit_root="$BUILD_ROOT/deb-audit-root" control_root="$BUILD_ROOT/deb-audit-control" extracted_abi="$BUILD_ROOT/extracted-elf-abi-audit.json" extracted_icon_sha256 required missing=""
  dpkg-deb -c "$OUT_DEB" > "$contents"
  for required in "./opt/taiji-agent/runtime/agent/venv/bin/python" "./opt/taiji-agent/runtime/node/bin/node" "./opt/taiji-agent/runtime/lib" "./opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron" "./opt/taiji-agent/resources/payload-contract.json" "./opt/taiji-agent/resources/linux-compatibility-policy.json" "./opt/taiji-agent/resources/taiji-release-manifest.json" "./opt/taiji-agent/resources/elf-abi-audit.json" "./opt/taiji-agent/runtime/web/server.pyc" "./opt/taiji-agent/scripts/taiji-native-verify" "./opt/taiji-agent/scripts/support_bundle.py" "./opt/taiji-agent/apps/taiji-desktop/src/main.js" "./opt/taiji-agent/apps/taiji-desktop/src/preload.js" "./opt/taiji-agent/resources/icons/taiji-agent.png" "./usr/share/applications/taiji-agent.desktop" "./usr/share/metainfo/taiji-agent.metainfo.xml" "./usr/share/icons/hicolor/32x32/apps/taiji-agent.png" "./usr/share/icons/hicolor/48x48/apps/taiji-agent.png" "./usr/share/icons/hicolor/64x64/apps/taiji-agent.png" "./usr/share/icons/hicolor/128x128/apps/taiji-agent.png" "./usr/share/icons/hicolor/192x192/apps/taiji-agent.png" "./usr/share/icons/hicolor/256x256/apps/taiji-agent.png" "./usr/share/icons/hicolor/512x512/apps/taiji-agent.png" "./usr/bin/taiji" "./usr/bin/taiji-agent" "./usr/bin/taiji-agent-support"; do
    grep -F "$required" "$contents" >/dev/null || missing="$missing$required"$'\n'
  done
  [ -z "$missing" ] || { printf '%s' "$missing" >&2; fail "DEB payload is missing required runtime paths"; }
  rm -rf "$audit_root" "$control_root"
  mkdir -p "$audit_root" "$control_root"
  dpkg-deb -x "$OUT_DEB" "$audit_root"
  dpkg-deb -e "$OUT_DEB" "$control_root"
  cmp -s "$POLICY_FILE" "$audit_root/opt/taiji-agent/resources/linux-compatibility-policy.json" || fail "DEB policy is not byte-identical"
  cmp -s "$ABI_BUILD_REPORT" "$audit_root/opt/taiji-agent/resources/elf-abi-audit.json" || fail "DEB ABI report changed during packaging"
  grep -F "$POLICY_SHA256" "$control_root/preinst" >/dev/null || fail "DEB preinst policy hash mismatch"
  grep -F "$POLICY_ID" "$control_root/preinst" >/dev/null || fail "DEB preinst policy id mismatch"
  bash -n "$control_root/preinst"
  [ "$(dpkg-deb -f "$OUT_DEB" Package)" = "$TAIJI_PACKAGE_NAME" ] || fail "DEB package identity mismatch"
  [ "$(dpkg-deb -f "$OUT_DEB" Architecture)" = "$TAIJI_PACKAGE_ARCHITECTURE" ] || fail "DEB architecture mismatch"
  [ "$(dpkg-deb -f "$OUT_DEB" Maintainer)" = "$TAIJI_PACKAGE_MAINTAINER" ] || fail "DEB maintainer mismatch"
  [ "$(dpkg-deb -f "$OUT_DEB" Depends)" = "$TAIJI_DEBIAN_DEPENDS" ] || fail "DEB Depends mismatch"
  python3 "$PAYLOAD_VERIFIER" --root "$audit_root" >/dev/null
  extracted_icon_sha256="$(python3 "$ICON_VALIDATOR" \
    --web-static "$audit_root/opt/taiji-agent/runtime/web/static" \
    --install-icons "$audit_root/usr/share/icons/hicolor" \
    --resource-icon "$audit_root/opt/taiji-agent/resources/icons/taiji-agent.png" \
    --print-digest)"
  [ "$extracted_icon_sha256" = "$ICON_SET_SHA256" ] \
    || fail "Extracted DEB icon digest changed during packaging"
  python3 "$ELF_AUDITOR" --root "$audit_root" --policy "$POLICY_FILE" --output "$extracted_abi" >/dev/null
  cmp -s "$ABI_BUILD_REPORT" "$extracted_abi" || fail "ELF ABI audit changed after DEB extraction"
}

write_package_manifest() {
  local deb_sha256 electron_sha256 desktop_sha256 abi_sha256 upgrade_contract_sha256 icon_set_sha256 built_at_utc out_deb_name
  out_deb_name="$(basename "$OUT_DEB")"
  deb_sha256="$(sha256sum "$OUT_DEB" | awk '{print $1}')"
  electron_sha256="$(sha256sum "$DESKTOP_RUNTIME/node_modules/electron/dist/electron" | awk '{print $1}')"
  desktop_sha256="$(sha256sum "$DESKTOP_FILE" | awk '{print $1}')"
  abi_sha256="$(sha256sum "$ABI_REPORT_PATH" | awk '{print $1}')"
  upgrade_contract_sha256="$(sha256sum "$REPO_ROOT/packaging/linux/upgrade-data-contract.json" | awk '{print $1}')"
  icon_set_sha256="$ICON_SET_SHA256"
  printf '%s\n' "$icon_set_sha256" | grep -Eq '^[0-9a-f]{64}$' \
    || fail "Canonical icon digest is missing or invalid"
  built_at_utc="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  cat > "$MANIFEST_PATH" <<MANIFEST
{
  "schema": "taiji-package-manifest/v3",
  "package": "$TAIJI_PACKAGE_NAME",
  "version": "$VERSION",
  "architecture": "$TAIJI_PACKAGE_ARCHITECTURE",
  "source_commit": "$SOURCE_COMMIT",
  "deb_basename": "$(basename "$OUT_DEB")",
  "deb_sha256": "$deb_sha256",
  "maintainer": "$TAIJI_PACKAGE_MAINTAINER",
  "compatibility_policy_id": "$POLICY_ID",
  "compatibility_policy_sha256": "$POLICY_SHA256",
  "upgrade_data_contract_id": "taiji-linux-upgrade-data-v1",
  "upgrade_data_contract_sha256": "$upgrade_contract_sha256",
  "elf_abi_audit_basename": "elf-abi-audit.json",
  "elf_abi_audit_sha256": "$abi_sha256",
  "electron_executable_sha256": "$electron_sha256",
  "desktop_entry_sha256": "$desktop_sha256",
  "icon_set_sha256": "$icon_set_sha256",
  "built_at_utc": "$built_at_utc"
}
MANIFEST
  chmod 0644 "$MANIFEST_PATH"
  (cd "$OUT_DIR" && sha256sum "$out_deb_name" > "$out_deb_name.sha256")
}

write_launch_manifest() {
  local manifest_install_root="/opt/${INSTALL_ROOT##*/}"
  [ "$manifest_install_root" = "/opt/taiji-agent" ] || fail "Unexpected launch manifest install root: $manifest_install_root"
  cat > "$LAUNCH_MANIFEST_PATH" <<MANIFEST
{
  "schema": "taiji-release-manifest/v1",
  "platform": "linux",
  "arch": "$TAIJI_PACKAGE_ARCHITECTURE",
  "version": "$VERSION",
  "commit": "$SOURCE_COMMIT",
  "installRoot": "$manifest_install_root"
}
MANIFEST
  chmod 0644 "$LAUNCH_MANIFEST_PATH"
}

if [ "$(uname -s)" != "Linux" ]; then fail "Refusing to build final DEB on non-Linux host"; fi
case "$(uname -m)" in x86_64|amd64) ;; *) fail "Refusing to build on non-x86_64 host: $(uname -m)" ;; esac
for cmd in dpkg dpkg-deb rsync npm node sha256sum file ldd strings perl python3 openssl stat mktemp cmp readelf date; do require_cmd "$cmd"; done
load_policy_contract
resolve_source_commit
validate_build_host_glibc
[ -n "$PACKAGED_NODE_ROOT" ] || fail "TAIJI_PACKAGED_NODE_ROOT is required for the verified offline Node runtime"
[ -d "$PRIVATE_LIBRARY_SYSROOT" ] || fail "Private-library sysroot is missing: $PRIVATE_LIBRARY_SYSROOT"
for component in "$RUNTIME_STAGER" "$PYTHON_RUNTIME_STAGER" "$ELECTRON_RUNTIME_STAGER" "$DESKTOP_JS_STAGER" "$PRIVATE_LIB_STAGER" "$ELF_AUDITOR" "$PREINST_RENDERER"; do [ -f "$component" ] || fail "Missing packaging component: $component"; done
[ -f "$ICON_VALIDATOR" ] || fail "Missing icon validator: $ICON_VALIDATOR"
SOURCE_AGENT_PYTHON="$SOURCE_AGENT_DIR/venv/bin/python"
[ -x "$SOURCE_AGENT_PYTHON" ] || fail "Missing Linux Agent venv: $SOURCE_AGENT_PYTHON"
(cd "$SOURCE_AGENT_DIR" && "$SOURCE_AGENT_PYTHON" -m taiji_runtime.main --help >/dev/null 2>&1) || fail "Linux Agent venv module entrypoint failed"
verify_linux_electron_runtime
validate_desktop_entry "$DESKTOP_FILE"
validate_appstream_metadata
validate_packaged_config_template
scan_webui_offline_assets

rm -rf "$BUILD_ROOT"
mkdir -p "$INSTALL_ROOT/bin" "$INSTALL_ROOT/config" "$INSTALL_ROOT/licenses" "$INSTALL_ROOT/resources/icons" "$INSTALL_ROOT/scripts" "$INSTALL_ROOT/runtime/lib" "$AGENT_RUNTIME" "$WEB_RUNTIME" "$PKG_ROOT/DEBIAN" "$PKG_ROOT/usr/bin" "$PKG_ROOT/usr/share/applications" "$PKG_ROOT/usr/share/metainfo" "$OUT_DIR"
for size in 32 48 64 128 192 256 512; do
  mkdir -p "$PKG_ROOT/usr/share/icons/hicolor/${size}x${size}/apps"
done
chmod 0755 "$PKG_ROOT/opt" "$INSTALL_ROOT"
chmod 0755 "$INSTALL_ROOT/resources"
chmod 0755 "$INSTALL_ROOT/runtime/lib"
archive_old_packages
stage_python_runtime
install -m 0644 "$VERSION_FILE" "$INSTALL_ROOT/VERSION"
printf '%s\n' "$("$SOURCE_AGENT_PYTHON" -c 'import platform; print(platform.python_version())')" > "$AGENT_RUNTIME/PYTHON_VERSION"
chmod 0644 "$AGENT_RUNTIME/PYTHON_VERSION"
printf '%s\n' "$VERSION" > "$WEB_RUNTIME/PRODUCT_VERSION"
chmod 0644 "$WEB_RUNTIME/PRODUCT_VERSION"
install -m 0644 "$PAYLOAD_CONTRACT" "$INSTALL_ROOT/resources/payload-contract.json"
install -m 0644 "$POLICY_FILE" "$POLICY_INSTALL_PATH"
write_launch_manifest
python3 "$RUNTIME_STAGER" --repo-root "$REPO_ROOT" --install-root "$INSTALL_ROOT" --node-root "$PACKAGED_NODE_ROOT" --public-key-fingerprint "$ISSUER_PUBLIC_KEY_FINGERPRINT"
chmod 0755 "$INSTALL_ROOT/resources/license"
[ "$("$INSTALL_ROOT/runtime/node/bin/node" --version)" = "v$PACKAGED_NODE_VERSION" ] || fail "Staged Node runtime version mismatch"
python3 "$PRIVATE_LIB_STAGER" --root "$PKG_ROOT" --policy "$POLICY_FILE" --sysroot "$PRIVATE_LIBRARY_SYSROOT" --output "$PRIVATE_STAGE_REPORT" >/dev/null
rsync -a "$LAB_DIR/config"/ "$INSTALL_ROOT/config"/
install -m 0755 "$LAB_DIR/scripts/runtime-env.sh" "$INSTALL_ROOT/scripts/runtime-env.sh"
install -m 0755 "$LAB_DIR/scripts/start-agent.sh" "$INSTALL_ROOT/scripts/start-agent.sh"
install -m 0755 "$LAB_DIR/scripts/start-webui.sh" "$INSTALL_ROOT/scripts/start-webui.sh"
install -m 0755 "$LAB_DIR/scripts/stop-all.sh" "$INSTALL_ROOT/scripts/stop-all.sh"
install -m 0755 "$LAB_DIR/scripts/health-check.sh" "$INSTALL_ROOT/scripts/health-check.sh"
install -m 0755 "$LAB_DIR/scripts/taiji-native-verify" "$INSTALL_ROOT/scripts/taiji-native-verify"
install -m 0755 "$LAB_DIR/scripts/taiji-agent-diagnose" "$INSTALL_ROOT/scripts/taiji-agent-diagnose"
install -m 0755 "$REPO_ROOT/packaging/linux/support_bundle.py" "$INSTALL_ROOT/scripts/support_bundle.py"
install -m 0644 "$LAB_DIR/scripts/sync-packaged-config.py" "$INSTALL_ROOT/scripts/sync-packaged-config.py"
rewrite_product_text_tokens "$INSTALL_ROOT/scripts"
[ -f "$SOURCE_AGENT_DIR/LICENSE" ] && install -m 0644 "$SOURCE_AGENT_DIR/LICENSE" "$INSTALL_ROOT/licenses/agent-runtime.LICENSE" || true
[ -f "$SOURCE_WEB_DIR/LICENSE" ] && install -m 0644 "$SOURCE_WEB_DIR/LICENSE" "$INSTALL_ROOT/licenses/web-runtime.LICENSE" || true
mkdir -p "$DESKTOP_RUNTIME/src" "$DESKTOP_RUNTIME/node_modules"
install -m 0644 "$APP_DIR/package.json" "$DESKTOP_RUNTIME/package.json"
node "$DESKTOP_JS_STAGER" --source "$APP_DIR/src" --destination "$DESKTOP_RUNTIME/src" --entry main.js --entry preload.js
python3 "$ELECTRON_RUNTIME_STAGER" \
  --source "$APP_DIR/node_modules/electron" \
  --destination "$DESKTOP_RUNTIME/node_modules/electron" \
  --archive "$ELECTRON_ARCHIVE" \
  --policy "$POLICY_FILE" \
  --require-linux-x86-64
install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-agent" "$PKG_ROOT/usr/bin/taiji-agent"
install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji" "$PKG_ROOT/usr/bin/taiji"
install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-agent-diagnose" "$PKG_ROOT/usr/bin/taiji-agent-diagnose"
install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-agent-support" "$PKG_ROOT/usr/bin/taiji-agent-support"
install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-native-verify" "$INSTALL_ROOT/bin/taiji-native-verify"
install -m 0644 "$DESKTOP_FILE" "$PKG_ROOT/usr/share/applications/taiji-agent.desktop"
install -m 0644 "$SOURCE_WEB_DIR/static/favicon-512.png" "$INSTALL_ROOT/resources/icons/taiji-agent.png"
for size in 32 48 64 128 192 256 512; do
  install -m 0644 "$SOURCE_WEB_DIR/static/favicon-$size.png" "$PKG_ROOT/usr/share/icons/hicolor/${size}x${size}/apps/taiji-agent.png"
done
install -m 0644 "$APPSTREAM_FILE" "$PKG_ROOT/usr/share/metainfo/taiji-agent.metainfo.xml"
ICON_SET_SHA256="$(python3 "$ICON_VALIDATOR" \
  --web-static "$SOURCE_WEB_DIR/static" \
  --install-icons "$PKG_ROOT/usr/share/icons/hicolor" \
  --resource-icon "$INSTALL_ROOT/resources/icons/taiji-agent.png" \
  --print-digest)"
printf '%s\n' "$ICON_SET_SHA256" | grep -Eq '^[0-9a-f]{64}$' \
  || fail "Canonical icon digest is invalid"
python3 "$ELF_AUDITOR" --root "$PKG_ROOT" --policy "$POLICY_FILE" --output "$ABI_BUILD_REPORT" >/dev/null
install -m 0644 "$ABI_BUILD_REPORT" "$ABI_REPORT_PATH"
python3 "$PREINST_RENDERER" --template "$SCRIPT_DIR/preinst" --policy "$POLICY_FILE" --output "$PKG_ROOT/DEBIAN/preinst"
install -m 0755 "$SCRIPT_DIR/postinst" "$PKG_ROOT/DEBIAN/postinst"
install -m 0755 "$SCRIPT_DIR/prerm" "$PKG_ROOT/DEBIAN/prerm"
install -m 0755 "$SCRIPT_DIR/postrm" "$PKG_ROOT/DEBIAN/postrm"
installed_size="$(du -sk "$PKG_ROOT" | awk '{print $1}')"
cat > "$PKG_ROOT/DEBIAN/control" <<CONTROL
Package: $TAIJI_PACKAGE_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $TAIJI_PACKAGE_ARCHITECTURE
Installed-Size: $installed_size
Maintainer: $TAIJI_PACKAGE_MAINTAINER
Depends: $TAIJI_DEBIAN_DEPENDS
Description: Taiji Agent local desktop app
 Local desktop shell and offline runtime for Taiji Agent WebUI and Agent API.
CONTROL
python3 "$PAYLOAD_VERIFIER" --root "$PKG_ROOT" >/dev/null
scan_package_tree
dpkg-deb --root-owner-group -Zxz --build "$PKG_ROOT" "$OUT_DEB" >/dev/null
scan_deb_release_artifact
audit_deb_payload
write_package_manifest
echo "Built: $OUT_DEB"
echo "Checksum: $OUT_DEB.sha256"
echo "Manifest: $MANIFEST_PATH"
