# Kylin amd64 Golden Candidate and Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` to implement this plan task-by-task. The implementation worker must use `test-driven-development`; reviews run in the order specification compliance, then code quality.

**Goal:** Prevent a lost long-running SSH stream from starting Kylin `00` twice, merge the minimal P1 into formal `main`, then build exactly one identity-bound candidate DEB and, only after a separate approval, accept that same DEB on a clean Kylin snapshot.

**Architecture:** Keep `scripts/taiji-linux-golden-orchestrator.py` as a plan/checkpoint-only coordinator. Add one Linux-specific controller helper, `packaging/linux/kylin_remote_build.py`, that launches the frozen `00` boundary only when the attempt result is absent, persists a strict remote result atomically, polls `RUNNING` read-only every 300 seconds, and treats `FAILED`/`SUCCEEDED` as terminal without rebuilding. Do not change `00`, `99`, `01`, cross-platform core, Windows adapters, `FETCH_PENDING`, or release/signing contracts.

**Tech Stack:** Python 3.8-compatible standard library, fixed `/usr/bin/ssh` argv, POSIX Bash on the remote Kylin host, `unittest`, existing Taiji golden-orchestrator JSON/state contracts.

---

## Boundaries and evidence

- Repository: `/Users/bwb/Documents/工作/taiji-agentv1.0`
- Branch: `codex/kylin-amd64-golden-delivery`
- Worktree: `/Users/bwb/Documents/工作/taiji-agentv1.0/.worktrees/kylin-amd64-golden-delivery`
- Verified baseline: `8244cc32d8b7cc0004e65baa7ae13a2b9662e9ac`; existing golden-orchestrator tests `36/36` passed in `197.992s`.
- Current evidence ceiling: local source and simulated contract only; no current trio, candidate DEB, real SSH build, offline rehearsal, or target acceptance.
- Updated closeout authorization cancels PR and GitHub CI. It permits local commit, local full gates, formal-root `main` inclusion/reverification, and then one non-force fast-forward push of the verified commit to `origin/main`. If fetched `origin/main` is not an ancestor of that exact verified commit, or any force push/history overwrite would be needed, stop and report. This still does not authorize SSH, SCP, sudo, network dependencies, `99/00/01`, installation, signing, Tag, Release, or publication.
- Scope-changing blocker recorded in `output/kylin-golden/BLOCKED.md`: root `VERSION=1.0.2` while frozen operator text still says `1.0.0`. This plan must not edit that out-of-scope file; formal `99` stops until the blocker is independently resolved or explicitly authorized.

## File ownership

- Create: `packaging/linux/kylin_remote_build.py` — the only remote-build result parser, launcher, poller, and fixed remote worker renderer.
- Modify: `scripts/taiji-linux-golden-orchestrator.py` — replace only the long inline remote `00` command with the helper command; bind the helper in `SOURCE_TRUST_PATHS`.
- Create: `tests/test_kylin_remote_build.py` — focused behavior and hostile-result tests.
- Modify: `tests/test_linux_golden_orchestrator.py` — prove the plan uses the helper and no longer embeds a repeatable long `00` SSH command.
- Modify: `tests/python38_linux_packaging_gate.py` — compile/import the new helper under the existing Python 3.8 grammar/runtime gate.
- Modify: `docs/runbooks/taiji-kylin-uos-offline-delivery.md` — document result status/recovery semantics and evidence ceiling.
- Do not modify any other product, pipeline, platform, installer, acceptance, release, or publication file.

### Task 0: Identity, authority, and baseline

- [x] Verify physical repository, common Git directory, branch, HEAD, and clean state.
- [x] Read `AGENTS.md`, lifecycle runbook, Kylin delivery runbook, declarative packaging interface, packaging Skill, golden orchestrator, and focused tests.
- [x] Run the packaging Skill doctor only against the explicit repository; result was `compatibility_status=pass`, with `prepare-builder-input` still approval-required.
- [x] Create and verify the specified branch/worktree.
- [x] Run the existing focused baseline without SSH or packaging.

### Task 1: RED — specify one-attempt remote result behavior

**Files:**

- Create: `tests/test_kylin_remote_build.py`
- Modify: `tests/test_linux_golden_orchestrator.py`
- Modify: `tests/python38_linux_packaging_gate.py`

- [x] **Step 1: Write strict result-shape tests**

  Define test fixtures with exactly this required payload shape:

  ```python
  {
      "schema": "taiji-kylin-remote-build-result/v1",
      "source_commit": "a" * 40,
      "remote_attempt_id": "b" * 16,
      "input": {
          "archive": {"basename": "taijiagent-制包机输入-<commit>.tar.gz", "bytes": 1, "sha256": "c" * 64},
          "manifest": {"basename": "taijiagent-制包机输入-<commit>.manifest.json", "bytes": 1, "sha256": "d" * 64},
          "checksum": {"basename": "taijiagent-制包机输入-<commit>.tar.gz.sha256", "bytes": 1, "sha256": "e" * 64},
      },
      "status": "RUNNING",  # RUNNING | FAILED | SUCCEEDED
      "phase": "00",        # 00 | review
      "exit_code": None,     # None while RUNNING; integer when terminal
      "started_at": "2026-08-24T00:00:00Z",
      "finished_at": None,   # None while RUNNING; UTC timestamp when terminal
      "remote_log": {
          "basename": "02-remote-build.log",
          "bytes": 0,
          "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      },
  }
  ```

  Tests must reject duplicate keys, invalid UTF-8/JSON, missing or extra required keys, wrong types, unknown status/phase, invalid timestamps, invalid input/log basename/size/SHA256, terminal status without exit/finish, `RUNNING` with terminal fields, and mismatched `source_commit`, `remote_attempt_id`, or exact trio identity.

- [x] **Step 2: Write state-decision tests**

  The wished-for API is:

  ```python
  decide_remote_build_action(None) == "START"
  decide_remote_build_action(running) == "POLL"
  decide_remote_build_action(failed) == "FAIL"
  decide_remote_build_action(succeeded) == "CONTINUE"
  ```

  Prove that only `None` can select `START`; malformed or identity-mismatched bytes raise `RemoteBuildError` and never degrade to `START`.

- [x] **Step 3: Write the disconnect/no-repeat regression**

  Use a temporary fake build entry as the external boundary, not a mock of the parser/decision code. The first launch must atomically publish `RUNNING` before the fake build is started. Re-entering the controller with the same attempt after a simulated controller-stream loss must observe `RUNNING` or `SUCCEEDED`; the fake build invocation counter must remain exactly `1`.

- [x] **Step 4: Write terminal-state behavior tests**

  - `RUNNING`: query only, wait using the fixed `POLL_INTERVAL_SECONDS == 300`, never launch.
  - `FAILED`: exit nonzero, preserve result/log, never launch.
  - `SUCCEEDED`: exit zero so the existing retrieve-review/retrieve-log commands continue, never launch.
  - Remote result path that is a symlink, hardlink, wrong owner/mode, oversized, missing after a reported launch, or otherwise unsafe: fail closed without launching.

- [x] **Step 5: Write orchestrator integration tests**

  Assert the `remote_build` plan:

  ```python
  helper = next(c for c in plan["commands"] if c["label"] == "run or resume frozen 00 builder")
  assert helper["argv"][:4] == ["/usr/bin/python3", "-I", "-B", expected_helper]
  assert "kylin" in helper["argv"]
  assert "300" not in helper["argv"]  # interval is a fixed helper contract, not operator-tunable
  assert "00_制包机_生成离线交付包.sh" not in helper["argv"]
  ```

  Also assert `packaging/linux/kylin_remote_build.py` is in `SOURCE_TRUST_PATHS`, and the Python 3.8 gate names the helper.

- [x] **Step 6: Run RED and capture the expected failure**

  Run:

  ```bash
  /usr/bin/python3 -I -B tests/test_kylin_remote_build.py
  /usr/bin/python3 -I -B tests/test_linux_golden_orchestrator.py
  ```

  Expected: the new focused test fails because `packaging/linux/kylin_remote_build.py` and helper-backed plan behavior do not exist. The existing test file may also fail only at the newly tightened remote-build assertions. Any unrelated baseline failure stops implementation.

### Task 2: GREEN — implement the dedicated result controller

**Files:**

- Create: `packaging/linux/kylin_remote_build.py`
- Modify: `scripts/taiji-linux-golden-orchestrator.py`

- [x] **Step 1: Implement the smallest public contract**

  Provide these names, keeping all implementation in this single dedicated module:

  ```python
  RESULT_SCHEMA = "taiji-kylin-remote-build-result/v1"
  POLL_INTERVAL_SECONDS = 300

  class RemoteBuildError(RuntimeError):
      pass

  def load_remote_build_result(raw, *, source_commit, remote_attempt_id): ...
  def decide_remote_build_action(result): ...
  def main(argv=None): ...
  ```

  Use only the Python standard library. Use duplicate-key rejection and exact-key/type validation. Never interpret unreadable, malformed, or wrong-identity state as missing.

- [x] **Step 2: Implement one safe remote query**

  The helper invokes fixed `/usr/bin/ssh` argv with `BatchMode=yes` and `ConnectTimeout=5`, plus the existing replacement environment and optional validated `SSH_AUTH_SOCK`. The remote read command must distinguish absent from unsafe and present, require a current-user `0600` single-link regular file inside the fixed attempt directory, and print only the bounded result bytes. It must not scan any other directory.

- [x] **Step 3: Implement one no-clobber launch and atomic terminal updates**

  Before starting `00`, the remote launcher must:

  1. create the fixed log as a regular owner-only file;
  2. detach one fixed wrapper that atomically competes to publish a complete `RUNNING` result at `<remote_dir>/remote-build-result.json` without replacing an existing result;
  3. only the wrapper that published `RUNNING` may immediately `exec` the fixed worker, so controller transport loss cannot trigger a second start;
  4. execute the existing checksum, extract, frozen `00`, and review preparation sequence unchanged;
  5. atomically replace the result with `FAILED` or `SUCCEEDED`, recording final phase, exit code, finish time, and the log basename/bytes/SHA256.

  The worker must preserve the real failing exit code and must not use `|| true`, weaken `set -Eeuo pipefail`, patch remote source, delete an existing result, or create a generic retry/queue framework.

- [x] **Step 4: Implement fixed reconciliation**

  Pseudocode is intentionally finite:

  ```python
  while True:
      result = query()
      action = decide_remote_build_action(result)
      if action == "START":
          launch_once()       # launcher returns only after RUNNING is durable
          continue
      if action == "POLL":
          time.sleep(300)
          continue
      if action == "CONTINUE":
          return 0
      if action == "FAIL":
          return nonzero
  ```

  A failed SSH query is an error, not `None`. After any launch attempt, absence of the durable `RUNNING` record is an unknown/unsafe state and must stop; it must not call `launch_once()` again in the same or resumed controller path.

- [x] **Step 5: Integrate only the long builder command**

  In `scripts/taiji-linux-golden-orchestrator.py`:

  - add `packaging/linux/kylin_remote_build.py` to `SOURCE_TRUST_PATHS`;
  - keep the existing commit-specific directory creation, exact trio transfer, review retrieval, and remote-log retrieval commands;
  - replace only `run frozen 00 builder and prepare immutable review tree` with `run or resume frozen 00 builder`, whose argv is the trusted Python helper plus exact host, account home, remote directory, source commit, attempt id, input basenames, and result basename;
  - remove the obsolete inline `_remote_script()` only if no caller remains;
  - preserve `env_mode=replace`, SSH passthrough/sensitive handling, approval boundary, log path, stage order, candidate binding, and checkpoint semantics.

- [x] **Step 6: Run focused GREEN**

  Run the exact RED commands again. Expected: all focused tests pass with no skip/todo/xfail and no warning. Then run:

  ```bash
  /usr/bin/python3 -I -B tests/python38_linux_packaging_gate.py
  git diff --check
  ```

  If a real Python 3.8 interpreter is absent, report the gate's grammar/static result separately as `真实 Python 3.8 runtime 未验证`; do not download one.

### Task 3: Document and review the local P1

**Files:**

- Modify: `docs/runbooks/taiji-kylin-uos-offline-delivery.md`
- Update ignored status: `output/kylin-golden/PROGRESS.md`, `output/kylin-golden/BLOCKED.md`

- [x] **Step 1: Update the canonical runbook**

  Add a short subsection beside the golden `remote_build` description. State the schema, exact fields, `RUNNING/FAILED/SUCCEEDED` behavior, 300-second read-only polling, atomic/no-clobber rule, disconnect recovery, fail-closed identity/JSON behavior, and the evidence ceiling: success only permits review/log retrieval and local validation; it is not yet a `remote_build` pass or `候选 DEB 已构建` until the retrieved DEB and required evidence are checkpointed.

- [x] **Step 2: Self-review the diff**

  Run:

  ```bash
  git status --short
  git diff -- packaging/linux/kylin_remote_build.py scripts/taiji-linux-golden-orchestrator.py tests/test_kylin_remote_build.py tests/test_linux_golden_orchestrator.py tests/python38_linux_packaging_gate.py docs/runbooks/taiji-kylin-uos-offline-delivery.md
  rg -n 'TODO|FIXME|skip|xfail|xpass|\\|\\| true|rm -rf' packaging/linux/kylin_remote_build.py tests/test_kylin_remote_build.py
  ```

  Any new occurrence must be explained and removed unless it is an assertion proving prohibition. Confirm no unrelated file changed.

- [x] **Step 3: Commit the independent P1**

  Stage only the six implementation/runbook paths plus this plan file and commit:

  ```text
  fix(packaging): persist Kylin remote build result
  ```

- [ ] **Step 4: Run two-stage review**

  The one `gpt-5.6-sol/high` spec review is complete and its four P1-closure findings are being fixed without a repeat spec review. After focused GREEN, run exactly one fresh `gpt-5.6-sol/high` code-quality review. Do not add a third review; non-P1 findings are recorded and deferred.

### Task 4: One related local gate set and direct fast-forward closeout

- [ ] Run once: taiji-package shell grammar, target dispatch, state v2, core boundaries, orchestration, candidate, transport, Linux golden orchestrator, Kylin Skill, `python38_linux_packaging_gate.py`, and `git diff --check`.
- [ ] Record exact commands, counts, skips/errors, and source branch/worktree.
- [ ] Do not push the feature branch, create a PR, or run/rely on GitHub CI for this task.
- [ ] Fetch the current remote ref read-only. Require `origin/main` to equal the recorded branch base and be an ancestor of the exact locally verified feature commit. Any remote advance/divergence, non-fast-forward integration, rebase/cherry-pick, or force-push requirement stops closeout and must be reported.
- [ ] In the formal repository, fast-forward local `main` to the exact verified feature commit, prove inclusion/tree identity, rerun the non-destructive formal-main checks, and record final main commit/tree/clean state.
- [ ] Only after formal-main reverification, run a non-force `git push origin main:main`. Verify the returned/fetched `origin/main` equals the same commit. Never use `--force`, `--force-with-lease`, history rewrite, or branch-protection bypass.
- [ ] Perform safe cleanup only after the full audit; preserve unknown/valuable content and do not delete unrelated worktrees, artifacts, logs, runtime, credentials, or evidence.

### Task 5: Candidate build authorization gate

- [ ] Stop while `BLOCKED.md` still contains the version-identity mismatch or any other scope-changing blocker.
- [ ] From final verified `main`, run local doctor/plan and read-only online doctor only within their current authorization.
- [ ] Before `99`, SSH/SCP, apt/sudo/network dependencies, or actual `00`, separately present: exact final main commit/tree, trio basenames/bytes/SHA256, host `kylin`, remote path, impact, outputs, rollback, and stop conditions. Wait for the named stage approval.
- [ ] After approval, execute one `99`, one remote `00`, and one `01`/preflight for the same identity. Never run a parallel build; never rebuild from `FETCH_PENDING`.
- [ ] Retrieve and report DEB basename/bytes/SHA256, source commit/tree, canonical policy SHA256, formal log, `remote-build-result.json`, manifest, report, sidecar, and `.build-success`. The highest label at this point is `候选 DEB 已构建`.

### Task 6: Clean-snapshot target acceptance authorization gate

- [ ] Stop and present the exact candidate identity, target, known clean snapshot ID, install/remove scope, data impact, backup/rollback, controlled N-1 materials, non-production challenge, test license/Provider credentials, and real GUI session. Missing any item means `未验证`.
- [ ] Only after a new explicit approval, restore the same `kylin` host to the named clean snapshot, prove package/user-state absence, perform offline graphical double-click installation of the same DEB, and run installed `/usr/bin/taiji-agent-acceptance`.
- [ ] Use `frontend-ux-qa`; emit the required Chinese 《前端 UX QA 报告》 for menu/icon/first setup/single instance/real attachment chat/diagnostic export/full process and port exit.
- [ ] Do not sign, publish, create a Tag/Release, or infer other Kylin/UOS/openKylin systems passed.

## Completion labels

- Local implementation/tests only: `分支已实现` at most.
- Merged and formally reverified: `已合并 main` at most.
- One bound DEB with closed build evidence: `候选 DEB 已构建`.
- Clean target acceptance of that same SHA256: `目标机已验证` only for the named host/snapshot.
- Signing and publication remain out of scope.
