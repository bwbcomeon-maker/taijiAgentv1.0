from api.expert_teams.launch_profiles import list_launch_profiles


def test_standalone_launch_profiles_use_local_docx_templates():
    profiles = {profile["id"]: profile for profile in list_launch_profiles()}

    assert profiles["content-work-report"]["render_template_id"] == "standalone-work-report"
    assert profiles["research-report"]["render_template_id"] == "standalone-research-report"
    assert all(
        not profile["render_template_id"].startswith("enterprise-")
        for profile in profiles.values()
    )
