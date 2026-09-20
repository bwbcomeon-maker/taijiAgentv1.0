from __future__ import annotations

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
AGENT_PYTHON = Path(
    os.environ.get(
        "TAIJI_AGENT_PYTHON",
        ROOT / "hermes-local-lab" / "sources" / "hermes-agent" / "venv" / "bin" / "python",
    )
)
AGENT_DIR = ROOT / "hermes-local-lab" / "sources" / "hermes-agent"
NODE = os.environ.get("TAIJI_TEST_NODE", "node")
OPENSSL = "/usr/bin/openssl"
TEST_MACHINE_CODE = "sha256:" + "c" * 64
OTHER_MACHINE_CODE = "sha256:" + "d" * 64
TEST_DEVICE_ID = "sha256:" + "1" * 64
OTHER_DEVICE_ID = "sha256:" + "2" * 64


def _node(script: str, *, env: dict | None = None) -> dict:
    proc = subprocess.run(
        [NODE, "-e", script],
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
        # options.features 已不再决定授权标签（产品来自机器码自动识别），
        # 这里只保留其余必填输入的拒绝行为。
        legacy_request = {
            "request_type": "taiji_machine_license_request",
            "product": "taiji-agent",
            "binding_type": "machine_fingerprint_v3",
            "machine_code": TEST_MACHINE_CODE,
            "machine_code_short": "cccccccccccc",
            "device_id": TEST_DEVICE_ID,
            "device_id_short": "111111111111",
            "fingerprint_quality": "strong",
            "risk_flags": [],
        }
        script = textwrap.dedent(
            f"""
            const core = require({json.dumps(str(CORE_JS))});
            const machineRequest = {json.dumps(legacy_request)};
            const cases = [
              () => core.issueLicense({{ customer: '', days: 30, machineRequest, privateKeyPem: 'x' }}),
              () => core.issueLicense({{ customer: '客户', days: 0, machineRequest, privateKeyPem: 'x' }}),
              () => core.issueLicense({{ customer: '客户', days: 30, machineRequest }}),
              () => core.issueLicense({{ customer: '客户', days: 30, privateKeyPem: 'x' }}),
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
        self.assertIn("发证私钥未安装", data["messages"][2])
        self.assertIn("请先导入机器码文件", data["messages"][3])

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
            [OPENSSL, "pkey", "-pubin", "-in", str(issuer_key), "-outform", "DER"],
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

    def test_gui_no_manual_features_input_and_shows_readonly_product(self):
        index_html = (ROOT / "tools" / "taiji-license-issuer" / "index.html").read_text(encoding="utf-8")
        renderer_js = (ROOT / "tools" / "taiji-license-issuer" / "renderer.js").read_text(encoding="utf-8")
        main_js = (ROOT / "tools" / "taiji-license-issuer" / "main.js").read_text(encoding="utf-8")

        # 可编辑"功能包"输入框必须移除，界面只显示只读产品识别结果。
        self.assertNotIn('id="features"', index_html)
        self.assertNotIn("value(\"features\")", renderer_js)
        self.assertNotIn("features:", renderer_js)
        self.assertNotIn("features: form.features", main_js)
        self.assertIn('id="productStatus"', index_html)
        self.assertIn("待识别", index_html)
        self.assertIn("已自动识别", renderer_js)
        self.assertIn("兼容旧版机器码", renderer_js)

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


def _machine_request(required_features=None, machine_code=TEST_MACHINE_CODE, device_id=TEST_DEVICE_ID, label="测试终端"):
    request = {
        "request_type": "taiji_machine_license_request",
        "product": "taiji-agent",
        "binding_type": "machine_fingerprint_v3",
        "machine_code": machine_code,
        "machine_code_short": machine_code.split(":", 1)[1][:12],
        "device_id": device_id,
        "device_id_short": device_id.split(":", 1)[1][:12],
        "fingerprint_quality": "strong",
        "risk_flags": [],
        "generated_at": "2026-09-20T00:00:00Z",
        "machine_label": label,
    }
    if required_features is not None:
        request["required_features"] = required_features
    return request


KONGTIAN_REQUEST = _machine_request(["kongtian_agent"], label="国网终端")
LEGACY_TAIJI_REQUEST = _machine_request(label="太极旧终端")


class LicenseIssuerProductAutoDetectTest(unittest.TestCase):
    """按机器码 required_features 自动识别产品并写入正确功能标签。"""

    def test_kongtian_request_file_auto_signs_kongtian_features(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            request_dir = tmp / "国网 机器码 目录"
            request_dir.mkdir()
            request_path = request_dir / "kongtian-agent-machine-code-测试.json"
            request_path.write_text(
                json.dumps(KONGTIAN_REQUEST, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            script = textwrap.dedent(
                f"""
                const crypto = require('crypto');
                const fs = require('fs');
                const core = require({json.dumps(str(CORE_JS))});
                const keys = crypto.generateKeyPairSync('rsa', {{ modulusLength: 2048 }});
                const privatePath = {json.dumps(str(tmp / "private.pem"))};
                fs.writeFileSync(privatePath, keys.privateKey.export({{ type: 'pkcs8', format: 'pem' }}));
                const request = core.readMachineRequestFile({json.dumps(str(request_path))});
                const product = core.resolveProductLicense(request);
                const result = core.issueAndWriteLicense({{
                  customer: '国网客户',
                  days: 30,
                  features: 'chat,writing',
                  licenseId: 'lic-kongtian-auto',
                  notBefore: '2026-09-20T00:00:00Z',
                  outputPath: {json.dumps(str(tmp / "license.jwt"))},
                  privateKeyPath: privatePath,
                  recordPath: {json.dumps(str(tmp / "issued_licenses.jsonl"))},
                  machineRequest: request,
                  now: new Date('2026-09-20T08:00:00Z')
                }});
                const [head, body, signature] = result.token.split('.');
                const verified = crypto.verify(
                  'RSA-SHA256',
                  Buffer.from(head + '.' + body),
                  keys.publicKey,
                  Buffer.from(signature, 'base64')
                );
                console.log(JSON.stringify({{
                  payloadFeatures: result.payload.features,
                  productId: product.productId,
                  productName: product.productName,
                  productFeatures: product.features,
                  basis: product.basis,
                  verified,
                  payloadProduct: result.payload.product,
                  payloadAud: result.payload.aud,
                  payloadMachineCode: result.payload.machine_code,
                  payloadDeviceId: result.payload.device_id,
                  payloadMachineRequestId: result.payload.machine_request_id,
                  record: fs.readFileSync({json.dumps(str(tmp / "issued_licenses.jsonl"))}, 'utf8').trim()
                }}));
                """
            )
            data = _node(script)

            self.assertEqual(data["payloadFeatures"], ["kongtian_agent"])
            self.assertEqual(data["productId"], "kongtian_agent")
            self.assertIn("国网空天智能体", data["productName"])
            self.assertEqual(data["productFeatures"], ["kongtian_agent"])
            self.assertIn("kongtian_agent", data["basis"])
            self.assertTrue(data["verified"])
            self.assertEqual(data["payloadProduct"], "taiji-agent")
            self.assertEqual(data["payloadAud"], "taiji-agent")
            self.assertEqual(data["payloadMachineCode"], TEST_MACHINE_CODE)
            self.assertEqual(data["payloadDeviceId"], TEST_DEVICE_ID)
            self.assertEqual(data["payloadMachineRequestId"], KONGTIAN_REQUEST["request_id"]
                             if "request_id" in KONGTIAN_REQUEST else "")

            record = json.loads(data["record"])
            self.assertEqual(record["product_id"], "kongtian_agent")
            self.assertIn("国网空天智能体", record["product_name"])
            self.assertEqual(record["features"], ["kongtian_agent"])
            self.assertTrue(record.get("recognition_basis"))
            self.assertNotIn("PRIVATE KEY", data["record"])

    def test_legacy_taiji_request_without_declaration_keeps_default_features(self):
        script = textwrap.dedent(
            f"""
            const core = require({json.dumps(str(CORE_JS))});
            const request = core.parseMachineRequest(JSON.stringify({json.dumps(LEGACY_TAIJI_REQUEST)}));
            if ('requiredFeatures' in request) {{
              throw new Error('旧格式机器码不应携带 requiredFeatures');
            }}
            const product = core.resolveProductLicense(request);
            const normalizedTwice = core.normalizeMachineRequest(request);
            const productTwice = core.resolveProductLicense(normalizedTwice);
            console.log(JSON.stringify({{
              productId: product.productId,
              productName: product.productName,
              features: product.features,
              basis: product.basis,
              stable: product.productId === productTwice.productId && JSON.stringify(product.features) === JSON.stringify(productTwice.features),
              hasField: 'requiredFeatures' in normalizedTwice
            }}));
            """
        )
        data = _node(script)
        self.assertEqual(data["productId"], "taiji_agent")
        self.assertIn("太极智能体", data["productName"])
        self.assertEqual(data["features"], ["chat", "writing"])
        self.assertTrue(data["stable"])
        self.assertFalse(data["hasField"])

    def test_repeated_normalization_and_ipc_roundtrip_is_stable(self):
        script = textwrap.dedent(
            f"""
            const core = require({json.dumps(str(CORE_JS))});
            const raw = {json.dumps(KONGTIAN_REQUEST)};
            const once = core.normalizeMachineRequest(raw);
            const twice = core.normalizeMachineRequest(once);
            // IPC 序列化（JSON round-trip）后再规范化，识别结果必须一致
            const viaIpc = core.normalizeMachineRequest(JSON.parse(JSON.stringify(once)));
            const products = [once, twice, viaIpc].map((item) => core.resolveProductLicense(item));
            console.log(JSON.stringify({{
              sameNormalized: JSON.stringify({{...twice, sourcePath: ''}}) === JSON.stringify({{...viaIpc, sourcePath: ''}}),
              features: products.map((item) => item.features.join(',')),
              productIds: products.map((item) => item.productId)
            }}));
            """
        )
        data = _node(script)
        self.assertTrue(data["sameNormalized"])
        self.assertEqual(set(data["features"]), {"kongtian_agent"})
        self.assertEqual(set(data["productIds"]), {"kongtian_agent"})

    def test_missing_field_differs_from_invalid_declarations(self):
        script = textwrap.dedent(
            f"""
            const core = require({json.dumps(str(CORE_JS))});
            const base = {json.dumps(LEGACY_TAIJI_REQUEST)};
            const missing = core.normalizeMachineRequest({{...base}});
            const cases = [
              {{...base, required_features: []}},
              {{...base, required_features: null}},
              {{...base, required_features: 'kongtian_agent'}},
              {{...base, required_features: [123]}},
              {{...base, required_features: ['kongtian_agent', null]}},
              {{...base, required_features: ['   ']}}
            ];
            const messages = cases.map((item) => {{
              try {{
                core.normalizeMachineRequest(item);
                return 'NO_ERROR';
              }} catch (err) {{
                return err.message;
              }}
            }});
            console.log(JSON.stringify({{ missingKept: !('requiredFeatures' in missing), messages }}));
            """
        )
        data = _node(script)
        self.assertTrue(data["missingKept"])
        for message in data["messages"]:
            self.assertNotEqual(message, "NO_ERROR")
            self.assertIn("required_features", message)

    def test_label_trim_dedupe_order_and_known_mappings(self):
        script = textwrap.dedent(
            f"""
            const core = require({json.dumps(str(CORE_JS))});
            const base = {json.dumps(LEGACY_TAIJI_REQUEST)};
            const resolve = (declaration) => core.resolveProductLicense({{...base, required_features: declaration}});
            console.log(JSON.stringify({{
              whitespace: resolve(['  kongtian_agent  ']).productId,
              duplicates: resolve(['kongtian_agent', 'kongtian_agent']).productId,
              taijiOrder: resolve(['writing', 'chat']).productId,
              taijiSingle: resolve(['chat']).productId,
              taijiFeatures: resolve(['writing', 'chat']).features.join(',')
            }}));
            """
        )
        data = _node(script)
        self.assertEqual(data["whitespace"], "kongtian_agent")
        self.assertEqual(data["duplicates"], "kongtian_agent")
        self.assertEqual(data["taijiOrder"], "taiji_agent")
        self.assertEqual(data["taijiSingle"], "taiji_agent")
        # 只使用已知映射，不照抄机器请求里的标签子集
        self.assertEqual(data["taijiFeatures"], "chat,writing")

    def test_unknown_case_mixed_and_conflicting_declarations_rejected(self):
        script = textwrap.dedent(
            f"""
            const core = require({json.dumps(str(CORE_JS))});
            const base = {json.dumps(LEGACY_TAIJI_REQUEST)};
            const cases = [
              ['unknown_feature'],
              ['Kongtian_Agent'],
              ['kongtian_agent', 'chat'],
              ['writing', 'kongtian_agent'],
              ['chat', 'bad_label']
            ];
            const conflict = {{...base, required_features: ['kongtian_agent'], requiredFeatures: ['chat']}};
            const consistent = {{...base, required_features: ['kongtian_agent'], requiredFeatures: ['kongtian_agent']}};
            const messages = cases.map((declaration) => {{
              try {{
                core.resolveProductLicense({{...base, required_features: declaration}});
                return 'NO_ERROR';
              }} catch (err) {{
                return err.message;
              }}
            }});
            let conflictMessage = 'NO_ERROR';
            try {{ core.normalizeMachineRequest(conflict); }} catch (err) {{ conflictMessage = err.message; }}
            let consistentProduct = 'NO_ERROR';
            try {{ consistentProduct = core.resolveProductLicense(consistent).productId; }} catch (err) {{ consistentProduct = err.message; }}
            console.log(JSON.stringify({{ messages, conflictMessage, consistentProduct }}));
            """
        )
        data = _node(script)
        for message in data["messages"]:
            self.assertNotEqual(message, "NO_ERROR")
        self.assertIn("不一致", data["conflictMessage"])
        self.assertEqual(data["consistentProduct"], "kongtian_agent")

    def test_stale_options_features_cannot_override_detection(self):
        script = textwrap.dedent(
            f"""
            const crypto = require('crypto');
            const core = require({json.dumps(str(CORE_JS))});
            const keys = crypto.generateKeyPairSync('rsa', {{ modulusLength: 2048 }});
            const privateKeyPem = keys.privateKey.export({{ type: 'pkcs8', format: 'pem' }});
            const kongtian = core.normalizeMachineRequest({json.dumps(KONGTIAN_REQUEST)});
            const legacy = core.normalizeMachineRequest({json.dumps(LEGACY_TAIJI_REQUEST)});
            const stale = core.issueLicense({{
              customer: '客户', days: 30, features: 'chat,writing',
              privateKeyPem, machineRequest: kongtian
            }});
            const injected = core.issueLicense({{
              customer: '客户', days: 30, features: 'kongtian_agent',
              privateKeyPem, machineRequest: legacy
            }});
            console.log(JSON.stringify({{
              stale: stale.payload.features,
              injected: injected.payload.features
            }}));
            """
        )
        data = _node(script)
        self.assertEqual(data["stale"], ["kongtian_agent"])
        self.assertEqual(data["injected"], ["chat", "writing"])

    def test_mixed_batch_issues_per_product_and_bad_request_aborts_all(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            zip_path = tmp / "licenses.zip"
            record_path = tmp / "issued_licenses.jsonl"
            taiji_machine_code = "sha256:" + "e" * 64
            taiji_device_id = "sha256:" + "3" * 64
            legacy_taiji_other = _machine_request(
                machine_code=taiji_machine_code,
                device_id=taiji_device_id,
                label="太极旧终端",
            )
            kongtian_other = _machine_request(
                ["kongtian_agent"],
                machine_code=OTHER_MACHINE_CODE,
                device_id=OTHER_DEVICE_ID,
                label="国网二号",
            )
            script = textwrap.dedent(
                f"""
                const crypto = require('crypto');
                const fs = require('fs');
                const core = require({json.dumps(str(CORE_JS))});
                const keys = crypto.generateKeyPairSync('rsa', {{ modulusLength: 2048 }});
                const privatePath = {json.dumps(str(tmp / "private.pem"))};
                fs.writeFileSync(privatePath, keys.privateKey.export({{ type: 'pkcs8', format: 'pem' }}));
                const good = core.issueBatchZip({{
                  customer: '混合客户',
                  days: 15,
                  features: 'chat,writing',
                  outputPath: {json.dumps(str(zip_path))},
                  privateKeyPath: privatePath,
                  recordPath: {json.dumps(str(record_path))},
                  now: new Date('2026-09-20T00:00:00Z'),
                  machineRequests: [
                    {json.dumps(KONGTIAN_REQUEST)},
                    {json.dumps(legacy_taiji_other)},
                    {json.dumps(kongtian_other)}
                  ]
                }});
                console.log(JSON.stringify({{
                  files: good.files.map((file) => ({{
                    name: file.name,
                    productId: file.product_id,
                    productName: file.product_name,
                    features: file.features
                  }}))
                }}));
                """
            )
            data = _node(script)
            self.assertEqual(len(data["files"]), 3)
            by_short = {}
            for entry in data["files"]:
                for short, code in (("cccccccccccc", TEST_MACHINE_CODE), ("dddddddddddd", OTHER_MACHINE_CODE)):
                    if short in entry["name"]:
                        by_short.setdefault(short, []).append(entry)
            kongtian_entries = [e for e in data["files"] if e["productId"] == "kongtian_agent"]
            taiji_entries = [e for e in data["files"] if e["productId"] == "taiji_agent"]
            self.assertEqual(len(kongtian_entries), 2)
            self.assertEqual(len(taiji_entries), 1)
            for entry in kongtian_entries:
                self.assertEqual(entry["features"], ["kongtian_agent"])
                self.assertIn("国网空天智能体", entry["productName"])
            for entry in taiji_entries:
                self.assertEqual(entry["features"], ["chat", "writing"])
                self.assertIn("太极智能体", entry["productName"])

            # 任何一份请求声明异常：整批拒绝，不生成 zip、不写记录
            bad_zip = tmp / "bad" / "licenses.zip"
            bad_record = tmp / "bad" / "issued_licenses.jsonl"
            bad_script = textwrap.dedent(
                f"""
                const crypto = require('crypto');
                const fs = require('fs');
                const core = require({json.dumps(str(CORE_JS))});
                const keys = crypto.generateKeyPairSync('rsa', {{ modulusLength: 2048 }});
                const privatePath = {json.dumps(str(tmp / "bad-private.pem"))};
                fs.writeFileSync(privatePath, keys.privateKey.export({{ type: 'pkcs8', format: 'pem' }}));
                const bad = {{...{json.dumps(legacy_taiji_other)}, sourcePath: '/tmp/batch-dir/broken-终端.json', required_features: ['not_a_product']}};
                let message = 'NO_ERROR';
                try {{
                  core.issueBatchZip({{
                    customer: '异常批次',
                    days: 15,
                    outputPath: {json.dumps(str(bad_zip))},
                    privateKeyPath: privatePath,
                    recordPath: {json.dumps(str(bad_record))},
                    now: new Date('2026-09-20T00:00:00Z'),
                    machineRequests: [{json.dumps(KONGTIAN_REQUEST)}, bad]
                  }});
                }} catch (err) {{ message = err.message; }}
                console.log(JSON.stringify({{
                  message,
                  zipExists: fs.existsSync({json.dumps(str(bad_zip))}),
                  recordExists: fs.existsSync({json.dumps(str(bad_record))})
                }}));
                """
            )
            bad = _node(bad_script)
            self.assertNotEqual(bad["message"], "NO_ERROR")
            self.assertIn("broken-终端.json", bad["message"])
            self.assertFalse(bad["zipExists"])
            self.assertFalse(bad["recordExists"])

    def test_utf8_bom_and_crlf_request_files_are_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bom_path = tmp / "bom-request.json"
            body = json.dumps(KONGTIAN_REQUEST, ensure_ascii=False, indent=2)
            bom_path.write_bytes(b"\xef\xbb\xbf" + body.replace("\n", "\r\n").encode("utf-8"))
            script = textwrap.dedent(
                f"""
                const crypto = require('crypto');
                const core = require({json.dumps(str(CORE_JS))});
                const keys = crypto.generateKeyPairSync('rsa', {{ modulusLength: 2048 }});
                const privateKeyPem = keys.privateKey.export({{ type: 'pkcs8', format: 'pem' }});
                const request = core.readMachineRequestFile({json.dumps(str(bom_path))});
                const product = core.resolveProductLicense(request);
                const issued = core.issueLicense({{
                  customer: 'BOM 客户', days: 30, privateKeyPem, machineRequest: request
                }});
                console.log(JSON.stringify({{
                  productId: product.productId,
                  features: issued.payload.features,
                  label: issued.payload.machine_label
                }}));
                """
            )
            data = _node(script)
            self.assertEqual(data["productId"], "kongtian_agent")
            self.assertEqual(data["features"], ["kongtian_agent"])
            self.assertEqual(data["label"], "国网终端")


    def test_decorated_request_ipc_roundtrip_keeps_protocol_fields(self):
        # 复现 2026-09-20 真实 Electron 实测缺陷：main.js 把识别结果挂在请求的
        # product 字段上会覆盖线协议 product=taiji-agent，签发时按“产品不匹配”
        # 拒绝。识别展示数据必须使用独立字段（productRecognition），
        # 且往返 IPC 序列化后重新规范化、签发仍必须成功。
        script = textwrap.dedent(
            f"""
            const crypto = require('crypto');
            const core = require({json.dumps(str(CORE_JS))});
            const keys = crypto.generateKeyPairSync('rsa', {{ modulusLength: 2048 }});
            const privateKeyPem = keys.privateKey.export({{ type: 'pkcs8', format: 'pem' }});
            const request = core.normalizeMachineRequest({json.dumps(KONGTIAN_REQUEST)});
            const recognition = core.resolveProductLicense(request);
            const decorated = {{ ...request, productRecognition: recognition }};
            const protocolProduct = decorated.product;
            const viaIpc = JSON.parse(JSON.stringify(decorated));
            const issued = core.issueLicense({{
              customer: 'IPC 客户', days: 30, privateKeyPem, machineRequest: viaIpc
            }});
            const reResolved = core.resolveProductLicense(core.normalizeMachineRequest(viaIpc));
            console.log(JSON.stringify({{
              protocolProduct,
              features: issued.payload.features,
              payloadProduct: issued.payload.product,
              reResolved: reResolved.productId
            }}));
            """
        )
        data = _node(script)
        self.assertEqual(data["protocolProduct"], "taiji-agent")
        self.assertEqual(data["features"], ["kongtian_agent"])
        self.assertEqual(data["payloadProduct"], "taiji-agent")
        self.assertEqual(data["reResolved"], "kongtian_agent")


if __name__ == "__main__":
    unittest.main()
