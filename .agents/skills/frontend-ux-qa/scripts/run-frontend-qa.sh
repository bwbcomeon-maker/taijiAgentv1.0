#!/usr/bin/env bash

# 保守的前端 QA 辅助脚本。
# 只读取已有 package.json scripts，并运行已经存在的常见检查。
# 不安装依赖，不修改 package.json，不修改 lockfile，不修改业务文件，不生成新的视觉基线。

set -u
set -o pipefail

PROJECT_DIR="${1:-$(pwd)}"
PACKAGE_JSON="$PROJECT_DIR/package.json"
CHECKS="lint typecheck test test:unit test:e2e test:playwright test:ui build"
exit_code=0

print_section() {
  printf '\n== %s ==\n' "$1"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

detect_package_manager() {
  if [ -f "$PROJECT_DIR/pnpm-lock.yaml" ]; then
    printf 'pnpm'
  elif [ -f "$PROJECT_DIR/yarn.lock" ]; then
    printf 'yarn'
  elif [ -f "$PROJECT_DIR/bun.lockb" ] || [ -f "$PROJECT_DIR/bun.lock" ]; then
    printf 'bun'
  elif [ -f "$PROJECT_DIR/package-lock.json" ] || [ -f "$PROJECT_DIR/npm-shrinkwrap.json" ]; then
    printf 'npm'
  else
    printf 'npm'
  fi
}

script_exists() {
  local script_name="$1"
  node -e '
const fs = require("fs");
const pkg = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const name = process.argv[2];
process.exit(pkg.scripts && Object.prototype.hasOwnProperty.call(pkg.scripts, name) ? 0 : 1);
' "$PACKAGE_JSON" "$script_name"
}

list_scripts() {
  node -e '
const fs = require("fs");
const pkg = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
const scripts = pkg.scripts || {};
for (const name of Object.keys(scripts).sort()) {
  console.log(`${name}: ${scripts[name]}`);
}
' "$PACKAGE_JSON"
}

run_script() {
  local package_manager="$1"
  local script_name="$2"

  print_section "运行 $script_name"
  case "$package_manager" in
    pnpm)
      pnpm run "$script_name"
      ;;
    yarn)
      yarn run "$script_name"
      ;;
    bun)
      bun run "$script_name"
      ;;
    npm)
      npm run "$script_name"
      ;;
    *)
      printf '未验证：未知包管理器 %s\n' "$package_manager"
      return 1
      ;;
  esac
}

print_section "前端 QA 辅助脚本"
printf '项目目录：%s\n' "$PROJECT_DIR"

if [ ! -f "$PACKAGE_JSON" ]; then
  printf '未验证：未找到 package.json，无法读取前端 scripts。\n'
  exit 0
fi

if ! command_exists node; then
  printf '未验证：未找到 node，无法读取 package.json scripts。\n'
  exit 1
fi

PACKAGE_MANAGER="$(detect_package_manager)"
printf '检测到包管理器：%s\n' "$PACKAGE_MANAGER"

if ! command_exists "$PACKAGE_MANAGER"; then
  printf '未验证：检测到 %s，但当前环境无法执行该命令。\n' "$PACKAGE_MANAGER"
  exit 1
fi

print_section "可用 scripts"
if ! list_scripts; then
  printf '未验证：package.json 解析失败。\n'
  exit 1
fi

for check in $CHECKS; do
  if script_exists "$check"; then
    if ! run_script "$PACKAGE_MANAGER" "$check"; then
      printf '失败：%s 检查未通过。\n' "$check"
      exit_code=1
    fi
  else
    printf '未配置，跳过：%s\n' "$check"
  fi
done

if [ "$exit_code" -eq 0 ]; then
  print_section "结果"
  printf '已运行所有已配置的常见检查脚本；未配置项已跳过。\n'
else
  print_section "结果"
  printf '存在检查失败，请查看上方输出。\n'
fi

exit "$exit_code"
