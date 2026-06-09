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
APP_DIR="$REPO_ROOT/apps/taiji-desktop"
ELECTRON_BIN="$APP_DIR/node_modules/electron/dist/electron"
DESKTOP_FILE="$REPO_ROOT/packaging/linux/taiji-agent.desktop"
VERSION="${TAIJI_AGENT_VERSION:-0.1.0}"
ARCH="amd64"
BUILD_ROOT="$REPO_ROOT/runtime/package-build/deb"
PKG_ROOT="$BUILD_ROOT/root"
INSTALL_ROOT="$PKG_ROOT/opt/taiji-agent"
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

scan_package_tree() {
  if find "$PKG_ROOT" \( -name '.DS_Store' -o -name '._*' -o -name '__pycache__' -o -name '*.pyc' \) | grep -q .; then
    echo "Package tree contains macOS/Python cache metadata; clean before release." >&2
    find "$PKG_ROOT" \( -name '.DS_Store' -o -name '._*' -o -name '__pycache__' -o -name '*.pyc' \) >&2
    exit 1
  fi

  if find "$INSTALL_ROOT" \( -name '.env' -o -name '*.pem' -o -name 'id_rsa' -o -name 'id_ed25519' \) | grep -q .; then
    echo "Package tree contains local secrets or private-key shaped files; refusing release." >&2
    find "$INSTALL_ROOT" \( -name '.env' -o -name '*.pem' -o -name 'id_rsa' -o -name 'id_ed25519' \) >&2
    exit 1
  fi
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

for cmd in dpkg-deb rsync npm sha256sum file ldd strings; do
  require_cmd "$cmd"
done

if [ ! -x "$LAB_DIR/sources/hermes-agent/venv/bin/hermes" ]; then
  echo "Missing Linux Agent venv. Run hermes-local-lab/scripts/setup-local.sh on this Linux build host first." >&2
  exit 1
fi

verify_linux_electron_runtime
validate_desktop_entry "$DESKTOP_FILE"

rm -rf "$BUILD_ROOT"
mkdir -p \
  "$INSTALL_ROOT" \
  "$INSTALL_ROOT/bin" \
  "$PKG_ROOT/DEBIAN" \
  "$PKG_ROOT/usr/bin" \
  "$PKG_ROOT/usr/share/applications" \
  "$PKG_ROOT/usr/share/icons/hicolor/512x512/apps" \
  "$OUT_DIR"
archive_old_packages

rsync -a \
  --exclude '.git' \
  --exclude '.DS_Store' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.env' \
  --exclude 'hermes-home' \
  --exclude 'logs' \
  --exclude 'tmp' \
  --exclude 'workspace' \
  --exclude 'reports' \
  "$LAB_DIR"/ "$INSTALL_ROOT"/

mkdir -p "$INSTALL_ROOT/apps"
rsync -a \
  --exclude '.git' \
  --exclude '.DS_Store' \
  --exclude '__pycache__' \
  "$APP_DIR" "$INSTALL_ROOT/apps/"

install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji-agent" "$PKG_ROOT/usr/bin/taiji-agent"
install -m 0755 "$REPO_ROOT/packaging/linux/bin/taiji" "$PKG_ROOT/usr/bin/taiji"
install -m 0644 "$DESKTOP_FILE" "$PKG_ROOT/usr/share/applications/taiji-agent.desktop"
install -m 0644 "$LAB_DIR/sources/hermes-webui/static/favicon-512.png" "$PKG_ROOT/usr/share/icons/hicolor/512x512/apps/taiji-agent.png"
cat > "$INSTALL_ROOT/bin/taiji-native-verify" <<'VERIFY'
#!/usr/bin/env bash
set -euo pipefail
export TAIJI_AGENT_ROOT="${TAIJI_AGENT_ROOT:-/opt/taiji-agent}"
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
sha256sum "$OUT_DEB" > "$OUT_DEB.sha256"

echo "Built: $OUT_DEB"
echo "Checksum: $OUT_DEB.sha256"
