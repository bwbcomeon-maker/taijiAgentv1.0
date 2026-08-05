#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORT_DIR="$SCRIPT_DIR/诊断报告"

mkdir -p "$REPORT_DIR"
chmod 0700 "$REPORT_DIR"

if ! command -v taiji-agent-support >/dev/null 2>&1; then
  printf '%s\n' '[FAIL] 未找到 taiji-agent-support，请先安装太极 Agent DEB。' >&2
  exit 1
fi

if ! taiji-agent-support --output-dir "$REPORT_DIR"; then
  printf '%s\n' '[FAIL] 支持包生成失败。' >&2
  exit 1
fi

printf '%s\n' '[OK] 诊断报告已生成（tar.gz 与 sha256 sidecar）。'
printf '%s\n' '请只提交该支持包，不要提交其它日志、附件或用户目录文件。'
