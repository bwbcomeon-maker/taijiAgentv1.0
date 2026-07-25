from __future__ import annotations

import json
import struct
import subprocess
import textwrap
from pathlib import Path

from api.expert_teams.catalog import (
    CONTENT_CREATOR_TEAM_ID,
    DEEP_RESEARCH_TEAM_ID,
    get_template,
)


ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = ROOT / "static" / "assets" / "taiji" / "expert-teams"
STYLE = ROOT / "static" / "expert-team-v3.css"
SCRIPT = ROOT / "static" / "expert-team-v3.js"


def _png_size(path: Path) -> tuple[int, int]:
    payload = path.read_bytes()
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    assert payload[12:16] == b"IHDR"
    return struct.unpack(">II", payload[16:24])


def _asset_path(url: str) -> Path:
    assert url.startswith("static/assets/taiji/expert-teams/")
    assert "://" not in url
    return ROOT / url


def _run_visual_hooks(body: str) -> dict:
    source = textwrap.dedent(
        f"""
        const fs = require('fs');
        const vm = require('vm');
        const context = {{
          window: {{}},
          document: {{
            readyState: 'loading',
            addEventListener() {{}},
            getElementById() {{ return null; }},
          }},
          console,
          AbortController,
        }};
        vm.createContext(context);
        let source = fs.readFileSync('static/expert-team-v3.js', 'utf8');
        source = source.replace(
          'window.ExpertTeamV3 = Object.freeze({{',
          `window.__expertTeamVisualHooks = {{
            normalizeTeam, teamCard, memberRowsHtml, exampleTaskRowsHtml, handlePortalImageError,
            bindPortalEvents, localExpertTeamImage,
          }};\n  window.ExpertTeamV3 = Object.freeze({{`,
        );
        vm.runInContext(source, context);
        const hooks = context.window.__expertTeamVisualHooks;
        {body}
        """
    )
    completed = subprocess.run(
        ["node", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_expert_team_catalog_binds_complete_local_2x_visual_assets():
    content = get_template(CONTENT_CREATOR_TEAM_ID)
    research = get_template(DEEP_RESEARCH_TEAM_ID)

    assert len(content["members"]) == 5
    assert len(research["members"]) == 6

    for team in (content, research):
        assert team["image_alt"].strip()
        urls = [team["image"], *(member["image"] for member in team["members"])]
        assert len(urls) == len(set(urls))
        assert all(member["image_alt"].strip() for member in team["members"])
        for url in urls:
            path = _asset_path(url)
            assert path.is_file(), url
            width, height = _png_size(path)
            assert width >= 192 and height >= 192, (url, width, height)


def test_standalone_catalog_does_not_seed_enterprise_document_templates():
    content = get_template(CONTENT_CREATOR_TEAM_ID)
    research = get_template(DEEP_RESEARCH_TEAM_ID)

    work_report = next(item for item in content["examples"] if item["id"] == "work_report")
    research_report = next(item for item in research["examples"] if item["id"] == "research_report")

    assert work_report["document_brief_seed"]["document_control"]["render_template_id"] == "standalone-work-report"
    assert research_report["document_brief_seed"]["document_control"]["render_template_id"] == "standalone-research-report"


def test_expert_team_v3_css_has_avatar_and_single_scroll_responsive_contract():
    style = STYLE.read_text(encoding="utf-8")

    assert ".et3-member-avatar" in style
    assert ".et3-member-copy" in style
    assert "width: 44px" in style
    assert "height: 44px" in style
    assert "@media (max-width: 760px)" in style
    assert "max-height: none" in style
    assert "overflow: visible" in style


def test_desktop_team_detail_uses_compact_two_column_tasks_without_nested_scroll():
    style = STYLE.read_text(encoding="utf-8")

    assert ".et3-template-list { display: grid; grid-template-columns: repeat(2" in style
    assert ".et3-dialog {" in style and "overflow: hidden" in style
    assert ".et3-template span" in style
    assert "-webkit-line-clamp: 2" in style


def test_active_expert_workbench_hides_only_the_overlapping_global_brand_pill():
    style = STYLE.read_text(encoding="utf-8")

    assert "body.expert-team-v3-active .taiji-workspace-brand-pill" in style
    assert "display: none !important" in style


def test_workbench_progress_uses_one_flexible_row_for_five_or_six_stages():
    style = STYLE.read_text(encoding="utf-8")

    assert ".et3-progress { display: flex" in style
    assert ".et3-progress > span { flex: 1 1 0" in style
    assert "grid-template-columns: repeat(4" not in style


def test_team_cards_use_local_cover_and_meaningful_catalog_alt_without_remote_requests():
    result = _run_visual_hooks(
        """
        const team = hooks.normalizeTeam({
          id: 'content-creator-team',
          title: '内容创作专家团',
          image: 'https://invalid.example/remote-cover.png',
          image_alt: '内容创作专家团五位专家协作插画',
          examples: [{id: 'work_report', available: true, launch_profile_id: 'profile-1'}],
        });
        const html = hooks.teamCard(team);
        console.log(JSON.stringify({html, image: team.image, imageAlt: team.image_alt}));
        """
    )

    assert "https://invalid.example" not in result["html"]
    assert "static/assets/taiji/expert-teams/team-content-cover.png" in result["html"]
    assert 'alt="内容创作专家团五位专家协作插画"' in result["html"]
    assert 'data-et3-image' in result["html"]
    assert 'data-et3-image-fallback' in result["html"]


def test_team_dialog_member_rows_render_local_44px_avatar_name_role_and_alt():
    result = _run_visual_hooks(
        """
        const html = hooks.memberRowsHtml([{
          id: 'director',
          name: '写作总导演',
          role: '流程编排',
          image: 'static/assets/taiji/expert-teams/content-director.png',
          image_alt: '写作总导演头像',
        }]);
        console.log(JSON.stringify({html}));
        """
    )

    html = result["html"]
    assert 'class="et3-member-avatar"' in html
    assert 'alt="写作总导演头像"' in html
    assert "static/assets/taiji/expert-teams/content-director.png" in html
    assert 'class="et3-member-copy"' in html
    assert "写作总导演" in html
    assert "流程编排" in html
    assert 'data-et3-image-fallback' in html


def test_unavailable_task_keeps_its_use_case_and_shows_a_separate_release_status():
    result = _run_visual_hooks(
        """
        const html = hooks.exampleTaskRowsHtml([{
          id: 'meeting_minutes',
          label: '会议纪要',
          summary: '整理议题、形成意见、责任分工和后续跟踪事项。',
          available: false,
          disabled_reason: '该任务尚未完成完整交付验证',
        }], null);
        console.log(JSON.stringify({html}));
        """
    )

    html = result["html"]
    assert "会议纪要" in html
    assert "整理议题、形成意见、责任分工和后续跟踪事项。" in html
    assert "暂未开放" in html
    assert "该任务尚未完成完整交付验证" in html
    assert 'aria-label="会议纪要。整理议题、形成意见、责任分工和后续跟踪事项。 暂未开放：该任务尚未完成完整交付验证"' in html
    assert ">meeting_minutes<" not in html


def test_invalid_member_image_is_not_requested_and_uses_visible_text_fallback():
    result = _run_visual_hooks(
        """
        const html = hooks.memberRowsHtml([{
          id: 'reviewer',
          name: '复核专家',
          role: '复核交付',
          image: '//invalid.example/avatar.png',
          image_alt: '复核专家头像',
        }]);
        console.log(JSON.stringify({html}));
        """
    )

    html = result["html"]
    assert "invalid.example" not in html
    assert 'data-et3-image-fallback' in html
    assert 'data-et3-image-fallback hidden' not in html
    assert ">复</span>" in html


def test_delegated_image_error_hides_broken_image_reveals_text_fallback_and_survives_redraw():
    result = _run_visual_hooks(
        """
        const listeners = [];
        const root = {addEventListener(type, handler, options) { listeners.push({type, handler, options}); }};
        hooks.bindPortalEvents(root);
        const fallback = {
          hidden: true,
          attrs: {'aria-hidden': 'true'},
          matches(selector) { return selector === '[data-et3-image-fallback]'; },
          removeAttribute(name) { delete this.attrs[name]; },
        };
        const image = {
          hidden: false,
          attrs: {src: 'static/assets/taiji/expert-teams/team-content-cover.png'},
          nextElementSibling: fallback,
          matches(selector) { return selector === '[data-et3-image]'; },
          setAttribute(name, value) { this.attrs[name] = value; },
          removeAttribute(name) { delete this.attrs[name]; },
        };
        const errorListener = listeners.find(item => item.type === 'error');
        const handled = errorListener.handler({target: image});
        console.log(JSON.stringify({
          listenerTypes: listeners.map(item => item.type),
          errorCapture: errorListener && errorListener.options && errorListener.options.capture,
          handled,
          imageHidden: image.hidden,
          imageSrc: image.attrs.src || null,
          imageAriaHidden: image.attrs['aria-hidden'] || null,
          fallbackHidden: fallback.hidden,
          fallbackAriaHidden: fallback.attrs['aria-hidden'] || null,
        }));
        """
    )

    assert result == {
        "listenerTypes": ["click", "input", "error"],
        "errorCapture": True,
        "handled": True,
        "imageHidden": True,
        "imageSrc": None,
        "imageAriaHidden": "true",
        "fallbackHidden": False,
        "fallbackAriaHidden": None,
    }
