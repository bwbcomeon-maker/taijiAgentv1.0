#!/bin/bash
set -euo pipefail

show_help() {
  printf '%s\n' 'Usage: scripts/release-check.sh --tag vX.Y.Z[-rc.N] --release-notes /absolute/path/to/release-notes.md'
  printf '%s\n' '       scripts/release-check.sh --tag vX.Y.Z --release-notes /absolute/path/to/release-notes.md --hotfix-from vA.B.C'
  printf '%s\n' 'Performs a read-only release metadata preflight, then runs scripts/verify.sh --full exactly once.'
  printf '%s\n' 'The explicit --hotfix-from mode accepts a clean non-main hotfix branch/worktree rooted at an annotated stable baseline tag.'
}

if [ "$#" -eq 1 ] && [ "$1" = "--help" ]; then
  show_help
  exit 0
fi

fail() {
  printf 'release preflight: FAIL: %s\n' "$1" >&2
  exit 1
}

TAG_NAME=""
RELEASE_NOTES=""
HOTFIX_FROM=""
TAG_SEEN=0
NOTES_SEEN=0
HOTFIX_SEEN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tag)
      [ "$TAG_SEEN" -eq 0 ] || fail "duplicate --tag"
      [ "$#" -ge 2 ] || fail "missing value for --tag"
      TAG_NAME="$2"
      TAG_SEEN=1
      shift 2
      ;;
    --release-notes)
      [ "$NOTES_SEEN" -eq 0 ] || fail "duplicate --release-notes"
      [ "$#" -ge 2 ] || fail "missing value for --release-notes"
      RELEASE_NOTES="$2"
      NOTES_SEEN=1
      shift 2
      ;;
    --hotfix-from)
      [ "$HOTFIX_SEEN" -eq 0 ] || fail "duplicate --hotfix-from"
      [ "$#" -ge 2 ] || fail "missing value for --hotfix-from"
      HOTFIX_FROM="$2"
      HOTFIX_SEEN=1
      shift 2
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[ "$TAG_SEEN" -eq 1 ] || fail "--tag is required"
[ "$NOTES_SEEN" -eq 1 ] || fail "--release-notes is required"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
VERIFY_SCRIPT="$ROOT/scripts/verify.sh"

unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
export GIT_OPTIONAL_LOCKS=0

command -v git >/dev/null 2>&1 || fail "git is unavailable"
REPOSITORY_ROOT="$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null)" || fail "script root is not a Git repository"
[ "$REPOSITORY_ROOT" = "$ROOT" ] || fail "script is not running from the repository root"

BRANCH_NAME="$(git -C "$ROOT" symbolic-ref --quiet --short HEAD 2>/dev/null)" || fail "HEAD is detached"
STATUS_OUTPUT="$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all)" || fail "unable to inspect repository status"

if [ "$HOTFIX_SEEN" -eq 1 ]; then
  [ -n "$HOTFIX_FROM" ] || fail "--hotfix-from must not be empty"
  [ -e "$ROOT/.git" ] && [ ! -L "$ROOT/.git" ] || fail "hotfix script root must be a real Git checkout"
  [ "$BRANCH_NAME" != "main" ] || fail "hotfix mode requires a non-main branch"
  [ -z "$STATUS_OUTPUT" ] || fail "hotfix worktree must be clean"
else
  [ -d "$ROOT/.git" ] && [ ! -L "$ROOT/.git" ] || fail "script root must be the primary repository checkout"
  [ "$BRANCH_NAME" = "main" ] || fail "primary repository branch must be main"
  [ -z "$STATUS_OUTPUT" ] || fail "primary repository must be clean"
fi

if ! [[ "$TAG_NAME" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[1-9][0-9]*)?$ ]]; then
  fail "tag must match vX.Y.Z or vX.Y.Z-rc.N with N greater than zero"
fi

if [ "$HOTFIX_SEEN" -eq 1 ]; then
  if ! [[ "$HOTFIX_FROM" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
    fail "hotfix baseline tag must be a stable semantic version"
  fi
  HOTFIX_BASE_MAJOR="${BASH_REMATCH[1]}"
  HOTFIX_BASE_MINOR="${BASH_REMATCH[2]}"
  HOTFIX_BASE_PATCH="${BASH_REMATCH[3]}"

  if ! [[ "$TAG_NAME" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
    fail "hotfix candidate tag must be a stable semantic version"
  fi
  HOTFIX_TARGET_MAJOR="${BASH_REMATCH[1]}"
  HOTFIX_TARGET_MINOR="${BASH_REMATCH[2]}"
  HOTFIX_TARGET_PATCH="${BASH_REMATCH[3]}"
  if [ "$HOTFIX_TARGET_MAJOR" != "$HOTFIX_BASE_MAJOR" ] || \
     [ "$HOTFIX_TARGET_MINOR" != "$HOTFIX_BASE_MINOR" ] || \
     [ "$HOTFIX_TARGET_PATCH" -le "$HOTFIX_BASE_PATCH" ]; then
    fail "hotfix candidate tag must be a newer patch version of the baseline"
  fi
fi

TAG_TYPE="$(git -C "$ROOT" cat-file -t "refs/tags/$TAG_NAME" 2>/dev/null)" || fail "tag does not exist"
[ "$TAG_TYPE" = "tag" ] || fail "tag must be annotated"
TAG_COMMIT="$(git -C "$ROOT" rev-parse --verify "refs/tags/$TAG_NAME^{}" 2>/dev/null)" || fail "tag target cannot be resolved"
HEAD_COMMIT="$(git -C "$ROOT" rev-parse --verify HEAD 2>/dev/null)" || fail "HEAD cannot be resolved"
[ "$TAG_COMMIT" = "$HEAD_COMMIT" ] || fail "tag must dereference to HEAD"

if [ "$HOTFIX_SEEN" -eq 1 ]; then
  HOTFIX_BASE_TYPE="$(git -C "$ROOT" cat-file -t "refs/tags/$HOTFIX_FROM" 2>/dev/null)" || fail "hotfix baseline tag does not exist"
  [ "$HOTFIX_BASE_TYPE" = "tag" ] || fail "hotfix baseline tag must be annotated"
  HOTFIX_BASE_COMMIT="$(git -C "$ROOT" rev-parse --verify "refs/tags/$HOTFIX_FROM^{}" 2>/dev/null)" || fail "hotfix baseline tag target cannot be resolved"
  git -C "$ROOT" merge-base --is-ancestor "$HOTFIX_BASE_COMMIT" "$HEAD_COMMIT" >/dev/null 2>&1 || fail "hotfix baseline tag must be an ancestor of HEAD"
fi

TAG_VERSION="${TAG_NAME#v}"
BASE_VERSION="${TAG_VERSION%%-rc.*}"
VERSION_FILE="$ROOT/VERSION"
DESKTOP_PACKAGE="$ROOT/apps/taiji-desktop/package.json"
[ -f "$VERSION_FILE" ] && [ ! -L "$VERSION_FILE" ] || fail "VERSION must be a regular non-symlink file"
[ -f "$DESKTOP_PACKAGE" ] && [ ! -L "$DESKTOP_PACKAGE" ] || fail "Desktop package.json must be a regular non-symlink file"
ROOT_VERSION="$(sed -n '1p' "$VERSION_FILE")" || fail "unable to read VERSION"
command -v python3 >/dev/null 2>&1 || fail "python3 is unavailable for Desktop version parsing"
DESKTOP_VERSION="$(python3 -c 'import json, sys; value = json.load(open(sys.argv[1], encoding="utf-8")).get("version"); isinstance(value, str) or sys.exit(2); print(value)' "$DESKTOP_PACKAGE")" || fail "unable to read Desktop package version"
[ "$ROOT_VERSION" = "$BASE_VERSION" ] || fail "tag base version does not match VERSION"
[ "$DESKTOP_VERSION" = "$BASE_VERSION" ] || fail "tag base version does not match Desktop package version"

case "$RELEASE_NOTES" in
  /*) ;;
  *) fail "release notes path must be absolute" ;;
esac
[ -f "$RELEASE_NOTES" ] && [ ! -L "$RELEASE_NOTES" ] || fail "release notes must be a regular non-symlink file"
grep -q '[^[:space:]]' "$RELEASE_NOTES" || fail "release notes must contain non-whitespace text"

[ -x "$VERIFY_SCRIPT" ] || fail "scripts/verify.sh must be executable"
if [ "$HOTFIX_SEEN" -eq 1 ]; then
  printf 'hotfix source preflight: PASS: %s -> %s on %s\n' "$HOTFIX_FROM" "$TAG_NAME" "$BRANCH_NAME"
  printf '%s\n' 'published-baseline and GitHub Release identity remain separate authorization/evidence gates.'
fi
printf '%s\n' 'release metadata preflight: PASS; invoking required full local verification.'
printf '%s\n' 'target-machine, offline, upgrade, rollback, packaging, signing, and publication gates remain independently unverified.'
exec "$VERIFY_SCRIPT" --full
