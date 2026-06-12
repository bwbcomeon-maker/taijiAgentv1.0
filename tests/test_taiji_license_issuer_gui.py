import json
import os
import plistlib
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_JS = ROOT / "tools" / "taiji-license-issuer" / "issuer-core.js"
APP_BUNDLE = ROOT / "tools" / "taiji-license-issuer" / "启动太极License签发工具.app"
AGENT_PYTHON = ROOT / "hermes-local-lab" / "sources" / "hermes-agent" / "venv" / "bin" / "python"
AGENT_DIR = ROOT / "hermes-local-lab" / "sources" / "hermes-agent"


def _node(script: str, *, env: dict | None = None) -> dict:
    proc = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return json.loads(proc.stdout)


class TaijiLicenseIssuerGuiTest(unittest.TestCase):
    def test_issuer_generates_product_valid_license_and_safe_record(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            script = textwrap.dedent(
                f"""
                const crypto = require('crypto');
                const fs = require('fs');
                const core = require({json.dumps(str(CORE_JS))});
                const keys = crypto.generateKeyPairSync('rsa', {{ modulusLength: 2048 }});
                const privatePem = keys.privateKey.export({{ type: 'pkcs8', format: 'pem' }});
                const publicPem = keys.publicKey.export({{ type: 'spki', format: 'pem' }});
                const privatePath = {json.dumps(str(tmp / "private.pem"))};
                const publicPath = {json.dumps(str(tmp / "public.pem"))};
                const outputPath = {json.dumps(str(tmp / "license.jwt"))};
                const recordPath = {json.dumps(str(tmp / "issued_licenses.jsonl"))};
                fs.writeFileSync(privatePath, privatePem);
                fs.writeFileSync(publicPath, publicPem);
                const result = core.issueAndWriteLicense({{
                  customer: '测试客户',
                  days: 30,
                  features: 'chat,writing',
                  notBefore: '2026-06-11T00:00:00Z',
                  licenseId: 'lic-gui-test',
                  maxVersion: '1.2.3',
                  outputPath,
                  privateKeyPath: privatePath,
                  recordPath,
                  now: new Date('2026-06-11T08:00:00Z')
                }});
                const record = fs.readFileSync(recordPath, 'utf8').trim();
                console.log(JSON.stringify({{
                  payload: result.payload,
                  tokenPath: outputPath,
                  publicPath,
                  record,
                  token: result.token
                }}));
                """
            )
            data = _node(script)

            payload = data["payload"]
            self.assertEqual(payload["license_id"], "lic-gui-test")
            self.assertEqual(payload["customer"], "测试客户")
            self.assertEqual(payload["product"], "taiji-agent")
            self.assertEqual(payload["aud"], "taiji-agent")
            self.assertEqual(payload["features"], ["chat", "writing"])
            self.assertEqual(payload["not_before"], "2026-06-11T00:00:00Z")
            self.assertEqual(payload["expires_at"], "2026-07-11T00:00:00Z")
            self.assertEqual(payload["max_version"], "1.2.3")

            record = json.loads(data["record"])
            self.assertEqual(record["license_id"], "lic-gui-test")
            self.assertEqual(record["customer"], "测试客户")
            self.assertEqual(record["jwt_hash"][:7], "sha256:")
            self.assertNotIn(data["token"], data["record"])
            self.assertNotIn("PRIVATE KEY", data["record"])

            verify_script = textwrap.dedent(
                f"""
                import pathlib
                import sys
                sys.path.insert(0, {json.dumps(str(AGENT_DIR))})
                import taiji_license
                token_path = pathlib.Path({json.dumps(data["tokenPath"])})
                public_key = pathlib.Path({json.dumps(data["publicPath"])}).read_text(encoding='utf-8')
                status = taiji_license.load_license_status(
                    path=token_path,
                    public_key=public_key,
                    now=1781179200,
                    environ={{'TAIJI_LICENSE_REQUIRED': '1'}},
                    check_state=False,
                )
                print(status.status)
                """
            )
            verifier = subprocess.run(
                [str(AGENT_PYTHON), "-c", verify_script],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            self.assertEqual(verifier.stdout.strip(), "valid")

    def test_issuer_rejects_invalid_required_inputs(self):
        script = textwrap.dedent(
            f"""
            const core = require({json.dumps(str(CORE_JS))});
            const cases = [
              () => core.issueLicense({{ customer: '', days: 30, features: 'chat', privateKeyPem: 'x' }}),
              () => core.issueLicense({{ customer: '客户', days: 0, features: 'chat', privateKeyPem: 'x' }}),
              () => core.issueLicense({{ customer: '客户', days: 30, features: ' , ', privateKeyPem: 'x' }}),
              () => core.issueLicense({{ customer: '客户', days: 30, features: 'chat' }}),
            ];
            const messages = cases.map((fn) => {{
              try {{
                fn();
                return 'NO_ERROR';
              }} catch (err) {{
                return err.message;
              }}
            }});
            console.log(JSON.stringify({{ messages }}));
            """
        )
        data = _node(script)

        self.assertIn("客户名称不能为空", data["messages"][0])
        self.assertIn("有效天数必须大于 0", data["messages"][1])
        self.assertIn("功能包不能为空", data["messages"][2])
        self.assertIn("发证私钥未安装", data["messages"][3])

    def test_default_private_key_path_can_be_overridden_by_env(self):
        with tempfile.TemporaryDirectory() as td:
            override = Path(td) / "signing-private.pem"
            script = textwrap.dedent(
                f"""
                const core = require({json.dumps(str(CORE_JS))});
                console.log(JSON.stringify({{
                  path: core.resolvePrivateKeyPath({{ env: {{ TAIJI_LICENSE_PRIVATE_KEY_FILE: {json.dumps(str(override))} }} }})
                }}));
                """
            )
            data = _node(script)
            self.assertEqual(data["path"], str(override))

    def test_macos_app_bundle_double_click_launcher_is_structurally_valid(self):
        info_path = APP_BUNDLE / "Contents" / "Info.plist"
        launcher_path = APP_BUNDLE / "Contents" / "MacOS" / "taiji-license-issuer-launcher"

        self.assertTrue(info_path.is_file())
        self.assertTrue(launcher_path.is_file())
        self.assertTrue(launcher_path.stat().st_mode & stat.S_IXUSR)

        info = plistlib.loads(info_path.read_bytes())
        self.assertEqual(info["CFBundlePackageType"], "APPL")
        self.assertEqual(info["CFBundleExecutable"], "taiji-license-issuer-launcher")
        self.assertEqual(info["CFBundleIdentifier"], "local.taiji.license.issuer.launcher")
        self.assertIn("License", info["CFBundleDisplayName"])

        launcher = launcher_path.read_text(encoding="utf-8")
        self.assertIn("Electron.app/Contents/MacOS/Electron", launcher)
        self.assertIn('"$ELECTRON_BIN" "$TOOL_DIR"', launcher)
        self.assertIn("/usr/bin/osascript", launcher)
        self.assertIn("taiji-license-issuer", launcher)
        self.assertNotIn("/Users/bwb/", launcher)


if __name__ == "__main__":
    unittest.main()
