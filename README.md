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

## GitHub Workflow

`main` is the only formal runtime, packaging and release source. Day-to-day
changes use a short-lived `codex/*` branch in an isolated worktree:

1. Create a worktree/branch from current `main`.
2. Implement and test locally, then create one clear local commit.
3. Push the branch and open a pull request; do not push development commits
   directly to `main`.
4. GitHub CI selects the smallest safe test scope from changed paths. Add the
   `full-ci` label to force every automated suite.
5. Merge only after the required `CI Gate` check passes, then fast-forward the
   formal local `main` and verify it is clean.

Documentation-only changes use the fast lane. Normal code changes run root
contracts plus affected modules. Workflow, dependency, security, provider,
license, migration, packaging and release changes run every automated suite.
Real Electron, OAuth/provider, WPS and Kylin/UOS target-machine checks remain
release gates because shared GitHub runners cannot reproduce those environments.

See [the solo-development runbook](docs/runbooks/github-pr-ci-workflow.md) for
the exact commands and recovery procedure.
