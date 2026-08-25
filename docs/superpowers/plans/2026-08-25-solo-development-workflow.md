# Taiji Agent Solo Development Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mandatory branch/worktree/PR workflow with a safe single-writer `main` workflow backed by local verification, Sol review, immutable version tags, and release-only gates.

**Architecture:** Keep `main` as the latest locally verified development line and move day-to-day assurance into two repository-owned local scripts: a change-safety scanner and a risk-classified verification dispatcher. Preserve GitHub Actions as asynchronous `main` evidence and preserve the existing Linux release evidence chain; add a separate read-only tag/release preflight without performing packaging, installation, tagging, or publication.

**Tech Stack:** Bash 3.2-compatible shell, Python 3.11 `unittest`, Git, GitHub Actions YAML, GitHub CLI/API, Node.js/npm, existing Taiji Agent/Desktop/DOCX/WebUI test runners.

---

## Fixed scope and non-negotiable invariants

- Work only in `/Users/bwb/Documents/工作/taiji-agentv1.0` on `main`; do not create a branch or worktree.
- One source-writing agent owns the working tree. Read-only Sol/spec/quality auditors may run in parallel but may not edit.
- Unless the user explicitly limits work to `local-only`, the default flow is: edit `main` → local verification → exact staging → Sol final review of the complete staged bytes → local commit → fetch and prove no remote-ahead divergence → normal push to `origin/main`, without another permission prompt. “按标准收尾” is an explicit shorthand for this default, not an additional authorization gate.
- Sol's pre-staging review is advisory. Before commit, Sol must review `git status --short`, the complete unstaged diff, cached name/status, cached `--check`, and the complete cached diff. Any staged-byte change invalidates the prior verdict and requires a fresh review.
- Never use force push, rebase of remote `main`, `git reset --hard`, `git clean`, broad staging, or deletion of user-owned work.
- `main` means the latest locally verified development version, not a stable public release.
- An RC is an immutable annotated `vX.Y.Z-rc.N` tag; a stable version is an immutable annotated `vX.Y.Z` tag; a GitHub Release binds one stable tag.
- Tag, Release, packaging, signing, installation, deployment, target-machine acceptance, upgrade, and rollback exercises remain separately authorized actions.
- Branch/worktree use is an approved exception for hotfixes, high-risk rewrites/upgrades, parallel source writers, or maintenance of multiple released versions.
- Do not modify business behavior, `VERSION`, the existing `scripts/taiji-release-check.sh` evidence chain, historical `docs/reviews`, `docs/handoffs`, `qa-evidence`, `reports`, old plans/specs, generated outputs, or vendor/upstream contribution rules.

### Task 1: Establish RED contracts and obtain Sol specification approval

**Files:**
- Create: `tests/test_solo_development_workflow.py`
- Read for contract binding: `VERSION`
- Read for contract binding: `apps/taiji-desktop/package.json`

- [ ] **Step 1: Add one focused contract suite before production changes**

Create `SoloDevelopmentWorkflowContracts` with these exact behavioral tests:

- `test_active_rules_define_direct_main_and_release_identity`: active root rules and README say locally verified development `main`, default verified commit/fetch/normal-push authority unless explicitly `local-only`, staged-byte-bound Sol final review, annotated RC/stable tags, and Release-to-stable-tag binding.
- `test_active_rules_have_no_mandatory_daily_branch_worktree_pr_or_ci`: the active root rules, README, lifecycle, new runbook, and two nested `AGENTS.md` files contain no daily mandatory branch/worktree/PR/required-CI rule.
- `test_old_pr_runbook_is_replaced_one_for_one`: `docs/runbooks/github-pr-ci-workflow.md` is absent and `docs/runbooks/solo-development-workflow.md` is present.
- `test_nested_agent_rules_defer_to_taiji_root_scope`: both nested `AGENTS.md` files explicitly state that Taiji root workflow/Git rules win in this monorepo scope; the Hermes Agent file no longer recommends `git reset --hard`.
- `test_change_safety_scans_staged_unstaged_and_untracked`: a temporary Git repository proves all three change states are scanned; staged and unstaged tracked-file deletions pass after path/status audit without attempting symlink, regular-file, or content reads on the absent path.
- `test_change_safety_keeps_index_and_worktree_views_separate`: staged ACMRT content is read from its fixed index blob even when the worktree is safe or deleted, while staged deletion does not hide a recreated untracked worktree secret.
- `test_change_safety_rejects_non_stage_zero_symlink_and_gitlink_index_entries`: unmerged entries and index modes `120000`/`160000` fail closed.
- `test_change_safety_uses_bounded_race_checked_reads_and_caps_entries`: worktree reads use `O_NOFOLLOW`, descriptor metadata checks and bounded reads; index reads pin the OID; manifest changes fail as `change-set-raced`; excessive change entries are rejected.
- `test_change_safety_ignores_ambient_git_locators`: ambient `GIT_DIR`, `GIT_WORK_TREE`, and `GIT_INDEX_FILE` point at a decoy repository, but the scanner still finds the target repository's unsafe change without printing its secret.
- `test_change_safety_handles_ignored_forced_special_and_size_boundaries`: ignored untracked files stay outside the change set, the same file staged with `git add -f` is rejected, untracked symlink/FIFO paths fail closed, and sparse files prove the 1 MiB per-file and 4 MiB aggregate limits.
- `test_change_safety_rejects_private_material_and_obvious_outputs`: runtime-assembled private-key/token samples and `.DS_Store`, `__pycache__`, `.pyc`, log, coverage, archive, installer, and package outputs return non-zero without printing secret values.
- `test_change_safety_allows_public_keys_and_test_fixtures`: `-----BEGIN PUBLIC KEY-----` and complete explicit fake/template values pass; `tests/fixtures/**` is not a blanket exemption and real secrets there still fail.
- `test_change_safety_allows_deleting_tracked_package_output`: deleting a tracked transient/package artifact passes because no such path remains in that staged or worktree view.
- `test_verify_help_plan_and_real_suite_contracts`: `/bin/bash --help` documents default classification, offline `--full`, read-only `--plan`, isolated `--browser-smoke`, and “all registered Taiji local gates”; executing `--plan --full` locks safety, baseline and every real cwd/argv/fixed test path, while a clean temporary repository falls back to safety, baseline, and root without mutation.
- `test_verify_offline_modes_are_bash32_and_do_not_mutate_environment`: default and `--full` reject installs, network/SSH, browser/background/service launches; static checks reject Bash-newer `declare -A`, `mapfile`, `readarray`, case conversion, and `|&`; `/bin/bash -n`, `--help`, and `--plan --full` all succeed; default classification uses the fail-closed `--local-changes` interface while `--path` remains compatible.
- `test_verify_baseline_is_safety_first_and_missing_files_fail_closed`: both diff checks and the fixed shell syntax list run after safety, and a missing registered baseline file stops execution explicitly.
- `test_verify_preflights_all_selected_suites_before_running_any_suite`: after safety and baseline, all selected suite dependencies are checked once before the first suite executes.
- `test_verify_reports_separate_python_and_agent_runner_resolution`: root/WebUI/browser select `TAIJI_AGENT_PYTHON`, then Agent `venv`, then `.venv`; the Agent suite calls canonical `scripts/run_tests.sh` and lets it resolve its own interpreter. Both resolutions are printed separately.
- `test_browser_smoke_missing_playwright_and_exit_passthrough`: a temporary repository and stub interpreter prove missing Playwright exits only with `3` without invoking smoke, while a real invocation of `browser_smoke.py` passes through exits `0`, `1`, and `2` exactly and never prints false PASS on failure.
- `test_release_check_requires_clean_main_annotated_tag_versions_notes_and_full_verify`: temporary repositories prove stable and RC positives invoke `--full` exactly once; every earlier negative leaves `verify.log` absent; failed full verification returns exactly `9` with one log line; `rc.0` is invalid; `--help` performs no preflight.
- `test_release_check_is_read_only`: snapshot the entire worktree including ignored paths by path/type/mode/symlink target/hash, require no before/after change, set `GIT_OPTIONAL_LOCKS=0`, and statically reject Git writes, network/GitHub, build/package/install/sign/publish, and filesystem-write commands.
- `test_existing_linux_release_gate_is_byte_for_byte_preserved`: require SHA256 `321ef6555afc8fb56500331b05e3778a690353864afcf76316e3ef9f0cd69b15` for `scripts/taiji-release-check.sh`.
- `test_main_validation_workflow_contract`: workflow name is `Main Validation`; triggers are `push` to `main` and `workflow_dispatch` only; job `CI Gate` and step `Require every selected job to pass` remain exact.
- `test_release_evidence_contract_uses_main_validation_push`: producer, validator, fixture, and producer tests require workflow `Main Validation`, event `push`, branch `main`, job `CI Gate`, and the existing required step.

All fixture subprocesses set `GIT_CONFIG_NOSYSTEM=1` and `GIT_CONFIG_GLOBAL=/dev/null`. Assemble synthetic secrets at runtime from fragments so the test source itself does not trip the real safety scanner. Use temporary repositories and real subprocess exit codes rather than mocks for Git state.

- [ ] **Step 2: Run the RED suite and confirm it fails for missing artifacts**

Run:

```bash
hermes-local-lab/sources/hermes-agent/venv/bin/python -m unittest -v tests.test_solo_development_workflow
```

Expected: `FAILED`; failures identify missing production scripts and the old workflow/evidence identity. Governance contracts and the byte-for-byte Linux gate contract may already pass. The summary must contain `errors=0`; no contract may error because of a typo, timeout, invalid fixture, or ambient Git configuration.

- [ ] **Step 3: Sol performs the first specification review**

Give Sol the user requirements, this plan, and the RED output. Sol must return `SPEC REVIEW: PASS` only after mapping every fixed invariant and all 18 tests above to an implementation task. If Sol returns any finding, update only this test/plan contract and rerun RED before continuing.

### Task 2: Replace active governance documents with the solo-main contract

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/runbooks/development-lifecycle.md`
- Delete: `docs/runbooks/github-pr-ci-workflow.md`
- Create: `docs/runbooks/solo-development-workflow.md`
- Modify: `hermes-local-lab/sources/hermes-agent/AGENTS.md`
- Modify: `hermes-local-lab/sources/hermes-webui/AGENTS.md`
- Test: `tests/test_solo_development_workflow.py`

- [ ] **Step 1: Rewrite the canonical lifecycle around six explicit states**

In `docs/runbooks/development-lifecycle.md`, define these states without carrying forward Draft PR/Ready PR/mandatory-CI transitions:

1. bound source and single-writer ownership;
2. local development on `main`;
3. RED/GREEN verification and optional Sol working-tree pre-review;
4. exact staging, Sol final review bound to the complete staged bytes, and local commit;
5. remote refresh, zero remote-ahead divergence, and normal push;
6. separately authorized RC/stable tag, Release, packaging, installation, and publication gates.

Retain the existing source/runtime/artifact/installed/released evidence distinctions, non-destructive rollback with `git revert`, explicit runtime/config provenance, target-environment limitations, and authorization boundaries. Define hotfix/high-risk/multi-version/parallel-writing branches as approved exceptions, not defaults.

- [ ] **Step 2: Replace the supporting runbook one-for-one**

Delete `docs/runbooks/github-pr-ci-workflow.md` and create `docs/runbooks/solo-development-workflow.md` with exact sections for:

- daily `main` workflow;
- single-writer Agent ownership and read-only parallel audit;
- local verification modes and clear prerequisite failures;
- Sol final pre-commit review over `git status --short`, complete unstaged `git diff`, `git diff --cached --name-status`, `git diff --cached --check`, and complete `git diff --cached`; any staged-byte change forces a fresh review;
- exact staging, Conventional Commit, fetch/divergence checks, normal push, and `git revert` recovery;
- RC/stable annotated tags and GitHub Release authority;
- exceptional branch/worktree hotfix flow;
- asynchronous `Main Validation` evidence and Linux candidate extra gate boundaries.

State explicitly that `scripts/taiji-release-check.sh` remains the extra Linux candidate DEB/signature/certification/CI evidence gate and is not part of daily `verify.sh`.

- [ ] **Step 3: Align root and nested entry points**

Update `AGENTS.md` and `README.md` to link only the new runbook and summarize the new default flow. A normal user request to modify, fix, or complete repository work authorizes and requires local verification, exact staging, staged Sol final review, commit, remote refresh, and normal `main` push unless the user explicitly limits the task to `local-only`; do not ask again before commit/push. “按标准收尾” is an explicit shorthand for the same default rather than an extra gate. Keep Tag/Release/package/install/deploy/persistent-service actions outside it.

At the start of both nested `AGENTS.md` files, add a Taiji-monorepo scope note: upstream component engineering guidance remains valid, but root `AGENTS.md` and `docs/runbooks/development-lifecycle.md` control Git topology, commit/push, review, CI, and release authority. Change “one logical change per PR” to “one logical change per Taiji commit,” and remove the stale-branch `git reset --hard`/squash-PR recipe while retaining its underlying warning against overwriting unrelated changes.

- [ ] **Step 4: Run the governance subset**

Run:

```bash
hermes-local-lab/sources/hermes-agent/venv/bin/python -m unittest -v \
  tests.test_solo_development_workflow.SoloDevelopmentWorkflowContracts.test_active_rules_define_direct_main_and_release_identity \
  tests.test_solo_development_workflow.SoloDevelopmentWorkflowContracts.test_active_rules_have_no_mandatory_daily_branch_worktree_pr_or_ci \
  tests.test_solo_development_workflow.SoloDevelopmentWorkflowContracts.test_old_pr_runbook_is_replaced_one_for_one \
  tests.test_solo_development_workflow.SoloDevelopmentWorkflowContracts.test_nested_agent_rules_defer_to_taiji_root_scope
```

Expected: `Ran 4 tests` and `OK`.

### Task 3: Add local change safety and the unified verification dispatcher

**Files:**
- Create: `scripts/check-local-change-safety.py`
- Create: `scripts/verify.sh`
- Modify: `scripts/classify-ci-scope.py`
- Modify: `tests/test_ci_scope_classifier.py`
- Test: `tests/test_solo_development_workflow.py`

- [ ] **Step 1: Implement the fail-closed local change scanner**

`scripts/check-local-change-safety.py` must derive the repository from its own location; remove ambient `GIT_DIR`, `GIT_WORK_TREE`, `GIT_COMMON_DIR`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, and `GIT_ALTERNATE_OBJECT_DIRECTORIES`; disable rename detection and keep two independent NUL-delimited views:

```text
git diff --no-renames --cached --name-only -z
git diff --no-renames --name-only -z
git ls-files --others --exclude-standard -z
```

For staged ACMRT paths, parse `git ls-files --stage -z`, reject every non-stage-zero/unmerged entry and modes `120000`/`160000`, pin the stage-zero mode/OID, query blob size first, then scan that exact blob. A staged deletion skips only the index view. For unstaged/untracked paths, scan the worktree view; an unstaged deletion skips only that view, so staged deletion plus a recreated untracked file is still scanned. A deletion of a tracked transient/package output passes when no view would retain or submit it.

Existing worktree paths are opened with `os.open(..., O_NOFOLLOW)`, checked with `fstat`, read in bounded chunks through at most `MAX_FILE_BYTES + 1`, then checked again for device, inode, size, and mtime. Re-enumerate the index and path/type/mode/metadata manifests after scanning; any change fails as `change-set-raced`. Cap the number of change entries as well as bytes. Symlinks, FIFOs, sockets, devices, and all other non-regular types fail closed. Ignored untracked paths remain outside the third Git query, but if the same ignored path is force-staged it enters the staged query and must be scanned/rejected.

Set a 1 MiB maximum per content view and a 4 MiB aggregate maximum, using worktree metadata and fixed-OID blob size before reads so sparse or oversized content cannot force allocation. Emit finding classes `file-size-limit` and `total-size-limit`. Report only path plus finding class, never matched content or secret values. Reject high-confidence private keys, credential assignments with real values, and obvious transient/package outputs. Only complete explicit forms such as `${NAME}`, `{{...}}`, `<placeholder>`, `self.*`, and clearly fake values are exempt; a brace/parenthesis character or a `tests/fixtures/**` path alone is never an exemption. Exit `0` with `local change safety: PASS`; exit non-zero with `local change safety: FAIL` and finding count.

- [ ] **Step 2: Make the existing classifier reusable for dirty local paths**

Keep `--path` compatible (including leading-hyphen filenames as `--path="$path"`), and add one official `--local-changes` interface. That interface derives its repository from the script, clears ambient Git locators, then sequentially queries staged and unstaged paths with `--no-renames`, untracked paths, and `git ls-files -u`; any Git failure or conflict exits non-zero. A clean result retains root fallback. Treat active governance paths including `AGENTS.md`, `docs/runbooks/development-lifecycle.md`, and `docs/runbooks/solo-development-workflow.md` as high risk. No “skip verification” label or downgrade option is added.

- [ ] **Step 3: Implement `scripts/verify.sh` with offline verification and a separate real browser smoke**

Use `/bin/bash` 3.2-compatible syntax, `set -euo pipefail`, repository-relative paths, and the classifier's `--local-changes` result. A classifier failure stops fail-closed; it never becomes clean fallback or implicit `--full`. Statically avoid `declare -A`, `mapfile`, `readarray`, `${value,,}`, `${value^^}`, `|&`, and other Bash-newer features. A clean change set conservatively falls back to safety, baseline, and root contracts. `--full` selects all registered Taiji local gates that can execute offline in the current prepared environment; do not describe this as the full upstream dependency test universe. `--plan` is a pure read-only modifier for default or `--full`: it prints interpreter/runner identities plus the exact 15 ordered `PLAN<TAB>label<TAB>cwd=...<TAB>argv=...` records without checking prerequisites or executing suites. `--browser-smoke` remains separate and invokes the real `hermes-local-lab/sources/hermes-webui/tests/browser_smoke.py`; `--help` exits without checking prerequisites.

The real suite registry must dispatch:

- safety first: selected root/WebUI/browser interpreter runs `scripts/check-local-change-safety.py` before every other selected gate;
- baseline next in every actual mode: run `git diff --check`, `git diff --cached --check`, then `/bin/bash -n` over the current CI Linux shell list plus `scripts/verify.sh` and, when present, `scripts/release-check.sh`; a registered missing file fails explicitly;
- root ownership: selected interpreter runs `-m unittest discover -s tests -p 'test_*.py'` from repository root;
- Desktop: `npm run check` and `node --test tests/*.test.js` in `apps/taiji-desktop`;
- DOCX: `npm test` in `hermes-local-lab/sources/docx-engine-v2`;
- Agent ownership: invoke canonical `scripts/run_tests.sh` from Agent cwd with the five existing Taiji focused regression paths from `.github/workflows/ci.yml` plus `tests/tools/test_public_chat_brand_guard.py`; do not inject the root/WebUI interpreter because the runner resolves Agent `venv`/`.venv` itself;
- WebUI ownership: `npm run lint:runtime` plus the exact focused pytest list from `.github/workflows/ci.yml`, using the selected root/WebUI/browser interpreter;
- branding: Agent runner receives both distinct same-named paths `tests/test_cli_skin_integration.py` and `tests/cli/test_cli_skin_integration.py`;
- bootstrap: Agent runner receives `tests/test_hermes_bootstrap.py`; selected root/WebUI/browser Python runs WebUI `tests/test_bootstrap_discover_agent.py`, `tests/test_bootstrap_dotenv.py`, `tests/test_bootstrap_foreground.py`, and `tests/test_bootstrap_python_selection.py`;
- coexistence: root discovery already owns `tests.test_canonical_account_home`; WebUI focused tests already own `tests/test_brand_privacy.py`; the extra coexistence command therefore runs only WebUI `tests/test_taiji_single_runtime_profiles.py`;
- browser smoke: only in `--browser-smoke`, run `hermes-local-lab/sources/hermes-webui/tests/browser_smoke.py`, which starts the WebUI with isolated temporary state and is responsible for browser/server cleanup.

For root, WebUI, safety, and browser smoke, select explicit `TAIJI_AGENT_PYTHON` first, then Agent `venv/bin/python`, then Agent `.venv/bin/python`, and print that identity. Print the Agent `scripts/run_tests.sh` identity separately as self-resolving. A selected Agent or WebUI module also selects the applicable branding/bootstrap/coexistence extras; `--full` selects every group. After safety and baseline, preflight all selected suite dependencies once before executing any suite. Require selected Python/Node/npm binaries, required `node_modules`, Agent runner, and WebUI eslint; print the exact missing prerequisite and exit non-zero. `--plan` and `--help` never check those suite prerequisites. Before browser smoke, prove selected Python can `import playwright`; only that missing prerequisite exits `3` and does not invoke smoke. If import succeeds, invoke the real smoke and propagate its `0`, `1`, or `2` unchanged, with no PASS marker for non-zero. Default and `--full` never install dependencies, access the network, launch a browser, package artifacts, SSH, or start a persistent service. `--browser-smoke` may launch only the isolated smoke's temporary browser/server and must propagate cleanup/result.

- [ ] **Step 4: Make scripts executable and prove GREEN**

Run:

```bash
chmod +x scripts/verify.sh scripts/check-local-change-safety.py
/bin/bash -n scripts/verify.sh
/bin/bash scripts/verify.sh --help
/bin/bash scripts/verify.sh --plan --full
python3 -m py_compile scripts/check-local-change-safety.py scripts/classify-ci-scope.py
hermes-local-lab/sources/hermes-agent/venv/bin/python -m unittest -v \
  tests.test_ci_scope_classifier \
  tests.test_solo_development_workflow
set +e
./scripts/verify.sh --browser-smoke
browser_smoke_status=$?
set -e
test "$browser_smoke_status" -eq 3
```

Expected: Bash/Python syntax checks and both plan/help calls exit `0`; `--plan --full` prints the exact safety-first registry without executing it; both unittest modules end `OK`; the browser-smoke attempt exits `3` with `browser smoke prerequisite missing: Python Playwright`, never prints `PASS`, leaves no server/browser process, and is recorded `未验证（缺 Python Playwright 前置）`. This known missing optional prerequisite does not block this workflow-governance commit or push.

### Task 4: Add a read-only tag and Release preflight without weakening Linux release gates

**Files:**
- Create: `scripts/release-check.sh`
- Test: `tests/test_solo_development_workflow.py`
- Preserve byte-for-byte: `scripts/taiji-release-check.sh`

- [ ] **Step 1: Implement the explicit release preflight interface**

Support exactly:

```bash
./scripts/release-check.sh --tag v1.0.2 --release-notes /absolute/path/to/v1.0.2-release-notes.md
./scripts/release-check.sh --help
```

`--help` must exit `0` before any repository, Git, version, notes, or interpreter prerequisite check. Reject other unknown/missing arguments. With `GIT_OPTIONAL_LOCKS=0`, require primary clean `main`; an annotated tag matching `^v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[1-9][0-9]*)?$` (therefore rejecting `rc.0`); tag dereference equal to `HEAD`; and a non-symlink regular Release Notes file containing non-whitespace text. Normalize a stable tag by stripping `v`; normalize an RC tag by stripping both `v` and its trailing `-rc.N`; the resulting base version must equal both root `VERSION` and `apps/taiji-desktop/package.json` version. Complete every preflight before invoking `scripts/verify.sh --full` exactly once as the final command, and propagate its exact exit code.

The script is read-only: it must not call Git write subcommands (`fetch`, `pull`, `push`, `tag`, `add`, `commit`, `reset`), `gh`, network clients, build/package/install/sign/publish tools, or filesystem-write commands (`touch`, `mkdir`, `cp`, `mv`, `rm`, `tee`). A positive test snapshots every worktree path, including ignored content, by type, mode, symlink target, and regular-file SHA256 before/after. Its final message must state that target-machine, offline, upgrade, rollback, packaging, signing, and publication gates remain independently unverified.

- [ ] **Step 2: Verify the positive and negative temporary-repository cases**

Run:

```bash
chmod +x scripts/release-check.sh
/bin/bash -n scripts/release-check.sh
hermes-local-lab/sources/hermes-agent/venv/bin/python -m unittest -v \
  tests.test_solo_development_workflow.SoloDevelopmentWorkflowContracts.test_release_check_requires_clean_main_annotated_tag_versions_notes_and_full_verify \
  tests.test_solo_development_workflow.SoloDevelopmentWorkflowContracts.test_release_check_is_read_only \
  tests.test_solo_development_workflow.SoloDevelopmentWorkflowContracts.test_existing_linux_release_gate_is_byte_for_byte_preserved
test "$(shasum -a 256 scripts/taiji-release-check.sh | awk '{print $1}')" = \
  '321ef6555afc8fb56500331b05e3778a690353864afcf76316e3ef9f0cd69b15'
git diff --exit-code -- scripts/taiji-release-check.sh
git diff --cached --exit-code -- scripts/taiji-release-check.sh
```

Expected: `Ran 3 tests` and `OK`; positives call `--full` once, preflight negatives never call it, a full-verification exit `9` is returned unchanged, the entire ignored-inclusive worktree snapshot is identical, and the existing Linux release-check has the exact bound SHA256 with neither unstaged nor staged diff. Do not run the new preflight against the live repository because this task does not authorize a tag or Release.

### Task 5: Convert CI to asynchronous main evidence and minimally adjust branch protection

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `scripts/produce-taiji-github-ci-evidence.py`
- Modify: `scripts/validate-taiji-release-evidence.py`
- Modify: `tests/github_ci_v2_fixture.py`
- Modify: `tests/test_github_ci_evidence_producer.py`
- Modify: `tests/test_ci_scope_classifier.py`
- Test: `tests/test_solo_development_workflow.py`

- [ ] **Step 1: Change only the workflow trigger/name contract**

Rename the workflow to `Main Validation`; remove `pull_request`; keep only `push` on `main` and `workflow_dispatch`. Remove pull-request label/base expressions and derive classification base from `github.event.before`, falling back to `HEAD^` for manual dispatch. Keep the job name `CI Gate`, step name `Require every selected job to pass`, path classifier, selected jobs, pinned Actions, and fail-closed aggregation intact.

Document that this run is asynchronous, non-required for daily push, and retained as formal release evidence when the release chain explicitly consumes it.

- [ ] **Step 2: Migrate the hard-coded formal evidence identity together**

Change workflow identity from `Pull Request CI` to `Main Validation` in the producer, validator, fixture, and producer tests. Preserve event `push`, branch `main`, job `CI Gate`, required step name, schema v2, raw response hashes, freshness, repository identity, and live revalidation. Keep negative tests rejecting `pull_request`, wrong branch, wrong workflow, wrong job, and wrong step.

- [ ] **Step 3: Run the CI/evidence contract suite**

Run:

```bash
hermes-local-lab/sources/hermes-agent/venv/bin/python -m unittest -v \
  tests.test_ci_scope_classifier \
  tests.test_github_ci_evidence_producer \
  tests.test_release_ci_v2_chain \
  tests.test_github_ci_live_revalidation \
  tests.test_solo_development_workflow
```

Expected: all modules end `OK`; assertions still prove `event=push`, `head_branch=main`, `CI Gate`, and `Require every selected job to pass`.

- [ ] **Step 4: Delete only the two obsolete GitHub branch-protection subresources**

Disable shell tracing, obtain the existing GitHub credential through `git credential fill`, retain the password only in shell process memory, and inject it as `GH_TOKEN` only into each `gh` process. Never print the credential, pass it as an argument, or write it to disk. Read and retain an in-memory summary of the current protection first; then delete only `required_status_checks` and `required_pull_request_reviews` through their child-resource endpoints:

```bash
set +x
credential_payload="$(printf 'protocol=https\nhost=github.com\n\n' | git credential fill)"
github_token="$(printf '%s\n' "$credential_payload" | awk -F= '$1=="password" {sub(/^[^=]*=/, ""); print; exit}')"
unset credential_payload
test -n "$github_token"
protection_api='repos/bwbcomeon-maker/taijiAgentv1.0/branches/main/protection'
before_invariants="$(GH_TOKEN="$github_token" gh api "$protection_api" --jq '{enforce_admins:.enforce_admins.enabled,required_linear_history:.required_linear_history.enabled,required_conversation_resolution:.required_conversation_resolution.enabled,allow_force_pushes:.allow_force_pushes.enabled,allow_deletions:.allow_deletions.enabled,block_creations:.block_creations.enabled,lock_branch:.lock_branch.enabled,allow_fork_syncing:.allow_fork_syncing.enabled,restrictions:.restrictions}')"
before_required="$(GH_TOKEN="$github_token" gh api "$protection_api" --jq '{required_status_checks,required_pull_request_reviews}')"
GH_TOKEN="$github_token" gh api --method DELETE "$protection_api/required_status_checks"
GH_TOKEN="$github_token" gh api --method DELETE "$protection_api/required_pull_request_reviews"
after_required="$(GH_TOKEN="$github_token" gh api "$protection_api" --jq '{required_status_checks,required_pull_request_reviews}')"
after_invariants="$(GH_TOKEN="$github_token" gh api "$protection_api" --jq '{enforce_admins:.enforce_admins.enabled,required_linear_history:.required_linear_history.enabled,required_conversation_resolution:.required_conversation_resolution.enabled,allow_force_pushes:.allow_force_pushes.enabled,allow_deletions:.allow_deletions.enabled,block_creations:.block_creations.enabled,lock_branch:.lock_branch.enabled,allow_fork_syncing:.allow_fork_syncing.enabled,restrictions:.restrictions}')"
test "$after_required" = '{"required_pull_request_reviews":null,"required_status_checks":null}'
test "$after_invariants" = "$before_invariants"
GH_TOKEN="$github_token" gh api "$protection_api" --jq '{required_status_checks,required_pull_request_reviews,enforce_admins:.enforce_admins.enabled,required_linear_history:.required_linear_history.enabled,required_conversation_resolution:.required_conversation_resolution.enabled,allow_force_pushes:.allow_force_pushes.enabled,allow_deletions:.allow_deletions.enabled}'
unset github_token before_invariants before_required after_required after_invariants
```

Expected: both required fields are `null`; the before/after invariant summaries compare byte-for-byte equal; the final object confirms `enforce_admins=true`, `required_linear_history=true`, the existing `required_conversation_resolution` value unchanged, `allow_force_pushes=false`, and `allow_deletions=false`. Do not use the whole-protection `PUT` endpoint and do not create JSON/jq temporary files. If credential lookup, GET, either DELETE, or final verification fails, stop all subsequent external mutation, report which child deletion (if any) succeeded, report the exact GitHub path `Settings → Branches → main protection rule → Require a pull request before merging / Require status checks to pass before merging`, and do not claim direct-push enablement.

### Task 6: Complete verification, exact staging, Sol staged review, commit, and normal push

**Files:**
- Stage only the files listed in Tasks 1–5
- Include: `docs/superpowers/plans/2026-08-25-solo-development-workflow.md`
- Include the full-verification fixture integrations: `tests/test_formal_build_driver_contract.py`, `tests/test_formal_build_test_evidence_contract.py`, `tests/test_linux_python_runtime_staging.py`, and `tests/test_taiji_kylin_packaging_skill.py`

Full verification exposed four test-only integration fixtures that had drifted from existing production contracts: the overflow child exited before kill/reap could be exercised on macOS without `waitid`; the formal runtime harness lacked the sealed Python launcher identity and held invocation stub; the dependency-profile fixture touched the real Agent `venv`; and the Skill packager fixture created `dist` inside the real source tree. Task 6 repairs only those isolated test fixtures. It does not modify their production drivers, setup scripts, runtime staging, or packager implementations.

The same run exposed one verification-dispatch prerequisite gap: Desktop `node_modules` could exist while the `acorn` module directly required by `packaging/linux/stage-desktop-js-closure.js` was absent. The selected-suite preflight now checks `apps/taiji-desktop/node_modules/acorn` before any suite executes, preserving fail-fast behavior without installing dependencies.

Full integration also showed that ambient `~/.hermes/hermes-agent` could win WebUI test discovery and omit repository-only modules such as `agent.provider_credentials`. Every selected WebUI pytest command, including bootstrap and coexistence extras, now binds `HERMES_WEBUI_AGENT_DIR` to the repository sibling Agent source and `HERMES_WEBUI_PYTHON` to the already selected root/WebUI Python. This keeps source and runtime identity deterministic without changing WebUI `conftest.py` or business code.

Final review found additional fail-closed gaps and closes them without changing product behavior: credential scanning now searches high-confidence tokens anywhere in each staged/worktree view, recognizes quoted JSON/YAML and exported assignments plus encrypted private-key headers, requires an explicit `TEST_ONLY`/`TEST-ONLY` marker instead of accepting arbitrary `TEST` prefixes, and still keeps Python dynamic expressions under AST control; the 1,024-entry cap counts the complete unique set including staged/unstaged deletions and conflicts; the generic release preflight rejects undeclared linked worktrees, while an explicit `--hotfix-from <published-stable-tag>` mode closes the approved Hotfix path by binding a clean non-main branch/worktree, an annotated stable baseline ancestor, and a newer annotated stable patch Tag at HEAD; and release governance separates the clean-main/version/notes/full-verification Tag candidate check from concrete Tag authorization, then runs `release-check.sh` only after the annotated Tag exists and before any separately authorized Tag push or GitHub Release. The script does not infer or fetch GitHub Release identity for the baseline; that evidence remains independent. No Tag, Release, publication, or other external action is performed by these fixes.

- [ ] **Step 1: Run complete local verification and repository hygiene gates**

Run:

```bash
./scripts/check-local-change-safety.py
./scripts/verify.sh --plan --full
./scripts/verify.sh --full
/bin/bash -n scripts/verify.sh scripts/release-check.sh scripts/taiji-release-check.sh
python3 -m py_compile \
  scripts/check-local-change-safety.py \
  scripts/classify-ci-scope.py \
  scripts/produce-taiji-github-ci-evidence.py \
  scripts/validate-taiji-release-evidence.py
git diff --check
test "$(shasum -a 256 scripts/taiji-release-check.sh | awk '{print $1}')" = \
  '321ef6555afc8fb56500331b05e3778a690353864afcf76316e3ef9f0cd69b15'
git diff --exit-code -- scripts/taiji-release-check.sh
git diff --cached --exit-code -- scripts/taiji-release-check.sh
git status --short
```

Expected: safety and offline `--full` verification print `PASS`; `--plan --full` prints all registered Taiji local gates in the exact safety-first order without executing them; syntax/compile/diff checks exit `0`; the existing Linux release gate retains its exact SHA256 with no staged or unstaged diff; status contains only planned paths. Target-machine, installation, upgrade, rollback, packaging, signing, Tag, and Release remain `未验证/未执行`.

- [ ] **Step 2: Attempt the real browser smoke and preserve its evidence boundary**

Run after the blocking offline gates, but before staging:

```bash
set +e
./scripts/verify.sh --browser-smoke
browser_smoke_status=$?
set -e
if [ "$browser_smoke_status" -eq 0 ]; then
  printf '%s\n' 'browser smoke: PASS'
elif [ "$browser_smoke_status" -eq 3 ]; then
  printf '%s\n' 'browser smoke: UNVERIFIED - Python Playwright prerequisite missing'
else
  printf '%s\n' "browser smoke: FAIL (exit $browser_smoke_status)" >&2
  exit "$browser_smoke_status"
fi
```

Expected in the current environment: exit `3`, explicit `browser smoke prerequisite missing: Python Playwright`, final status `UNVERIFIED`, no false pass marker from `verify.sh`, and no leftover smoke server/browser process. The known prerequisite-missing status is reported but does not block this commit/push; a real executed smoke failure (any other non-zero code) does block.

- [ ] **Step 3: Stage the exact task paths and audit the complete cached patch**

Run:

```bash
git add -- \
  .github/workflows/ci.yml \
  AGENTS.md \
  README.md \
  docs/runbooks/development-lifecycle.md \
  docs/runbooks/github-pr-ci-workflow.md \
  docs/runbooks/solo-development-workflow.md \
  docs/superpowers/plans/2026-08-25-solo-development-workflow.md \
  hermes-local-lab/sources/hermes-agent/AGENTS.md \
  hermes-local-lab/sources/hermes-webui/AGENTS.md \
  scripts/check-local-change-safety.py \
  scripts/classify-ci-scope.py \
  scripts/produce-taiji-github-ci-evidence.py \
  scripts/release-check.sh \
  scripts/validate-taiji-release-evidence.py \
  scripts/verify.sh \
  tests/github_ci_v2_fixture.py \
  tests/test_ci_scope_classifier.py \
  tests/test_formal_build_driver_contract.py \
  tests/test_formal_build_test_evidence_contract.py \
  tests/test_github_ci_evidence_producer.py \
  tests/test_linux_python_runtime_staging.py \
  tests/test_solo_development_workflow.py \
  tests/test_taiji_kylin_packaging_skill.py
git status --short
git diff
git diff --cached --name-status
git diff --cached --check
git diff --cached
./scripts/check-local-change-safety.py
test "$(shasum -a 256 scripts/taiji-release-check.sh | awk '{print $1}')" = \
  '321ef6555afc8fb56500331b05e3778a690353864afcf76316e3ef9f0cd69b15'
git diff --exit-code -- scripts/taiji-release-check.sh
git diff --cached --exit-code -- scripts/taiji-release-check.sh
```

Expected: exactly the listed paths appear, with one deletion for the old runbook and one addition for the new runbook; no `VERSION`, business source, historical evidence, report, generated output, or `scripts/taiji-release-check.sh` change appears.

- [ ] **Step 4: Sol reviews the exact staged candidate**

Provide Sol with the fixed invariants, verification logs, browser-smoke status, and the outputs of `git status --short`, complete unstaged `git diff`, `git diff --cached --name-status`, `git diff --cached --check`, and the complete `git diff --cached`. Require both `SPEC REVIEW: PASS` and `QUALITY REVIEW: PASS` against these exact staged bytes. Quality review must check unrelated edits, secret/artifact leakage, real test execution, document/code agreement, RC/stable normalization, tracked-deletion handling, ignored/special/size safety cases, preserved Linux evidence hash/diffs, Bash 3.2 portability, interpreter ownership, and exact commit scope.

If any review finding requires an edit, make the smallest fix, rerun affected tests plus safety, stage only the affected planned path, rerun all five status/unstaged/cached views plus both Linux-gate diff/hash checks, and submit the new staged candidate to Sol again. Any staged-byte change invalidates both earlier verdicts. Do not commit until the latest staged bytes receive both PASS verdicts.

- [ ] **Step 5: Commit once, refresh remote, prove zero remote-ahead divergence, and push normally**

Run:

```bash
git commit -m "chore: simplify solo development and release workflow"
git fetch origin main
git rev-list --left-right --count HEAD...origin/main
git merge-base --is-ancestor origin/main HEAD
git push origin main
git fetch origin main
git rev-list --left-right --count HEAD...origin/main
git status --short
```

Expected before push: `<local-ahead> 0` with a positive local-ahead count and ancestor check exit `0`; any remote-ahead count stops the push without rebase/reset/force. Expected after push: `0 0` and empty status. Record `git rev-parse HEAD` as the final commit.

- [ ] **Step 6: Record asynchronous GitHub evidence without turning it into a daily gate**

Run:

```bash
FINAL_COMMIT="$(git rev-parse HEAD)"
gh run list --workflow ci.yml --branch main --event push --limit 1 \
  --json databaseId,status,conclusion,headSha,name,workflowName,url
```

Expected: the newest run is `Main Validation` with `headSha` equal to `FINAL_COMMIT`; `queued` or `in_progress` is reported as asynchronous current status, not a push failure. A future formal release must still obtain a successful bound run plus all separate release/target gates.

Final report must separate `已实时验证`, `未实时验证`, and `未执行`, and include modified files, deleted old flow, new default flow, verification results, branch-protection result, final commit, push result, asynchronous workflow status, and all independently authorized remaining gates.
