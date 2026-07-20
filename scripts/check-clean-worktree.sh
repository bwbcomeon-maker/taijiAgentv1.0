#!/usr/bin/env bash
set -euo pipefail

# Repository-selection variables can make `git -C <declared root>` inspect a
# different checkout.  Provenance checks must derive state from the explicit
# path, never from ambient shell state.
unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE
unset GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES

mode="formal"
repo_root_input=""
source_root_input=""

usage() {
  cat <<'EOF'
Usage:
  check-clean-worktree.sh [--mode formal|development]
                          [--repo-root PATH]
                          [--source-root PATH]

formal (default):
  Require a clean local main checked out in the repository's primary worktree.

development:
  Explicitly allow a branch or linked worktree, including local changes, while
  still requiring the declared source root to match that Git worktree.
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
    --source-root)
      [ "$#" -ge 2 ] || fail "--source-root requires a value"
      source_root_input="$2"
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

command -v git >/dev/null 2>&1 || fail "git is required"

if [ -z "$repo_root_input" ]; then
  repo_root_input="$(git rev-parse --show-toplevel 2>/dev/null)" \
    || fail "not inside a Git worktree"
fi
repo_root="$(physical_dir "$repo_root_input")"

git_top_raw="$(git -C "$repo_root" rev-parse --show-toplevel 2>/dev/null)" \
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

common_dir_raw="$(git -C "$git_top" rev-parse --git-common-dir 2>/dev/null)" \
  || fail "cannot resolve git common dir"
case "$common_dir_raw" in
  /*) common_dir_candidate="$common_dir_raw" ;;
  *) common_dir_candidate="$git_top/$common_dir_raw" ;;
esac
common_dir="$(physical_dir "$common_dir_candidate")"
canonical_root="$(physical_dir "$common_dir/..")"

if [ "$git_top" = "$canonical_root" ]; then
  worktree_kind="primary"
else
  worktree_kind="linked"
fi

branch="$(git -C "$git_top" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
head_commit="$(git -C "$git_top" rev-parse HEAD 2>/dev/null)" \
  || fail "cannot resolve HEAD"
status="$(git -C "$git_top" status --porcelain=v1 --untracked-files=all)"
if [ -n "$status" ]; then
  dirty="1"
else
  dirty="0"
fi

printf 'mode: %s\n' "$mode"
printf 'source_root: %s\n' "$source_root"
printf 'repo: %s\n' "$git_top"
printf 'canonical_root: %s\n' "$canonical_root"
printf 'git_common_dir: %s\n' "$common_dir"
printf 'worktree: %s\n' "$worktree_kind"
printf 'branch: %s\n' "${branch:-detached}"
printf 'head: %s\n' "$head_commit"
printf 'dirty: %s\n' "$dirty"

if [ "$mode" = "development" ]; then
  printf 'development source isolation gate passed\n'
  exit 0
fi

[ "$worktree_kind" = "primary" ] \
  || fail "formal source must use the primary worktree: current=$git_top canonical=$canonical_root"
[ "$branch" = "main" ] \
  || fail "formal source must be branch main: current=${branch:-detached}"

main_commit="$(git -C "$git_top" rev-parse refs/heads/main 2>/dev/null)" \
  || fail "local refs/heads/main does not exist"
[ "$head_commit" = "$main_commit" ] \
  || fail "formal source HEAD does not match local main: head=$head_commit main=$main_commit"

if [ "$dirty" != "0" ]; then
  printf '%s\n' "$status" >&2
  fail "formal source worktree is dirty"
fi

printf 'canonical main source gate passed\n'
