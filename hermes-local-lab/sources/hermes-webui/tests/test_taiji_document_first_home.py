import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
TAIJI_HOME_JS = (ROOT / "static" / "taiji-home.js").read_text(encoding="utf-8")
I18N_JS = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")


def test_cron_navigation_uses_unambiguous_scheduled_task_copy():
    nav_start = INDEX_HTML.index('data-taiji-panel="tasks"')
    nav_end = INDEX_HTML.index("</button>", nav_start)
    nav = INDEX_HTML[nav_start:nav_end]

    assert "定时任务" in nav
    assert ">任务<" not in nav
    assert "tasks:'定时任务'" in TAIJI_HOME_JS
    assert "tasks:{title:'定时任务',i18nKey:'scheduled_jobs',panelId:'panelTasks'}" in TAIJI_HOME_JS


def test_simplified_chinese_task_tab_matches_scheduled_job_domain():
    zh_start = I18N_JS.index("  zh: {")
    zh_end = I18N_JS.index("\n  },", zh_start)
    zh = I18N_JS[zh_start:zh_end]

    assert "tab_tasks: '定时任务'" in zh
    assert "scheduled_jobs: '定时任务'" in zh
    assert "tasks_empty_title: '选择一个定时任务'" in zh
    assert "tasks_empty_sub: '从任务列表中选择一个定时任务" in zh


def test_simplified_chinese_onboarding_has_no_english_password_fallback():
    zh_start = I18N_JS.index("  zh: {")
    zh_end = I18N_JS.index("\n  },", zh_start)
    zh = I18N_JS[zh_start:zh_end]

    assert "onboarding_step_password_desc: '共享设备使用前可启用密码保护。'" in zh
    assert "onboarding_finish_help: '完成后会保存设置并进入应用。'" in zh
    expected_localized_lines = (
        "onboarding_notice_system_ready: '本机应用已就绪。'",
        "onboarding_notice_system_unavailable: '本机应用仍在准备中。请等待就绪检查通过后再完成设置。'",
        "onboarding_config_file: '配置状态：'",
        "onboarding_env_file: '凭据状态：'",
        "onboarding_notice_setup_required: '请选择提供商并在此保存凭据。'",
        "onboarding_oauth_provider_ready_body: '当前应用已配置为使用 OAuth 提供商",
        "onboarding_oauth_provider_not_ready_body: '当前应用已配置为使用 <strong>{provider}</strong>",
        "onboarding_workspace_placeholder: '可选的本地工作区路径'",
        "onboarding_api_key_help_prefix: '已保存在本机凭据存储中'",
    )
    assert all(line in zh for line in expected_localized_lines)
    assert "Protect the app before sharing it." not in zh
    assert "Finishing saves your setup and opens the app." not in zh
    assert "The local app is ready." not in zh
    assert "Choose a provider and save credentials here." not in zh


def test_english_task_surface_uses_one_scheduled_task_term():
    en_start = I18N_JS.index("  en: {")
    en_end = I18N_JS.index("\n  },", en_start)
    en = I18N_JS[en_start:en_end]
    nav_start = INDEX_HTML.index('data-taiji-panel="tasks"')
    nav_end = INDEX_HTML.index("</button>", nav_start)
    nav = INDEX_HTML[nav_start:nav_end]

    assert "tab_tasks: 'Scheduled tasks'" in en
    assert "scheduled_jobs: 'Scheduled tasks'" in en
    assert "tasks_empty_title: 'Select a scheduled task'" in en
    assert "tasks_empty_sub: 'Pick a scheduled task from the task list" in en
    assert 'data-i18n="tab_tasks"' in nav
    assert "tasks:{title:'定时任务',i18nKey:'scheduled_jobs',panelId:'panelTasks'}" in TAIJI_HOME_JS

    task_lines = [
        line
        for line in en.splitlines()
        if re.match(r"\s*(?:new_job|create_job|cron_[a-z0-9_]+):", line)
    ]
    assert task_lines
    assert not any(re.search(r"\bjobs?\b", line.split(":", 1)[1], re.IGNORECASE) for line in task_lines)
