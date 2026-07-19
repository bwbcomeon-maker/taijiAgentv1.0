# Task B2 hardening gate summary

## Candidate

- Commit: `2b0428a6`
- Scope: custom image/vision providers, pinned outbound transport, canonical
  credential transactions, profile isolation, and OAuth/WebUI credential
  writers.
- Review: two independent specification/correctness reviews approved the
  candidate with no P0, P1, or P2 findings.

## Authoritative GREEN runs

| Gate | Tests | Failures | Errors | Skipped | Evidence |
| --- | ---: | ---: | ---: | ---: | --- |
| Agent provider/transport target | 479 | 0 | 0 | 0 | `task-b2-green-agent.xml` |
| Credential transaction target | 245 | 0 | 0 | 0 | `task-b2-green-credential.xml` |
| WebUI/OAuth/provider target | 288 | 0 | 0 | 0 | `task-b2-green-webui.xml` |

The three JUnit files were validated with `xmllint`; their hostnames were
replaced with `local-qa` before being tracked. They contain no local absolute
paths, production PID/port values, or recognized real-secret patterns.

## Invalid harness runs

An initial orchestration command incorrectly forced `TAIJI_RUNTIME_HOME`.
That variable has higher precedence than the per-test `HERMES_CONFIG_PATH`
fixtures, so it intentionally collapsed isolated credential roots and produced
21 Agent, 68 credential, and 77 WebUI failures. Those runs are classified as
invalid test-harness evidence, not product failures and not passing evidence.

The authoritative runs unset both `TAIJI_RUNTIME_HOME` and
`HERMES_CONFIG_PATH`, used an isolated temporary `HOME`, and used a dedicated
WebUI test port/state directory. Initial sandbox-only socket permission errors
were also excluded; the same suites were rerun in the approved isolated local
test environment.

Raw RED, diagnostic, invalid-harness, and runtime-guard evidence was preserved
outside the repository in the rollback freeze archive before cleanup:

`sha256:8f5e425e9d7d586fa8d53769b1c4ed2074933efdc0cbf12f9caf85e8c444558a`

## Static checks

- Targeted Ruff checks for the changed B2/OAuth surfaces: pass.
- Python compilation: pass.
- `node --check` for `static/onboarding.js`: pass.
- `git diff --check`: pass.
- Whole-file Ruff still reports 28 inherited violations in legacy WebUI files;
  the parent snapshot reports 29 on the same files, so this commit introduced
  no new Ruff violation. Legacy cleanup remains a separate task.

## Remaining limits

- Real enterprise proxy and real Provider credentials: not verified.
- Real browser/Electron visual and accessibility E2E: not verified in B2.
- A same-user process with direct filesystem permission could unlink/recreate
  the credential lock directory entry after `flock`; the product itself has no
  such deletion path. This remains a non-blocking P3 operating-system boundary.
