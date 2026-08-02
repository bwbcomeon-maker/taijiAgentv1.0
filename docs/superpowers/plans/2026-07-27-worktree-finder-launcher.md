# Worktree Finder Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Finder-visible Taiji desktop `.app` automatically launch a linked worktree in development mode with source-specific state isolation while preserving formal-mode behavior in the primary checkout.

**Architecture:** Keep the `.app` wrapper minimal and use it only to open the physically adjacent source `.command` through Terminal, because Finder-launched unsigned shell apps cannot reliably read sibling scripts under macOS-protected Documents folders. Put source-mode resolution, the provenance gate, and source-instance-specific XDG/runtime paths in the `.command` launcher.

**Tech Stack:** Bash, macOS app bundle/LaunchServices, Electron, Node.js built-in test runner, Git provenance scripts.

---

### Task 1: Lock the Finder launcher contract with failing tests

**Files:**
- Modify: `apps/taiji-desktop/tests/source-provenance-launcher.test.js`

- [x] **Step 1: Write a test for linked-worktree mode selection**

Add assertions that the `.app` launcher resolves `git-common-dir`, compares the canonical primary root with `REPO_DIR`, defaults linked worktrees to `development`, preserves explicit `TAIJI_SOURCE_MODE`, and invokes `scripts/check-clean-worktree.sh` with the resolved mode and source root.

- [x] **Step 2: Write a test for source-instance runtime isolation**

Add assertions that the generated runner exports source-specific `XDG_STATE_HOME`, `TAIJI_RUNTIME_HOME`, `TAIJI_WORKSPACE`, and `TAIJI_AGENT_TMP_DIR`, and that the launcher logs the resolved source mode and isolated paths.

- [x] **Step 3: Run the focused test and verify RED**

Run: `node --test apps/taiji-desktop/tests/source-provenance-launcher.test.js`

Expected: the two new tests fail because the current `.app` defaults to `formal`, does not run the source gate, and does not export isolated runtime paths.

### Task 2: Implement the minimal Finder launcher change

**Files:**
- Modify: `hermes-local-lab/启动太极Agent桌面端.app/Contents/MacOS/taiji-agent-desktop-launcher`
- Modify: `hermes-local-lab/启动太极Agent桌面端.command`

- [x] **Step 1: Delegate Finder launch to the adjacent source command**

Resolve the adjacent `启动太极Agent桌面端.command` from the `.app` physical path and open it with Terminal. Report a Finder dialog only when the adjacent launcher cannot be opened.

- [x] **Step 2: Resolve the default source mode**

If `TAIJI_SOURCE_MODE` is unset, use `formal` when `.git` is a directory and `development` when `.git` is a file. Keep an explicit caller-provided mode unchanged.

- [x] **Step 3: Run the existing source gate before dependency or Electron launch**

Invoke `scripts/check-clean-worktree.sh --mode "$TAIJI_SOURCE_MODE" --repo-root "$REPO_DIR" --source-root "$REPO_DIR"`; on failure, show the existing Finder error dialog and exit without starting Electron.

- [x] **Step 4: Define and forward isolated runtime paths**

Derive all paths below from `SOURCE_INSTANCE_ID`, create them, log them, and export them in the generated runner:

```text
XDG_STATE_HOME=$HOME/.local/state/taiji-agent/source-instances/$SOURCE_INSTANCE_ID
TAIJI_RUNTIME_HOME=$HOME/.local/share/taiji-agent/source-instances/$SOURCE_INSTANCE_ID/runtime-home
TAIJI_WORKSPACE=$HOME/.local/share/taiji-agent/source-instances/$SOURCE_INSTANCE_ID/workspace
TAIJI_AGENT_TMP_DIR=$HOME/.local/state/taiji-agent/source-instances/$SOURCE_INSTANCE_ID/tmp
```

- [x] **Step 5: Run focused tests and verify GREEN**

Run: `node --test apps/taiji-desktop/tests/source-provenance-launcher.test.js`

Expected: all launcher provenance tests pass.

### Task 3: Verify the real Finder-equivalent lifecycle and commit

**Files:**
- Verify: `hermes-local-lab/启动太极Agent桌面端.app/Contents/Info.plist`
- Verify: `hermes-local-lab/启动太极Agent桌面端.app/Contents/MacOS/taiji-agent-desktop-launcher`
- Verify: `apps/taiji-desktop/src/main.js`

- [x] **Step 1: Run static gates**

Run:

```bash
bash -n hermes-local-lab/启动太极Agent桌面端.app/Contents/MacOS/taiji-agent-desktop-launcher
plutil -lint hermes-local-lab/启动太极Agent桌面端.app/Contents/Info.plist
npm --prefix apps/taiji-desktop run check
git diff --check
```

Expected: every command exits zero.

- [x] **Step 2: Launch through the `.app` and verify provenance**

Launch only the current worktree app with `open -n`, then inspect its new launcher/desktop logs, Electron window, current process commands, selected ports, and health endpoints. Evidence must show the current worktree path, current commit, `development`, and the source-instance-specific state/runtime/workspace paths.

- [x] **Step 3: Verify the expert-team entry**

Use the existing Playwright/Electron smoke path or direct UI inspection to verify that the loaded application exposes “专家团” and the required-sections workflow from this worktree. Do not invoke a real Provider.

- [x] **Step 4: Close only this source instance and verify cleanup**

Quit the launched Electron instance normally and confirm only its isolated Agent/WebUI PID files and listeners are gone. Do not use broad process termination commands.

- [ ] **Step 5: Commit explicit paths**

Stage only the launcher, its test, and this plan; commit with `fix(desktop): launch linked worktrees from Finder`.
