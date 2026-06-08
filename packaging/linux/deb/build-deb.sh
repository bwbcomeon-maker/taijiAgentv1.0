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
VERSION="${TAIJI_AGENT_VERSION:-0.1.0}"
ARCH="amd64"
BUILD_ROOT="$REPO_ROOT/runtime/package-build/deb"
PKG_ROOT="$BUILD_ROOT/root"
INSTALL_ROOT="$PKG_ROOT/opt/taiji-agent"
OUT_DIR="$REPO_ROOT/packages/麒麟操作系统安装包"
OUT_DEB="$OUT_DIR/taiji-agent_${VERSION}_${ARCH}.deb"

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

for cmd in dpkg-deb rsync npm sha256sum; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
done

if [ ! -x "$LAB_DIR/sources/hermes-agent/venv/bin/hermes" ]; then
  echo "Missing Linux Agent venv. Run hermes-local-lab/scripts/setup-local.sh on this Linux build host first." >&2
  exit 1
fi

if [ ! -x "$APP_DIR/node_modules/electron/dist/electron" ]; then
  echo "Missing Linux Electron runtime. Run npm ci inside apps/taiji-desktop on this Linux build host first." >&2
  exit 1
fi

rm -rf "$BUILD_ROOT"
mkdir -p \
  "$INSTALL_ROOT" \
  "$INSTALL_ROOT/bin" \
  "$PKG_ROOT/DEBIAN" \
  "$PKG_ROOT/usr/bin" \
  "$PKG_ROOT/usr/share/applications" \
  "$PKG_ROOT/usr/share/icons/hicolor/512x512/apps" \
  "$OUT_DIR"

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
install -m 0644 "$REPO_ROOT/packaging/linux/taiji-agent.desktop" "$PKG_ROOT/usr/share/applications/taiji-agent.desktop"
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
Depends: libc6, libgtk-3-0, libnss3, libxss1, libasound2, libatk-bridge2.0-0, libdrm2, libgbm1, libxkbcommon0, xdg-utils, ca-certificates
Description: Taiji Agent local desktop app
 Local desktop shell and offline runtime for Taiji Agent WebUI and Agent API.
CONTROL

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

dpkg-deb --root-owner-group -Zxz --build "$PKG_ROOT" "$OUT_DEB"
sha256sum "$OUT_DEB" > "$OUT_DEB.sha256"

echo "Built: $OUT_DEB"
echo "Checksum: $OUT_DEB.sha256"
