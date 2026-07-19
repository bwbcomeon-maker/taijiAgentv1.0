from pathlib import Path


STREAMING = Path(__file__).resolve().parents[1] / "api" / "streaming.py"


def test_session_agent_signature_includes_image_capability_runtime_fingerprint():
    source = STREAMING.read_text(encoding="utf-8")
    start = source.index("_sig_blob = _json.dumps")
    end = source.index("_agent_sig =", start)
    signature_block = source[start:end]

    assert "image_capability_runtime_fingerprint" in signature_block
