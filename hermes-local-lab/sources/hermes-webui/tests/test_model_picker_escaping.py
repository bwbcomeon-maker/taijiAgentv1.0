from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_model_picker_escapes_provider_supplied_model_labels():
    src = (ROOT / "static" / "ui.js").read_text()

    assert '<span class="model-opt-id">${esc(m.id)}</span>' in src
    assert '<span class="model-opt-name">${esc(m.name)}</span>' in src
    assert '<span class="model-opt-id">${m.id}</span>' not in src
    assert '<span class="model-opt-name">${m.name}</span>' not in src


def test_providers_panel_escapes_load_error_text():
    src = (ROOT / "static" / "panels.js").read_text()
    start = src.index("async function loadProvidersPanel")
    end = src.index("async function _refreshProviderQuota", start)
    panel = src[start:end]

    assert "加载提供商失败：'+esc(e.message||String(e))+'" in panel
    assert "加载提供商失败：'+e.message+'" not in panel
    assert "加载提供商失败：'+(e.message||String(e))" not in panel
