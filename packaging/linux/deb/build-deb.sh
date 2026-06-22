#!/usr/bin/env bash
# Build an offline-first Taiji Agent DEB package.
#
# Run this on Linux x86_64/amd64 only. The script deliberately refuses to build
# final packages on macOS so Apple metadata and wrong Electron binaries cannot
# enter the release artifact.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
LAB_DIR="$REPO_ROOT/hermes-local-lab"
SOURCE_AGENT_DIR="$LAB_DIR/sources/hermes-agent"
SOURCE_WEB_DIR="$LAB_DIR/sources/hermes-webui"
APP_DIR="$REPO_ROOT/apps/taiji-desktop"
ELECTRON_BIN="$APP_DIR/node_modules/electron/dist/electron"
DESKTOP_FILE="$REPO_ROOT/packaging/linux/taiji-agent.desktop"
DEFAULT_CONFIG="$LAB_DIR/config/taiji-default-config.yaml"
VERSION="${TAIJI_AGENT_VERSION:-0.1.0}"
ARCH="amd64"
BUILD_ROOT="$REPO_ROOT/runtime/package-build/deb"
PKG_ROOT="$BUILD_ROOT/root"
INSTALL_ROOT="$PKG_ROOT/opt/taiji-agent"
AGENT_RUNTIME="$INSTALL_ROOT/runtime/agent"
WEB_RUNTIME="$INSTALL_ROOT/runtime/web"
OUT_DIR="$REPO_ROOT/packages/麒麟操作系统安装包"
OUT_DEB="$OUT_DIR/taiji-agent_${VERSION}_${ARCH}.deb"
ARCHIVE_DIR="$OUT_DIR/旧版本归档"
DEB_DEPENDS="libc6, libgtk-3-0, libnss3, libnspr4, libxss1, libasound2, libatk1.0-0, libatk-bridge2.0-0, libatspi2.0-0, libdrm2, libgbm1, libxkbcommon0, libx11-6, libxcomposite1, libxdamage1, libxext6, libxfixes3, libxrandr2, libxrender1, libxshmfence1, libxcb1, libcups2, libdbus-1-3, libglib2.0-0, libpango-1.0-0, libcairo2, libexpat1, libfontconfig1, libsecret-1-0, libxtst6, libuuid1, xdg-utils, ca-certificates"

fail() {
  echo "$*" >&2
  exit 1
}

warn() {
  echo "Warning: $*" >&2
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "Missing required command: $1"
  fi
}

verify_linux_electron_runtime() {
  if [ ! -x "$ELECTRON_BIN" ]; then
    fail "Missing Linux Electron runtime. Run npm ci inside apps/taiji-desktop on this Linux build host first."
  fi

  electron_file="$(file "$ELECTRON_BIN")"
  case "$electron_file" in
    *ELF*64-bit*x86-64*|*ELF*64-bit*X86-64*|*ELF*64-bit*80386*)
      ;;
    *)
      echo "$electron_file" >&2
      fail "Electron runtime is not a Linux x86_64 ELF binary. Re-run npm ci on Linux amd64."
      ;;
  esac

  ldd_output="$(ldd "$ELECTRON_BIN" 2>&1 || true)"
  if printf '%s\n' "$ldd_output" | grep -q 'not found'; then
    echo "$ldd_output" >&2
    fail "Electron runtime has missing shared libraries; add them to DEB Depends or the offline dependency bundle before release."
  fi
}

validate_desktop_entry() {
  local desktop="$1"
  if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$desktop"
    return
  fi
  warn "desktop-file-validate not found; using structural desktop entry checks"
  grep -qx 'Type=Application' "$desktop" || fail "Desktop entry missing Type=Application"
  grep -qx 'Name=太极 Agent' "$desktop" || fail "Desktop entry missing expected Name"
  grep -qx 'Exec=/usr/bin/taiji-agent' "$desktop" || fail "Desktop entry missing expected Exec"
  grep -qx 'Icon=taiji-agent' "$desktop" || fail "Desktop entry missing expected Icon"
  grep -qx 'Terminal=false' "$desktop" || fail "Desktop entry must not require a terminal"
}

archive_old_packages() {
  mkdir -p "$ARCHIVE_DIR"
  find "$OUT_DIR" -maxdepth 1 -type f \( -name 'taiji-agent_*_amd64.deb' -o -name 'taiji-agent_*_amd64.deb.sha256' \) \
    ! -name "$(basename "$OUT_DEB")" \
    ! -name "$(basename "$OUT_DEB").sha256" \
    -exec mv {} "$ARCHIVE_DIR"/ \;
}

scan_private_key_material() {
  if find "$INSTALL_ROOT" \( -name '.env' -o -name 'id_rsa' -o -name 'id_ed25519' -o -name '*.key' \) | grep -q .; then
    echo "Package tree contains local secrets or private-key shaped files; refusing release." >&2
    find "$INSTALL_ROOT" \( -name '.env' -o -name 'id_rsa' -o -name 'id_ed25519' -o -name '*.key' \) >&2
    exit 1
  fi

  if find "$INSTALL_ROOT" \( -name 'license.jwt' -o -name '*.jwt' \) | grep -q .; then
    echo "Package tree contains customer license files; refusing release." >&2
    find "$INSTALL_ROOT" \( -name 'license.jwt' -o -name '*.jwt' \) >&2
    exit 1
  fi

  private_key_paths=""
  while IFS= read -r -d '' candidate; do
    if grep -Eq 'BEGIN .*PRIVATE KEY' "$candidate" 2>/dev/null; then
      private_key_paths="${private_key_paths}${candidate}"$'\n'
    fi
  done < <(find "$INSTALL_ROOT" -type f \( -name '*.pem' -o -name '*.crt' -o -name '*.cer' \) -print0)

  if [ -n "$private_key_paths" ]; then
    echo "Package tree contains private key material; refusing release." >&2
    printf '%s' "$private_key_paths" >&2
    exit 1
  fi
}

validate_packaged_config_template() {
  if [ ! -f "$DEFAULT_CONFIG" ]; then
    fail "Missing packaged default config template: $DEFAULT_CONFIG"
  fi
  "$SOURCE_AGENT_PYTHON" - "$DEFAULT_CONFIG" <<'PY'
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1])
data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
sensitive_keys = (
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "private_key",
    "wechat",
    "weixin",
    "corpsecret",
)

def scan(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key).strip().lower()
            if any(marker in key_text for marker in sensitive_keys):
                raise SystemExit(f"sensitive key in packaged default config: {prefix}{key}")
            scan(child, f"{prefix}{key}.")
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            scan(child, f"{prefix}{idx}.")
    elif isinstance(value, str) and "BEGIN " in value and "PRIVATE KEY" in value:
        raise SystemExit(f"private key shaped value in packaged default config: {prefix.rstrip('.')}")

scan(data)
required = [
    ("model", "provider"),
    ("model", "default"),
    ("webui", "feature_visibility"),
]
for parent, key in required:
    if not isinstance(data.get(parent), dict) or key not in data[parent]:
        raise SystemExit(f"missing {parent}.{key} in packaged default config")
PY
}

compile_sourceless_python() {
  local target="$1"
  local python_bin="$2"
  find "$target" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  "$python_bin" -m compileall -q -b "$target"
  find "$target" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  find "$target" -type f -name '*.py' ! -path '*/venv/*' -delete
}

remove_editable_install_metadata() {
  local site_packages
  while IFS= read -r -d '' site_packages; do
    find "$site_packages" -maxdepth 1 \( \
      -name '__editable__*' -o \
      -name '*editable*' -o \
      -iname '*hermes*' \
    \) -exec rm -rf {} +
  done < <(find "$AGENT_RUNTIME/venv" -type d -name site-packages -print0)

  find "$AGENT_RUNTIME/venv/bin" -maxdepth 1 -iname '*hermes*' -exec rm -f {} + 2>/dev/null || true
}

repair_packaged_venv_paths() {
  local source_venv installed_venv source_agent installed_agent file
  source_venv="$SOURCE_AGENT_DIR/venv"
  installed_venv="/opt/taiji-agent/runtime/agent/venv"
  source_agent="$SOURCE_AGENT_DIR"
  installed_agent="/opt/taiji-agent/runtime/agent"

  while IFS= read -r -d '' file; do
    SOURCE_VENV="$source_venv" \
    INSTALLED_VENV="$installed_venv" \
    SOURCE_AGENT="$source_agent" \
    INSTALLED_AGENT="$installed_agent" \
      perl -pi -e 's/\Q$ENV{SOURCE_VENV}\E/$ENV{INSTALLED_VENV}/g; s/\Q$ENV{SOURCE_AGENT}\E/$ENV{INSTALLED_AGENT}/g' "$file"
  done < <(
    find "$AGENT_RUNTIME/venv/bin" -maxdepth 1 -type f -print0 2>/dev/null || true
    [ -f "$AGENT_RUNTIME/venv/pyvenv.cfg" ] && printf '%s\0' "$AGENT_RUNTIME/venv/pyvenv.cfg"
  )
}

rename_internal_agent_modules() {
  if [ -d "$AGENT_RUNTIME/hermes_cli" ]; then
    mv "$AGENT_RUNTIME/hermes_cli" "$AGENT_RUNTIME/taiji_cli"
  fi

  local source target
  for source in "$AGENT_RUNTIME"/hermes_*.py; do
    [ -e "$source" ] || continue
    target="$AGENT_RUNTIME/taiji_${source##*/hermes_}"
    mv "$source" "$target"
  done

  if [ -f "$AGENT_RUNTIME/agent/transports/hermes_tools_mcp_server.py" ]; then
    mv "$AGENT_RUNTIME/agent/transports/hermes_tools_mcp_server.py" \
      "$AGENT_RUNTIME/agent/transports/taiji_tools_mcp_server.py"
  fi

  rm -rf \
    "$AGENT_RUNTIME/hermes" \
    "$AGENT_RUNTIME/hermes-agent" \
    "$AGENT_RUNTIME/hermes_agent.egg-info" \
    "$AGENT_RUNTIME/.hermes" \
    "$AGENT_RUNTIME/setup-hermes.sh" \
    "$AGENT_RUNTIME/HERMES.md" \
    "$AGENT_RUNTIME/hermes-already-has-routines.md"
}

rewrite_product_text_tokens() {
  local target="$1"
  find "$target" -type f ! -path '*/venv/*' \( \
    -name '*.py' -o \
    -name '*.js' -o \
    -name '*.css' -o \
    -name '*.html' -o \
    -name '*.json' -o \
    -name '*.yaml' -o \
    -name '*.yml' -o \
    -name '*.toml' -o \
    -name '*.txt' -o \
    -name '*.md' \
  \) -print0 | xargs -0 -r perl -pi -e 's/HERMES_/TAIJI_/g; s/HERMES/TAIJI/g; s/Hermes/Taiji/g; s/hermes/taiji/g'
}

write_packaged_webui_version() {
  local base digest
  base="${TAIJI_WEBUI_VERSION:-}"
  if [ -z "$base" ] && command -v git >/dev/null 2>&1; then
    base="$(git -C "$SOURCE_WEB_DIR" describe --tags --always 2>/dev/null || true)"
    if [ -z "$base" ]; then
      base="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || true)"
    fi
  fi
  if [ -z "$base" ]; then
    base="taiji-webui"
  fi
  digest="$(
    find "$WEB_RUNTIME" -type f \
      ! -name '_version.txt' \
      ! -name '*.pyc' \
      -print0 \
      | sort -z \
      | xargs -0 sha256sum \
      | sha256sum \
      | awk '{print substr($1,1,12)}'
  )"
  mkdir -p "$WEB_RUNTIME/api"
  printf '%s-pkg.%s\n' "$base" "$digest" > "$WEB_RUNTIME/api/_version.txt"
}

stage_python_runtime() {
  mkdir -p "$AGENT_RUNTIME" "$WEB_RUNTIME"

  rsync -a \
    --exclude '.git' \
    --exclude '.github' \
    --exclude '.DS_Store' \
    --exclude '._*' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.env' \
    --exclude '.env.example' \
    --exclude '*.example' \
    --exclude '.envrc' \
    --exclude '.dockerignore' \
    --exclude '.gitattributes' \
    --exclude '.gitignore' \
    --exclude '.hadolint.yaml' \
    --exclude '.mailmap' \
    --exclude 'license.jwt' \
    --exclude '*.jwt' \
    --exclude '.pytest_cache' \
    --exclude '.playwright-mcp' \
    --exclude 'docs' \
    --exclude 'tests' \
    --exclude 'website' \
    --exclude 'articles' \
    --exclude 'demos' \
    --exclude 'datagen-config-examples' \
    --exclude 'docker' \
    --exclude 'nix' \
    --exclude 'packaging' \
    --exclude 'plugins/hermes-achievements' \
    --exclude 'plugins/kanban/systemd' \
    --exclude 'plugins/security-guidance' \
    --exclude 'skills' \
    --exclude 'scripts' \
    --exclude 'optional-skills' \
    --exclude 'optional-mcps' \
    --exclude 'locales' \
    --exclude 'ui-tui' \
    --exclude 'web' \
    --exclude 'venv' \
    --exclude 'Dockerfile' \
    --exclude 'docker-compose*' \
    --exclude 'flake.*' \
    --exclude 'MANIFEST.in' \
    --exclude 'uv.lock' \
    --exclude 'package*.json' \
    --exclude 'pyproject.toml' \
    --exclude 'LICENSE' \
    --exclude '*.md' \
    "$SOURCE_AGENT_DIR"/ "$AGENT_RUNTIME"/

  rename_internal_agent_modules
  rewrite_product_text_tokens "$AGENT_RUNTIME"

  rsync -a \
    --exclude '.git' \
    --exclude '.DS_Store' \
    --exclude '._*' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    "$SOURCE_AGENT_DIR/venv"/ "$AGENT_RUNTIME/venv"/
  remove_editable_install_metadata
  repair_packaged_venv_paths

  rsync -a \
    --exclude '.git' \
    --exclude '.DS_Store' \
    --exclude '._*' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '.env' \
    --exclude '.env.example' \
    --exclude '.env.docker.example' \
    --exclude '*.example' \
    --exclude '.dockerignore' \
    --exclude '.gitignore' \
    --exclude 'license.jwt' \
    --exclude '*.jwt' \
    --exclude '.pytest_cache' \
    --exclude '.github' \
    --exclude 'docs' \
    --exclude 'reports' \
    --exclude 'scripts' \
    --exclude 'ctl.sh' \
    --exclude 'start.sh' \
    --exclude 'docker_init.bash' \
    --exclude 'docker*' \
    --exclude '*compose*' \
    --exclude 'start.ps1' \
    --exclude 'pyproject.toml' \
    --exclude 'eslint*' \
    --exclude 'package*.json' \
    --exclude 'tests' \
    --exclude 'node_modules' \
    --exclude 'Dockerfile' \
    --exclude 'LICENSE' \
    --exclude '*.md' \
    "$SOURCE_WEB_DIR"/ "$WEB_RUNTIME"/

  rewrite_product_text_tokens "$WEB_RUNTIME"
  write_packaged_webui_version

  compile_sourceless_python "$AGENT_RUNTIME" "$SOURCE_AGENT_PYTHON"
  compile_sourceless_python "$WEB_RUNTIME" "$SOURCE_AGENT_PYTHON"
}

scan_product_privacy() {
  local name_hits text_hits
  name_hits="$(find "$INSTALL_ROOT" \
    -path "$INSTALL_ROOT/licenses" -prune -o \
    -path "$INSTALL_ROOT/licenses/*" -prune -o \
    -path "$AGENT_RUNTIME/venv/lib*" -prune -o \
    -iname '*hermes*' -print)"
  if [ -n "$name_hits" ]; then
    echo "Package tree contains legacy product names in visible paths; refusing release." >&2
    printf '%s\n' "$name_hits" >&2
    exit 1
  fi

  text_hits="$(find "$INSTALL_ROOT" \
    -path "$INSTALL_ROOT/licenses" -prune -o \
    -path "$INSTALL_ROOT/licenses/*" -prune -o \
    -path "$AGENT_RUNTIME/venv/lib*" -prune -o \
    -type f \
    ! -name '*.pyc' \
    ! -name '*.so' \
    ! -name '*.png' \
    ! -name '*.jpg' \
    ! -name '*.jpeg' \
    ! -name '*.gif' \
    -print0 | xargs -0 -r grep -I -n -E 'hermes|Hermes|HERMES_|hermes_cli|hermes-agent|hermes-webui|hermes-home' 2>/dev/null || true)"
  if [ -n "$text_hits" ]; then
    echo "Package tree contains legacy product names in text files; refusing release." >&2
    printf '%s\n' "$text_hits" >&2
    exit 1
  fi
}

scan_webui_offline_assets() {
  local static_dir required missing cdn_hits
  static_dir="$SOURCE_WEB_DIR/static"
  [ -d "$static_dir" ] || fail "Missing WebUI static directory: $static_dir"

  cdn_hits="$(grep -RInE 'cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com' "$static_dir" \
    --include='*.html' \
    --include='*.js' \
    --include='*.css' \
    --include='*.mjs' 2>/dev/null || true)"
  if [ -n "$cdn_hits" ]; then
    printf '%s\n' "$cdn_hits" >&2
    fail "WebUI static assets still depend on CDN; vendor them under static/vendor before building an offline package."
  fi

  for required in \
    "vendor/xterm/5.3.0/xterm.css" \
    "vendor/xterm/5.3.0/xterm.js" \
    "vendor/xterm-addon-fit/0.8.0/xterm-addon-fit.js" \
    "vendor/xterm-addon-web-links/0.9.0/xterm-addon-web-links.js" \
    "vendor/prismjs/1.29.0/themes/prism-tomorrow.min.css" \
    "vendor/prismjs/1.29.0/themes/prism.min.css" \
    "vendor/prismjs/1.29.0/prism.min.js" \
    "vendor/pdfjs-dist/4.9.155/pdf.min.mjs" \
    "vendor/pdfjs-dist/4.9.155/pdf.worker.min.mjs" \
    "vendor/mermaid/10.9.3/mermaid.min.js"; do
    if [ ! -f "$static_dir/$required" ]; then
      missing="${missing:-}${static_dir}/${required}"$'\n'
    fi
  done
  if [ -n "${missing:-}" ]; then
    printf '%s' "$missing" >&2
    fail "WebUI offline vendor assets are incomplete."
  fi
}

scan_package_tree() {
  if find "$PKG_ROOT" \( -name '.DS_Store' -o -name '._*' -o -name '__pycache__' \) | grep -q .; then
    echo "Package tree contains macOS/Python cache metadata; clean before release." >&2
    find "$PKG_ROOT" \( -name '.DS_Store' -o -name '._*' -o -name '__pycache__' \) >&2
    exit 1
  fi

  scan_private_key_material
  scan_product_privacy
}

scan_deb_release_artifact() {
  dpkg-deb -I "$OUT_DEB" >/dev/null
  dpkg-deb -c "$OUT_DEB" >/dev/null

  local pattern
  for pattern in LIBARCHIVE.xattr com.apple.provenance PaxHeaders SCHILY.xattr; do
    if strings "$OUT_DEB" | grep -F "$pattern" >/dev/null; then
      fail "DEB contains forbidden archive metadata marker: $pattern"
    fi
  done
}

audit_deb_payload() {
  local contents required missing
  contents="$BUILD_ROOT/deb-contents.txt"
  dpkg-deb -c "$OUT_DEB" > "$contents"
  for required in \
    "./opt/taiji-agent/runtime/agent/venv/bin/python" \
    "./opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron" \
    "./opt/taiji-agent/runtime/web/server.pyc" \
    "./opt/taiji-agent/scripts/taiji-native-verify" \
    "./usr/share/applications/taiji-agent.desktop" \
    "./usr/bin/taiji" \
    "./usr/bin/taiji-agent"; do
    if ! grep -F "$required" "$contents" >/dev/null; then
      missing="${missing:-}${required}"$'\n'
    fi
  done
  if [ -n "${missing:-}" ]; then
    printf '%s' "$missing" >&2
    fail "DEB payload is missing required runtime paths."
  fi
}

if [ "$(uname -s)" != "Linux" ]; then
  echo "Refusing to build final DEB on $(uname -s). Use Linux x86_64/amd64." >&2
  exit 1
fi

case "$(uname -m)" in
  x86_64|amd64) ;;
  *)
    echo "Refusing to build Hygon/UOS package on non-x86_64 host: $(uname -m)" >&2
    exit 1
    ;;
esac

for cmd in dpkg-deb rsync npm sha256sum file ldd strings perl; do
  require_cmd "$cmd"
done

if [ -n "${TAIJI_LICENSE_PRIVATE_KEY:-}" ] || [ -n "${TAIJI_LICENSE_PRIVATE_KEY_FILE:-}" ]; then
  warn "license signing private-key environment variables are ignored by package builds"
fi

SOURCE_AGENT_PYTHON="$SOURCE_AGENT_DIR/venv/bin/python"
if [ ! -x "$SOURCE_AGENT_PYTHON" ]; then
  echo "Missing Linux Agent venv. Run hermes-local-lab/scripts/setup-local.sh on this Linux build host first." >&2
  exit 1
fi
if ! (cd "$SOURCE_AGENT_DIR" && "$SOURCE_AGENT_PYTHON" -m taiji_runtime.main --help >/dev/null 2>&1); then
  echo "Linux Agent venv module entrypoint failed. Re-run hermes-local-lab/scripts/setup-local.sh on this Linux build host." >&2
  exit 1
fi

verify_linux_electron_runtime
validate_desktop_entry "$DESKTOP_FILE"
validate_packaged_config_template
scan_webui_offline_assets

rm -rf "$BUILD_ROOT"
mkdir -p \
  "$INSTALL_ROOT" \
  "$INSTALL_ROOT/bin" \
  "$INSTALL_ROOT/config" \
  "$INSTALL_ROOT/licenses" \
  "$INSTALL_ROOT/resources/icons" \
  "$INSTALL_ROOT/scripts" \
  "$AGENT_RUNTIME" \
  "$WEB_RUNTIME" \
  "$PKG_ROOT/DEBIAN" \
  "$PKG_ROOT/usr/bin" \
  "$PKG_ROOT/usr/share/applications" \
  "$PKG_ROOT/usr/share/icons/hicolor/512x512/apps" \
  "$OUT_DIR"
archive_old_packages

stage_python_runtime

rsync -a "$LAB_DIR/config"/ "$INSTALL_ROOT/config"/
install -m 0755 "$LAB_DIR/scripts/runtime-env.sh" "$INSTALL_ROOT/scripts/runtime-env.sh"
install -m 0755 "$LAB_DIR/scripts/start-agent.sh" "$INSTALL_ROOT/scripts/start-agent.sh"
install -m 0755 "$LAB_DIR/scripts/start-webui.sh" "$INSTALL_ROOT/scripts/start-webui.sh"
install -m 0755 "$LAB_DIR/scripts/stop-all.sh" "$INSTALL_ROOT/scripts/stop-all.sh"
install -m 0755 "$LAB_DIR/scripts/health-check.sh" "$INSTALL_ROOT/scripts/health-check.sh"
install -m 0755 "$LAB_DIR/scripts/taiji-native-verify" "$INSTALL_ROOT/scripts/taiji-native-verify"
install -m 0755 "$LAB_DIR/scripts/taiji-agent-diagnose" "$INSTALL_ROOT/scripts/taiji-agent-diagnose"
install -m 0644 "$LAB_DIR/scripts/sync-packaged-config.py" "$INSTALL_ROOT/scripts/sync-packaged-config.py"

if [ -f "$SOURCE_AGENT_DIR/LICENSE" ]; then
  install -m 0644 "$SOURCE_AGENT_DIR/LICENSE" "$INSTALL_ROOT/licenses/agent-runtime.LICENSE"
fi
if [ -f "$SOURCE_WEB_DIR/LICENSE" ]; then
  install -m 0644 "$SOURCE_WEB_DIR/LICENSE" "$INSTALL_ROOT/licenses/web-runtime.LICENSE"
fi

mkdir -p "$INSTALL_ROOT/apps"
rsync -a \
  --exclude '.git' \
  --exclude '.DS_Store' \
  --exclude '__pycache__' \
  "$APP_DIR" "$INSTALL_ROOT/apps/"

install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-agent" "$PKG_ROOT/usr/bin/taiji-agent"
install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji" "$PKG_ROOT/usr/bin/taiji"
install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-agent-diagnose" "$PKG_ROOT/usr/bin/taiji-agent-diagnose"
install -m 0644 "$DESKTOP_FILE" "$PKG_ROOT/usr/share/applications/taiji-agent.desktop"
install -m 0644 "$SOURCE_WEB_DIR/static/favicon-512.png" "$PKG_ROOT/usr/share/icons/hicolor/512x512/apps/taiji-agent.png"
install -m 0644 "$SOURCE_WEB_DIR/static/favicon-512.png" "$INSTALL_ROOT/resources/icons/taiji-agent.png"
cat > "$INSTALL_ROOT/bin/taiji-native-verify" <<'VERIFY'
#!/usr/bin/env bash
set -euo pipefail
export TAIJI_AGENT_ROOT="${TAIJI_AGENT_ROOT:-/opt/taiji-agent}"
export TAIJI_AGENT_USE_USER_DIRS="${TAIJI_AGENT_USE_USER_DIRS:-1}"
exec "$TAIJI_AGENT_ROOT/scripts/taiji-native-verify" "$@"
VERIFY
chmod 0755 "$INSTALL_ROOT/bin/taiji-native-verify"

install -m 0755 "$SCRIPT_DIR/preinst" "$PKG_ROOT/DEBIAN/preinst"
install -m 0755 "$SCRIPT_DIR/postinst" "$PKG_ROOT/DEBIAN/postinst"
install -m 0755 "$SCRIPT_DIR/prerm" "$PKG_ROOT/DEBIAN/prerm"
install -m 0755 "$SCRIPT_DIR/postrm" "$PKG_ROOT/DEBIAN/postrm"

installed_size="$(du -sk "$PKG_ROOT" | awk '{print $1}')"
cat > "$PKG_ROOT/DEBIAN/control" <<CONTROL
Package: taiji-agent
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Installed-Size: $installed_size
Maintainer: Taiji Agent Team <support@example.invalid>
Depends: $DEB_DEPENDS
Description: Taiji Agent local desktop app
 Local desktop shell and offline runtime for Taiji Agent WebUI and Agent API.
CONTROL

scan_package_tree

dpkg-deb --root-owner-group -Zxz --build "$PKG_ROOT" "$OUT_DEB"
scan_deb_release_artifact
audit_deb_payload
sha256sum "$OUT_DEB" > "$OUT_DEB.sha256"

echo "Built: $OUT_DEB"
echo "Checksum: $OUT_DEB.sha256"
