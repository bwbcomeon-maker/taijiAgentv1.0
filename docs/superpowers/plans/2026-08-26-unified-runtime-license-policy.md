# Unified Runtime License Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the source-development license exemption so every real Taiji runtime uses one fail-closed authorization and authentication policy, while isolated automated tests continue to use explicit test fixtures rather than a product bypass.

**Architecture:** Keep one parameter-free runtime guard (`require_valid_license()`) and one fixed runtime policy for source, test, candidate, and installed execution. Runtime profiles may resolve trusted public-key/version/license/state/device files from different carrier locations, but they cannot change `required`, machine binding, signer identity, expiry/version checks, clock rollback, or failure behavior. Explicit low-level validator parameters remain test/candidate-validation seams and are never exposed by runtime entry points.

**Tech Stack:** Python 3.11+, PyJWT, cryptography, vanilla JavaScript, Node.js test runner, pytest, Playwright, Electron/WebUI HTTP APIs, Git.

---

## File map

### Runtime policy and execution guards

- Modify `hermes-local-lab/sources/hermes-agent/taiji_license.py`: fixed unified policy, trusted source resource resolution, canonical candidate validation, state writes.
- Modify `hermes-local-lab/sources/hermes-agent/tests/test_taiji_license.py`: source/installed parity, override rejection, valid/missing/expired/mismatch coverage.
- Modify `hermes-local-lab/sources/hermes-agent/tests/gateway/test_api_server_license.py`: prove source and installed API execution endpoints both fail closed.

### WebUI projections and visible recovery

- Modify `hermes-local-lab/sources/hermes-webui/static/panels.js`: remove `not_required` success rendering and development-exemption copy.
- Modify `hermes-local-lab/sources/hermes-webui/static/style.css`: remove the `not_required` success selector.
- Modify `hermes-local-lab/sources/hermes-webui/api/product_diagnostics.py`: only a valid authorization is ready.
- Modify `hermes-local-lab/sources/hermes-webui/api/onboarding.py`: remove source-development readiness and copy.
- Modify `hermes-local-lab/sources/hermes-webui/tests/test_taiji_license_routes.py`: visible status/UI contract.
- Modify `hermes-local-lab/sources/hermes-webui/tests/test_product_diagnostics.py`: diagnostic projection contract.
- Modify `hermes-local-lab/sources/hermes-webui/tests/test_onboarding_mvp.py`: setup readiness contract.
- Modify `hermes-local-lab/sources/hermes-webui/CHANGELOG.md`: release-note-ready behavior change.

### Real-runtime test cleanup and browser acceptance

- Create `hermes-local-lab/sources/hermes-webui/tests/unified_license_test_fixture.js`: shared isolated V3 test-license preparation; never a product runtime switch.
- Create `hermes-local-lab/sources/hermes-webui/tests/unified_license_browser_smoke.js`: isolated real browser proof for missing, blocked, import, valid, and responsive states.
- Modify `hermes-local-lab/sources/hermes-webui/tests/chat_artifact_electron_smoke.js`: remove runtime authorization disable variables.
- Modify `hermes-local-lab/sources/hermes-webui/tests/expert_team_electron_artifact_smoke.js`: remove runtime authorization disable variables.
- Modify `hermes-local-lab/sources/hermes-webui/tests/expert_team_research_recovery_electron_smoke.js`: remove runtime authorization disable variables.
- Modify `hermes-local-lab/sources/hermes-webui/tests/expert_team_v3_electron_smoke.js`: remove runtime authorization disable variables.
- Modify `hermes-local-lab/sources/hermes-webui/tests/image_capability_center_electron_smoke.js`: remove runtime authorization disable variables.
- Modify `hermes-local-lab/sources/hermes-webui/tests/session_bundle_migration_electron_smoke.js`: remove runtime authorization disable variables.
- Modify `hermes-local-lab/sources/hermes-webui/tests/worktree_public_contract_electron_smoke.js`: remove runtime authorization disable variables.
- Create `tests/test_unified_runtime_license_policy.py`: repository-level invariant that real launch/smoke code contains no license-disable switch or development-exemption copy.

### QA evidence

- Create `docs/reviews/unified-runtime-license-feature-contract-2026-08-26.md`: frontend feature contract.
- Create `docs/reviews/unified-runtime-license-ux-qa-2026-08-26.md`: final Chinese UX QA report.
- Create `qa-evidence/unified-runtime-license-20260826/`: browser screenshots and machine-readable smoke result; no token, private key, machine request, or full machine code may be stored.

## Task 1: Establish RED evidence for canonical source authorization

**Files:**
- Modify: `hermes-local-lab/sources/hermes-agent/tests/test_taiji_license.py:188-265`
- Modify: `hermes-local-lab/sources/hermes-agent/tests/gateway/test_api_server_license.py:36-130`

- [ ] **Step 1: Replace the source exemption regression with fixed-policy expectations**

Add this helper and tests beside the existing `installed_production_profile` fixture. The helper patches only resource loaders and machine identity; it does not patch `require_valid_license()` or the policy result.

```python
def _patch_source_runtime_resources(
    monkeypatch,
    tmp_path,
    *,
    public_key: str,
    machine_fingerprint=TEST_MACHINE_FINGERPRINT,
):
    license_path = tmp_path / "config/taiji-agent/licenses/active-license.jwt"
    state_path = tmp_path / "state/taiji-agent/license-state.json"
    device_path = tmp_path / "config/taiji-agent/license-device.json"
    license_path.parent.mkdir(parents=True, mode=0o700)
    state_path.parent.mkdir(parents=True, mode=0o700)
    monkeypatch.setattr(
        taiji_license.taiji_runtime_profile,
        "is_installed_production",
        lambda: False,
    )
    monkeypatch.setattr(
        taiji_license.taiji_runtime_profile,
        "installation_profile",
        lambda: "source-development",
    )
    monkeypatch.setattr(taiji_license, "runtime_license_path", lambda: license_path)
    monkeypatch.setattr(
        taiji_license,
        "runtime_license_state_path",
        lambda: state_path,
        raising=False,
    )
    monkeypatch.setattr(
        taiji_license,
        "runtime_license_device_path",
        lambda: device_path,
        raising=False,
    )
    monkeypatch.setattr(
        taiji_license,
        "_load_source_public_key",
        lambda _policy: public_key,
        raising=False,
    )
    monkeypatch.setattr(
        taiji_license,
        "_load_source_version",
        lambda: "1.0.0",
        raising=False,
    )
    monkeypatch.setattr(
        taiji_license,
        "get_machine_fingerprint",
        lambda **_kwargs: dict(machine_fingerprint),
    )
    return license_path, state_path, device_path


def test_source_runtime_missing_license_is_required_and_blocked(
    monkeypatch, tmp_path, signing_keys
):
    _, public_key = signing_keys
    _patch_source_runtime_resources(
        monkeypatch,
        tmp_path,
        public_key=public_key,
    )

    status = taiji_license.load_license_status()
    blocked = taiji_license.require_valid_license()

    assert status.status == "missing"
    assert status.required is True
    assert status.policy == "unified-runtime"
    assert status.machine_binding_required is True
    assert blocked is not None
    assert blocked.code == "license_missing"


def test_source_runtime_valid_license_uses_same_machine_bound_policy(
    monkeypatch, tmp_path, signing_keys
):
    private_key, public_key = signing_keys
    license_path, _, _ = _patch_source_runtime_resources(
        monkeypatch,
        tmp_path,
        public_key=public_key,
    )
    _write_token(license_path, private_key, max_version="1.0.0")
    license_path.chmod(0o600)

    status = taiji_license.load_license_status()
    blocked = taiji_license.require_valid_license()

    assert status.status == "valid"
    assert status.required is True
    assert status.policy == "unified-runtime"
    assert status.machine_binding_required is True
    assert status.machine_matched is True
    assert blocked is None


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TAIJI_LICENSE_REQUIRED", "0"),
        ("TAIJI_LICENSE_MACHINE_BINDING_REQUIRED", "0"),
        ("TAIJI_LICENSE_ALLOW_LEGACY_MACHINE_BINDING", "1"),
        ("TAIJI_LICENSE_PUBLIC_KEY", "attacker-controlled-key"),
        ("TAIJI_LICENSE_PUBLIC_KEY_FILE", "/tmp/attacker-public.pem"),
    ],
)
def test_source_runtime_rejects_every_policy_override(
    monkeypatch, tmp_path, signing_keys, name, value
):
    _, public_key = signing_keys
    _patch_source_runtime_resources(
        monkeypatch,
        tmp_path,
        public_key=public_key,
    )
    monkeypatch.setenv(name, value)

    status = taiji_license.load_license_status()

    assert status.status == "invalid"
    assert status.required is True
    assert status.policy == "unified-runtime"
    assert status.code == "license_policy_override_forbidden"
```

- [ ] **Step 2: Add a source-profile API execution test**

Add a second fixture that patches source resources to a missing isolated license, then reuse the existing endpoint parameterization. The assertion must remain before Agent creation.

```python
@pytest.fixture()
def source_missing_license(monkeypatch, tmp_path):
    monkeypatch.setattr(
        taiji_license.taiji_runtime_profile,
        "is_installed_production",
        lambda: False,
    )
    monkeypatch.setattr(
        taiji_license.taiji_runtime_profile,
        "installation_profile",
        lambda: "source-development",
    )
    monkeypatch.setattr(
        taiji_license,
        "runtime_license_path",
        lambda: tmp_path / "config/taiji-agent/licenses/active-license.jwt",
        raising=False,
    )
    monkeypatch.setattr(
        taiji_license,
        "runtime_license_state_path",
        lambda: tmp_path / "state/taiji-agent/license-state.json",
        raising=False,
    )


@pytest.mark.asyncio
async def test_source_execution_is_blocked_before_agent_creation(source_missing_license):
    adapter = _make_adapter()
    app = _create_license_app(adapter)

    async with TestClient(TestServer(app)) as cli:
        with patch.object(adapter, "_create_agent") as create_agent:
            response = await cli.post(
                "/v1/chat/completions",
                json={"messages": [{"role": "user", "content": "hello"}]},
            )
            body = await response.json()

    assert response.status == 403
    assert body["error"]["code"] == "license_missing"
    create_agent.assert_not_called()
```

- [ ] **Step 3: Run the focused tests and retain RED evidence**

Run:

```bash
cd hermes-local-lab/sources/hermes-agent
scripts/run_tests.sh tests/test_taiji_license.py::test_source_runtime_missing_license_is_required_and_blocked tests/test_taiji_license.py::test_source_runtime_valid_license_uses_same_machine_bound_policy tests/test_taiji_license.py::test_source_runtime_rejects_every_policy_override tests/gateway/test_api_server_license.py::test_source_execution_is_blocked_before_agent_creation -q
```

Expected: FAIL because the current source no-argument path returns `not_required`, `_source_development_status()` bypasses file validation, and the new runtime resource helpers do not exist.

## Task 2: Implement the unified runtime policy

**Files:**
- Modify: `hermes-local-lab/sources/hermes-agent/taiji_license.py:55-285`
- Modify: `hermes-local-lab/sources/hermes-agent/taiji_license.py:1330-1915`
- Test: `hermes-local-lab/sources/hermes-agent/tests/test_taiji_license.py`

- [ ] **Step 1: Add one fixed runtime policy and trusted source resource paths**

Replace `production_license_policy()` with the fixed runtime policy below. Keep `_explicit_license_policy()` only for calls that pass explicit validator parameters.

```python
RUNTIME_LICENSE_POLICY_NAME = "unified-runtime"


def runtime_license_policy() -> LicensePolicy:
    """Return the immutable policy used by every real runtime entry point."""
    return LicensePolicy(
        name=RUNTIME_LICENSE_POLICY_NAME,
        version=PRODUCTION_LICENSE_POLICY_VERSION,
        required=True,
        machine_binding_required=True,
        allow_legacy_machine_binding=False,
        public_key_path=PRODUCTION_PUBLIC_KEY_PATH,
        public_key_fingerprint=PRODUCTION_PUBLIC_KEY_FINGERPRINT,
        reject_environment_overrides=True,
    )


def runtime_license_path() -> Path:
    if taiji_runtime_profile.is_installed_production():
        return PRODUCTION_LICENSE_PATH
    return default_license_path()


def runtime_license_state_path() -> Path:
    if taiji_runtime_profile.is_installed_production():
        return PRODUCTION_LICENSE_STATE_PATH
    return default_license_state_path()


def runtime_license_device_path() -> Path:
    if taiji_runtime_profile.is_installed_production():
        return PRODUCTION_LICENSE_DEVICE_PATH
    return default_license_device_path()
```

Delete `_source_development_status()`. Rename `_is_implicit_production_request()` to `_is_canonical_runtime_request()` without changing its parameter-free detection contract.

- [ ] **Step 2: Add source public-key and version loaders with fixed identity checks**

The source checkout may own its files as the developer account, but it must use the same fixed signer fingerprint and a valid repository `VERSION`.

```python
def _source_repo_root() -> Path:
    root = Path(__file__).resolve().parents[3]
    if not (root / ".git").exists():
        raise _LicensePublicKeyError
    return root


def _load_source_public_key(policy: LicensePolicy) -> str:
    path = _source_repo_root() / INTERNAL_ISSUER_PUBLIC_KEY_RELATIVE
    expected = str(policy.public_key_fingerprint or "").lower()
    try:
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise _LicensePublicKeyError
        public_key_pem = path.read_text(encoding="utf-8").strip()
        actual = _public_key_fingerprint(public_key_pem)
    except (OSError, ValueError, TypeError):
        raise _LicensePublicKeyError from None
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise _LicensePublicKeyError
    if not hmac.compare_digest(actual, expected):
        raise _LicensePublicKeyError
    return public_key_pem


def _load_source_version() -> str:
    path = _source_repo_root() / "VERSION"
    try:
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise _LicenseVersionError
        version = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise _LicenseVersionError from None
    if re.fullmatch(
        r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)",
        version,
    ) is None:
        raise _LicenseVersionError
    return version
```

- [ ] **Step 3: Route every parameter-free status/guard call through the fixed policy**

In `load_license_status()`:

1. Compute `canonical = _is_canonical_runtime_request(...)`.
2. Use `runtime_license_policy()` for `canonical`; use `_explicit_license_policy(env)` only otherwise.
3. For canonical requests, reject every name in `PRODUCTION_SECURITY_OVERRIDE_ENVS` in source and installed profiles.
4. Resolve `license_path`, `state_path`, and `device_path` with the three runtime path helpers.
5. Validate the license/state/device file shape with `_validate_production_user_file()` for both source and installed runtimes.
6. Load installed key/version with `_load_production_public_key()` and `_load_production_version()`; load source key/version with the two new source functions.
7. Force the validation environment below before calling `_load_license_status_impl()`.

```python
validation_env = dict(env)
validation_env[LICENSE_REQUIRED_ENV] = "1"
validation_env[LICENSE_MACHINE_BINDING_REQUIRED_ENV] = "1"
validation_env[LICENSE_ALLOW_LEGACY_MACHINE_BINDING_ENV] = "0"
validation_env[VERSION_ENV] = product_version
validation_env[LICENSE_DEVICE_FILE_ENV] = str(device_path)
validation_env.pop(LICENSE_PUBLIC_KEY_ENV, None)
validation_env.pop(LICENSE_PUBLIC_KEY_FILE_ENV, None)
status = _load_license_status_impl(
    path=license_path,
    state_path=state_path,
    public_key=resolved_public_key,
    now=now,
    environ=validation_env,
    check_state=check_state,
    machine_fingerprint=machine_fingerprint,
)
return _attach_policy(status, policy)
```

The canonical missing-license return must be exactly:

```python
return _attach_policy(
    LicenseStatus(
        status="missing",
        required=True,
        code="license_missing",
        message=MESSAGE_MISSING,
        machine_binding_required=True,
    ),
    policy,
)
```

- [ ] **Step 4: Make candidate import and state writes use the same canonical resources**

For `validate_license_candidate()`, keep the candidate path explicit but resolve policy, signer, product version, device path, and state path from the canonical runtime. Do not accept an environment-selected public key or relaxed machine binding.

For `require_license_for_validation()`, replace the production-name branch with the runtime-policy branch:

```python
if status.policy == RUNTIME_LICENSE_POLICY_NAME:
    license_path = runtime_license_path()
    license_state_path = runtime_license_state_path()
else:
    license_path = (
        Path(path).expanduser() if path is not None else default_license_path(env)
    )
    license_state_path = (
        Path(state_path).expanduser()
        if state_path is not None
        else default_license_state_path(env)
    )
```

- [ ] **Step 5: Run core GREEN tests**

Before running, update the existing fixed-policy unit tests and assertions that
call `production_license_policy()` or expect `policy == "production"` so they call
`runtime_license_policy()` and expect `policy == "unified-runtime"`. Do not keep
an alias that lets real runtime code select the retired production-only name.

Run:

```bash
cd hermes-local-lab/sources/hermes-agent
scripts/run_tests.sh tests/test_taiji_license.py tests/gateway/test_api_server_license.py -q
```

Expected: PASS, with source and installed runtime status reporting `required=true`, `machine_binding_required=true`, `policy=unified-runtime`, and all execution endpoints blocking before Agent creation when authorization is unavailable.

- [ ] **Step 6: Run same-root regression searches**

Run:

```bash
rg -n '_source_development_status|license_not_required|status="not_required"' taiji_license.py tests/test_taiji_license.py
```

Expected: no matches.

- [ ] **Step 7: Sol-review and commit the core policy**

Precisely stage only:

```bash
git add hermes-local-lab/sources/hermes-agent/taiji_license.py hermes-local-lab/sources/hermes-agent/tests/test_taiji_license.py hermes-local-lab/sources/hermes-agent/tests/gateway/test_api_server_license.py
```

After the required final staged-bytes Sol PASS, commit:

```bash
git commit -m "fix(license): require authorization in every runtime"
```

## Task 3: Remove runtime-disable fixtures from real Electron smokes

**Files:**
- Create: `tests/test_unified_runtime_license_policy.py`
- Create: `hermes-local-lab/sources/hermes-webui/tests/unified_license_test_fixture.js`
- Modify: the seven `hermes-local-lab/sources/hermes-webui/tests/*electron_smoke.js` files listed in the file map

- [ ] **Step 1: Add a repository security invariant**

Create the following root unittest. It scans only real launch/smoke code, not low-level validator unit tests that intentionally exercise rejected inputs.

```python
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "hermes-local-lab/sources/hermes-webui"
REAL_RUNTIME_ROOTS = (
    ROOT / "apps/taiji-desktop/src",
    ROOT / "hermes-local-lab/scripts",
)
REAL_RUNTIME_FILES = (
    WEBUI / "static/panels.js",
    WEBUI / "static/style.css",
    WEBUI / "api/onboarding.py",
    WEBUI / "api/product_diagnostics.py",
    *sorted((WEBUI / "tests").glob("*electron*_smoke.js")),
)
FORBIDDEN_SNIPPETS = (
    'TAIJI_LICENSE_REQUIRED: "0"',
    "TAIJI_LICENSE_REQUIRED: '0'",
    'TAIJI_LICENSE_MACHINE_BINDING_REQUIRED: "0"',
    "TAIJI_LICENSE_MACHINE_BINDING_REQUIRED: '0'",
    "开发环境无需授权",
    "开发源码模式无需授权",
    "not_required",
    "status==='not_required'",
    'data-license-status="not_required"',
)

TEST_ACCOUNT_HOME_HOOK_ENV = "TAIJI_LICENSE_TEST_" + "ACCOUNT_HOME"


class UnifiedRuntimeLicensePolicyTests(unittest.TestCase):
    def test_real_runtime_sources_do_not_disable_or_exempt_authorization(self):
        failures = []
        paths = list(REAL_RUNTIME_FILES)
        for root in REAL_RUNTIME_ROOTS:
            paths.extend(
                path
                for path in sorted(root.rglob("*"))
                if path.suffix in {".js", ".py", ".sh", ".command"}
            )
        for path in paths:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for snippet in FORBIDDEN_SNIPPETS:
                if snippet in text:
                    failures.append(
                        f"{path.relative_to(ROOT)} contains forbidden runtime snippet"
                    )
        self.assertEqual(failures, [])

    def test_account_home_hook_is_confined_to_the_shared_test_fixture(self):
        # Product launchers and every discovered Electron smoke must not know the
        # temporary-account hook. Only the shared test fixture may create it.
        paths = list(REAL_RUNTIME_FILES)
        for root in REAL_RUNTIME_ROOTS:
            paths.extend(path for path in sorted(root.rglob("*")) if path.is_file())
        failures = [
            str(path.relative_to(ROOT))
            for path in paths
            if TEST_ACCOUNT_HOME_HOOK_ENV in path.read_text(
                encoding="utf-8", errors="ignore"
            )
        ]
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the invariant and retain RED evidence**

Run:

```bash
hermes-local-lab/sources/hermes-agent/venv/bin/python -m unittest tests.test_unified_runtime_license_policy -v
```

Expected: FAIL and list the seven Electron smoke files plus the current
development-exemption status/copy in the WebUI runtime files. The invariant does
not scan its own unit-test source or low-level validator tests, so negative test
literals cannot create false positives.

- [ ] **Step 3: Add one isolated V3 test-license fixture helper**

Create `unified_license_test_fixture.js` with these exact exported functions:

- `sanitizeUnifiedLicenseRuntimeEnv(baseEnv)`;
- `cleanupUnifiedLicenseFixture(options)` where `options` contains the exact
  `runtimeEnv` object passed to `prepareUnifiedLicenseFixture()`;
- `loadUnifiedLicenseTestSigner(options)` where `options` contains `repoRoot`,
  `agentDir`, `pythonBin`, and optional `environ`;
- `issueUnifiedLicenseForMachineRequest(options)` where `options` contains
  `repoRoot`, `machineRequest`, and `signer`;
- `prepareUnifiedLicenseFixture(options)` where `options` contains `repoRoot`,
  `agentDir`, `pythonBin`, and `runtimeEnv`.

The implementation must:

1. Iterate every input environment key and compare `key.toUpperCase()` against
   the deny list. Delete every case variant of the security-policy variables,
   private-key/hook variables, `PYTHONHOME`, and `PYTHONPATH` from the returned
   product child environment. Isolated `XDG_CONFIG_HOME`, `XDG_STATE_HOME`,
   `HERMES_HOME`, and `TAIJI_RUNTIME_HOME` remain useful for non-license smoke
   state, but they are not and must not be treated as license-path controls.
2. Read the test signer only in the Node harness process. Require a regular,
   non-symlink, mode-0600 key, derive its SPKI public key with
   `crypto.createPublicKey()`, query
   `taiji_license.PRODUCTION_PUBLIC_KEY_FINGERPRINT` from the selected Agent
   Python without accepting an environment override, and compare the two with
   `crypto.timingSafeEqual()`. Catch `realpath`, `lstat`, and `readFile`
   failures and replace them with one stable error that does not include the
   supplied key path. Do not print the path, key, payload, token, machine code,
   or device identity.
3. Create a mode-0700 temporary account profile, a test-only
   `sitecustomize.py`, and a mode-0700 Python wrapper executable. The wrapper
   restores the hook directory through `PYTHONPATH` after the real launchers
   clear inherited Python paths, sets `TAIJI_LICENSE_TEST_ACCOUNT_HOME` to the
   temporary profile, and then `exec`s the selected real Python. On every
   Python startup, `sitecustomize.py` immediately pops that variable and
   replaces only the system account lookup used in that process:
   `pwd.getpwuid(os.getuid()).pw_dir` on POSIX, or
   `win32profile.GetUserProfileDirectory()` on Windows. The product code,
   Electron app, launchers, and seven smokes never read or name this hook.
4. Point `TAIJI_AGENT_PYTHON`, `TAIJI_WEBUI_PYTHON`, and
   `HERMES_WEBUI_PYTHON` in the sanitized Electron child environment to this
   wrapper. Query the fixed production public-key fingerprint and call
   `taiji_license.build_machine_request()` through that same wrapper, in
   memory, and refuse a non-V3 or weak/risky request. This preserves the real
   unified required policy; it only simulates a separate operating system
   account for test resource isolation.
5. Sign a one-day fixture with `issueLicense()`, the repository `VERSION`, and
   only the minimum smoke-test features. Write only `issued.token` to the
   canonical account-relative path
   `<temporary-profile>/.config/taiji-agent/licenses/active-license.jwt`, with
   parent mode 0700 and file mode 0600. The same profile, device file, and
   rollback state are reused across restart phases. Return only sanitized
   fields such as `policy_fixture=true`, public-key fingerprint short form, and
   expiration date; never return or log the key path, token, complete machine
   code, or complete device identity.
6. Keep the supplied `runtimeEnv` to temporary-profile association in a
   `WeakMap`. `cleanupUnifiedLicenseFixture({ runtimeEnv })` synchronously and
   idempotently removes the entire temporary account profile, including the
   canonical token, device/state files, hook, and wrapper, without returning or
   printing its path. Register a synchronous process-exit fallback for an
   unclosed fixture. If explicit removal fails, retain the active record for a
   later retry/exit cleanup and throw one stable error that contains no profile
   path. Explicit cleanup remains the primary path.

The POSIX wrapper path is required because both `start-agent.sh` and
`start-webui.sh` deliberately clear `PYTHONPATH` before launching Python.
Windows source Electron currently replaces the source Python selector with its
bundled interpreter, so this fixture does not write the real Windows profile;
Windows Electron isolation remains explicitly unverified until it can run
under a dedicated temporary OS account or an equivalent launcher-independent
test boundary.

This helper is test code, not imported by `apps/`, `hermes-agent`, WebUI API, or
any launcher. It contains no fallback key and no alternate accepted signer.

- [ ] **Step 4: Replace disable variables with the valid fixture in all seven smokes**

From each listed Electron smoke, remove these entries wherever present:

```javascript
TAIJI_LICENSE_REQUIRED: "0",
TAIJI_LICENSE_MACHINE_BINDING_REQUIRED: "0",
```

Also remove the single-quoted forms. Preserve `XDG_CONFIG_HOME` and `XDG_STATE_HOME`; those provide isolated resource locations but do not relax the policy. Do not add a replacement test-mode flag.

Before launching Electron or a standalone Agent, build one child environment,
sanitize it, prepare the temporary account and license, and pass that exact
environment to the product processes. `prepareUnifiedLicenseFixture()` mutates
only the supplied test environment's Python selectors to the generated wrapper:

```javascript
const {
  cleanupUnifiedLicenseFixture,
  prepareUnifiedLicenseFixture,
  sanitizeUnifiedLicenseRuntimeEnv,
} = require("./unified_license_test_fixture");

const runtimeEnv = sanitizeUnifiedLicenseRuntimeEnv({
  ...process.env,
  XDG_CONFIG_HOME: dirs.config,
  XDG_STATE_HOME: dirs.state,
  TAIJI_RUNTIME_HOME: dirs.runtimeHome,
  TAIJI_WORKSPACE: dirs.workspace,
  // Existing non-license smoke configuration remains here.
});
prepareUnifiedLicenseFixture({ repoRoot, agentDir, pythonBin, runtimeEnv });
// Electron/Agent spawn receives `env: runtimeEnv`.
try {
  // Run the smoke and close/terminate only its owned processes.
} finally {
  cleanupUnifiedLicenseFixture({ runtimeEnv });
}
```

For scripts with a restart phase, reuse the same isolated XDG roots and fixture;
never read or modify the developer's real authorization files. Every changed
smoke calls cleanup only after its owned processes have closed, on both success
and failure. Result JSON may include the sanitized fixture fields but never the
temporary profile path.

- [ ] **Step 5: Run syntax checks, a focused fixture self-test, and one real licensed Electron representative**

Run `node --check` on the shared helper and all seven changed smokes. Run a
focused Node self-test which proves that the product environment contains no
private-key/security override variable, the request is strong V3, and a fresh
Python process through the wrapper reports
`taiji_license.load_license_status().status == "valid"`, without printing any
secret or complete machine/device identity. Then run the worktree smoke because
it exercises a real chat chain through a local Provider:

```bash
for file in hermes-local-lab/sources/hermes-webui/tests/unified_license_test_fixture.js hermes-local-lab/sources/hermes-webui/tests/*electron*_smoke.js; do node --check "$file"; done
PLAYWRIGHT_NODE_PATH="apps/taiji-desktop/node_modules/playwright" TAIJI_LICENSE_TEST_PRIVATE_KEY_FILE="tools/taiji-license-issuer/private/signing-private.pem" node hermes-local-lab/sources/hermes-webui/tests/worktree_public_contract_electron_smoke.js --out-dir /tmp/taiji-unified-license-worktree-smoke-20260826
```

Expected: syntax checks and the focused fixture self-test exit 0; on POSIX the
real chat smoke exits 0, its product process does not inherit the signer path,
and its sanitized result records the unified test fixture without any secret or
full machine identity. If the representative smoke fails because Electron,
Playwright, or the local Provider is unavailable, first distinguish that
external boundary from a fixture/authentication failure and report it as
unverified; do not restore a disable variable. The other six smoke flows are
syntax-verified here and remain covered by their existing focused acceptance
commands when those suites are run. Windows Electron account isolation remains
unverified and must not write the developer's real authorization directory.

- [ ] **Step 6: Re-run the invariant**

Run the same unittest command.

Expected: the only remaining failures are the WebUI `not_required` copy/status
paths in `panels.js`, `style.css`, onboarding, and diagnostics, which Task 4
removes.

## Task 4: Align WebUI, onboarding, and diagnostics with the unified policy

**Files:**
- Modify: `hermes-local-lab/sources/hermes-webui/static/panels.js:10648-10725`
- Modify: `hermes-local-lab/sources/hermes-webui/static/style.css:4359`
- Modify: `hermes-local-lab/sources/hermes-webui/api/product_diagnostics.py:124-138`
- Modify: `hermes-local-lab/sources/hermes-webui/api/onboarding.py:70-103`
- Modify: `hermes-local-lab/sources/hermes-webui/api/routes.py:9865-9900`
- Modify: `hermes-local-lab/sources/hermes-agent/taiji_license.py`
- Modify: `hermes-local-lab/sources/hermes-agent/tests/test_taiji_license.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_taiji_license_routes.py:45-64`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_product_diagnostics.py`
- Modify: `hermes-local-lab/sources/hermes-webui/tests/test_onboarding_mvp.py`
- Modify: `hermes-local-lab/sources/hermes-webui/CHANGELOG.md:1-80`

- [x] **Step 1: Replace the old frontend contract with unified-state assertions**

Replace `test_development_license_not_required_is_presented_as_non_error_state()` with:

```python
def test_license_panel_has_no_environment_exemption_state_or_copy():
    panels_js = _read("static/panels.js")
    styles = _read("static/style.css")

    assert "开发环境无需授权" not in panels_js
    assert "开发源码模式无需授权" not in panels_js
    assert "status==='not_required'" not in panels_js
    assert 'data-license-status="not_required"' not in styles
    assert "if(s==='valid') return '授权有效';" in panels_js
    assert "const state=status==='valid'?'ok'" in panels_js
    assert "icon.textContent=status==='valid'?'✓':'!'" in panels_js
```

Add direct projection tests using small fake license modules:

Add these module imports at the top of the affected test files before using the
monkeypatch targets:

```python
from api import onboarding, product_diagnostics
```

```python
def test_license_diagnostic_requires_valid_status(monkeypatch):
    class FakeStatus:
        def to_public_dict(self):
            return {"status": "missing", "required": True}

    class FakeLicense:
        @staticmethod
        def load_license_status():
            return FakeStatus()

    monkeypatch.setattr(product_diagnostics, "_license_module", lambda: FakeLicense)
    assert product_diagnostics._probe_license() == {"status": "blocked"}


def test_source_setup_requires_valid_authorization(monkeypatch):
    class FakeStatus:
        def to_public_dict(self):
            return {
                "status": "missing",
                "required": True,
                "code": "license_missing",
                "message": "未安装有效授权",
            }

    class FakeProfile:
        @staticmethod
        def is_installed_production():
            return False

    class FakeLicense:
        taiji_runtime_profile = FakeProfile()

        @staticmethod
        def load_license_status():
            return FakeStatus()

    monkeypatch.setattr(product_diagnostics, "_license_module", lambda: FakeLicense)
    item, installed = onboarding._license_setup_item()
    assert installed is False
    assert item["ready"] is False
    assert item["status"] == "action_required"
    assert item["code"] == "license_missing"
```

- [x] **Step 2: Run WebUI RED tests**

Run:

```bash
cd hermes-local-lab/sources/hermes-webui
../hermes-agent/venv/bin/python -m pytest -q tests/test_taiji_license_routes.py tests/test_product_diagnostics.py tests/test_onboarding_mvp.py
```

Expected: FAIL because `not_required` is still a successful UI/setup/diagnostic state.

- [x] **Step 3: Implement the visible and projection changes**

Apply these exact rules:

```javascript
function _taijiLicenseStatusLabel(status){
 const s=String(status&&status.status||'').toLowerCase();
 if(s==='valid') return '授权有效';
 if(s==='expired') return '授权已到期';
 if(s==='missing') return '未安装授权';
 if(s==='invalid') return '授权无效';
 return '授权状态不可用';
}
```

Inside `_renderTaijiLicenseStatus()` use:

```javascript
const state=status==='valid'?'ok':(status==='expired'||status==='missing'||status==='invalid')?'danger':'warn';
```

Use `授权状态不可用` as the only fallback summary, and set the icon with:

```javascript
if(icon) icon.textContent=status==='valid'?'✓':'!';
```

Change the CSS selector to:

```css
.taiji-license-panel[data-license-status="valid"]{border-color:color-mix(in srgb,var(--success) 38%,var(--border));}
```

Change diagnostics to:

```python
if required and status == "valid":
    return {"status": "ready"}
if status in {"missing", "invalid", "expired", "blocked"}:
    return {"status": "blocked"}
return {"status": "degraded"}
```

Change onboarding readiness and copy to:

```python
ready = required and status == "valid"
if ready:
    reason = "产品授权有效，可以离线使用。"
else:
    reason = str(
        public.get("message") or "未检测到可用授权，请先导入离线授权文件。"
    )
```

Replace the route-owned temporary candidate/install sequence with one public
Agent-core API, `install_license_token(token)`. The API validates the exact
in-memory normalized token using the immutable runtime policy, writes only a
valid token to `runtime_license_path()` via the existing cross-platform secure
atomic writer rooted at `PRODUCTION_USER_HOME`, and securely reads it back for
constant-time content comparison. Invalid input and policy overrides never
replace the current authorization. Write/read failures return one path-free,
token-free invalid status. The WebUI route selects no path, creates no candidate
file, and returns only the public status.

- [x] **Step 4: Add the release-note-ready changelog entry**

Under `[Unreleased] -> Changed`, add:

```markdown
- Taiji source, test, candidate, and installed runtimes now use the same mandatory offline authorization policy. Development-mode exemption states and environment switches can no longer bypass signature, V3 machine binding, expiry/version, or clock-rollback checks; Settings continues to expose machine-code export, authorization import, and recovery status when execution is blocked.
```

- [x] **Step 5: Run WebUI GREEN tests and static checks**

Run:

```bash
cd hermes-local-lab/sources/hermes-webui
../hermes-agent/venv/bin/python -m pytest -q tests/test_taiji_license_routes.py tests/test_product_diagnostics.py tests/test_onboarding_mvp.py
npm run lint:runtime
node --check static/panels.js
```

Expected: PASS.

- [x] **Step 6: Run the repository invariant again**

Run:

```bash
hermes-local-lab/sources/hermes-agent/venv/bin/python -m unittest tests.test_unified_runtime_license_policy -v
```

Expected: PASS with no runtime disable switch or development-exemption copy in the scanned launch/smoke surfaces.

- [ ] **Step 7: Sol-review and commit UI/runtime-smoke alignment**

Precisely stage the Task 3 and Task 4 paths. After final staged-bytes Sol PASS, commit:

```bash
git commit -m "fix(webui): remove development license exemption"
```

## Task 5: Add isolated real-browser authorization acceptance

**Files:**
- Create: `hermes-local-lab/sources/hermes-webui/tests/unified_license_browser_smoke.js`
- Create at runtime: `qa-evidence/unified-runtime-license-20260826/01-missing-authorization.png`
- Create at runtime: `qa-evidence/unified-runtime-license-20260826/02-valid-authorization.png`
- Create at runtime: `qa-evidence/unified-runtime-license-20260826/03-narrow-missing-authorization.png`
- Create at runtime: `qa-evidence/unified-runtime-license-20260826/result.json`

- [ ] **Step 1: Implement a no-bypass browser smoke harness**

The script must:

1. Create a temporary `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `HERMES_HOME`, `TAIJI_RUNTIME_HOME`, and workspace.
2. Delete inherited `TAIJI_LICENSE_REQUIRED`, `TAIJI_LICENSE_MACHINE_BINDING_REQUIRED`, `TAIJI_LICENSE_ALLOW_LEGACY_MACHINE_BINDING`, `TAIJI_LICENSE_PUBLIC_KEY`, `TAIJI_LICENSE_PUBLIC_KEY_FILE`, `TAIJI_LICENSE_FILE`, `TAIJI_LICENSE_STATE_FILE`, `TAIJI_LICENSE_DEVICE_FILE`, and `TAIJI_LICENSE_TEST_PRIVATE_KEY_FILE` from the WebUI child environment before assigning the isolated XDG roots. The harness process may read the test key; the product process must not inherit its path or contents.
3. Start the real WebUI server with the real source Agent directory and no Provider credentials.
4. Open Settings -> Models in Chromium, verify `未安装授权`, and assert both exemption strings are absent.
5. POST one chat start and assert `license_blocked`/`license_missing` appears before an Agent stream exists.
6. Fetch `/api/license/machine-request`, sign a short-lived V3 fixture with `issueLicense()` from `tools/taiji-license-issuer/issuer-core.js`, and POST it to `/api/license/import` without logging the token.
7. Refresh status and assert `授权有效`, matching machine state, and no exemption copy.
8. POST the same chat start again and assert the response no longer contains `license_blocked` or `license_missing`; with Provider credentials intentionally absent, a Provider-configuration error is acceptable evidence that execution passed the authorization gate.
9. Capture desktop and 760px-wide screenshots.
10. Write only booleans, status codes, viewport dimensions, screenshot basenames, source commit, and public-key fingerprint short form to `result.json`.
11. Refuse to run the valid-import phase unless `TAIJI_LICENSE_TEST_PRIVATE_KEY_FILE` names a regular mode-0600 key whose derived public pair matches the fixed runtime fingerprint.

Reuse the shared signer validator and signing function; never write the token or
machine request to evidence output:

```javascript
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");
const {
  issueUnifiedLicenseForMachineRequest,
  loadUnifiedLicenseTestSigner,
  sanitizeUnifiedLicenseRuntimeEnv,
} = require("./unified_license_test_fixture");

const signer = loadUnifiedLicenseTestSigner({
  repoRoot,
  agentDir,
  pythonBin,
});
const issued = issueUnifiedLicenseForMachineRequest({
  repoRoot,
  machineRequest,
  signer,
});

// Import with JSON.stringify({ license: issued.token }); never log `issued`,
// `issued.token`, `issued.payload`, or the full machine request.
```

- [ ] **Step 2: Run the browser smoke with isolated state**

Run:

```bash
PLAYWRIGHT_NODE_PATH="apps/taiji-desktop/node_modules/playwright" TAIJI_LICENSE_TEST_PRIVATE_KEY_FILE="tools/taiji-license-issuer/private/signing-private.pem" node hermes-local-lab/sources/hermes-webui/tests/unified_license_browser_smoke.js --out-dir qa-evidence/unified-runtime-license-20260826
```

Expected: exit 0; `result.json` reports missing authorization blocked before Agent creation, valid import accepted, exemption copy absent, desktop and narrow layouts without horizontal overflow, and three screenshots exist.

- [ ] **Step 3: Inspect screenshots and browser semantics**

Inspect all three screenshots. Check visible status text, recovery controls, main/auxiliary hierarchy, focus appearance, clipped text, horizontal overflow, and color-independent icon/text state. Record findings in the UX report; do not call screenshot or browser validation passed unless this inspection was actually performed.

## Task 6: Complete the feature contract and Chinese UX QA report

**Files:**
- Create: `docs/reviews/unified-runtime-license-feature-contract-2026-08-26.md`
- Create: `docs/reviews/unified-runtime-license-ux-qa-2026-08-26.md`

- [ ] **Step 1: Write the feature contract**

Use the project template and include rows for:

```markdown
| 能力 | 数据/API/状态存在 | UI 入口存在 | 用户反馈存在 | 错误处理存在 | 空/加载/禁用状态 | 键盘/可访问性支持 | E2E/浏览器测试 | 状态 | 备注 |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| 查看授权状态 | 是 | 是 | 是 | 是 | 是 | 是 | 是 | 通过 | 设置 -> 模型 -> 授权管理 |
| 导出机器码 | 是 | 是 | 是 | 是 | 不适用 | 是 | 是 | 通过 | 可恢复入口 |
| 导入授权 | 是 | 是 | 是 | 是 | 是 | 是 | 是 | 通过 | 候选先校验后原子替换 |
| 刷新状态 | 是 | 是 | 是 | 是 | 是 | 是 | 是 | 通过 | 不重启应用 |
| 无授权阻止真实执行 | 是 | 是 | 是 | 是 | 是 | 是 | 是 | 通过 | Agent 创建和流启动之前阻止 |
| 开发/测试/生产策略一致 | 是 | 不适用 | 是 | 是 | 不适用 | 不适用 | 是 | 通过 | 仅资源位置随载体变化 |
```

- [ ] **Step 2: Write the final Chinese UX QA report**

Populate every section from `frontend-ux-qa/references/ux-audit-report-template.md`. The report must explicitly state:

- affected page/component/user path;
- main, auxiliary, and advanced content;
- browser command, source commit, isolated runtime/config roots, and screenshot basenames;
- keyboard and accessible-name checks for Import, Machine Code, and Refresh;
- desktop and narrow viewport results;
- empty/loading/error/success/disabled/destructive-state coverage;
- automated accessibility and visual-regression status as `未验证` if the project has no configured tool;
- P0/P1/P2/P3 findings and whether each was fixed;
- no claim about installed packages, Kylin/UOS targets, releases, or deployment.

- [ ] **Step 3: Sol-review and commit QA evidence/docs**

Precisely stage the browser smoke, sanitized evidence, feature contract, UX report, and no unrelated output. After final staged-bytes Sol PASS, commit:

```bash
git commit -m "test(license): verify unified browser authorization flow"
```

## Task 7: Run full local verification and perform same-root audit

**Files:**
- Verify only; do not edit unless a failure is proven related to this task.

- [ ] **Step 1: Run focused authorization suites**

```bash
cd hermes-local-lab/sources/hermes-agent
scripts/run_tests.sh tests/test_taiji_license.py tests/gateway/test_api_server_license.py tests/run_agent/test_taiji_license_final_guard.py -q
```

Expected: PASS.

- [ ] **Step 2: Run focused WebUI suites**

```bash
cd hermes-local-lab/sources/hermes-webui
../hermes-agent/venv/bin/python -m pytest -q tests/test_taiji_license_routes.py tests/test_product_diagnostics.py tests/test_onboarding_mvp.py tests/test_brand_privacy.py tests/test_expert_team_v2_runtime.py
npm run lint:runtime
```

Expected: PASS.

- [ ] **Step 3: Run Desktop and root security contracts**

```bash
cd apps/taiji-desktop
npm run check
node --test tests/*.test.js
cd ../..
hermes-local-lab/sources/hermes-agent/venv/bin/python -m unittest tests.test_unified_runtime_license_policy tests.test_linux_desktop_packaging_static tests.test_strict_build_toolchain_contract -v
```

Expected: PASS.

- [ ] **Step 4: Run the project full gate**

```bash
scripts/verify.sh --full
```

Expected: exit 0. Any skipped or unavailable browser/optional dependency is recorded exactly as the script reports and is not promoted to PASS.

- [ ] **Step 5: Run the canonical isolated browser smoke gate**

```bash
scripts/verify.sh --browser-smoke
```

Expected: exit 0. If prerequisites are missing and the script exits 3, mark that gate `未验证`; it does not replace the dedicated unified-license smoke from Task 5.

- [ ] **Step 6: Audit the repository for remaining exemptions and adjacent entry points**

Run:

```bash
rg -n --hidden --glob '!node_modules/**' --glob '!qa-evidence/**' --glob '!reports/**' --glob '!docs/superpowers/**' '开发环境无需授权|开发源码模式无需授权|license_not_required|status="not_required"|TAIJI_LICENSE_REQUIRED: ["\x27]0|TAIJI_LICENSE_MACHINE_BINDING_REQUIRED: ["\x27]0' .
rg -n 'require_valid_license\(' hermes-local-lab/sources/hermes-agent hermes-local-lab/sources/hermes-webui/api --glob '!tests/**'
```

Expected: the exemption search has no real-runtime matches; the guard search accounts for Agent final execution, API execution, WebUI chat, and expert-team execution entry points.

## Task 8: Final staged-bytes Sol audit, commit boundary, and normal push

**Files:**
- Review all task commits and any remaining tracked diff.

- [ ] **Step 1: Confirm identity and ownership before closeout**

```bash
pwd -P
git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short
git worktree list --porcelain
```

Expected: expected repository, `main`, one known writing owner, no unrelated dirty paths.

- [ ] **Step 2: Run the required final Sol views**

For any remaining staged bytes, the read-only Sol auditor must review:

```bash
git status --short
git diff
git diff --cached --name-status
git diff --cached --check
git diff --cached
```

Expected: PASS with no P0/P1/P2 findings and staged paths limited to this task. Any staged-byte change invalidates the review and requires rerunning affected tests and all five views.

- [ ] **Step 3: Commit any final report-only delta**

If the reports changed after the Task 6 commit, precisely stage only those report files, obtain a fresh Sol PASS, and commit:

```bash
git commit -m "docs: record unified license acceptance"
```

If no report delta exists, do not create an empty commit.

- [ ] **Step 4: Refresh remote state and prove no remote lead**

```bash
git fetch origin main
git merge-base --is-ancestor origin/main HEAD
git rev-list --left-right --count HEAD...origin/main
```

Expected: `origin/main` is an ancestor of local `HEAD`; output is `<local-ahead> 0`.

- [ ] **Step 5: Push normally and verify SHA alignment**

```bash
git push origin main
git rev-parse HEAD
git rev-parse origin/main
git ls-remote --heads origin main
```

Expected: all three SHA values match. Do not force-push, create/move a Tag, create a Release, package, install, deploy, or alter a target machine.

- [ ] **Step 6: Produce the final task status card and Chinese UX QA summary**

Report:

- exact commits and pushed SHA;
- root cause and affected entry points;
- changed behavior and compatibility impact for developers/tests;
- the representative licensed Electron smoke result and the six changed smoke
  scripts that received syntax-only verification, reported explicitly as such;
- focused and full verification commands with actual results;
- real-browser source/runtime/config boundary and screenshot links;
- UX QA status plus P0/P1/P2/P3 list;
- unverified installed, packaged, Kylin/UOS, Release, and deployment layers;
- rollback entry as normal `git revert <task-commit>` rather than history rewriting.
