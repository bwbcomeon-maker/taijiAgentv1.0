"""Fixed target reference registry for the candidate pipeline."""

import json
import re
from pathlib import Path

from .errors import PipelineError


SAFE_TARGET_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
BUILTIN_TARGET_FILES = {
    "kylin-amd64": "kylin-amd64.json",
    "windows-x64": "windows-x64.json",
}


def _target_invalid(message):
    raise PipelineError(message, category="TARGET_INVALID")


def resolve_target_reference(value, target_dir, registered=None):
    """Resolve one fixed target ID or an existing absolute JSON file."""

    mapping = dict(BUILTIN_TARGET_FILES if registered is None else registered)
    raw = str(value) if isinstance(value, Path) else value
    if not isinstance(raw, str) or not raw:
        _target_invalid("target reference is required")
    if SAFE_TARGET_ID.fullmatch(raw):
        filename = mapping.get(raw)
        if filename is None:
            _target_invalid("unknown target id: {}".format(raw))
        path = Path(target_dir) / filename
    else:
        path = Path(raw)
        if not path.is_absolute():
            _target_invalid("target must be a registered id or absolute JSON path")
    try:
        metadata = path.lstat()
    except OSError as exc:
        _target_invalid("target file is unavailable: {}".format(exc))
    if path.is_symlink() or not path.is_file() or metadata.st_nlink != 1:
        _target_invalid("target path must be an ordinary file")
    return path.resolve()


def load_target_reference(path, expected_target_id=None):
    """Read a target object without importing code or applying platform policy."""

    target_path = Path(path)
    try:
        payload = json.loads(target_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _target_invalid("target JSON is unreadable or invalid: {}".format(exc))
    if not isinstance(payload, dict):
        _target_invalid("target JSON must be an object")
    if expected_target_id is not None and payload.get("target_id") != expected_target_id:
        _target_invalid("target_id does not match the registered target")
    return payload


def create_adapter(target_id):
    from ..adapters.kylin_amd64 import KylinAmd64Adapter
    from ..adapters.windows_x64 import WindowsX64Adapter

    factories = {
        "kylin-amd64": KylinAmd64Adapter,
        "windows-x64": WindowsX64Adapter,
    }
    factory = factories.get(target_id)
    if factory is None:
        _target_invalid("no adapter is registered for target: {}".format(target_id))
    return factory()
