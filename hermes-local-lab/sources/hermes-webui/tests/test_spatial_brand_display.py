from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_spatial_brand_and_agent_composer_copy():
    html = (ROOT / "static/index.html").read_text()
    boot = (ROOT / "static/boot.js").read_text()
    assert 'class="taiji-brand-title">国网空天智能体</div>' in html
    assert 'class="taiji-brand-subtitle">空间数据运检智能体</div>' in html
    assert 'placeholder="输入消息给 Agent…"' in html
    assert "?'太极智能体':name" not in boot
