"""Helpers for loading Hermes .env files consistently across entrypoints."""

from __future__ import annotations

import os
import sys
from io import StringIO
from pathlib import Path

from dotenv import load_dotenv


# Env var name suffixes that indicate credential values.  These are the
# only env vars whose values we sanitize on load — we must not silently
# alter arbitrary user env vars, but credentials are known to require
# pure ASCII (they become HTTP header values).
_CREDENTIAL_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_KEY")

# Operator-controlled transaction policy must come from the process manager,
# never from the credential data file whose safety it governs.
_DOTENV_PROTECTED_KEYS = frozenset({"HERMES_CREDENTIAL_GROUP_SHARED"})

# Names we've already warned about during this process, so repeated
# load_hermes_dotenv() calls (user env + project env, gateway hot-reload,
# tests) don't spam the same warning multiple times.
_WARNED_KEYS: set[str] = set()

# Map of env-var name → source label ("bitwarden", etc.) for credentials
# that were injected by an external secret source during load_hermes_dotenv().
# Used by setup / `hermes model` flows to label detected credentials so
# users understand WHERE a key came from when their .env doesn't contain it
# directly (otherwise the "credentials detected ✓" line looks identical to
# the .env case and they don't know Bitwarden is wired up).
_SECRET_SOURCES: dict[str, str] = {}

# HERMES_HOME paths we've already pulled external secrets for during this
# process.  ``load_hermes_dotenv()`` is called at module-import time from
# several hot modules (cli.py, hermes_cli/main.py, run_agent.py,
# trajectory_compressor.py, gateway/run.py, ...), so without this guard the
# Bitwarden status line gets printed 3-5x per startup.  Bitwarden's own
# in-process cache prevents redundant network calls, but the print, the
# config re-parse, and the ASCII sanitization sweep still ran every time.
_APPLIED_HOMES: set[str] = set()


def get_secret_source(env_var: str) -> str | None:
    """Return the label of the secret source that supplied ``env_var``, if any.

    Returns ``"bitwarden"`` for keys pulled from Bitwarden Secrets Manager
    during the current process's ``load_hermes_dotenv()`` call.  Returns
    ``None`` for keys that came from ``.env``, the shell environment, or
    aren't tracked.  The returned label is metadata only: credential-pool
    persistence may store it to explain the origin of a borrowed secret, but
    must never treat it as authorization to persist the raw value.
    """
    return _SECRET_SOURCES.get(env_var)


def reset_secret_source_cache() -> None:
    """Forget which HERMES_HOME paths have already had external secrets applied.

    The first call to ``_apply_external_secret_sources(home_path)`` in a
    process pulls from Bitwarden (or other configured backend), records the
    applied keys in ``_SECRET_SOURCES``, and remembers ``home_path`` so
    subsequent calls in the same process are no-ops.  Call this to force the
    next call to re-pull — useful for tests, and for long-running processes
    that want to refresh after a config change.
    """
    _APPLIED_HOMES.clear()


def format_secret_source_suffix(env_var: str) -> str:
    """Return a human-readable suffix like ``" (from Bitwarden)"`` or ``""``.

    Use this when printing a detected credential so the user can see where
    it came from.  Empty string when the credential came from ``.env`` or
    the shell — those are the implicit / "default" cases users already
    understand.
    """
    source = get_secret_source(env_var)
    if not source:
        return ""
    if source == "bitwarden":
        return " (from Bitwarden)"
    # Generic fallback — future-proofing for additional secret sources
    # (e.g. 1Password, HashiCorp Vault) without having to update every
    # call site.
    return f" (from {source})"


def _format_offending_chars(value: str, limit: int = 3) -> str:
    """Return a compact 'U+XXXX ('c'), ...' summary of non-ASCII codepoints."""
    seen: list[str] = []
    for ch in value:
        if ord(ch) > 127:
            label = f"U+{ord(ch):04X}"
            if ch.isprintable():
                label += f" ({ch!r})"
            if label not in seen:
                seen.append(label)
            if len(seen) >= limit:
                break
    return ", ".join(seen)


def _sanitize_loaded_credentials() -> None:
    """Strip non-ASCII characters from credential env vars in os.environ.

    Called after dotenv loads so the rest of the codebase never sees
    non-ASCII API keys.  Only touches env vars whose names end with
    known credential suffixes (``_API_KEY``, ``_TOKEN``, etc.).

    Emits a one-line warning to stderr when characters are stripped.
    Silent stripping would mask copy-paste corruption (Unicode lookalike
    glyphs from PDFs / rich-text editors, ZWSP from web pages) as opaque
    provider-side "invalid API key" errors (see #6843).
    """
    for key, value in list(os.environ.items()):
        if not any(key.endswith(suffix) for suffix in _CREDENTIAL_SUFFIXES):
            continue
        try:
            value.encode("ascii")
            continue
        except UnicodeEncodeError:
            pass
        cleaned = value.encode("ascii", errors="ignore").decode("ascii")
        os.environ[key] = cleaned
        if key in _WARNED_KEYS:
            continue
        _WARNED_KEYS.add(key)
        stripped = len(value) - len(cleaned)
        detail = _format_offending_chars(value) or "non-printable"
        print(
            f"  Warning: {key} contained {stripped} non-ASCII character"
            f"{'s' if stripped != 1 else ''} ({detail}) — stripped so the "
            f"key can be sent as an HTTP header.",
            file=sys.stderr,
        )
        print(
            "  This usually means the key was copy-pasted from a PDF, "
            "rich-text editor, or web page that substituted lookalike\n"
            "  Unicode glyphs for ASCII letters. If authentication fails "
            "(e.g. \"API key not valid\"), re-copy the key from the\n"
            "  provider's dashboard and run `hermes setup` (or edit the "
            ".env file in a plain-text editor).",
            file=sys.stderr,
        )


def _load_dotenv_with_fallback(
    path: Path,
    *,
    override: bool,
    sanitized_lines: list[str] | None = None,
) -> None:
    if sanitized_lines is not None:
        load_dotenv(
            stream=StringIO("".join(sanitized_lines)),
            override=override,
        )
    else:
        try:
            load_dotenv(dotenv_path=path, override=override, encoding="utf-8")
        except UnicodeDecodeError:
            load_dotenv(dotenv_path=path, override=override, encoding="latin-1")
    # Strip non-ASCII characters from credential env vars that were just
    # loaded.  API keys must be pure ASCII since they're sent as HTTP
    # header values (httpx encodes headers as ASCII).  Non-ASCII chars
    # typically come from copy-pasting keys from PDFs or rich-text editors
    # that substitute Unicode lookalike glyphs (e.g. ʋ U+028B for v).
    _sanitize_loaded_credentials()


def _sanitize_env_file_if_needed(
    path: Path,
    *,
    config_path: Path | None = None,
    persist: bool = True,
) -> list[str]:
    """Pre-sanitize a .env file before python-dotenv reads it.

    python-dotenv does not handle corrupted lines where multiple
    KEY=VALUE pairs are concatenated on a single line (missing newline).
    This produces mangled values — e.g. a bot token duplicated 8×
    (see #8908).

    Also strips embedded null bytes which crash ``os.environ[k] = v``
    with ``ValueError: embedded null byte`` — typically introduced by
    copy-pasting API keys from terminals or rich-text editors.

    We delegate to ``hermes_cli.config._sanitize_env_lines`` which
    already knows all valid Hermes env-var names and can split
    concatenated lines correctly.
    """
    if not path.exists():
        return []
    try:
        from hermes_cli.config import _sanitize_env_lines
    except ImportError:
        return []  # early bootstrap — config module not available yet

    read_kw = {"encoding": "utf-8-sig", "errors": "replace"}

    def _read_and_sanitize(target: Path) -> tuple[list[str], list[str]]:
        with open(target, **read_kw) as file_handle:
            original_lines = file_handle.readlines()
        # Strip null bytes before _sanitize_env_lines so they never reach
        # python-dotenv (which passes them to os.environ and crashes with
        # ValueError).
        stripped = [line.replace("\x00", "") for line in original_lines]
        return original_lines, _sanitize_env_lines(stripped)

    if not persist:
        _original, sanitized = _read_and_sanitize(path)
        return sanitized

    target_config_path = config_path or path.with_name("config.yaml")
    from agent.provider_credentials import (
        credential_transaction,
        replace_config_env_payload_strict,
    )

    # Hold the shared config/.env lock across read, repair, epoch
    # reconciliation, and publish. Without the outer transaction a canonical
    # credential writer could land between our read and replace.
    with credential_transaction(target_config_path) as spec:
        original, sanitized = _read_and_sanitize(spec.env_target)
        if sanitized == original:
            return sanitized

        from dotenv import dotenv_values
        from agent.image_gen_verification import (
            bump_capability_config_epochs,
            capability_epochs_for_secret_env,
        )

        def _parsed_values(lines: list[str]) -> dict[str, str]:
            values = dotenv_values(
                stream=StringIO("".join(lines)),
                interpolate=False,
            )
            return {
                str(key): str(value or "")
                for key, value in values.items()
                if key is not None
            }

        before_values = _parsed_values(original)
        after_values = _parsed_values(sanitized)
        changed_keys = tuple(
            sorted(
                key
                for key in set(before_values) | set(after_values)
                if before_values.get(key) != after_values.get(key)
            )
        )
        projection_keys = tuple(
            key
            for key in changed_keys
            if key not in _DOTENV_PROTECTED_KEYS
        )

        def _advance_repaired_capability_epochs(
            config_data: dict,
        ) -> None:
            capabilities = {
                capability
                for key in changed_keys
                for capability in capability_epochs_for_secret_env(
                    config_data,
                    key,
                    env_values=before_values,
                )
            }
            if capabilities:
                bump_capability_config_epochs(
                    config_data,
                    *sorted(capabilities),
                )

        replace_config_env_payload_strict(
            _advance_repaired_capability_epochs,
            "".join(sanitized).encode("utf-8"),
            config_path=target_config_path,
            env_keys=projection_keys,
        )
        return sanitized


def load_hermes_dotenv(
    *,
    hermes_home: str | os.PathLike | None = None,
    project_env: str | os.PathLike | None = None,
) -> list[Path]:
    """Load Hermes environment files with user config taking precedence.

    Behavior:
    - `~/.hermes/.env` overrides stale shell-exported values when present.
    - project `.env` acts as a dev fallback and only fills missing values when
      the user env exists.
    - if no user env exists, the project `.env` also overrides stale shell vars.
    """
    loaded: list[Path] = []

    home_path = Path(
        hermes_home
        or os.getenv("TAIJI_RUNTIME_HOME")
        or os.getenv("HERMES_HOME")
        or Path.home() / ".hermes"
    )
    user_env = home_path / ".env"
    project_env_path = Path(project_env) if project_env else None
    protected_values = {
        key: os.environ.get(key)
        for key in _DOTENV_PROTECTED_KEYS
    }

    def _restore_process_authority() -> None:
        for key, value in protected_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    try:
        # Fix corrupted .env files before python-dotenv parses them (#8908).
        user_lines = None
        if user_env.exists():
            user_lines = _sanitize_env_file_if_needed(
                user_env,
                config_path=home_path / "config.yaml",
            )
        project_lines = None
        if project_env_path and project_env_path.exists():
            # Project .env is source material, not the canonical user
            # credential store. Repair it in memory for dotenv without
            # mutating the checkout.
            project_lines = _sanitize_env_file_if_needed(
                project_env_path,
                persist=False,
            )

        if user_env.exists():
            _load_dotenv_with_fallback(
                user_env,
                override=True,
                sanitized_lines=user_lines,
            )
            loaded.append(user_env)

        if project_env_path and project_env_path.exists():
            _load_dotenv_with_fallback(
                project_env_path,
                override=not loaded,
                sanitized_lines=project_lines,
            )
            loaded.append(project_env_path)

        _apply_external_secret_sources(home_path)
        return loaded
    finally:
        _restore_process_authority()


def _apply_external_secret_sources(home_path: Path) -> None:
    """Pull secrets from external sources (currently Bitwarden) into env.

    Runs AFTER dotenv loads so .env values are visible (we use them to
    locate the access token) but BEFORE the rest of Hermes reads
    ``os.environ`` for credentials.  Any failure here is logged and
    swallowed — external secret sources must never block startup.

    Idempotent within a process: subsequent calls for the same
    ``home_path`` are no-ops.  ``load_hermes_dotenv()`` runs at import
    time from several hot modules (cli.py, hermes_cli/main.py,
    run_agent.py, trajectory_compressor.py, ...), so without this guard
    the Bitwarden status line would print 3-5x per CLI startup.  Use
    ``reset_secret_source_cache()`` if you need to force a re-pull
    (tests, future ``hermes secrets bitwarden sync`` from a long-running
    process).
    """
    home_key = str(Path(home_path).resolve())
    if home_key in _APPLIED_HOMES:
        return
    _APPLIED_HOMES.add(home_key)

    try:
        cfg = _load_secrets_config(home_path)
    except Exception:  # noqa: BLE001 — config errors must not block startup
        return

    bw_cfg = (cfg or {}).get("bitwarden") or {}
    if not bw_cfg.get("enabled"):
        return

    try:
        from agent.secret_sources.bitwarden import apply_bitwarden_secrets
    except ImportError:
        return

    result = apply_bitwarden_secrets(
        enabled=True,
        access_token_env=bw_cfg.get("access_token_env", "BWS_ACCESS_TOKEN"),
        project_id=bw_cfg.get("project_id", ""),
        override_existing=bool(bw_cfg.get("override_existing", False)),
        cache_ttl_seconds=float(bw_cfg.get("cache_ttl_seconds", 300)),
        auto_install=bool(bw_cfg.get("auto_install", True)),
        server_url=str(bw_cfg.get("server_url", "") or "").strip(),
        home_path=home_path,
    )

    if result.applied:
        # Re-run the ASCII sanitization pass: BSM values are user-supplied
        # and might have the same copy-paste corruption as a manually
        # edited .env (see #6843).
        _sanitize_loaded_credentials()
        # Remember where these came from so the setup / `hermes model`
        # flows can label detected credentials with "(from Bitwarden)" —
        # otherwise users see "credentials ✓" with no hint that the value
        # came from BSM rather than .env.
        for name in result.applied:
            _SECRET_SOURCES[name] = "bitwarden"
        print(
            f"  Bitwarden Secrets Manager: applied {len(result.applied)} "
            f"secret{'s' if len(result.applied) != 1 else ''} "
            f"({', '.join(sorted(result.applied))})",
            file=sys.stderr,
        )
    if result.error:
        print(
            f"  Bitwarden Secrets Manager: {result.error}",
            file=sys.stderr,
        )
    for warn in result.warnings:
        print(
            f"  Bitwarden Secrets Manager: {warn}",
            file=sys.stderr,
        )


def _load_secrets_config(home_path: Path) -> dict:
    """Read just the ``secrets:`` section out of config.yaml.

    Imported lazily and isolated from the main config loader so a
    malformed config can't take down dotenv loading entirely.
    """
    config_path = home_path / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except ImportError:
        return {}
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001
        return {}
    return data.get("secrets") or {}
