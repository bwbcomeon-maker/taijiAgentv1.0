#!/bin/zsh
set -euo pipefail

ROOT=/Users/bwb/Documents/工作/taiji-agentv1.0
WT="$ROOT/.worktrees/main-consolidation-20260717"
QA_ROOT=/tmp/taiji-main-consolidation-20260717
RUNTIME_EVIDENCE="$WT/qa-evidence/main-consolidation-20260717/daily-runtime/task-b1"

capture_electron() {
  local snapshot="$1"
  local prefix="$RUNTIME_EVIDENCE/$snapshot-electron"

  if lsof -t -c Electron -a -d cwd 2>/dev/null \
      | sort -u > "$prefix-pid.txt" && test -s "$prefix-pid.txt"; then
    print -r -- present > "$prefix-presence.txt"
    while IFS= read -r pid; do
      lsof -p "$pid" -a -d cwd -Fpn | rg '^[pn]'
    done < "$prefix-pid.txt" | sort -u > "$prefix-cwd.txt"
    if ! lsof -nP -c Electron -a -iTCP -sTCP:LISTEN -Fpcn 2>/dev/null \
        | rg '^[pcn]' | sort -u > "$prefix-listener.txt"; then
      : > "$prefix-listener.txt"
    fi
  else
    print -r -- absent > "$prefix-presence.txt"
    : > "$prefix-pid.txt"
    : > "$prefix-cwd.txt"
    : > "$prefix-listener.txt"
  fi
}

capture_port() {
  local snapshot="$1"
  local port="$2"
  local health_mode="$3"
  local prefix="$RUNTIME_EVIDENCE/$snapshot-$port"

  if test "$health_mode" = health; then
    : > "$prefix-headers.txt"
    : > "$prefix-health.json"
    : > "$prefix-commit-header.txt"
  fi

  if lsof -nP -iTCP:"$port" -sTCP:LISTEN -Fpcn 2>/dev/null \
      | rg '^[pcn]' | sort -u > "$prefix-listener.txt" \
      && test -s "$prefix-listener.txt"; then
    print -r -- present > "$prefix-presence.txt"
    sed -n 's/^p//p' "$prefix-listener.txt" | sort -u > "$prefix-pid.txt"
    while IFS= read -r pid; do
      lsof -p "$pid" -a -d cwd -Fpn | rg '^[pn]'
    done < "$prefix-pid.txt" | sort -u > "$prefix-cwd.txt"

    if test "$health_mode" = health \
        && { test "$snapshot" = before \
          || test "$(< "$RUNTIME_EVIDENCE/before-$port-presence.txt")" = present; }; then
      curl -fsS -D "$prefix-headers.txt" \
        -o "$prefix-health.json" "http://127.0.0.1:$port/health"
      rg -i '^(x-.*commit|server-commit|x-source-revision):' \
        "$prefix-headers.txt" > "$prefix-commit-header.txt"
      test -s "$prefix-health.json"
      test -s "$prefix-commit-header.txt"
    fi
  else
    print -r -- absent > "$prefix-presence.txt"
    : > "$prefix-listener.txt"
    : > "$prefix-pid.txt"
    : > "$prefix-cwd.txt"
  fi
}

capture_runtime() {
  local snapshot="$1"
  capture_electron "$snapshot"
  capture_port "$snapshot" 18642 health
  capture_port "$snapshot" 18787 health
  capture_port "$snapshot" 18643 protected
}

assert_same_file() {
  local label="$1"
  local before="$2"
  local after="$3"
  cmp -s "$before" "$after" || {
    print -u2 -r -- "runtime changed: $label"
    return 1
  }
}

assert_component_unchanged() {
  local component="$1"
  local health_mode="$2"
  local before_prefix="$RUNTIME_EVIDENCE/before-$component"
  local after_prefix="$RUNTIME_EVIDENCE/after-$component"
  local before_presence

  assert_same_file "$component presence" \
    "$before_prefix-presence.txt" "$after_prefix-presence.txt"
  assert_same_file "$component pid" \
    "$before_prefix-pid.txt" "$after_prefix-pid.txt"
  assert_same_file "$component cwd" \
    "$before_prefix-cwd.txt" "$after_prefix-cwd.txt"
  assert_same_file "$component listener" \
    "$before_prefix-listener.txt" "$after_prefix-listener.txt"
  before_presence="$(< "$before_prefix-presence.txt")"
  if test "$before_presence" = present; then
    if test "$health_mode" = health; then
      assert_same_file "$component health" \
        "$before_prefix-health.json" "$after_prefix-health.json"
      assert_same_file "$component commit header" \
        "$before_prefix-commit-header.txt" \
        "$after_prefix-commit-header.txt"
    fi
  else
    test "$(< "$after_prefix-presence.txt")" = absent
  fi
}

assert_qa_not_on_protected_ports() {
  local port pid cwd
  for port in 18642 18643 18787; do
    while IFS= read -r pid; do
      cwd="$(lsof -p "$pid" -a -d cwd -Fn | sed -n 's/^n//p')"
      case "$cwd" in
        "$QA_ROOT"*|"$WT"*)
          print -u2 -r -- \
            "QA process $pid occupies protected port $port ($cwd)"
          return 1
          ;;
      esac
    done < "$RUNTIME_EVIDENCE/after-$port-pid.txt"
  done
}

assert_runtime_unchanged() {
  assert_component_unchanged electron protected
  assert_component_unchanged 18642 health
  assert_component_unchanged 18787 health
  assert_component_unchanged 18643 protected
  assert_qa_not_on_protected_ports
}

for required_command in lsof curl rg sort cmp; do
  command -v "$required_command" >/dev/null
done

mode="${1:-}"
case "$mode" in
  before)
    capture_runtime before
    test "$(< "$RUNTIME_EVIDENCE/before-18642-presence.txt")" = absent
    test "$(< "$RUNTIME_EVIDENCE/before-18787-presence.txt")" = absent
    for component in electron 18642 18787 18643; do
      print -r -- \
        "$component $(< "$RUNTIME_EVIDENCE/before-$component-presence.txt")"
    done
    print -r -- "RUNTIME_GUARD_BEFORE_CAPTURED"
    ;;
  after)
    for component in electron 18642 18787 18643; do
      test -s "$RUNTIME_EVIDENCE/before-$component-presence.txt"
    done
    capture_runtime after
    assert_runtime_unchanged
    for test_port in 19642 19787 25513; do
      test -z "$(
        lsof -nP -t -iTCP:"$test_port" -sTCP:LISTEN 2>/dev/null || true
      )"
    done
    print -r -- "RUNTIME_GUARD_AFTER_PASSED"
    ;;
  *)
    print -u2 -r -- "usage: $0 before|after"
    exit 2
    ;;
esac
