"""Coverage for cron/gateway guidance in the Tasks panel and Docker docs."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = ROOT / "static" / "index.html"
PANELS_JS = ROOT / "static" / "panels.js"
DOCKER_DOC = ROOT / "docs" / "docker.md"


def test_tasks_panel_has_gateway_notice_container():
    html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'id="cronGatewayNotice"' in html
    assert "detail-alert" in html


def test_cron_panel_loads_gateway_status_for_scheduling_guidance():
    panels = PANELS_JS.read_text(encoding="utf-8")

    assert "function _cronGatewayNoticeHtml" in panels
    assert "function loadCronGatewayNotice" in panels
    assert "api('/api/gateway/status')" in panels
    assert "本地任务服务未启用" in panels
    assert "本地任务服务未运行" in panels
    assert "本地任务服务状态暂不可确认" in panels
    assert "定时任务自动执行依赖本地任务服务" in panels
    assert "Gateway not configured" not in panels
    assert "Gateway not running" not in panels
    assert "scheduled jobs require the Hermes gateway daemon" not in panels
    assert "Docker install" not in panels
    assert "github.com/nesquena/hermes-webui" not in panels
    assert "loadCronGatewayNotice()" in panels


def test_docker_docs_explain_single_container_cron_gateway_boundary():
    docs = DOCKER_DOC.read_text(encoding="utf-8")

    assert "single-container setup runs the WebUI only" in docs
    assert "scheduled jobs require the Hermes gateway daemon" in docs
    assert "Gateway not configured" in docs
    assert "docker-compose.two-container.yml" in docs
