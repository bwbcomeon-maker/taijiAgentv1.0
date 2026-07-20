"""
Shared pytest fixtures for webui-mvp tests.

TEST ISOLATION:
  Tests run against a SEPARATE server instance on an auto-derived test port
  with a completely separate state directory. Production data is never touched.
  The test state dir is wiped before each full test run and again on teardown.

PATH DISCOVERY:
  No hardcoded paths. Discovery order:
    1. Environment variables (HERMES_WEBUI_AGENT_DIR, HERMES_WEBUI_PYTHON, etc.)
    2. Sibling checkout heuristics relative to this repo
    3. Common install paths (~/.hermes/hermes-agent)
    4. System python3 as a last resort
"""
import json
import ipaddress
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
import webbrowser
import pytest

# ── Repo root discovery ────────────────────────────────────────────────────
# conftest.py lives at <repo>/tests/conftest.py
TESTS_DIR  = pathlib.Path(__file__).parent.resolve()
REPO_ROOT  = TESTS_DIR.parent.resolve()
HOME       = pathlib.Path.home()
HERMES_HOME = pathlib.Path(os.getenv('HERMES_HOME', str(HOME / '.hermes')))

# ── Test server config ────────────────────────────────────────────────────
# Port and state dir auto-derive from the repo path and pytest process when no
# env var is set, giving concurrent runs in the same worktree separate server
# resources. This is intentionally process-scoped: a second pytest invocation
# must never kill or delete the first invocation's server/state.
# Override with HERMES_WEBUI_TEST_PORT / HERMES_WEBUI_TEST_STATE_DIR to pin.

def _auto_test_port(repo_root, process_id=None) -> int:
    """Map repo path + pytest PID into the 20000-29999 test port range."""
    import hashlib
    repo_offset = int(hashlib.md5(str(repo_root).encode()).hexdigest(), 16) % 10000
    pid = os.getpid() if process_id is None else int(process_id)
    return 20000 + ((repo_offset + pid) % 10000)

def _auto_state_dir_name(repo_root, process_id=None) -> str:
    import hashlib
    h = hashlib.md5(str(repo_root).encode()).hexdigest()[:8]
    pid = os.getpid() if process_id is None else int(process_id)
    return f"webui-test-{h}-{pid}"


def _port_is_available(port: int) -> bool:
    """Return True only when a loopback listener can safely bind this port."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", int(port)))
    except OSError:
        return False
    finally:
        probe.close()
    return True


def _select_auto_test_port(repo_root) -> int:
    """Select a free process-scoped port without signalling its current owner."""
    first = _auto_test_port(repo_root)
    for offset in range(1000):
        candidate = 20000 + ((first - 20000 + offset) % 10000)
        if _port_is_available(candidate):
            return candidate
    raise RuntimeError("unable to find a free Hermes WebUI test port")

_EXPLICIT_TEST_PORT = os.getenv('HERMES_WEBUI_TEST_PORT')
TEST_PORT      = int(_EXPLICIT_TEST_PORT) if _EXPLICIT_TEST_PORT else _select_auto_test_port(REPO_ROOT)
TEST_BASE      = f"http://127.0.0.1:{TEST_PORT}"
TEST_STATE_DIR = pathlib.Path(os.getenv(
    'HERMES_WEBUI_TEST_STATE_DIR',
    str(HERMES_HOME / _auto_state_dir_name(REPO_ROOT))
))
TEST_WORKSPACE = TEST_STATE_DIR / 'test-workspace'
TEST_CUSTOMER_WORKSPACE_ROOT = (
    HOME
    / ".cache"
    / f"taiji-webui-customer-{_auto_state_dir_name(REPO_ROOT)}"
)
TEST_SERVER_LOG = TEST_STATE_DIR / "server-test.log"
TEST_SERVER_FAILURE_LOG = (
    pathlib.Path(tempfile.gettempdir())
    / f"taiji-webui-test-server-{TEST_PORT}-{os.getpid()}.log"
)

# Publish at module level so api.config, _pytest_port.py, and any test module
# importing stateful API code during collection see the isolated test paths.
#
# Direct assignment is intentional for production-risk paths: tests that import
# api.config/api.models in the pytest process must never inherit the real
# ~/.hermes state tree before the server subprocess fixture starts.
os.environ['HERMES_WEBUI_TEST_PORT'] = str(TEST_PORT)
os.environ['HERMES_WEBUI_TEST_STATE_DIR'] = str(TEST_STATE_DIR)
os.environ['HERMES_WEBUI_STATE_DIR'] = str(TEST_STATE_DIR)
os.environ['HERMES_WEBUI_DEFAULT_WORKSPACE'] = str(TEST_WORKSPACE)
os.environ['HERMES_HOME'] = str(TEST_STATE_DIR)
os.environ['HERMES_BASE_HOME'] = str(TEST_STATE_DIR)
# Hermes Agent sessions may inherit HERMES_CONFIG_PATH pointing at the live
# ~/.hermes/config.yaml.  Override it before any product modules are imported so
# tests that read/write config.yaml stay inside the isolated test home.
os.environ['HERMES_CONFIG_PATH'] = str(TEST_STATE_DIR / 'config.yaml')


def _reject_external_browser(url, *_args, **_kwargs):
    raise AssertionError(
        "tests must not open the user's default browser; "
        f"mock the browser call explicitly (attempted URL: {url!r})"
    )


@pytest.fixture(autouse=True)
def _block_external_browser(monkeypatch):
    """Fail closed when a WebUI test leaks into the user's real browser."""
    monkeypatch.setattr(webbrowser, "open", _reject_external_browser)
    monkeypatch.setattr(webbrowser, "open_new", _reject_external_browser)
    monkeypatch.setattr(webbrowser, "open_new_tab", _reject_external_browser)


@pytest.fixture(autouse=True)
def _isolate_hermes_config_path():
    """Keep profile/.env side effects from leaking the live config path across tests."""
    isolated_config_path = str(TEST_STATE_DIR / 'config.yaml')
    os.environ['HERMES_CONFIG_PATH'] = isolated_config_path
    yield
    os.environ['HERMES_CONFIG_PATH'] = isolated_config_path


@pytest.fixture(autouse=True)
def _restore_profile_home_globals():
    """Restore HERMES_HOME / HERMES_BASE_HOME after every test.

    Several tests call ``api.profiles.switch_profile()`` (or set HERMES_HOME
    directly) which mutates ``os.environ['HERMES_HOME']`` IN PLACE — not via
    monkeypatch — so the change is not auto-reverted at test teardown. In the
    normal sequential run the next test usually re-establishes its own profile so
    the leak is masked, but under pytest-shard (or pytest-randomly) the leaked
    HERMES_HOME points at a deleted tmpdir and breaks any later test whose
    config/profile resolution reads it (e.g. test_title_aux_routing's
    background-worker profile routing, which then falls back to DEFAULT_CONFIG
    where ``model`` is an empty string). Snapshotting at the conftest level fixes
    the whole class at once, regardless of which test does the leaking.
    """
    saved_home = os.environ.get('HERMES_HOME')
    saved_base = os.environ.get('HERMES_BASE_HOME')
    # Re-derive the cached base-home global BEFORE the test runs too: a prior
    # test's teardown ordering (monkeypatch restoring sys.modules['api.profiles']
    # after this fixture's teardown) can leave the live module's
    # _DEFAULT_HERMES_HOME stale. Fixing it at setup time guarantees each test
    # starts from a base root that matches the current (restored) env.
    _rederive_default_hermes_home()
    yield
    for key, val in (('HERMES_HOME', saved_home), ('HERMES_BASE_HOME', saved_base)):
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val
    _rederive_default_hermes_home()


def _rederive_default_hermes_home():
    """Recompute api.profiles._DEFAULT_HERMES_HOME from the current env.

    api.profiles caches the base home at import time. A test that re-imports
    api.profiles under a temporary HERMES_BASE_HOME (e.g. test_profile_env_isolation)
    corrupts that global to a now-deleted tmpdir, making get_hermes_home_for_profile
    resolve later tests' profiles under the dead path. Re-deriving keeps it honest.
    """
    prof_mod = sys.modules.get('api.profiles')
    if prof_mod is not None and hasattr(prof_mod, '_resolve_base_hermes_home'):
        try:
            prof_mod._DEFAULT_HERMES_HOME = prof_mod._resolve_base_hermes_home()
        except Exception:
            pass


def _clear_loaded_mapping(module, name, lock_name=None):
    """Clear one already-imported in-process registry without importing product code."""
    mapping = getattr(module, name, None)
    if mapping is None or not callable(getattr(mapping, "clear", None)):
        return
    lock = getattr(module, lock_name, None) if lock_name else None
    try:
        if lock is None:
            mapping.clear()
        else:
            with lock:
                mapping.clear()
    except Exception:
        # Cleanup must not hide the test's real assertion.  A malformed fake
        # registry will be replaced/restored by that test's own monkeypatch.
        pass


def _reset_loaded_runtime_state():
    """Remove mutable runtime state that must never cross a pytest boundary.

    WebUI unit tests exercise streaming and self-update code in the pytest
    process.  Those production modules intentionally keep streams, active
    runs, cached agents, and update summaries alive across requests.  In tests,
    however, a failed assertion used to leave those registries populated.  A
    later update test would then wait for a synthetic stream forever while
    holding ``_apply_lock``, causing a cascade of unrelated failures.

    Only modules already imported by a test are touched, so this fixture does
    not make lightweight/static tests import the full runtime.
    """
    config_module = sys.modules.get("api.config")
    if config_module is not None:
        for mapping_name, lock_name in (
            ("STREAMS", "STREAMS_LOCK"),
            ("ACTIVE_RUNS", "ACTIVE_RUNS_LOCK"),
            ("SESSION_AGENT_CACHE", "SESSION_AGENT_CACHE_LOCK"),
            ("CANCEL_FLAGS", None),
            ("AGENT_INSTANCES", None),
            ("SESSION_AGENT_LOCKS", None),
            ("STREAM_PARTIAL_TEXT", None),
            ("STREAM_REASONING_TEXT", None),
            ("STREAM_LIVE_TOOL_CALLS", None),
        ):
            _clear_loaded_mapping(config_module, mapping_name, lock_name)

    updates_module = sys.modules.get("api.updates")
    if updates_module is not None:
        cache_lock = getattr(updates_module, "_cache_lock", None)
        try:
            if cache_lock is None:
                _clear_loaded_mapping(updates_module, "_summary_cache")
            else:
                with cache_lock:
                    summary_cache = getattr(updates_module, "_summary_cache", None)
                    if callable(getattr(summary_cache, "clear", None)):
                        summary_cache.clear()
        except Exception:
            pass
        update_cache = getattr(updates_module, "_update_cache", None)
        if isinstance(update_cache, dict):
            update_cache.clear()
            update_cache.update({
                "webui": None,
                "agent": None,
                "checked_at": 0,
                "include_agent": True,
            })
        if hasattr(updates_module, "_check_in_progress"):
            updates_module._check_in_progress = False

    hermes_config = sys.modules.get("hermes_cli.config")
    if hermes_config is not None:
        for cache_name in ("_LOAD_CONFIG_CACHE", "_RAW_CONFIG_CACHE"):
            _clear_loaded_mapping(hermes_config, cache_name)


@pytest.fixture(autouse=True)
def _isolate_in_process_runtime_state():
    """Give every test a clean WebUI runtime-state boundary."""
    _reset_loaded_runtime_state()
    yield
    _reset_loaded_runtime_state()


# ── Server script: always relative to repo root ───────────────────────────
SERVER_SCRIPT = REPO_ROOT / 'server.py'
if not SERVER_SCRIPT.exists():
    raise RuntimeError(
        f"server.py not found at {SERVER_SCRIPT}. "
        "Is conftest.py in the tests/ subdirectory of the repo?"
    )

# ── Hermes agent discovery (mirrors api/config._discover_agent_dir) ───────
def _discover_agent_dir() -> pathlib.Path:
    candidates = [
        os.getenv('HERMES_WEBUI_AGENT_DIR', ''),
        str(HERMES_HOME / 'hermes-agent'),
        str(REPO_ROOT.parent / 'hermes-agent'),
        str(HOME / '.hermes' / 'hermes-agent'),
        str(HOME / 'hermes-agent'),
    ]
    for c in candidates:
        if not c:
            continue
        p = pathlib.Path(c).expanduser()
        if p.exists() and (p / 'run_agent.py').exists():
            return p.resolve()
    return None

# ── Python discovery (mirrors api/config._discover_python) ────────────────
def _discover_python(agent_dir) -> str:
    if os.getenv('HERMES_WEBUI_PYTHON'):
        return os.getenv('HERMES_WEBUI_PYTHON')
    if agent_dir:
        for venv_name in ('.venv', 'venv'):
            venv_py = agent_dir / venv_name / 'bin' / 'python'
            if venv_py.exists():
                return str(venv_py)
    local_venv = REPO_ROOT / '.venv' / 'bin' / 'python'
    if local_venv.exists():
        return str(local_venv)
    return shutil.which('python3') or shutil.which('python') or 'python3'

HERMES_AGENT = _discover_agent_dir()
VENV_PYTHON  = _discover_python(HERMES_AGENT)

# Work dir: agent dir if found, else repo root
WORKDIR = str(HERMES_AGENT) if HERMES_AGENT else str(REPO_ROOT)

# ── Agent availability detection ─────────────────────────────────────────────
# Tests that require hermes-agent modules (cron, skills, approval, chat/stream)
# are skipped when the agent isn't installed, instead of failing with 500 errors.
AGENT_AVAILABLE = HERMES_AGENT is not None

def _check_agent_modules():
    """Verify hermes-agent Python modules are actually importable."""
    if not HERMES_AGENT:
        return False
    try:
        import importlib
        # These are the modules that cause 500 errors when missing
        for mod in ['cron.jobs', 'tools.skills_tool']:
            importlib.import_module(mod)
        return True
    except (ImportError, ModuleNotFoundError):
        return False

AGENT_MODULES_AVAILABLE = _check_agent_modules()

# pytest marker: skip tests that need hermes-agent when it's not present
requires_agent = pytest.mark.skipif(
    not AGENT_AVAILABLE,
    reason="hermes-agent not found (skipping agent-dependent test)"
)
requires_agent_modules = pytest.mark.skipif(
    not AGENT_MODULES_AVAILABLE,
    reason="hermes-agent Python modules not importable (cron, skills_tool)"
)

def pytest_configure(config):
    config.addinivalue_line("markers", "requires_agent: skip when hermes-agent dir is not found")
    config.addinivalue_line("markers", "requires_agent_modules: skip when hermes-agent Python modules are not importable")


# ── Disable AWS IMDS probing for the pytest session ────────────────────────
# Background: when hermes-agent's bedrock_adapter / botocore credential chain
# runs during test execution (e.g. provider catalog enumeration triggered by
# api/config.py imports), botocore probes the EC2 Instance Metadata Service at
# 169.254.169.254 looking for an instance role. On VPS hosts where IMDS is
# reachable but rate-limited (HTTP 429) or non-responsive, this dominates wall
# time and turns a 161s test run into 600+s.
#
# Tests have no legitimate reason to call IMDS — the bedrock-related tests use
# explicit mocks or env-var creds. Setting AWS_EC2_METADATA_DISABLED before
# anything imports botocore is the supported way to silence the probe (matches
# the guard the hermes_cli/doctor.py command already uses in its parallel-probe
# block).
#
# Setting this here instead of in a fixture so it lands BEFORE any test-file
# imports trigger botocore initialisation.
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

# ── Permanent os.execv guard for the pytest session ────────────────────────
# Several tests in tests/test_update_banner_fixes.py exercise
# api.updates._schedule_restart(), which spawns a DAEMON thread that sleeps
# for a short delay and then calls ``os.execv(sys.executable, sys.argv)``.
# Those tests monkeypatch ``os.execv`` to a no-op for the test scope, but
# monkeypatch teardown happens at test exit — if the daemon thread has not
# yet woken up by then (system load, GC pause, _apply_lock contention), the
# real ``os.execv`` is restored before the thread fires it. The daemon then
# REPLACES the pytest process image with a fresh ``pytest tests/ -q ...``
# invocation, looking from the outside like pytest "hangs at 99%" and then
# restarts the entire suite from 0% — a self-perpetuating loop.
#
# Daemon threads cannot be reliably joined from a test fixture (they live in
# ``api.updates`` module scope), so the only safe answer is to render
# ``os.execv`` permanently inert for the pytest session. Production code is
# unaffected because production never imports this conftest.
#
# Tests that need to verify execv WAS called still monkeypatch it themselves
# — their patched version takes precedence over this no-op wrapper for the
# test's lifetime, and the no-op only kicks in after teardown for daemon
# threads that wake up late.
_real_execv = os.execv

def _pytest_session_safe_execv(_exe, _args):  # pragma: no cover — never called in prod
    # Drop the call on the floor. A late-firing daemon thread from
    # _schedule_restart() must not be able to re-exec the pytest process.
    return None

os.execv = _pytest_session_safe_execv

# ── Hermetic network isolation ─────────────────────────────────────────────
# Tests must not reach the public internet. Outbound to Anthropic / OpenAI /
# Amazon / OpenRouter / etc. is forbidden by default. The test suite already
# mocks every legitimate outbound (probe_provider_endpoint, get_available_models,
# urlopen calls inside api/config.py), so a real outbound socket is either a
# missing mock, a leaked credential triggering an SDK init, or an unintended
# regression like the one PR #1970 introduced where a new code path bypassed
# an existing mock and tried to hit the real LM Studio host.
#
# This module-level monkey-patch wraps all socket connection primitives so any
# non-loopback / non-documentation destination raises
# OSError("hermes test network isolation"). Tests that deliberately
# attempt outbound (only test_dns_resolution_failure today) opt back in
# explicitly via the `allow_outbound_network` fixture below.
#
# Allowed destinations (silent pass-through):
#   - 127.0.0.0/8     loopback
#   - ::1             IPv6 loopback
#   - AF_UNIX filesystem/abstract-namespace sockets
#   - RFC documentation networks (TEST-NET-1/2/3 and 2001:db8::/32)
#   - hostnames `localhost`, `*.localhost`, `*.test`, `*.example`,
#     and `*.invalid`
#
# A test that opts in via the `allow_outbound_network` fixture sees the real
# socket primitives.
import socket as _hermes_test_socket
_REAL_CREATE_CONNECTION = _hermes_test_socket.create_connection
_REAL_SOCKET_CONNECT = _hermes_test_socket.socket.connect
_REAL_SOCKET_CONNECT_EX = _hermes_test_socket.socket.connect_ex

_HERMES_ALLOWED_TEST_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8",
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "::1/128",
        "2001:db8::/32",
    )
)


def _hermes_addr_is_local(host: str) -> bool:
    """Return True only for loopback, Unix, or inert test destinations."""
    if isinstance(host, bytes):
        return host.startswith((b"/", b"\0"))
    if not isinstance(host, str):
        return False
    h = host.strip().lower()
    if not h:
        return False
    if h.startswith(("/", "\0")):
        return True
    if h == "localhost" or h.endswith(".localhost"):
        return True
    if h.endswith((".test", ".invalid", ".example")):
        return True
    try:
        address = ipaddress.ip_address(h.split("%", 1)[0])
    except ValueError:
        return False
    return any(address in network for network in _HERMES_ALLOWED_TEST_NETWORKS)


def _hermes_address_host(address):
    if isinstance(address, tuple):
        return address[0] if address else ""
    return address


def _hermes_blocked_create_connection(address, *a, **kw):
    host = _hermes_address_host(address)
    if _hermes_addr_is_local(host):
        return _REAL_CREATE_CONNECTION(address, *a, **kw)
    raise OSError(
        f"hermes test network isolation: outbound socket to {address!r} is blocked. "
        f"Tests should mock urllib.request.urlopen / requests / socket.create_connection. "
        f"If a test genuinely needs real outbound, request the allow_outbound_network fixture."
    )


def _hermes_blocked_socket_connect(self, address):
    host = _hermes_address_host(address)
    if _hermes_addr_is_local(host):
        return _REAL_SOCKET_CONNECT(self, address)
    raise OSError(
        f"hermes test network isolation: socket.connect to {address!r} is blocked."
    )


def _hermes_blocked_socket_connect_ex(self, address):
    host = _hermes_address_host(address)
    if _hermes_addr_is_local(host):
        return _REAL_SOCKET_CONNECT_EX(self, address)
    raise OSError(
        f"hermes test network isolation: socket.connect_ex to {address!r} is blocked."
    )


for _guard in (
    _hermes_blocked_create_connection,
    _hermes_blocked_socket_connect,
    _hermes_blocked_socket_connect_ex,
):
    _guard._taiji_test_network_block = True


_hermes_test_socket.create_connection = _hermes_blocked_create_connection
_hermes_test_socket.socket.connect = _hermes_blocked_socket_connect
_hermes_test_socket.socket.connect_ex = _hermes_blocked_socket_connect_ex


@pytest.fixture
def allow_outbound_network(monkeypatch):
    """Opt-in to real outbound network for the duration of one test.

    Swaps all three socket connection primitives back to the
    real (unwrapped) implementations for this test only, then monkeypatch
    teardown restores the wrapped versions. Direct swap is more reliable
    than a module-global toggle on CI runners where wrapper-closure
    lookup semantics can surprise.

    Use sparingly. Today zero tests in the repo call this — the previous
    test_dns_resolution_failure case was rewritten to mock socket.getaddrinfo
    instead, which is fully hermetic.
    """
    monkeypatch.setattr(_hermes_test_socket, "create_connection", _REAL_CREATE_CONNECTION)
    monkeypatch.setattr(_hermes_test_socket.socket, "connect", _REAL_SOCKET_CONNECT)
    monkeypatch.setattr(_hermes_test_socket.socket, "connect_ex", _REAL_SOCKET_CONNECT_EX)
    yield




# ── Environment isolation for tests ────────────────────────────────────────
# HERMES_WEBUI_SKIP_ONBOARDING is set by hosting providers (e.g. Agent37) and
# by some isolated test harnesses to short-circuit the onboarding wizard.
# When it leaks into the pytest environment, tests that exercise the wizard
# code paths (apply_onboarding_setup, etc.) fail because the function returns
# early without writing config files.
#
# This autouse fixture removes the variable for the test session. Tests that
# specifically need to validate the SKIP_ONBOARDING short-circuit can opt back
# in with `monkeypatch.setenv("HERMES_WEBUI_SKIP_ONBOARDING", "1")`.
@pytest.fixture(autouse=True, scope="session")
def _strip_skip_onboarding_env():
    prior = os.environ.pop("HERMES_WEBUI_SKIP_ONBOARDING", None)
    yield
    if prior is not None:
        os.environ["HERMES_WEBUI_SKIP_ONBOARDING"] = prior

def pytest_collection_modifyitems(config, items):
    """Auto-skip agent-dependent tests when hermes-agent is not available.

    Instead of requiring markers on every test function, we pattern-match
    test names to known categories that depend on hermes-agent modules.
    This keeps the test files clean and ensures new cron/skills tests
    get auto-skipped without manual annotation.
    """
    if AGENT_MODULES_AVAILABLE:
        return  # everything available, run all tests

    # Exact list of tests known to fail without hermes-agent.
    # These hit server endpoints that import cron.jobs, tools.skills_tool,
    # or require a running agent backend — returning 500 without the agent.
    _AGENT_DEPENDENT_TESTS = {
        # Cron endpoints (need cron.jobs module)
        'test_crons_list',
        'test_crons_list_has_required_fields',
        'test_crons_output_requires_job_id',
        'test_crons_output_real_job',
        'test_crons_run_nonexistent',
        'test_cron_create_success',
        'test_cron_update_unknown_job_404',
        'test_cron_delete_unknown_404',
        'test_crons_output_limit_param',
        'test_delivery_options_returns_200',
        'test_delivery_options_has_platforms',
        'test_delivery_options_structure',
        'test_delivery_options_includes_common_platforms',
        'test_delivery_options_local_label',
        # Skills endpoints (need tools.skills_tool module)
        'test_skills_list',
        'test_skills_list_has_required_fields',
        'test_skills_content_known',
        'test_skills_content_requires_name',
        'test_skills_search_returns_subset',
        'test_skill_save_delete_roundtrip',
        'test_skill_delete_unknown_404',
        # Agent backend (need running AIAgent)
        'test_chat_stream_opens_successfully',
        'test_approval_submit_and_respond',
        # Security redaction (flaky — session state varies across test ordering)
        'test_api_sessions_list_redacts_titles',
        # Workspace path (macOS /tmp -> /private/tmp symlink)
        'test_new_session_inherits_workspace',
        'test_workspace_add_valid',
        'test_workspace_rename',
        'test_last_workspace_updates_on_session_update',
        'test_new_session_inherits_last_workspace',
    }

    skip_marker = pytest.mark.skip(reason="requires hermes-agent (not installed)")
    skipped = 0

    for item in items:
        if item.name in _AGENT_DEPENDENT_TESTS:
            item.add_marker(skip_marker)
            skipped += 1

    if skipped:
        print(f"\nWARNING: hermes-agent not found; {skipped} agent-dependent tests will be skipped\n")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _post(base, path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {}


def _wait_for_server(base, timeout=20, proc=None):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(base + "/health", timeout=2) as r:
                if json.loads(r.read()).get("status") == "ok":
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def server_log_tail(path, max_chars=4000):
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"<unable to read server log: {exc}>"
    return text[-max_chars:] if text else "<server log is empty>"


# ── Session-scoped test server ────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def test_server():
    """
    Start an isolated test server on TEST_PORT with a clean state directory.
    Paths are discovered dynamically -- no hardcoded absolute path assumptions.
    """
    # Never kill an existing port owner. A collision may be another full test
    # run or a live development task, so fail closed with an actionable error.
    if not _port_is_available(TEST_PORT):
        pytest.fail(
            f"Hermes WebUI test port {TEST_PORT} is already occupied; "
            "refusing to signal or reuse the existing process. "
            "Unset HERMES_WEBUI_TEST_PORT for automatic per-process isolation."
        )

    # Clean slate
    if TEST_STATE_DIR.exists():
        shutil.rmtree(TEST_STATE_DIR)
    if TEST_CUSTOMER_WORKSPACE_ROOT.exists():
        shutil.rmtree(TEST_CUSTOMER_WORKSPACE_ROOT)
    TEST_STATE_DIR.mkdir(parents=True)
    TEST_WORKSPACE.mkdir(parents=True)
    TEST_CUSTOMER_WORKSPACE_ROOT.mkdir(parents=True)

    # Symlink real skills into test home so skill-related tests work,
    # but all write-heavy state stays isolated.
    real_skills  = HERMES_HOME / 'skills'
    test_skills  = TEST_STATE_DIR / 'skills'
    if real_skills.exists() and not test_skills.exists():
        test_skills.symlink_to(real_skills)

    # Isolated cron state
    (TEST_STATE_DIR / 'cron').mkdir(parents=True, exist_ok=True)

    # Expose TEST_STATE_DIR to the test process itself so that tests which write
    # directly to state.db (e.g. test_gateway_sync.py) always use the same path
    # as the server.  Other test files (test_auth_sessions.py) may override
    # HERMES_WEBUI_STATE_DIR for their own purposes, but HERMES_WEBUI_TEST_STATE_DIR
    # is reserved for this mapping and is never overridden by individual test files.
    # Export both port and state-dir as env vars so individual test files
    # can read them without importing conftest (avoids circular imports).
    os.environ.setdefault('HERMES_WEBUI_TEST_PORT', str(TEST_PORT))
    # os.environ already set at module level above; no-op here.

    env = os.environ.copy()
    # Strip ANY real credential env var so the test subprocess never inherits
    # production creds. The test server uses a mock/isolated config — no real
    # API calls are made, no real OAuth flow runs, no real cloud SDK should
    # ever be initialised with usable credentials.
    #
    # Without this strip, a stray credential left in the runner's env was
    # observed making outbound TLS to a real provider during test runs.
    # See investigation notes in pytest-pitfalls SKILL §B.3.
    _CRED_ENV_PREFIXES = (
        # LLM providers
        'OPENROUTER_API_KEY', 'OPENAI_API_KEY', 'OPENAI_BASE_URL',
        'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN',
        'GOOGLE_API_KEY', 'GOOGLE_APPLICATION_CREDENTIALS',
        'DEEPSEEK_API_KEY', 'XIAOMI_API_KEY',
        'XAI_API_KEY', 'MISTRAL_API_KEY', 'OLLAMA_API_KEY',
        'GROQ_API_KEY', 'TOGETHER_API_KEY', 'PERPLEXITY_API_KEY',
        'CEREBRAS_API_KEY', 'COHERE_API_KEY', 'FIREWORKS_API_KEY',
        'NOUS_API_KEY', 'NOVITA_API_KEY', 'TENCENT_API_KEY',
        'BIGMODEL_API_KEY', 'GLM_API_KEY', 'STEPFUN_API_KEY',
        'MINIMAX_API_KEY', 'LM_API_KEY', 'LMSTUDIO_API_KEY',
        'AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_ENDPOINT',
        # AWS — must be stripped or botocore probes IMDS / picks up real creds
        'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
        'AWS_PROFILE', 'AWS_BEARER_TOKEN_BEDROCK',
        # Memory providers, telemetry, dashboards
        'MEM0_API_KEY', 'HONCHO_API_KEY', 'SUPERMEMORY_API_KEY',
        # Messaging / gateway
        'TELEGRAM_BOT_TOKEN', 'DISCORD_BOT_TOKEN', 'SLACK_BOT_TOKEN',
        'SIGNAL_API_TOKEN', 'WHATSAPP_API_TOKEN',
        # Browser / image-gen / search
        'FIRECRAWL_API_KEY', 'FAL_KEY', 'TAVILY_API_KEY',
        'SERPER_API_KEY', 'BRAVE_API_KEY',
        # Github tokens (PR/issue tools shouldn't be exercised in tests)
        'GH_TOKEN', 'GITHUB_TOKEN',
    )
    for _k in list(env):
        if any(_k.startswith(p) for p in _CRED_ENV_PREFIXES):
            del env[_k]
    # Belt-and-suspenders: keep IMDS disabled in the spawn env too (we set it
    # at module level above for the pytest process, but make it explicit here
    # so it's never accidentally cleared by an env.update later).
    env["AWS_EC2_METADATA_DISABLED"] = "true"
    # Activate the same network-isolation block in the test_server subprocess
    # that conftest.py installs in the pytest process. server.py reads this
    # env var at import time and installs an identical socket-block guard.
    # Without this, the subprocess can make outbound requests that the
    # pytest-side block can't see.
    env["HERMES_WEBUI_TEST_NETWORK_BLOCK"] = "1"
    env.update({
        "HERMES_WEBUI_WORKSPACE_GIT_DESTRUCTIVE": "1",
        # Small archive-extraction cap so the zip-bomb guard is exercisable
        # against the out-of-process test server (the real 10x-upload default is
        # ~200MB — impractical to exceed in a test). 5MB is far above any other
        # test's archive payload, so only the bomb test trips it.
        "HERMES_WEBUI_MAX_EXTRACTED_MB":  "5",
        "HERMES_WEBUI_PORT":              str(TEST_PORT),
        "HERMES_WEBUI_HOST":              "127.0.0.1",
        "HERMES_WEBUI_STATE_DIR":         str(TEST_STATE_DIR),
        "HERMES_WEBUI_DEFAULT_WORKSPACE": str(TEST_WORKSPACE),
        "HERMES_WEBUI_DEFAULT_MODEL":     "openai/gpt-5.4-mini",
        "HERMES_HOME":                    str(TEST_STATE_DIR),
        "HERMES_CONFIG_PATH":             str(TEST_STATE_DIR / 'config.yaml'),
        # Belt-and-suspenders: HERMES_BASE_HOME hard-locks _DEFAULT_HERMES_HOME
        # in api/profiles.py to the test state dir regardless of profile switching
        # or any os.environ mutation that happens inside the server process.
        # Without this, a profile switch or active_profile file in the real
        # ~/.hermes can redirect _get_active_hermes_home() out of the sandbox,
        # causing onboarding writes (config.yaml, .env) to land in the production
        # ~/.hermes/profiles/webui/ and overwrite real API keys.
        "HERMES_BASE_HOME":               str(TEST_STATE_DIR),
        "HERMES_WEBUI_PASSWORD":          "",
    })

    # Pass agent dir if discovered so server.py doesn't have to re-discover
    if HERMES_AGENT:
        env["HERMES_WEBUI_AGENT_DIR"] = str(HERMES_AGENT)

    server_log_path = TEST_SERVER_LOG
    server_log = server_log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [VENV_PYTHON, str(SERVER_SCRIPT)],
        cwd=WORKDIR,
        env=env,
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )

    if not _wait_for_server(TEST_BASE, timeout=20, proc=proc):
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        server_log.flush()
        server_log.close()
        pytest.fail(
            f"Test server on port {TEST_PORT} did not start within 20s.\n"
            f"  server.py : {SERVER_SCRIPT}\n"
            f"  python    : {VENV_PYTHON}\n"
            f"  agent dir : {HERMES_AGENT}\n"
            f"  workdir   : {WORKDIR}\n"
            f"  log       : {server_log_path}\n"
            f"--- server log tail ---\n{server_log_tail(server_log_path)}"
        )

    try:
        yield proc
    finally:
        unexpected_exit = proc.poll() is not None
        if not unexpected_exit:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        server_log.flush()
        server_log.close()

        if unexpected_exit and server_log_path.exists():
            try:
                shutil.copy2(server_log_path, TEST_SERVER_FAILURE_LOG)
            except Exception:
                pass

        try:
            shutil.rmtree(TEST_STATE_DIR)
        except Exception:
            pass
        try:
            shutil.rmtree(TEST_CUSTOMER_WORKSPACE_ROOT)
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _fail_fast_if_test_server_exits(test_server):
    """Stop at the first unexpected server exit instead of cascading failures."""
    if test_server.poll() is not None:
        pytest.fail(
            f"Test server exited with code {test_server.returncode} before test setup.\n"
            f"--- server log tail ---\n{server_log_tail(TEST_SERVER_LOG)}"
        )
    yield
    if test_server.poll() is not None:
        pytest.fail(
            f"Test server exited with code {test_server.returncode} during the test.\n"
            f"--- server log tail ---\n{server_log_tail(TEST_SERVER_LOG)}"
        )


# ── Test base URL ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def base_url():
    return TEST_BASE


# ── Per-test model cache invalidation ────────────────────────────────────────
# The TTL cache for get_available_models() persists across tests within the
# same process. Tests that modify cfg in-memory won't trigger the mtime path,
# so the cache must be explicitly invalidated after each test that exercises
# provider/model detection.

@pytest.fixture(autouse=True)
def _invalidate_models_cache_after_test():
    """Force the TTL cache to be cleared before and after every test.

    This prevents state bleed where a test that calls get_available_models()
    populates the cache with a particular config, and the next test sees stale
    results even though it has mutated _cfg_cache in-memory.
    """
    try:
        from api.config import invalidate_models_cache
        invalidate_models_cache()
    except Exception:
        pass
    yield
    try:
        from api.config import invalidate_models_cache
        invalidate_models_cache()
    except Exception:
        pass


# ── Per-test session cleanup ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def cleanup_test_sessions():
    """
    Yields a list for tests to register created session IDs.
    Deletes all registered sessions after each test.
    Resets last_workspace to the test workspace to prevent state bleed.
    """
    created: list[str] = []
    yield created

    for sid in created:
        try:
            _post(TEST_BASE, "/api/session/delete", {"session_id": sid})
        except Exception:
            pass

    try:
        _post(TEST_BASE, "/api/sessions/cleanup_zero_message")
    except Exception:
        pass

    try:
        last_ws_file = TEST_STATE_DIR / "last_workspace.txt"
        last_ws_file.write_text(str(TEST_WORKSPACE), encoding='utf-8')
    except Exception:
        pass


# ── Convenience helpers ────────────────────────────────────────────────────────

def make_session_tracked(created_list, ws=None):
    """
    Create a session on the test server and register it for cleanup.

    Usage:
        def test_something(cleanup_test_sessions):
            sid, ws = make_session_tracked(cleanup_test_sessions)
    """
    body = {}
    if ws:
        body["workspace"] = str(ws)
    d = _post(TEST_BASE, "/api/session/new", body)
    sid = d["session"]["session_id"]
    # Internal runtime-home workspaces are deliberately omitted by the public
    # session projection. Tests still need the isolated workspace for cleanup.
    ws_path = pathlib.Path(d["session"].get("workspace") or ws or TEST_WORKSPACE)
    created_list.append(sid)
    return sid, ws_path
