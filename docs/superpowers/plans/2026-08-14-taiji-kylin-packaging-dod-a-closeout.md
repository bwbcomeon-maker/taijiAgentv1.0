# Taiji Kylin Packaging DoD-A Closeout Plan

## Goal

Close the remaining local implementation gaps in the repository-owned
`taiji-kylin-packaging` workflow without expanding the threat model. The result
may be described only as a locally verified golden-workflow candidate (DoD-A),
not as a built, installed, certified, signed, or published DEB (DoD-B).

## Fixed boundaries

- Work only in `codex/formal-source-identity-v4`, starting from
  `3c9c9aa36a166d09fb8fb946f0c8b16083beae4b`.
- Perform local source edits, unit/static tests, syntax checks, deterministic
  Skill packaging, and local commits only.
- Do not use network access, SSH, sudo, package installation, real DEB builds,
  target installation, signing, or publication.
- Do not add a root supervisor, temporary UID, NSS shim, Landlock, cgroup, PID
  namespace, or defenses against a persistent malicious same-UID writer.
- Keep the 20-target registry byte length (1864) and SHA256
  (`5fdcd9335ac9c722b224c06b03d817bd505cff4abc514b09f8d9ba604c11953b`)
  unchanged.

## Root causes being closed

1. `prepare_formal_build_test_dependencies()` exists but the production `00`
   main path does not call it, so the direct formal driver receives empty tool
   descriptors.
2. The direct driver hashes the held ESLint entrypoint but does not execute that
   held content; npm/ESLint identity evidence therefore does not describe the
   actual executable entrypoint.
3. The producer emits target output interleaved with target results while the
   validator accepts one aggregated stdout/stderr record before all target
   results. Tool version output also retains `Python ` / `v` prefixes rejected
   by the validator.
4. Target timeouts and output limits are target-local/unbounded in places, and
   exceptional paths do not uniformly close descriptors and reap processes.
5. Formal pytest starts in the source root, allowing initial conftest cache
   writes to fail or pollute the source tree.
6. The approved repository interface is `taiji-packaging-interface/v1` with six
   fields, including a distinct `builder_input_entry` for script `99`.

## Execution tasks

### Task 1: production dependency wiring

- Add a failing contract proving this exact order:
  `collect_artifacts` → `prepare_formal_build_test_dependencies` →
  `run_formal_build_tests_direct` → `write_pending_build_marker`.
- Add the single missing production call.
- Run the focused contract and `bash -n`.

### Task 2: held npm and ESLint entrypoints

- Add RED tests proving the held bytes are executed, not only hashed.
- Execute held CommonJS sources with a validated logical filename so relative
  `require()` calls resolve inside the frozen source closure.
- Pass only required descriptors and use a replaced, controlled environment.
- Require canonical ESLint JSON results and reject empty/error results.

### Task 3: formal v2 producer alignment

- Preserve the strict validator protocol:
  `suite_begin` → at most one aggregated stdout record → at most one aggregated
  stderr record → ordered target results → suite counts → suite pass.
- Normalize actual tool versions to bare canonical versions.
- Apply one 3600-second deadline per suite and bounded collection: stdout and
  stderr each at most 1 MiB per suite, result data at most 64 KiB.
- Close all descriptors and terminate/reap the target process group on every
  failure path. Emit `overall_status=pass` only after cleanup.
- Add a real producer-to-validator regression with output from multiple targets.

### Task 4: pytest writable scratch

- Start each formal pytest target in a private scratch directory.
- Use absolute selectors and exact rootdir, confcutdir, and config paths.
- Let initial conftests load in scratch, then use a try-last formal hook to
  validate root/config and change to the real suite root for collection/run.
- Restore scratch at session completion.
- Prove Agent gateway cache writes remain in scratch and WebUI relative static
  file access still works.

### Task 5: repository Skill interface

- Keep interface schema v1 and ratify the six-field exact contract.
- Route `prepare-builder-input` to script `99`; keep script `00` as the Linux
  build-host entry.
- Update evals before Skill text where behavior changes.
- Re-run Skill tests, doctor selftest, Python 3.8 grammar, and the local official
  Skill validator with an already available PyYAML-capable interpreter.

### Task 6: evidence, package, and branch closeout

- Update the requirements-to-implementation-to-test traceability matrix and the
  final DoD-A report with current evidence only.
- Run focused and combined release/formal/Skill tests, the isolated release
  runner, shell syntax checks, Python compile/3.8 gates, and `git diff --check`.
- Build the Skill twice in fresh temporary directories and require byte equality.
- Unpack and run doctor selftest; verify the sidecar, inventory, ZIP members, and
  secret/path-leak checks.
- Re-run all eight low-capability Skill eval scenarios against the final Skill
  SHA; any Skill change invalidates prior eval evidence.
- Commit explicit paths on the formal branch, then update the integration
  worktree with `git merge --ff-only codex/formal-source-identity-v4` and rerun
  the final local gates. Do not modify `main` and do not push.

## Stop conditions

- Any unknown test failure, registry/hash drift, external concurrent edit, or
  need for network/root/SSH/real packaging stops the affected task.
- Do not weaken tests or skip mandatory gates to meet a wall-clock target.
- Three failed fixes for the same root cause require architecture review instead
  of a fourth patch.

## Completion claim

DoD-A is complete only when both formal and integration are clean at the same
local commit, all applicable local gates have fresh passing evidence, and the
final `.skill`, sidecar, inventory, report, and traceability record refer to the
same source bytes. Linux/Python 3.8 runtime execution and all DoD-B activities
remain explicitly unverified until run in the proper environment.
