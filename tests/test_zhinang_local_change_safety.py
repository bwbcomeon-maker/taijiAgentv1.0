from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAFETY_PATH = ROOT / "scripts" / "check-local-change-safety.py"


def _load_safety_module():
    spec = importlib.util.spec_from_file_location("taiji_change_safety", SAFETY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixed_zhinang_source_exemptions_are_byte_exact():
    safety = _load_safety_module()
    assert len(safety.FIXED_UPSTREAM_FALSE_POSITIVES) == 8

    for relative in safety.FIXED_UPSTREAM_FALSE_POSITIVES:
        payload = (ROOT / relative).read_bytes()
        assert safety._content_findings(relative, payload) == []

        changed = payload + b"\nAPI_TOKEN=live_changed_credential_value_123456\n"
        findings = safety._content_findings(relative, changed)
        assert "credential-assignment" in findings

        private_key = payload + b"\n-----BEGIN " + b"PRIVATE KEY-----\n"
        findings = safety._content_findings(relative, private_key)
        assert "private-key" in findings
