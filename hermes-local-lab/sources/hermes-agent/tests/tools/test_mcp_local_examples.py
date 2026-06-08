from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_cli_config_example_includes_filesystem_and_playwright_mcp():
    text = (ROOT / "cli-config.yaml.example").read_text(encoding="utf-8")
    assert "filesystem-demo:" in text
    assert '@modelcontextprotocol/server-filesystem' in text
    assert "playwright-demo:" in text
    assert "@playwright/mcp@latest" in text
    assert "allowed_roots:" in text
    assert "browser_actions_require_confirmation: true" in text


def test_mcp_demo_assets_exist():
    demo_root = ROOT / "demos" / "mcp"
    assert (demo_root / "filesystem-demo" / "README.md").exists()
    assert (demo_root / "filesystem-demo" / "sample.txt").exists()
    assert (demo_root / "browser-fill-demo" / "index.html").exists()
