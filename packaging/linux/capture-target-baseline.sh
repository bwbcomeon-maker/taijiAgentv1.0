#!/bin/bash -p
set -euo pipefail
umask 077

IFS=$' \t\n'
PATH="/usr/sbin:/usr/bin:/sbin:/bin"
LANG=C
LC_ALL=C
export PATH LANG LC_ALL
unset BASH_ENV ENV CDPATH GLOBIGNORE \
  PYTHONPATH PYTHONHOME PYTHONSTARTUP PYTHONINSPECT PYTHONWARNINGS \
  LD_PRELOAD LD_LIBRARY_PATH DPKG_ROOT DPKG_ADMINDIR || true

SOURCE_PATH="${BASH_SOURCE[0]}"
case "$SOURCE_PATH" in
  /*) ;;
  */*) SOURCE_PATH="$(pwd -P)/$SOURCE_PATH" ;;
  *)
    echo "拒绝通过 PATH 查找目标基线采集脚本；请使用明确路径执行。" >&2
    exit 1
    ;;
esac
SCRIPT_DIR="$(cd -P -- "${SOURCE_PATH%/*}" && pwd -P)"
CURRENT_DIR="$(pwd -P)"
OUTPUT_PATH="${1:-$CURRENT_DIR/taiji-target-baseline.json}"

[ -x /usr/bin/python3 ] || {
  echo "目标机缺少 python3，无法生成结构化兼容基线；请先由交付人员补齐采集工具。" >&2
  exit 1
}
[ -f "$SCRIPT_DIR/target_baseline.py" ] && [ ! -L "$SCRIPT_DIR/target_baseline.py" ] || {
  echo "目标基线采集工具缺失或不是常规文件。" >&2
  exit 1
}
[ -f "$SCRIPT_DIR/deb/runtime-depends.txt" ] && [ ! -L "$SCRIPT_DIR/deb/runtime-depends.txt" ] || {
  echo "目标基线依赖契约缺失或不是常规文件。" >&2
  exit 1
}

exec -c /usr/bin/python3 "$SCRIPT_DIR/target_baseline.py" capture \
  --depends-file "$SCRIPT_DIR/deb/runtime-depends.txt" \
  --output "$OUTPUT_PATH"
