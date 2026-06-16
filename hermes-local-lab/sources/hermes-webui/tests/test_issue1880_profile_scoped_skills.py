import json
import os
import pathlib
import shutil
import urllib.error
import urllib.parse
import urllib.request

from tests._pytest_port import BASE
from tests.conftest import requires_agent_modules

pytestmark = requires_agent_modules


def _state_dir() -> pathlib.Path:
    return pathlib.Path(os.environ["HERMES_WEBUI_TEST_STATE_DIR"])


def _remove_path(path: pathlib.Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


class _IsolatedSkillsDirs:
    def __init__(self, profile: str):
        self.profile = profile
        self.state = _state_dir()
        self.root_skills = self.state / "skills"
        self.profile_home = self.state / "profiles" / profile
        self.profile_skills = self.profile_home / "skills"
        self._root_was_symlink = False
        self._root_symlink_target = None

    def __enter__(self):
        self._root_was_symlink = self.root_skills.is_symlink()
        if self._root_was_symlink:
            self._root_symlink_target = self.root_skills.resolve()
        _remove_path(self.root_skills)
        _remove_path(self.profile_home)
        self.root_skills.mkdir(parents=True, exist_ok=True)
        self.profile_skills.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, exc_type, exc, tb):
        _remove_path(self.profile_home)
        _remove_path(self.root_skills)
        if self._root_was_symlink and self._root_symlink_target is not None:
            self.root_skills.symlink_to(self._root_symlink_target)


def _write_skill(skills_dir: pathlib.Path, name: str, description: str, body: str) -> pathlib.Path:
    skill_dir = skills_dir / name
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}\n",
        encoding="utf-8",
    )
    (skill_dir / "references" / "note.md").write_text(
        f"linked file for {name}\n",
        encoding="utf-8",
    )
    return skill_dir


def _write_protection_config(profile_home: pathlib.Path, *skill_names: str) -> None:
    profile_home.mkdir(parents=True, exist_ok=True)
    protected_ids = "\n".join(f"      - {name}" for name in skill_names)
    (profile_home / "config.yaml").write_text(
        "skills:\n"
        "  protection:\n"
        "    enabled: true\n"
        "    audit_enabled: true\n"
        "    protected_ids:\n"
        f"{protected_ids}\n",
        encoding="utf-8",
    )


def _get(path: str, *, profile: str | None = None):
    headers = {}
    if profile:
        headers["Cookie"] = f"hermes_profile={profile}"
    req = urllib.request.Request(BASE + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read()), exc.code


def _post(path: str, body: dict, *, profile: str | None = None):
    headers = {"Content-Type": "application/json"}
    if profile:
        headers["Cookie"] = f"hermes_profile={profile}"
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read()), exc.code


def test_api_skills_list_and_content_respect_profile_cookie():
    profile = "skills1880"
    with _IsolatedSkillsDirs(profile) as dirs:
        _write_skill(
            dirs.root_skills,
            "root-only-skill-1880",
            "Root profile skill",
            "This skill belongs to the root profile.",
        )
        _write_skill(
            dirs.profile_skills,
            "profile-only-skill-1880",
            "Secondary profile skill",
            "This skill belongs to the selected browser profile.",
        )

        data, status = _get("/api/skills", profile=profile)

        assert status == 200
        names = {skill.get("name") for skill in data.get("skills", [])}
        assert "profile-only-skill-1880" in names
        assert "root-only-skill-1880" not in names

        root_data, root_status = _get("/api/skills")
        assert root_status == 200
        root_names = {skill.get("name") for skill in root_data.get("skills", [])}
        assert "root-only-skill-1880" in root_names
        assert "profile-only-skill-1880" not in root_names

        detail, detail_status = _get(
            "/api/skills/content?name=profile-only-skill-1880",
            profile=profile,
        )
        assert detail_status == 200
        assert detail.get("name") == "profile-only-skill-1880"
        assert "selected browser profile" in detail.get("content", "")

        linked_path = urllib.parse.quote("references/note.md", safe="")
        linked, linked_status = _get(
            f"/api/skills/content?name=profile-only-skill-1880&file={linked_path}",
            profile=profile,
        )
        assert linked_status == 200
        assert linked.get("content") == "linked file for profile-only-skill-1880\n"


def test_skill_detail_reads_resolved_file_without_skill_view_absolute_path(monkeypatch):
    profile = "skills1880direct"
    with _IsolatedSkillsDirs(profile) as dirs:
        _write_skill(
            dirs.profile_skills,
            "profile-direct-skill-1880",
            "Direct profile skill",
            "This skill should be read from the resolved SKILL.md file.",
        )

        from api import routes
        import tools.skills_tool as skills_tool

        monkeypatch.setattr(routes, "_active_skills_dir", lambda: dirs.profile_skills)

        def fail_if_skill_view_called(*_args, **_kwargs):
            raise AssertionError("WebUI local skill details must not call skill_view()")

        monkeypatch.setattr(skills_tool, "skill_view", fail_if_skill_view_called)

        detail = routes._skill_view_from_active_dir("profile-direct-skill-1880")

        assert detail["success"] is True
        assert detail["name"] == "profile-direct-skill-1880"
        assert "resolved SKILL.md file" in detail["content"]
        assert isinstance(detail["linked_files"], dict)


def test_protected_skill_metadata_only_and_content_blocked():
    profile = "skills1880protected"
    protected_name = "protected-skill-1880"
    secret = "PROTECTED_SECRET_1880"
    with _IsolatedSkillsDirs(profile) as dirs:
        skill_dir = _write_skill(
            dirs.profile_skills,
            protected_name,
            "Protected profile skill",
            f"This body must not leak: {secret}.",
        )
        (skill_dir / "references" / "note.md").write_text(
            f"linked file must not leak: {secret}\n",
            encoding="utf-8",
        )
        _write_protection_config(dirs.profile_home, protected_name)

        listing, list_status = _get("/api/skills", profile=profile)

        assert list_status == 200
        listed = {skill["name"]: skill for skill in listing.get("skills", [])}
        assert protected_name in listed
        assert listed[protected_name]["protected"] is True
        assert listed[protected_name]["content_available"] is False
        assert "content" not in listed[protected_name]
        assert "skill_dir" not in listed[protected_name]

        detail, detail_status = _get(
            f"/api/skills/content?name={protected_name}",
            profile=profile,
        )

        assert detail_status == 403
        dumped = json.dumps(detail, ensure_ascii=False)
        assert detail["protected"] is True
        assert detail["code"] == "protected_skill_content_unavailable"
        assert secret not in dumped
        assert "content" not in detail
        assert "linked_files" not in detail
        assert "skill_dir" not in detail

        linked_path = urllib.parse.quote("references/note.md", safe="")
        linked, linked_status = _get(
            f"/api/skills/content?name={protected_name}&file={linked_path}",
            profile=profile,
        )

        assert linked_status == 403
        assert secret not in json.dumps(linked, ensure_ascii=False)


def test_protected_skill_cannot_be_saved_or_deleted_from_webui():
    profile = "skills1880protectedwrite"
    protected_name = "protected-write-skill-1880"
    with _IsolatedSkillsDirs(profile) as dirs:
        skill_dir = _write_skill(
            dirs.profile_skills,
            protected_name,
            "Protected write skill",
            "original protected body",
        )
        _write_protection_config(dirs.profile_home, protected_name)

        saved, save_status = _post(
            "/api/skills/save",
            {
                "name": protected_name,
                "content": "---\nname: protected-write-skill-1880\n---\n\nchanged",
            },
            profile=profile,
        )

        assert save_status == 403
        assert saved["code"] == "protected_skill_write_blocked"
        assert "original protected body" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")

        deleted, delete_status = _post(
            "/api/skills/delete",
            {"name": protected_name},
            profile=profile,
        )

        assert delete_status == 403
        assert deleted["code"] == "protected_skill_delete_blocked"
        assert (skill_dir / "SKILL.md").exists()


def test_skill_save_and_delete_respect_profile_cookie():
    profile = "skills1880save"
    with _IsolatedSkillsDirs(profile) as dirs:
        content = "---\nname: profile-saved-skill-1880\ndescription: Saved profile skill\n---\n\n# Saved\n"

        saved, save_status = _post(
            "/api/skills/save",
            {"name": "profile-saved-skill-1880", "content": content},
            profile=profile,
        )

        assert save_status == 200
        saved_path = pathlib.Path(saved["path"]).resolve()
        saved_path.relative_to(dirs.profile_skills.resolve())
        assert saved_path.read_text(encoding="utf-8") == content
        assert not (dirs.root_skills / "profile-saved-skill-1880" / "SKILL.md").exists()

        deleted, delete_status = _post(
            "/api/skills/delete",
            {"name": "profile-saved-skill-1880"},
            profile=profile,
        )
        assert delete_status == 200
        assert deleted.get("ok") is True
        assert not saved_path.exists()
