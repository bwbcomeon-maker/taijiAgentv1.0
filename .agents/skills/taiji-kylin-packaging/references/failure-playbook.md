# Failure Playbook

Classify first; do not respond with a sequence of speculative现场 commands.

## Response order

1. Bind the failing source/input/DEB by current identity.
2. Identify the earliest stage whose evidence is invalid.
3. State the confirmed root cause or `证据不足`.
4. Repair one source-controlled authority.
5. Add a regression and resume from the earliest valid checkpoint.

## Common failures

| Symptom | Root cause | Fast diagnosis | Reusable fix | Resume from | Success evidence | Forbidden shortcut |
| --- | --- | --- | --- | --- | --- | --- |
| Test/build loaded another checkout | cwd, Git environment, or separate component roots selected a wrong worktree | Record physical repo, branch, HEAD, status, Python module paths, and Node entry | Use one verified source root; derive all component paths; clear Git/Python/Node locator environment | Source/input | Exact source identity plus clean rerun | Accepting an internally consistent log from the wrong tree |
| Tool changes between check and first use | Tool/archive was identified by pathname but consumed later | Compare actual held/snapshot identity at every consumer | Freeze before first use and pass the same retained entity | Tool preparation | Actual version/hash and consumer binding | Searching PATH again |
| Archive or inventory is reopened | A later consumer uses the mutable canonical pathname | Inspect actual argv and file-open chain | Pass the retained snapshot/descriptor and canonical basename | Input/tool preparation | Same snapshot identity at each consumer | Adding another before/after pathname hash |
| Test suite reports green with zero work | Collection, skip, deselect, or target count was not enforced | Read per-target collected/executed/skipped/deselected counts | Require positive collection, no skips/deselects, and exact execution equality | Formal tests | All canonical targets and overall pass | Letting another file's tests hide an empty target |
| `uv.lock needs to be updated` or fallback appears | Lock and dependency policy drifted | Inspect strict lock mode and source lock | Fix and commit the lock locally, then freeze a new source | Source/input | Strict locked dry-run and formal build log | Refreshing a lock on the build host or using unlocked fallback |
| Native executable/module fails in a temp workspace | Filesystem is `noexec` or blocks executable/library mapping | Run the source-defined executable and dynamic-load probes on the selected root | Use the canonical owner-only cache/`/var/tmp` selection | Workspace selection | Probe result and mount facts | Disabling platform security or defaulting to a known blocked temp root |
| Manifest/log/marker describes different bytes | Evidence was rebound after production | Recompute basename, bytes, SHA256, policy, source, and snapshot identity | Recreate evidence from unchanged frozen inputs | Earliest changed identity | All consumers bind one immutable candidate | Editing JSON or success markers by hand |
| Local unit tests are reported as Kylin success | Evidence scopes were collapsed | Compare environment and exact DEB identity | Use narrow status labels; run separately approved real build/target gates | Missing real stage | Current DEB-bound evidence from declared environment | Promoting macOS, container, old DEB, or screenshot evidence |
| Customer single-DEB install fails | Unsupported target, corrupt copy, package state, maintainer-script, or payload defect | Capture DEB basename/bytes/SHA, preflight, installer details, and dpkg status | Fix source and rebuild if it is a product defect | Transfer or source/input | `install ok installed` plus applicable installed checks | Shipping internal scripts or a historical local apt repository |

Historical logs are clues, not current evidence. Do not patch the build-host checkout or installed tree in place, disable Kysec, loosen strict locks, or fabricate a passing record.
