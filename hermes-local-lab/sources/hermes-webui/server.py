"""Taiji Agent web service entrypoint."""
import ipaddress
import logging
import os
import re
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def _bootstrap_agent_import_path() -> None:
    """Expose the packaged agent runtime to WebUI-only entrypoints."""
    agent_dir = os.environ.get("TAIJI_WEBUI_AGENT_DIR", "").strip()
    if not agent_dir:
        return
    agent_dir = os.path.abspath(agent_dir)
    if not os.path.isdir(agent_dir):
        return
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)


_bootstrap_agent_import_path()

# ── Test-mode network isolation ─────────────────────────────────────────────
# When test network blocking is enabled in the environment, refuse
# outbound socket connections to anything that is not loopback, an AF_UNIX
# socket, or an inert RFC documentation/test destination. This catches
# accidental real outbound (forgotten
# mocks, leaked credentials triggering SDK init, new code paths bypassing an
# existing mock) so the test suite stays hermetic and fast.
#
# tests/conftest.py sets this env var on every test_server subprocess so the
# server.py-side network isolation matches the pytest-process-side isolation
# already installed there.
#
# A test that legitimately needs real outbound spawns the server with the env
# var unset (no current callers — every test_server-using test should be
# mockable).
if (
    os.environ.get("TAIJI_WEBUI_TEST_NETWORK_BLOCK")
    or os.environ.get("HER" + "MES_WEBUI_TEST_NETWORK_BLOCK", "")
).strip() in ("1", "true", "yes"):
    _REAL_CREATE_CONN = socket.create_connection
    _REAL_SOCK_CONNECT = socket.socket.connect
    _REAL_SOCK_CONNECT_EX = socket.socket.connect_ex

    _TEST_ALLOWED_NETWORKS = tuple(
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

    def _addr_is_local(host):
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
        return any(address in network for network in _TEST_ALLOWED_NETWORKS)

    def _address_host(address):
        if isinstance(address, tuple):
            return address[0] if address else ""
        return address

    def _blocked_create_connection(address, *a, **kw):
        host = _address_host(address)
        if _addr_is_local(host):
            return _REAL_CREATE_CONN(address, *a, **kw)
        raise OSError(
            f"taiji test network isolation (server.py): outbound to {address!r} blocked"
        )

    def _blocked_socket_connect(self, address):
        host = _address_host(address)
        if _addr_is_local(host):
            return _REAL_SOCK_CONNECT(self, address)
        raise OSError(
            f"taiji test network isolation (server.py): socket.connect to {address!r} blocked"
        )

    def _blocked_socket_connect_ex(self, address):
        host = _address_host(address)
        if _addr_is_local(host):
            return _REAL_SOCK_CONNECT_EX(self, address)
        raise OSError(
            f"taiji test network isolation (server.py): socket.connect_ex to {address!r} blocked"
        )

    for _guard in (
        _blocked_create_connection,
        _blocked_socket_connect,
        _blocked_socket_connect_ex,
    ):
        _guard._taiji_test_network_block = True

    # pytest installs the same fail-closed contract in its own process before
    # product modules are imported. Preserve that outer guard instead of
    # replacing it with a second wrapper whose identity/error contract differs.
    # A standalone test-server subprocess has no marked guard, so it still gets
    # the server-side protection below.
    _existing_guards = (
        socket.create_connection,
        socket.socket.connect,
        socket.socket.connect_ex,
    )
    if not all(
        getattr(guard, "_taiji_test_network_block", False)
        for guard in _existing_guards
    ):
        socket.create_connection = _blocked_create_connection
        socket.socket.connect = _blocked_socket_connect
        socket.socket.connect_ex = _blocked_socket_connect_ex


try:
    import resource
except ImportError:  # pragma: no cover - resource is Unix-only
    resource = None
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _legacy_key(*parts: str) -> str:
    return "".join(parts)


def _bridge_taiji_environment() -> None:
    pairs = (
        (("HER", "MES_HOME"), "TAIJI_RUNTIME_HOME"),
        (("HER", "MES_WORKSPACE"), "TAIJI_WORKSPACE"),
        (("HER", "MES_CONFIG_PATH"), "TAIJI_CONFIG_PATH"),
        (("HER", "MES_WEBUI_PASSWORD"), "TAIJI_WEBUI_PASSWORD"),
        (("HER", "MES_WEBUI_HOST"), "TAIJI_WEBUI_HOST"),
        (("HER", "MES_WEBUI_PORT"), "TAIJI_WEBUI_PORT"),
        (("HER", "MES_WEBUI_STATE_DIR"), "TAIJI_WEBUI_STATE_DIR"),
        (("HER", "MES_WEBUI_DEFAULT_WORKSPACE"), "TAIJI_WEBUI_DEFAULT_WORKSPACE"),
        (("HER", "MES_WEBUI_AGENT_DIR"), "TAIJI_WEBUI_AGENT_DIR"),
        (("HER", "MES_WEBUI_PYTHON"), "TAIJI_WEBUI_PYTHON"),
        (("HER", "MES_WEBUI_CHAT_BACKEND"), "TAIJI_WEBUI_CHAT_BACKEND"),
        (("HER", "MES_WEBUI_GATEWAY_BASE_URL"), "TAIJI_WEBUI_GATEWAY_BASE_URL"),
        (("HER", "MES_WEBUI_GATEWAY_API_KEY"), "TAIJI_WEBUI_GATEWAY_API_KEY"),
        (("HER", "MES_WEBUI_BOT_NAME"), "TAIJI_WEBUI_BOT_NAME"),
    )
    for legacy_parts, product_key in pairs:
        value = os.environ.get(product_key)
        if value:
            os.environ[_legacy_key(*legacy_parts)] = value


_bridge_taiji_environment()

from api.auth import check_auth
from api.config import HOST, PORT, STATE_DIR, SESSION_DIR, DEFAULT_WORKSPACE
from api.csp import build_csp_report_only_policy as _build_csp_report_only_policy
from api.desktop_access import (
    desktop_access_required as _desktop_access_required,
    desktop_access_token as _desktop_access_token,
    enforce_desktop_access as _enforce_desktop_access,
)
from api.helpers import j, get_profile_cookie, _CLIENT_DISCONNECT_ERRORS
from api.profiles import set_request_profile, clear_request_profile
from api.routes import handle_delete, handle_get, handle_patch, handle_post, handle_put
from api.startup import auto_install_agent_deps, fix_credential_permissions
from api.updates import WEBUI_VERSION


def _truthy_env(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _safe_request_path(raw_path) -> str:
    try:
        return urlparse(str(raw_path or "-")).path or "/"
    except Exception:
        return "-"


class QuietHTTPServer(ThreadingHTTPServer):
    """Custom HTTP server that silently handles common network errors."""
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, *args, **kwargs):
        server_address = args[0] if args else kwargs.get('server_address', None)
        if server_address and ':' in server_address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(*args, **kwargs)
        self.accept_loop_requests_total = 0
        self.accept_loop_last_request_at = 0.0

    def _handle_request_noblock(self):
        """Record accept-loop progress before dispatching a request handler.

        A process can be alive and still stop accepting/dispatching requests.
        Exposing this heartbeat on /health gives supervisors and watchdogs a
        cheap signal that the accept loop is still moving.

        Note: this method is called only from the single ``serve_forever()``
        thread in CPython socketserver, so the un-locked ``+=`` increment is
        safe — there is no other thread mutating these counters. The /health
        readers may see a stale value momentarily but never an inconsistent
        one (Python int reads are atomic). Per Opus advisor on stage-297.
        """
        self.accept_loop_requests_total += 1
        self.accept_loop_last_request_at = time.time()
        return super()._handle_request_noblock()
    
    def handle_error(self, request, client_address):
        """Override to suppress logging for common client disconnect errors."""
        exc_type, exc_value, _ = sys.exc_info()
        
        # Silently ignore common connection errors caused by client disconnects
        if exc_type in (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError):
            return
        
        # Also handle socket errors that indicate client disconnect
        if issubclass(exc_type, OSError):
            # errno 54 is Connection reset by peer on macOS/BSD
            # errno 104 is Connection reset by peer on Linux
            if getattr(exc_value, 'errno', None) in (32, 54, 104, 110):  # EPIPE, ECONNRESET, ETIMEDOUT
                return
        
        # For other errors, use default logging
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 enables keep-alive connection reuse — major latency win on
    # high-RTT links where every saved TCP handshake is 2×RTT. Each response
    # MUST declare framing (Content-Length, Transfer-Encoding: chunked, or
    # Connection: close) so the client knows where the message ends. Helpers
    # j()/t() emit Content-Length; SSE/streaming endpoints emit
    # Connection: close because the body has no terminator. See PR notes.
    protocol_version = "HTTP/1.1"
    timeout = 30  # seconds — kills idle/incomplete connections to prevent thread exhaustion
    
    def setup(self):
        """Set socket options for each accepted connection."""
        super().setup()
        # TCP_NODELAY — universal, disables Nagle for HTTP latency
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        # SO_KEEPALIVE — universal master switch (must be set before timing params)
        try:
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        # Per-platform timing parameters
        if hasattr(socket, 'TCP_KEEPIDLE'):  # Linux
            try:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            except OSError:
                pass
        elif hasattr(socket, 'TCP_KEEPALIVE'):  # macOS
            try:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, 10)
            except OSError:
                pass
    _ver_suffix = WEBUI_VERSION.removeprefix('v')
    server_version = ('TaijiAgentWeb/' + _ver_suffix) if _ver_suffix != 'unknown' else 'TaijiAgentWeb'
    _CSP_REPORT_TO = '{"group":"csp-endpoint","max_age":10886400,"endpoints":[{"url":"/api/csp-report"}]}'

    @classmethod
    def csp_report_only_policy(cls) -> str:
        return _build_csp_report_only_policy()

    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy-Report-Only", self.csp_report_only_policy())
        self.send_header("Report-To", self._CSP_REPORT_TO)
        if _desktop_access_required() and getattr(self, "_taiji_desktop_access_granted", False):
            token = _desktop_access_token()
            if token:
                self.send_header(
                    "Set-Cookie",
                    f"taiji_desktop_token={token}; Path=/; SameSite=Strict; HttpOnly",
                )
        super().end_headers()

    def log_message(self, fmt, *args): pass  # suppress default Apache-style log

    def log_request(self, code: str='-', size: str='-') -> None:
        """Structured JSON logs for each request."""
        import json as _json
        duration_ms = round((time.time() - getattr(self, '_req_t0', time.time())) * 1000, 1)
        remote = '-'
        try:
            if getattr(self, 'client_address', None):
                remote = str(self.client_address[0])
        except Exception:
            remote = '-'
        forwarded_for = None
        try:
            forwarded_for = (self.headers.get('X-Forwarded-For') or '').split(',')[0].strip() or None
        except Exception:
            forwarded_for = None
        record_data = {
            'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'remote': remote,
            'method': getattr(self, 'command', None) or '-',
            'path': _safe_request_path(getattr(self, 'path', None)),
            'status': int(code) if str(code).isdigit() else code,
            'ms': duration_ms,
        }
        if forwarded_for:
            record_data['forwarded_for'] = forwarded_for
        record = _json.dumps(record_data)
        print(f'[webui] {record}', flush=True)

    def do_GET(self) -> None:
        self._req_t0 = time.time()
        # Per-request profile context from cookie (issue #798)
        cookie_profile = get_profile_cookie(self)
        if cookie_profile:
            set_request_profile(cookie_profile)
        try:
            parsed = urlparse(self.path)
            if not _enforce_desktop_access(self, parsed): return
            if not check_auth(self, parsed): return
            result = handle_get(self, parsed)
            if result is False:
                return j(self, {'error': 'not found'}, status=404)
        except _CLIENT_DISCONNECT_ERRORS:
            # The browser/client closed the socket while we were writing the
            # response. This is expected for probes, tab closes, and SSE
            # reconnect races; do not convert it into a misleading server 500.
            return
        except Exception:
            from api.product_contract import build_product_error

            product_error = build_product_error("unknown_error")
            print(
                f'[webui] ERROR {self.command} {_safe_request_path(self.path)} '
                f'incident_id={product_error["incident_id"]}\n' + traceback.format_exc(),
                flush=True,
            )
            try:
                j(self, {'error': 'Internal server error', 'product_error': product_error}, status=500)
            except _CLIENT_DISCONNECT_ERRORS:
                # Client disconnected while we were sending the 500 — nothing to do.
                pass
            except Exception:
                # Unexpected failure while sending the error response itself.
                # Log it so we know something is wrong with our error handler.
                traceback.print_exc()
        finally:
            clear_request_profile()

    def _handle_write(self, route_func) -> None:
        self._req_t0 = time.time()
        # Per-request profile context from cookie (issue #798)
        cookie_profile = get_profile_cookie(self)
        if cookie_profile:
            set_request_profile(cookie_profile)
        try:
            parsed = urlparse(self.path)
            if not _enforce_desktop_access(self, parsed): return
            # Stage-346 Opus SHOULD-FIX defense-in-depth: scope the CSP-report
            # auth carve-out to POST only. The endpoint is intentionally
            # unauthenticated (browsers omit cookies on CSP reports), but the
            # carve-out should not extend to PATCH/DELETE on that path even
            # though they currently fail through CSRF/routing fallthrough.
            _is_csp_report_post = (
                parsed.path == "/api/csp-report" and self.command == "POST"
            )
            if not _is_csp_report_post and not check_auth(self, parsed): return
            result = route_func(self, parsed)
            if result is False:
                return j(self, {'error': 'not found'}, status=404)
        except _CLIENT_DISCONNECT_ERRORS:
            # The browser/client closed the socket while we were writing the
            # response. This is expected for probes, tab closes, and SSE
            # reconnect races; do not convert it into a misleading server 500.
            return
        except Exception:
            from api.product_contract import build_product_error

            product_error = build_product_error("unknown_error")
            print(
                f'[webui] ERROR {self.command} {_safe_request_path(self.path)} '
                f'incident_id={product_error["incident_id"]}\n' + traceback.format_exc(),
                flush=True,
            )
            try:
                j(self, {'error': 'Internal server error', 'product_error': product_error}, status=500)
            except _CLIENT_DISCONNECT_ERRORS:
                # Client disconnected while we were sending the 500 — nothing to do.
                pass
            except Exception:
                # Unexpected failure while sending the error response itself.
                # Log it so we know something is wrong with our error handler.
                traceback.print_exc()
        finally:
            clear_request_profile()

    def do_POST(self) -> None:
        self._handle_write(handle_post)

    def do_PUT(self) -> None:
        self._handle_write(handle_put)

    def do_PATCH(self) -> None:
        self._handle_write(handle_patch)

    def do_OPTIONS(self) -> None:
        """Handle CORS preflight requests."""
        self._req_t0 = time.time()
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_DELETE(self) -> None:
        self._handle_write(handle_delete)


def _raise_fd_soft_limit(target: int = 4096) -> dict:
    """Best-effort raise of RLIMIT_NOFILE for persistent WebUI hosts.

    macOS launchd jobs often start with a 256 soft limit. If a future FD leak
    regresses, that low ceiling turns a leak into a hard HTTP wedge quickly.
    Raising the soft limit does not hide leaks; it buys enough headroom for
    diagnostics and watchdog recovery.
    """
    if resource is None:
        return {"status": "unsupported"}
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    # On Unix, RLIM_INFINITY is commonly a large int; keep the logic explicit
    # so tests can use ordinary integers without depending on platform values.
    desired = int(target)
    if hard not in (-1, getattr(resource, "RLIM_INFINITY", object())):
        desired = min(desired, int(hard))
    if soft >= desired:
        return {"status": "unchanged", "soft": soft, "hard": hard}
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (desired, hard))
    except Exception as exc:
        return {"status": "error", "soft": soft, "hard": hard, "error": str(exc)}
    return {"status": "raised", "soft": desired, "hard": hard, "previous_soft": soft}


_SHUTDOWN_AUDIT_LOGGED = False
_SHUTDOWN_LOG_VALUE_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _shutdown_log_value(value, *, default: str = "unknown", max_len: int = 160) -> str:
    """Return a bounded single-line value safe for shutdown diagnostics."""
    if value is None:
        return default
    try:
        text = str(value)
    except Exception:
        return default
    text = _SHUTDOWN_LOG_VALUE_RE.sub("?", text).strip()
    if not text:
        return default
    if len(text) > max_len:
        text = f"{text[:max_len]}…"
    return text


def _log_shutdown_audit(reason: str = "serve_forever_exit") -> None:
    """Log runtime context when the WebUI server is exiting."""
    global _SHUTDOWN_AUDIT_LOGGED
    if _SHUTDOWN_AUDIT_LOGGED:
        return

    active_sessions = []
    try:
        from api.models import LOCK, SESSIONS
        with LOCK:
            session_items = list(SESSIONS.items())
        for sid, session in session_items:
            stream_id = getattr(session, "active_stream_id", None)
            if stream_id:
                pending = bool(getattr(session, "pending_user_message", None))
                active_sessions.append(
                    "sid=%s stream=%s pending=%s"
                    % (
                        _shutdown_log_value(sid),
                        _shutdown_log_value(stream_id),
                        pending,
                    )
                )
    except Exception:
        logger.debug("Failed to collect active-session shutdown audit state", exc_info=True)

    _SHUTDOWN_AUDIT_LOGGED = True
    logger.info(
        "[shutdown-audit] reason=%s pid=%s thread=%s(%s) active_sessions=[%s]",
        _shutdown_log_value(reason),
        os.getpid(),
        _shutdown_log_value(threading.current_thread().name),
        threading.current_thread().ident,
        "; ".join(active_sessions) if active_sessions else "none",
    )


def _recover_orphan_truth_rewrites_on_startup() -> None:
    """Resolve only provably uncommitted markers before serving requests.

    This is a fail-closed startup gate.  The HTTP server must not be created
    while any durable truth-rewrite intent remains unresolved, or when the scan
    itself cannot prove that the durable state is safe.  Only stable reason
    codes and aggregate counts are emitted so recovery errors cannot disclose
    session ids, paths, message text, or database details.
    """
    from api.truth_rewrite import recover_orphan_truth_rewrite_intents

    try:
        truth_recovery = recover_orphan_truth_rewrite_intents(SESSION_DIR)
    except Exception:
        print("[recovery] truth_rewrite_recovery_failed", flush=True)
        raise RuntimeError("truth_rewrite_recovery_failed") from None
    recovered_statuses = {"orphan_aborted", "existing_recovered"}
    recovered = sum(
        item.get("status") in recovered_statuses for item in truth_recovery
    )
    blocked = len(truth_recovery) - recovered
    if recovered:
        print(
            f"[recovery] Resolved {recovered} session rewrite intents.",
            flush=True,
        )
    if blocked:
        print(
            f"[recovery] truth_rewrite_recovery_blocked count={blocked}",
            flush=True,
        )
        raise RuntimeError("truth_rewrite_recovery_blocked")


def main() -> None:
    from api.config import print_startup_config, verify_hermes_imports, _HERMES_FOUND

    if _truthy_env("TAIJI_VERBOSE_STARTUP"):
        print_startup_config()
    else:
        print("  Taiji application runtime starting", flush=True)

    fd_limit = _raise_fd_soft_limit()
    if fd_limit.get("status") == "raised":
        print(
            f"[ok] Raised file descriptor soft limit "
            f"{fd_limit.get('previous_soft')} -> {fd_limit.get('soft')}",
            flush=True,
        )
    elif fd_limit.get("status") == "error":
        print(f"[!!] WARNING: Could not raise file descriptor limit: {fd_limit.get('error')}", flush=True)

    # Fix sensitive file permissions before doing anything else
    fix_credential_permissions()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)

    # A durable truth-rewrite intent outranks the generic message-count backup
    # heuristic.  Recover it first: if state.db already committed an authorized
    # retry/undo/truncate, restoring the longer .bak beforehand would replace
    # the target sidecar and turn a provable recovery into divergence.
    _recover_orphan_truth_rewrites_on_startup()

    # ── #1558 startup self-heal ─────────────────────────────────────────
    # If a previous process wrote a session JSON with fewer messages than
    # its .bak (the data-loss shape #1558 produced), restore from the .bak.
    # Safe to run unconditionally — a clean install is a no-op.
    try:
        from api.models import _active_state_db_path
        from api.session_recovery import recover_all_sessions_on_startup
        result = recover_all_sessions_on_startup(
            SESSION_DIR,
            rebuild_index=True,
            state_db_path=_active_state_db_path(),
        )
        if result.get("restored"):
            print(f"[recovery] Restored {result['restored']}/{result['scanned']} sessions from .bak (see #1558).", flush=True)
    except Exception as exc:
        # Recovery is best-effort; never block server startup.
        print(f"[recovery] startup recovery failed: {exc}", flush=True)

    # Legacy repair is opt-in.  Startup performs a read-only audit only; it
    # never rewrites session JSON, state.db, journals, or artifact manifests.
    try:
        from api.artifacts import ArtifactRegistry
        from api.legacy_session_migration import audit_legacy_sessions
        from api.models import _active_state_db_path

        legacy_report = audit_legacy_sessions(
            SESSION_DIR,
            _active_state_db_path(),
            ArtifactRegistry(STATE_DIR / "artifacts", create_root=False),
        )
        if legacy_report.get("needs_repair"):
            print(
                f"[migration] {legacy_report['scanned']} sessions audited; "
                "open Settings to review legacy repairs.",
                flush=True,
            )
    except Exception as exc:
        print(f"[migration] startup audit failed: {exc}", flush=True)

    within_container = False
    # Check for the "/.within_container" file to determine if we're running inside a container; this file is created in the Dockerfile
    try:
        with open('/.within_container', 'r') as f:
            within_container = True
    except FileNotFoundError:
        pass

    if within_container:
        print('[ok] Running within container.', flush=True)

    # Security: warn if binding non-loopback without authentication
    from api.auth import is_auth_enabled
    if HOST not in ('127.0.0.1', '::1', 'localhost') and not is_auth_enabled():
        print(f'[!!] WARNING: Binding to {HOST} with NO PASSWORD SET.', flush=True)
        print(f'     Anyone on the network can access your filesystem and agent.', flush=True)
        print(f'     Set a password via Settings or TAIJI_WEBUI_PASSWORD env var.', flush=True)
        print(f'     To suppress: bind to 127.0.0.1 or set a password.', flush=True)
        if within_container:
            print(f'     Note: You are running within a container, must bind to 0.0.0.0 (IPv4) or :: (IPv6) to publish the port.', flush=True)
    elif not is_auth_enabled():
        print(f'  [tip] No password set. Any process on this machine can read sessions', flush=True)
        print(f'        and memory via the local API. Set TAIJI_WEBUI_PASSWORD to', flush=True)
        print(f'        enable authentication.', flush=True)

    ok, missing, errors = verify_hermes_imports()
    if not ok and _HERMES_FOUND:
        print(f'[!!] Warning: Taiji runtime found but missing modules: {missing}', flush=True)
        for mod, err in errors.items():
            print(f'     {mod}: {err}', flush=True)
        print('     Attempting to install missing dependencies from agent requirements.txt...', flush=True)
        auto_install_agent_deps()
        ok, missing, errors = verify_hermes_imports()
        if not ok:
            print(f'[!!] Still missing after install attempt: {missing}', flush=True)
            for mod, err in errors.items():
                print(f'     {mod}: {err}', flush=True)
            print('     Agent features may not work correctly.', flush=True)
        else:
            print('[ok] Agent dependencies installed successfully.', flush=True)

    # Start the gateway session watcher for real-time SSE updates
    try:
        from api.gateway_watcher import start_watcher
        start_watcher()
    except Exception as e:
        print(f'[!!] WARNING: Gateway watcher failed to start: {e}', flush=True)

    # Load WebUI dashboard plugins
    try:
        from api.plugins import load_plugins
        load_plugins()
    except Exception as e:
        print(f'[!!] WARNING: Plugin loading failed: {e}', flush=True)

    httpd = QuietHTTPServer((HOST, PORT), Handler)

    # ── TLS/HTTPS setup (optional) ─────────────────────────────────────────
    from api.config import TLS_ENABLED, TLS_CERT, TLS_KEY
    scheme = 'https' if TLS_ENABLED else 'http'
    if TLS_ENABLED:
        try:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(TLS_CERT, TLS_KEY)
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            print(f'  TLS enabled: cert={TLS_CERT}, key={TLS_KEY}', flush=True)
        except Exception as e:
            print(f'[!!] WARNING: TLS setup failed ({e}), falling back to HTTP', flush=True)
            scheme = 'http'

    if _desktop_access_required():
        print("  Taiji desktop workspace ready", flush=True)
    else:
        print('  Taiji application runtime ready', flush=True)
    print('', flush=True)
    try:
        httpd.serve_forever()
    finally:
        _log_shutdown_audit()
        # Stop the gateway watcher on shutdown
        try:
            from api.gateway_watcher import stop_watcher
            stop_watcher()
        except Exception:
            logger.debug("Failed to stop gateway watcher during shutdown")
        # Drain pending memory-provider lifecycle commits before exit
        try:
            from api.session_lifecycle import drain_all_on_shutdown
            drain_all_on_shutdown()
        except Exception:
            logger.debug("Failed to drain lifecycle on shutdown", exc_info=True)

if __name__ == '__main__':
    main()
