import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT / "taijiagent 打包交付"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TargetDesktopAcceptanceProducerTest(unittest.TestCase):
    def test_target_script_runs_only_installed_electron_and_emits_pre_sign_evidence(self):
        script = read_text(DELIVERY / "04_目标终端_桌面App验收并导出证据.sh")

        self.assertIn("TAIJI_TARGET_ACCEPTANCE_CHALLENGE", script)
        self.assertIn("/opt/taiji-agent/runtime/node/bin/node", script)
        self.assertIn("/opt/taiji-agent/runtime/agent/venv/bin/python", script)
        self.assertIn("/opt/taiji-agent/apps/taiji-desktop/node_modules/electron/dist/electron", script)
        self.assertIn("/usr/share/applications/taiji-agent.desktop", script)
        self.assertIn("run-installed-electron-acceptance.js", script)
        self.assertIn("assemble-target-evidence.py", script)
        self.assertIn("validate-taiji-release-evidence.py", script)
        self.assertIn('/opt/taiji-agent/bin/taiji-native-verify', script)
        self.assertIn('TAIJI_AGENT_ROOT="/opt/taiji-agent"', script)
        self.assertIn('-u TAIJI_AGENT_AGENT_DIR', script)
        self.assertIn('-u TAIJI_AGENT_WEBUI_DIR', script)
        self.assertIn('-u TAIJI_AGENT_PYTHON', script)
        self.assertIn('-u TAIJI_WEBUI_PYTHON', script)
        self.assertIn('-u TAIJI_AGENT_RUNTIME_ENV', script)
        self.assertIn('-u PYTHONPATH', script)
        self.assertIn('-u NODE_OPTIONS', script)
        self.assertNotIn('TAIJI_VERIFY_DESKTOP_SMOKE=1 /opt/taiji-agent/bin/taiji-native-verify', script)
        self.assertIn("--pre-sign", script)
        self.assertIn("target-verification.json", script)
        self.assertIn("driver-result.json", script)
        self.assertIn("desktop-app.png", script)
        self.assertIn("taiji-support-bundle.json", script)
        self.assertNotIn("playwright", script.lower())
        self.assertNotIn("mobile", script.lower())
        self.assertNotIn("sign-taiji-release-evidence", script)
        self.assertNotIn("PRIVATE_KEY", script)

    def test_target_script_fails_closed_on_platform_identity_and_existing_output(self):
        script = read_text(DELIVERY / "04_目标终端_桌面App验收并导出证据.sh")

        self.assertIn('if [ "$EUID" -eq 0 ]', script)
        self.assertIn('uname -s', script)
        self.assertIn('x86_64|amd64', script)
        self.assertIn('kylin|uos|openkylin', script)
        self.assertIn("DISPLAY", script)
        self.assertIn("WAYLAND_DISPLAY", script)
        self.assertIn("dpkg-query", script)
        self.assertIn("electron_executable_sha256", script)
        self.assertIn("desktop_entry_sha256", script)
        self.assertIn("machine_fingerprint_sha256", script)
        self.assertIn("sha256sum", script)
        self.assertIn("证据输出目录已存在，拒绝覆盖", script)

    def test_builder_stages_and_preflight_requires_the_acceptance_toolchain(self):
        builder = read_text(DELIVERY / "00_制包机_生成离线交付包.sh")
        preflight = read_text(DELIVERY / "01_制包机_发布预检.sh")
        validator = read_text(ROOT / "scripts/validate-taiji-release-evidence.py")
        gitignore = read_text(ROOT / ".gitignore")

        for filename in (
            "run-installed-electron-acceptance.js",
            "assemble-target-evidence.py",
            "validate-taiji-release-evidence.py",
            "signing-public.pem",
        ):
            self.assertIn(filename, builder)
            self.assertIn(filename, preflight)
            self.assertIn(f"验收工具/{filename}", validator)
        self.assertIn("stage_target_acceptance_tools", builder)
        self.assertIn("04_目标终端_桌面App验收并导出证据.sh", validator)
        self.assertIn("04_目标终端_桌面App验收并导出证据.sh", gitignore)
        self.assertNotIn('[ -x "$script" ]', preflight)
        self.assertIn('[ -f "$script" ] && [ ! -L "$script" ]', preflight)


if __name__ == "__main__":
    unittest.main()
