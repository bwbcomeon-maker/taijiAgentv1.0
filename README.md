# Taiji Agent v1.0 Local Lab

This repository contains a local source-based Hermes Agent and Hermes WebUI lab used for Taiji Agent experimentation.

## Contents

- `hermes-local-lab/sources/hermes-agent`: Hermes Agent source snapshot with local Taiji runtime fixes.
- `hermes-local-lab/sources/hermes-webui`: Hermes WebUI source snapshot with writing workflow and model configuration changes.
- `hermes-local-lab/scripts`: local setup, start, stop, and health-check scripts.
- `hermes-local-lab/custom-skills`: custom writing-agent skills used by the lab.
- `hermes-local-lab/vendor`: small upstream reference snapshots used during local integration.
- `hermes-local-lab/docs`: environment notes and run reports.

Runtime state, logs, API keys, generated workspace files, virtualenvs, and caches are intentionally not committed.

## Quick Start

Install `uv` and Python 3.11, then run:

```bash
cd hermes-local-lab
./scripts/setup-local.sh
./scripts/start-agent.sh
./scripts/start-webui.sh
./scripts/health-check.sh
```

Default local addresses:

- Hermes Agent API: `http://127.0.0.1:18642`
- Hermes WebUI: `http://127.0.0.1:18787`

To configure real model providers, copy `hermes-local-lab/.env.example` to `hermes-local-lab/.env` and fill in only the keys you need. Never commit `.env`.

## Development and Release Identity

Daily work uses direct development on `main`. In this repository, `main` is the
daily development line; verification must bind a specific commit or identified
working-tree content. The branch name alone proves no validation, and `main` is
not equivalent to a stable release. One coordinated writer owns the shared
working tree and Git index across tasks while Sol and other Agents may
audit read-only. Unless the user explicitly limits work to `local-only`, the
default closeout is local verification, exact staging, final Sol review of the
complete cached patch, one clear commit, remote refresh/divergence proof, and a
normal push to `origin/main` without another permission prompt. Any staged-byte
change invalidates that review and requires a fresh review before commit.

Analysis, review, or planning alone does not authorize edits or commit/push.
Current task boundaries and project rules take precedence over historical memory.
Use the repository-owned Skills linked from `AGENTS.md`, not same-named global
copies. If the required Sol review is unavailable or blocking issues remain at
the remediation limit, stop before committing.

`Main Validation` runs asynchronously after a push to `main`. It is non-required
supplemental evidence for daily work, not permission to skip local verification.
Real Electron, OAuth/provider, WPS/Word, packaging, signing, installation, and
Kylin/UOS target-machine checks remain separate gates.

Release identity is immutable and explicit:

- RC: annotated tag `vX.Y.Z-rc.N`.
- Stable: annotated tag `vX.Y.Z`.
- GitHub Release: bound to one formal stable tag; it does not redefine `main`.

Creating or moving a tag, creating a GitHub Release, packaging, installing,
deploying, or publishing requires separate authorization. See the canonical
[development lifecycle](docs/runbooks/development-lifecycle.md) and the
[solo-development runbook](docs/runbooks/solo-development-workflow.md).
