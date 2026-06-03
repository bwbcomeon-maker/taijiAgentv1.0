# Writing Agent Upstream Snapshot

- Source: https://github.com/xue1127/writing-agent
- Commit: a296f1cdfb88887b336f8dd2776257c18acab99d
- License: MIT, preserved in `LICENSE`
- Purpose: read-only upstream reference for the Hermes-native port under `custom-skills/writing-agent/`

This directory intentionally preserves the upstream project layout. Runtime
integration should use the Hermes skill package, not Claude Code `.claude`
agent loading.
