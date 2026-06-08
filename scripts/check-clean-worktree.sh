#!/usr/bin/env bash
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"
cd "$repo_root"

branch="$(git branch --show-current 2>/dev/null || true)"
upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
tracked_changes="$(git status --porcelain=v1 -uno | wc -l | tr -d ' ')"
untracked_visible="$(git ls-files --others --exclude-standard | wc -l | tr -d ' ')"

echo "repo: $repo_root"
echo "branch: ${branch:-detached}"
echo "upstream: ${upstream:-none}"
echo "tracked_changes: $tracked_changes"
echo "untracked_visible: $untracked_visible"

if [ "$tracked_changes" != "0" ] || [ "$untracked_visible" != "0" ]; then
  echo "status:"
  git status --short
  exit 1
fi

echo "worktree clean"
