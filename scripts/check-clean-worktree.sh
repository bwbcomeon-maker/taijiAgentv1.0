#!/bin/bash -p
set -euo pipefail

PATH="/usr/bin:/bin"
LANG=C
LC_ALL=C
export PATH LANG LC_ALL
unset BASH_ENV ENV CDPATH
unset -f git 2>/dev/null || true
while IFS= read -r -d '' _taiji_git_env_entry; do
  _taiji_git_env_name="${_taiji_git_env_entry%%=*}"
  case "$_taiji_git_env_name" in
    GIT_*) unset "$_taiji_git_env_name" ;;
  esac
done < <(/usr/bin/env -0)
unset _taiji_git_env_entry _taiji_git_env_name

GIT_CONFIG_NOSYSTEM=1
GIT_CONFIG_SYSTEM=/dev/null
GIT_CONFIG_GLOBAL=/dev/null
GIT_OPTIONAL_LOCKS=0
GIT_TERMINAL_PROMPT=0
GIT_NO_REPLACE_OBJECTS=1
GIT_ATTR_NOSYSTEM=1
export GIT_CONFIG_NOSYSTEM GIT_CONFIG_SYSTEM GIT_CONFIG_GLOBAL
export GIT_OPTIONAL_LOCKS GIT_TERMINAL_PROMPT GIT_NO_REPLACE_OBJECTS GIT_ATTR_NOSYSTEM

GIT=/usr/bin/git

# Repository-selection variables can make `git -C <declared root>` inspect a
# different checkout.  Provenance checks must derive state from the explicit
# path, never from ambient shell state.
unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE
unset GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES

mode="formal"
dirty_policy="strict"
repo_root_input=""
source_root_input=""
expected_head=""

usage() {
  cat <<'EOF'
Usage:
  check-clean-worktree.sh [--mode formal|development]
                          [--dirty-policy strict|runtime]
                          [--repo-root PATH]
                          [--source-root PATH]
                          [--expect-head FULL_COMMIT]

formal (default):
  Require a clean local main checked out in the repository's primary worktree.

development:
  Explicitly allow a branch or linked worktree, including local changes, while
  still requiring the declared source root to match that Git worktree.

dirty policy:
  strict (default) rejects every tracked or untracked change. runtime permits
  changes limited to repository-local coding-agent instructions (AGENTS.md and
  .agents/) and Markdown plans/specs under docs/superpowers/ for formal local
  launch only; development mode keeps its existing dirty-worktree preview
  behavior. Release and packaging must keep strict.
EOF
}

fail() {
  printf '[FAIL] %s\n' "$*" >&2
  exit 1
}

physical_dir() {
  local value="$1"
  [ -d "$value" ] || fail "directory does not exist: $value"
  (cd "$value" && pwd -P)
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --mode)
      [ "$#" -ge 2 ] || fail "--mode requires a value"
      mode="$2"
      shift 2
      ;;
    --repo-root)
      [ "$#" -ge 2 ] || fail "--repo-root requires a value"
      repo_root_input="$2"
      shift 2
      ;;
    --dirty-policy)
      [ "$#" -ge 2 ] || fail "--dirty-policy requires a value"
      dirty_policy="$2"
      shift 2
      ;;
    --source-root)
      [ "$#" -ge 2 ] || fail "--source-root requires a value"
      source_root_input="$2"
      shift 2
      ;;
    --expect-head)
      [ "$#" -ge 2 ] || fail "--expect-head requires a value"
      expected_head="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

case "$mode" in
  formal|development) ;;
  *) fail "unsupported mode: $mode" ;;
esac
case "$dirty_policy" in
  strict|runtime) ;;
  *) fail "unsupported dirty policy: $dirty_policy" ;;
esac

if [ -n "$expected_head" ]; then
  [ "${#expected_head}" -eq 40 ] || fail "expected head must be a full lowercase commit"
  case "$expected_head" in
    *[!0-9a-f]*) fail "expected head must be a full lowercase commit" ;;
  esac
fi

[ -x "$GIT" ] || fail "/usr/bin/git is required"

if [ -z "$repo_root_input" ]; then
  repo_root_input="$("$GIT" rev-parse --show-toplevel 2>/dev/null)" \
    || fail "not inside a Git worktree"
fi
repo_root="$(physical_dir "$repo_root_input")"

git_top_raw="$("$GIT" -C "$repo_root" rev-parse --show-toplevel 2>/dev/null)" \
  || fail "repo root is not inside a Git worktree: $repo_root"
git_top="$(physical_dir "$git_top_raw")"
[ "$repo_root" = "$git_top" ] \
  || fail "repo root does not match git top-level: repo=$repo_root git=$git_top"

if [ -z "$source_root_input" ]; then
  source_root_input="$repo_root"
fi
source_root="$(physical_dir "$source_root_input")"
[ "$source_root" = "$git_top" ] \
  || fail "source root does not match git top-level: source=$source_root git=$git_top"

common_dir_raw="$("$GIT" -C "$git_top" rev-parse --git-common-dir 2>/dev/null)" \
  || fail "cannot resolve git common dir"
case "$common_dir_raw" in
  /*) common_dir_candidate="$common_dir_raw" ;;
  *) common_dir_candidate="$git_top/$common_dir_raw" ;;
esac
common_dir="$(physical_dir "$common_dir_candidate")"
canonical_root="$(physical_dir "$common_dir/..")"
canonical_common_dir="$git_top/.git"

if [ -d "$canonical_common_dir" ] && [ ! -L "$canonical_common_dir" ] \
  && [ "$(physical_dir "$canonical_common_dir")" = "$common_dir" ]; then
  worktree_kind="primary"
else
  worktree_kind="linked"
fi

unsafe_git_config=""
while IFS= read -r -d '' git_config_name; do
  case "$git_config_name" in
    filter.*|include.*|includeif.*|includeIf.*|core.attributesfile|core.excludesfile|core.hookspath|core.worktree|diff.external|diff.*.command|diff.*.textconv|merge.*.driver)
      unsafe_git_config="${unsafe_git_config}${unsafe_git_config:+$'\n'}$git_config_name"
      ;;
  esac
done < <("$GIT" -C "$git_top" config --local --null --name-only --list --no-includes)
[ -z "$unsafe_git_config" ] || {
  printf '%s\n' "$unsafe_git_config" >&2
  fail "formal source Git config contains an unsafe executable filter or include"
}
unset unsafe_git_config git_config_name

branch="$("$GIT" -C "$git_top" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
head_commit="$("$GIT" -C "$git_top" rev-parse HEAD 2>/dev/null)" \
  || fail "cannot resolve HEAD"
if [ -n "$expected_head" ] && [ "$head_commit" != "$expected_head" ]; then
  fail "formal source expected head does not match actual HEAD: expected=$expected_head actual=$head_commit"
fi
status="$("$GIT" -C "$git_top" -c core.fsmonitor=false status --porcelain=v1 --untracked-files=all)"
if [ -n "$status" ]; then
  dirty="1"
else
  dirty="0"
fi

is_non_runtime_path() {
  case "$1" in
    AGENTS.md|.agents/*|docs/superpowers/plans/*.md|docs/superpowers/specs/*.md) return 0 ;;
    *) return 1 ;;
  esac
}

append_runtime_status() {
  runtime_status="${runtime_status}${runtime_status:+$'\n'}$1"
}

append_non_runtime_status() {
  non_runtime_status="${non_runtime_status}${non_runtime_status:+$'\n'}$1"
}

runtime_status=""
non_runtime_status=""
while IFS= read -r -d '' status_entry; do
  status_code="${status_entry:0:2}"
  status_path="${status_entry:3}"
  case "$status_code" in
    *R*|*C*)
      IFS= read -r -d '' original_path \
        || fail "malformed Git status rename/copy record"
      display_status="$status_code $original_path -> $status_path"
      if is_non_runtime_path "$original_path" && is_non_runtime_path "$status_path"; then
        append_non_runtime_status "$display_status"
      else
        append_runtime_status "$display_status"
      fi
      ;;
    *)
      display_status="$status_code $status_path"
      if is_non_runtime_path "$status_path"; then
        append_non_runtime_status "$display_status"
      else
        append_runtime_status "$display_status"
      fi
      ;;
  esac
done < <("$GIT" -C "$git_top" -c core.fsmonitor=false -c core.quotePath=false status --porcelain=v1 -z --untracked-files=all)
if [ -n "$runtime_status" ]; then runtime_dirty="1"; else runtime_dirty="0"; fi
if [ -n "$non_runtime_status" ]; then non_runtime_dirty="1"; else non_runtime_dirty="0"; fi

printf 'mode: %s\n' "$mode"
printf 'source_root: %s\n' "$source_root"
printf 'repo: %s\n' "$git_top"
printf 'canonical_root: %s\n' "$canonical_root"
printf 'git_common_dir: %s\n' "$common_dir"
printf 'worktree: %s\n' "$worktree_kind"
printf 'branch: %s\n' "${branch:-detached}"
printf 'head: %s\n' "$head_commit"
printf 'dirty: %s\n' "$dirty"
printf 'dirty_policy: %s\n' "$dirty_policy"
printf 'runtime_dirty: %s\n' "$runtime_dirty"
printf 'non_runtime_dirty: %s\n' "$non_runtime_dirty"

if [ "$mode" = "development" ]; then
  printf 'development source isolation gate passed\n'
  exit 0
fi

[ "$worktree_kind" = "primary" ] \
  || fail "formal source must use the primary worktree: current=$git_top canonical=$canonical_root"
[ "$branch" = "main" ] \
  || fail "formal source must be branch main: current=${branch:-detached}"

main_commit="$("$GIT" -C "$git_top" rev-parse refs/heads/main 2>/dev/null)" \
  || fail "local refs/heads/main does not exist"
[ "$head_commit" = "$main_commit" ] \
  || fail "formal source HEAD does not match local main: head=$head_commit main=$main_commit"

index_flag_status=""
while IFS= read -r -d '' index_entry; do
  index_tag="${index_entry:0:1}"
  index_path="${index_entry:2}"
  case "$index_tag" in
    S|[a-z])
      index_flag_status="${index_flag_status}${index_flag_status:+$'\n'}$index_tag $index_path"
      ;;
  esac
done < <("$GIT" -C "$git_top" -c core.fsmonitor=false -c core.quotePath=false ls-files -v -z)
[ -z "$index_flag_status" ] || {
  printf '%s\n' "$index_flag_status" >&2
  fail "formal source has assume-unchanged or skip-worktree index flags"
}

if [ "$dirty_policy" = "runtime" ] && [ "$non_runtime_dirty" != "0" ]; then
  printf '[WARN] non-runtime source changes ignored for local runtime:\n%s\n' "$non_runtime_status" >&2
fi

if [ "$dirty_policy" = "runtime" ] && [ "$runtime_dirty" != "0" ]; then
  printf '%s\n' "$runtime_status" >&2
  fail "formal source has runtime-affecting changes"
fi

if [ "$dirty_policy" = "strict" ] && [ "$dirty" != "0" ]; then
  printf '%s\n' "$status" >&2
  fail "formal source worktree is dirty"
fi

printf 'canonical main source gate passed\n'
