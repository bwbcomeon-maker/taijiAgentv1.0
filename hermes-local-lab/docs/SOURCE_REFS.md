# Source References

This monorepo stores the Hermes Agent and Hermes WebUI source trees directly, not as Git submodules. That keeps a normal `git clone` usable without a separate submodule fetch.

| Component | Upstream | Local branch before monorepo import | Local commit imported |
| --- | --- | --- | --- |
| Hermes Agent | https://github.com/NousResearch/hermes-agent.git | `taiji-local-agent` | `2a11a8c69 feat: support lab workspace runtime resolution` |
| Hermes WebUI | https://github.com/nesquena/hermes-webui.git | `taiji-local-webui` | `a5b835c1 feat: add writing workflow and model config` |

The previous nested `.git` directories were moved to `.local-git-metadata/` on
this machine before creating the root repository. They were never a runtime
dependency.

## 2026-07-21 source-completeness closure

A final import audit found 193 upstream-versioned files that still existed on
this machine but had never entered the parent repository:

- 27 Hermes Agent files: concept-diagram examples, achievement dashboard
  assets, the P5.js export pipeline, bundled fonts, and `userStories.json`;
- 166 Hermes WebUI PR/UX evidence assets under `docs/`.

The cause was a Git semantic trap: a nested repository keeps files that were
already tracked even when they match `.gitignore`, while the same files become
new untracked files after a monorepo import and can then be silently omitted by
parent or nested ignore rules. All 193 files were initially verified against
the recorded nested `HEAD` blob IDs and force-added by exact path. Six Markdown
diagram examples then received a mechanical trailing-whitespace cleanup so the
parent commit passes `git diff --check`; their final paths, executable modes,
and content are locked by the repository inventory test.

Before the obsolete metadata was removed, both complete source trees passed:

```bash
python3 scripts/check-imported-source-tree.py \
  --repo-root /path/to/taiji-agentv1.0 \
  --source-prefix hermes-local-lab/sources/hermes-agent \
  --source-git-dir .local-git-metadata/hermes-agent.git

python3 scripts/check-imported-source-tree.py \
  --repo-root /path/to/taiji-agentv1.0 \
  --source-prefix hermes-local-lab/sources/hermes-webui \
  --source-git-dir .local-git-metadata/hermes-webui.git
```

The checks covered 4,158 Agent entries and 1,100 WebUI entries with zero
missing physical paths and zero parent-untracked paths.

The raw metadata, including reflog-only and unreachable objects that a normal
Git bundle would omit, is retained outside the repository at:

`/Users/bwb/Documents/工作/taiji-agentv1.0-backups/20260720-main-consolidation-final/evidence/nested-git-metadata-pre-cleanup-20260721.tar.gz`

SHA-256:
`fdaaac3a3767e81d7100a6604db5c508a6b133a5c58d267e9d115c1d4bab0565`

The archive passed gzip/tar validation, was extracted into an isolated
directory, and both restored Git directories passed `git fsck --full`. The
active `.local-git-metadata/` directory was then removed.

For any future source refresh, run the same gate with
`--require-content-match` immediately after staging and before deleting the
source Git metadata. Then validate the resulting commit from `git archive` or
a fresh clone; `git add -A` alone is not an import-completeness gate.
