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

For day-to-day work:

```bash
git status
git add <changed-files>
git commit -m "describe the change"
git push
```

To roll back after pushing:

```bash
git log --oneline
git checkout <commit>
```

Use `git revert <commit>` when you want to create a new commit that safely undoes a previous change on GitHub.
