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
