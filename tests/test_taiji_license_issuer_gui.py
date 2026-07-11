import hashlib
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
TEST_MACHINE_CODE = "sha256:" + "c" * 64
OTHER_MACHINE_CODE = "sha256:" + "d" * 64
TEST_DEVICE_ID = "sha256:" + "1" * 64
OTHER_DEVICE_ID = "sha256:" + "2" * 64


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
                const machineRequest = {{
                  request_type: 'taiji_machine_license_request',
                  product: 'taiji-agent',
                  binding_type: 'machine_fingerprint_v3',
                  machine_code: {json.dumps(TEST_MACHINE_CODE)},
                  machine_code_short: 'cccccccccccc',
                  device_id: {json.dumps(TEST_DEVICE_ID)},
                  device_id_short: '111111111111',
                  fingerprint_quality: 'strong',
                  risk_flags: [],
                  generated_at: '2026-06-11T08:00:00Z',
                  machine_label: '一号终端'
                }};
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
                  machineRequest,
                  now: new Date('2026-06-11T08:00:00Z')
                }});
                const record = fs.readFileSync(recordPath, 'utf8').trim();
                console.log(JSON.stringify({{
                  payload: result.payload,
                  tokenPath: result.outputPath,
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
            self.assertEqual(payload["binding_type"], "machine_fingerprint_v3")
            self.assertEqual(payload["machine_code"], TEST_MACHINE_CODE)
            self.assertEqual(payload["device_id"], TEST_DEVICE_ID)
            self.assertEqual(payload["machine_label"], "一号终端")
            self.assertEqual(payload["machine_request_generated_at"], "2026-06-11T08:00:00Z")
            self.assertEqual(payload["fingerprint_quality"], "strong")
            self.assertEqual(payload["activation_mode"], "offline_machine_file")
            self.assertNotEqual(Path(data["tokenPath"]).name, "license.jwt")
            self.assertIn("测试客户", Path(data["tokenPath"]).name)
            self.assertIn("一号终端", Path(data["tokenPath"]).name)
            self.assertIn("cccccccccccc", Path(data["tokenPath"]).name)
            self.assertIn("20260611", Path(data["tokenPath"]).name)
            self.assertIn("20260711", Path(data["tokenPath"]).name)

            record = json.loads(data["record"])
            self.assertEqual(record["license_id"], "lic-gui-test")
            self.assertEqual(record["customer"], "测试客户")
            self.assertEqual(record["machine_code_short"], "cccccccccccc")
            self.assertEqual(record["device_id_short"], "111111111111")
            self.assertEqual(record["machine_label"], "一号终端")
            self.assertEqual(record["fingerprint_quality"], "strong")
            self.assertEqual(record["activation_mode"], "offline_machine_file")
            self.assertEqual(record["jwt_hash"][:7], "sha256:")
            self.assertNotIn(data["token"], data["record"])
            self.assertNotIn(TEST_MACHINE_CODE, data["record"])
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
                    machine_fingerprint={{
                        'binding_type': 'machine_fingerprint_v3',
                        'machine_code': {json.dumps(TEST_MACHINE_CODE)},
                        'machine_code_short': 'cccccccccccc',
                        'device_id': {json.dumps(TEST_DEVICE_ID)},
                        'device_id_short': '111111111111',
                        'fingerprint_quality': 'strong',
                    }},
                    check_state=False,
                )
                print(status.status)
                print(status.activation_mode)
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
            self.assertEqual(verifier.stdout.strip().splitlines(), ["valid", "offline_machine_file"])

    def test_issuer_rejects_invalid_required_inputs(self):
        script = textwrap.dedent(
            f"""
            const core = require({json.dumps(str(CORE_JS))});
            const cases = [
              () => core.issueLicense({{ customer: '', days: 30, features: 'chat', privateKeyPem: 'x' }}),
              () => core.issueLicense({{ customer: '客户', days: 0, features: 'chat', privateKeyPem: 'x' }}),
              () => core.issueLicense({{ customer: '客户', days: 30, features: ' , ', privateKeyPem: 'x' }}),
              () => core.issueLicense({{ customer: '客户', days: 30, features: 'chat' }}),
              () => core.issueLicense({{ customer: '客户', days: 30, features: 'chat', privateKeyPem: 'x' }}),
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
        self.assertIn("请先导入机器码文件", data["messages"][4])

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

    def test_cli_issuer_requires_machine_binding_and_generates_valid_license(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            key_script = textwrap.dedent(
                f"""
                const crypto = require('crypto');
                const fs = require('fs');
                const keys = crypto.generateKeyPairSync('rsa', {{ modulusLength: 2048 }});
                fs.writeFileSync({json.dumps(str(tmp / "private.pem"))}, keys.privateKey.export({{ type: 'pkcs8', format: 'pem' }}));
                fs.writeFileSync({json.dumps(str(tmp / "public.pem"))}, keys.publicKey.export({{ type: 'spki', format: 'pem' }}));
                console.log(JSON.stringify({{ ok: true }}));
                """
            )
            _node(key_script)
            machine_request = tmp / "taiji-machine-request.json"
            machine_request.write_text(
                json.dumps(
                    {
                        "request_type": "taiji_machine_license_request",
                        "product": "taiji-agent",
                        "binding_type": "machine_fingerprint_v3",
                        "machine_code": TEST_MACHINE_CODE,
                        "machine_code_short": "cccccccccccc",
                        "device_id": TEST_DEVICE_ID,
                        "device_id_short": "111111111111",
                        "fingerprint_quality": "strong",
                        "risk_flags": [],
                        "machine_label": "CLI 终端",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            output_path = tmp / "license.jwt"
            subprocess.run(
                [
                    str(AGENT_PYTHON),
                    str(ROOT / "hermes-local-lab" / "scripts" / "taiji_license_tool.py"),
                    "--customer",
                    "CLI 客户",
                    "--days",
                    "30",
                    "--machine-request",
                    str(machine_request),
                    "--not-before",
                    "2026-06-12T00:00:00Z",
                    "--output",
                    str(output_path),
                ],
                cwd=ROOT,
                env={**os.environ, "TAIJI_LICENSE_PRIVATE_KEY_FILE": str(tmp / "private.pem")},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
            self.assertFalse(output_path.exists())
            issued = list(tmp.glob("taiji-license-CLI-客户-CLI-终端-cccccccccccc-*.jwt"))
            self.assertEqual(len(issued), 1)

            verify_script = textwrap.dedent(
                f"""
                import pathlib
                import sys
                sys.path.insert(0, {json.dumps(str(AGENT_DIR))})
                import taiji_license
                status = taiji_license.load_license_status(
                    path=pathlib.Path({json.dumps(str(issued[0]))}),
                    public_key=pathlib.Path({json.dumps(str(tmp / "public.pem"))}).read_text(encoding='utf-8'),
                    now=1781222400,
                    environ={{'TAIJI_LICENSE_REQUIRED': '1'}},
                    machine_fingerprint={{
                        'binding_type': 'machine_fingerprint_v3',
                        'machine_code': {json.dumps(TEST_MACHINE_CODE)},
                        'machine_code_short': 'cccccccccccc',
                        'device_id': {json.dumps(TEST_DEVICE_ID)},
                        'device_id_short': '111111111111',
                        'fingerprint_quality': 'strong',
                    }},
                    check_state=False,
                )
                print(status.status)
                print(status.machine_label)
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
            lines = verifier.stdout.strip().splitlines()
            self.assertEqual(lines, ["valid", "CLI 终端"])

    def test_initializer_creates_signing_key_pair_and_license_can_be_verified(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            script = textwrap.dedent(
                f"""
                const core = require({json.dumps(str(CORE_JS))});
                const result = core.initializeSigningKeyPair({{
                  privateKeyPath: {json.dumps(str(tmp / "private" / "signing-private.pem"))}
                }});
                const machineRequest = {{
                  request_type: 'taiji_machine_license_request',
                  product: 'taiji-agent',
                  binding_type: 'machine_fingerprint_v3',
                  machine_code: {json.dumps(TEST_MACHINE_CODE)},
                  machine_code_short: 'cccccccccccc',
                  device_id: {json.dumps(TEST_DEVICE_ID)},
                  device_id_short: '111111111111',
                  fingerprint_quality: 'strong',
                  risk_flags: [],
                  machine_label: '桌面终端'
                }};
                const issued = core.issueAndWriteLicense({{
                  customer: '国家电网',
                  days: 30,
                  features: 'chat,writing',
                  outputPath: {json.dumps(str(tmp / "license.jwt"))},
                  privateKeyPath: result.privateKeyPath,
                  recordPath: {json.dumps(str(tmp / "issued_licenses.jsonl"))},
                  machineRequest,
                  now: new Date('2026-06-12T00:00:00Z')
                }});
                console.log(JSON.stringify({{
                  privateKeyPath: result.privateKeyPath,
                  publicKeyPath: result.publicKeyPath,
                  publicKeyPem: result.publicKeyPem,
                  payload: issued.payload,
                  tokenPath: issued.outputPath
                }}));
                """
            )
            data = _node(script)

            private_key = Path(data["privateKeyPath"])
            public_key = Path(data["publicKeyPath"])
            self.assertTrue(private_key.is_file())
            self.assertTrue(public_key.is_file())
            self.assertEqual(private_key.stat().st_mode & 0o777, 0o600)
            self.assertIn("BEGIN PUBLIC KEY", data["publicKeyPem"])

            verify_script = textwrap.dedent(
                f"""
                import pathlib
                import sys
                sys.path.insert(0, {json.dumps(str(AGENT_DIR))})
                import taiji_license
                status = taiji_license.load_license_status(
                    path=pathlib.Path({json.dumps(data["tokenPath"])}),
                    public_key={json.dumps(data["publicKeyPem"])},
                    now=1781222400,
                    environ={{'TAIJI_LICENSE_REQUIRED': '1'}},
                    machine_fingerprint={{
                        'binding_type': 'machine_fingerprint_v3',
                        'machine_code': {json.dumps(TEST_MACHINE_CODE)},
                        'machine_code_short': 'cccccccccccc',
                        'device_id': {json.dumps(TEST_DEVICE_ID)},
                        'device_id_short': '111111111111',
                        'fingerprint_quality': 'strong',
                    }},
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

    def test_product_fingerprint_matches_tracked_issuer_public_key_without_embedded_pem(self):
        product_source = (AGENT_DIR / "taiji_license.py").read_text(encoding="utf-8")
        issuer_key = ROOT / "tools" / "taiji-license-issuer" / "private" / "signing-public.pem"
        completed = subprocess.run(
            ["openssl", "pkey", "-pubin", "-in", str(issuer_key), "-outform", "DER"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        fingerprint = hashlib.sha256(completed.stdout).hexdigest()

        self.assertNotIn("DEFAULT_PUBLIC_KEY_PEM", product_source)
        self.assertNotIn("-----BEGIN PUBLIC KEY-----", product_source)
        self.assertIn(
            f'PRODUCTION_PUBLIC_KEY_FINGERPRINT = "{fingerprint}"',
            product_source,
        )

    def test_gui_exposes_signing_key_initialization_action(self):
        index_html = (ROOT / "tools" / "taiji-license-issuer" / "index.html").read_text(encoding="utf-8")
        preload_js = (ROOT / "tools" / "taiji-license-issuer" / "preload.js").read_text(encoding="utf-8")
        main_js = (ROOT / "tools" / "taiji-license-issuer" / "main.js").read_text(encoding="utf-8")
        renderer_js = (ROOT / "tools" / "taiji-license-issuer" / "renderer.js").read_text(encoding="utf-8")

        self.assertIn('id="initializeKey"', index_html)
        self.assertIn("初始化签发密钥", index_html)
        self.assertIn("initializeKey", preload_js)
        self.assertIn('issuer:initialize-key', main_js)
        self.assertIn("initializeKey.addEventListener", renderer_js)
        self.assertIn("缺少签发私钥", renderer_js)
        self.assertIn("chooseMachineRequest", preload_js)
        self.assertIn("chooseMachineRequestDir", preload_js)
        self.assertIn("issuer:choose-machine-request", main_js)
        self.assertIn("issuer:choose-machine-request-dir", main_js)
        self.assertIn("machineRequestPath", index_html)

    def test_issuer_batch_generates_zip_per_machine_and_safe_records(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            script = textwrap.dedent(
                f"""
                const crypto = require('crypto');
                const fs = require('fs');
                const core = require({json.dumps(str(CORE_JS))});
                const keys = crypto.generateKeyPairSync('rsa', {{ modulusLength: 2048 }});
                const privatePem = keys.privateKey.export({{ type: 'pkcs8', format: 'pem' }});
                const privatePath = {json.dumps(str(tmp / "private.pem"))};
                const zipPath = {json.dumps(str(tmp / "licenses.zip"))};
                const recordPath = {json.dumps(str(tmp / "issued_licenses.jsonl"))};
                fs.writeFileSync(privatePath, privatePem);
                const result = core.issueBatchZip({{
                  customer: '批量客户',
                  days: 15,
                  features: 'chat,writing',
                  outputPath: zipPath,
                  privateKeyPath: privatePath,
                  recordPath,
                  now: new Date('2026-06-12T00:00:00Z'),
                  machineRequests: [
                    {{
	                      request_type: 'taiji_machine_license_request',
	                      product: 'taiji-agent',
	                      binding_type: 'machine_fingerprint_v3',
	                      machine_code: {json.dumps(TEST_MACHINE_CODE)},
	                      machine_code_short: 'cccccccccccc',
	                      device_id: {json.dumps(TEST_DEVICE_ID)},
	                      device_id_short: '111111111111',
	                      fingerprint_quality: 'strong',
	                      risk_flags: [],
	                      machine_label: '一号终端'
	                    }},
	                    {{
	                      request_type: 'taiji_machine_license_request',
	                      product: 'taiji-agent',
	                      binding_type: 'machine_fingerprint_v3',
	                      machine_code: {json.dumps(OTHER_MACHINE_CODE)},
	                      machine_code_short: 'dddddddddddd',
	                      device_id: {json.dumps(OTHER_DEVICE_ID)},
	                      device_id_short: '222222222222',
	                      fingerprint_quality: 'strong',
	                      risk_flags: [],
	                      machine_label: '二号终端'
	                    }}
                  ]
                }});
                console.log(JSON.stringify({{
                  outputPath: result.outputPath,
                  files: result.files,
                  records: fs.readFileSync(recordPath, 'utf8').trim().split('\\n')
                }}));
                """
            )
            data = _node(script)

            self.assertEqual(len(data["files"]), 2)
            self.assertTrue(Path(data["outputPath"]).is_file())
            self.assertNotEqual(Path(data["outputPath"]).name, "licenses.zip")
            self.assertIn("批量客户", Path(data["outputPath"]).name)
            self.assertIn("2台", Path(data["outputPath"]).name)
            self.assertEqual(len(data["records"]), 2)
            self.assertNotIn(TEST_MACHINE_CODE, "\n".join(data["records"]))
            self.assertNotIn(OTHER_MACHINE_CODE, "\n".join(data["records"]))
            with __import__("zipfile").ZipFile(data["outputPath"]) as archive:
                names = sorted(archive.namelist())
                self.assertEqual(len(names), 2)
                self.assertTrue(all(name.endswith(".jwt") for name in names))
                self.assertTrue(all("批量客户" in name for name in names))
                self.assertTrue(any("一号终端" in name and "cccccccccccc" in name for name in names))
                self.assertTrue(any("二号终端" in name and "dddddddddddd" in name for name in names))
                tokens = [archive.read(name).decode("utf-8").strip() for name in names]
                self.assertTrue(all(token.count(".") == 2 for token in tokens))

    def test_issuer_rejects_legacy_or_duplicate_machine_requests(self):
        script = textwrap.dedent(
            f"""
            const core = require({json.dumps(str(CORE_JS))});
            const legacy = {{
              request_type: 'taiji_machine_license_request',
              product: 'taiji-agent',
              binding_type: 'machine_fingerprint_v2',
              machine_code: {json.dumps(TEST_MACHINE_CODE)},
              machine_code_short: 'cccccccccccc',
              machine_label: '旧版终端'
            }};
            const duplicateA = {{
              request_type: 'taiji_machine_license_request',
              product: 'taiji-agent',
              binding_type: 'machine_fingerprint_v3',
              machine_code: {json.dumps(TEST_MACHINE_CODE)},
              machine_code_short: 'cccccccccccc',
              device_id: {json.dumps(TEST_DEVICE_ID)},
              device_id_short: '111111111111',
              fingerprint_quality: 'strong',
              risk_flags: [],
              machine_label: '一号终端'
            }};
            const duplicateB = {{...duplicateA, machine_label: '复制出来的终端'}};
            const messages = [];
            try {{ core.normalizeMachineRequest(legacy); }} catch (err) {{ messages.push(err.message); }}
            try {{ core.issueBatchZip({{
              customer: '重复客户',
              days: 30,
              features: 'chat',
              outputPath: '/tmp/licenses.zip',
              privateKeyPem: 'not-used',
              privateKeyPath: '/tmp/missing.pem',
              machineRequests: [duplicateA, duplicateB]
            }}); }} catch (err) {{ messages.push(err.message); }}
            console.log(JSON.stringify({{messages}}));
            """
        )
        data = _node(script)
        self.assertIn("新版机器码", data["messages"][0])
        self.assertIn("重复", data["messages"][1])

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
