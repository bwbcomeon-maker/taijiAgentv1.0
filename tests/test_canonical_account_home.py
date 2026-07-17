import json
import os
import pwd
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ENV = ROOT / "hermes-local-lab" / "scripts" / "runtime-env.sh"
HEALTH_CHECK = ROOT / "hermes-local-lab" / "scripts" / "health-check.sh"
DESKTOP_MAIN = ROOT / "apps" / "taiji-desktop" / "src" / "main.js"
RESOLUTION_ERROR = (
    "Taiji Agent could not resolve the current account home "
    "from the system account database."
)


def _system_account_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def _poisoned_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for name in (
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "TAIJI_RUNTIME_HOME",
        "TAIJI_WORKSPACE",
        "TAIJI_STATE_DIR",
        "TAIJI_AGENT_TMP_DIR",
        "TAIJI_LICENSE_FILE",
        "TAIJI_LICENSE_STATE_FILE",
    ):
        env.pop(name, None)
    env.update(
        {
            "HOME": str(tmp_path / "poisoned-parent-home"),
            "TAIJI_ACCOUNT_HOME": str(tmp_path / "poisoned-parent-account"),
            "TAIJI_LICENSE_FILE": str(tmp_path / "poisoned-parent-license.jwt"),
            "TAIJI_LICENSE_STATE_FILE": str(tmp_path / "poisoned-parent-state.json"),
        }
    )
    return env


def _write_poisoned_env_files(runtime_home: Path, runtime_env: Path, tmp_path: Path) -> None:
    poisoned_dotenv_path = tmp_path / "poisoned-dotenv-path"
    poisoned_runtime_path = tmp_path / "poisoned-runtime-path"
    poisoned_dotenv_path.mkdir()
    poisoned_runtime_path.mkdir()
    mkdir_executable = shutil.which("mkdir")
    if not mkdir_executable:
        raise AssertionError("required test executable is unavailable: mkdir")
    (poisoned_dotenv_path / "mkdir").symlink_to(mkdir_executable)
    (poisoned_runtime_path / "mkdir").symlink_to(mkdir_executable)
    runtime_home.mkdir(parents=True)
    (runtime_home / ".env").write_text(
        "\n".join(
            (
                f"HOME={tmp_path / 'poisoned-dotenv-home'}",
                f"TAIJI_ACCOUNT_HOME={tmp_path / 'poisoned-dotenv-account'}",
                f"TAIJI_LICENSE_FILE={tmp_path / 'poisoned-dotenv-license.jwt'}",
                f"TAIJI_LICENSE_STATE_FILE={tmp_path / 'poisoned-dotenv-state.json'}",
                f"PATH={poisoned_dotenv_path}",
                (
                    "_taiji_resolve_system_account_home() "
                    f"{{ printf '%s\\n' '{tmp_path / 'poisoned-dotenv-function'}'; }}"
                ),
                (
                    "printf() "
                    f"{{ /bin/echo '{tmp_path / 'poisoned-dotenv-printf'}'; }}"
                ),
                "export -f printf",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    runtime_env.write_text(
        "\n".join(
            (
                f"HOME={tmp_path / 'poisoned-runtime-home'}",
                f"TAIJI_ACCOUNT_HOME={tmp_path / 'poisoned-runtime-account'}",
                f"TAIJI_LICENSE_FILE={tmp_path / 'poisoned-runtime-license.jwt'}",
                f"TAIJI_LICENSE_STATE_FILE={tmp_path / 'poisoned-runtime-state.json'}",
                f"PATH={poisoned_runtime_path}",
                (
                    "_taiji_resolve_system_account_home() "
                    f"{{ printf '%s\\n' '{tmp_path / 'poisoned-runtime-function'}'; }}"
                ),
                (
                    "printf() "
                    f"{{ /bin/echo '{tmp_path / 'poisoned-runtime-printf'}'; }}"
                ),
                "export -f printf",
            )
        )
        + "\n",
        encoding="utf-8",
    )


def _assert_canonical_license_environment(test: unittest.TestCase, values: dict[str, str]) -> None:
    account_home = _system_account_home()
    test.assertEqual(values["TAIJI_ACCOUNT_HOME"], str(account_home))
    test.assertEqual(
        values["TAIJI_LICENSE_FILE"],
        str(account_home / ".config" / "taiji-agent" / "licenses" / "active-license.jwt"),
    )
    test.assertEqual(
        values["TAIJI_LICENSE_STATE_FILE"],
        str(account_home / ".local" / "state" / "taiji-agent" / "license-state.json"),
    )


def _script_without_account_resolvers(script: Path, tmp_path: Path) -> Path:
    controlled_path = "PATH=/usr/bin:/bin:/usr/sbin:/sbin"
    source = script.read_text(encoding="utf-8")
    if source.count(controlled_path) != 1:
        raise AssertionError(f"account resolver path contract missing from {script}")
    resolverless_path = tmp_path / "resolverless-system-path"
    resolverless_path.mkdir()
    isolated_script = tmp_path / script.name
    isolated_script.write_text(
        source.replace(
            controlled_path,
            f"PATH={resolverless_path}",
            1,
        ),
        encoding="utf-8",
    )
    isolated_script.chmod(0o755)
    return isolated_script


class CanonicalAccountHomeBehaviorTest(unittest.TestCase):
    def test_runtime_env_overrides_poisoned_parent_dotenv_and_runtime_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            lab_dir = tmp_path / "lab"
            runtime_home = tmp_path / "runtime-home"
            runtime_env = tmp_path / "runtime.env"
            _write_poisoned_env_files(runtime_home, runtime_env, tmp_path)

            env = _poisoned_env(tmp_path)
            env.update(
                {
                    "TAIJI_AGENT_ROOT": str(lab_dir),
                    "TAIJI_AGENT_USE_USER_DIRS": "0",
                    "TAIJI_AGENT_SYNC_PACKAGED_CONFIG": "0",
                    "TAIJI_RUNTIME_HOME": str(runtime_home),
                    "TAIJI_WORKSPACE": str(tmp_path / "workspace"),
                    "TAIJI_AGENT_LOG_DIR": str(tmp_path / "logs"),
                    "TAIJI_AGENT_TMP_DIR": str(tmp_path / "tmp"),
                    "TAIJI_AGENT_RUNTIME_ENV": str(runtime_env),
                }
            )
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    (
                        "printf() { /bin/echo poisoned-parent-printf; }; "
                        "export -f printf; "
                        'set -e; source "$1"; /usr/bin/env'
                    ),
                    "bash",
                    str(RUNTIME_ENV),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            expected_names = {
                "TAIJI_ACCOUNT_HOME",
                "TAIJI_LICENSE_FILE",
                "TAIJI_LICENSE_STATE_FILE",
            }
            values = {
                name: value
                for line in result.stdout.splitlines()
                if "=" in line
                for name, value in (line.split("=", 1),)
                if name in expected_names
            }
            _assert_canonical_license_environment(self, values)

    def test_runtime_env_fails_closed_when_system_account_home_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            env = _poisoned_env(tmp_path)
            env.update(
                {
                    "TAIJI_AGENT_ROOT": str(tmp_path / "lab"),
                    "TAIJI_AGENT_USE_USER_DIRS": "0",
                    "TAIJI_AGENT_SYNC_PACKAGED_CONFIG": "0",
                    "TAIJI_RUNTIME_HOME": str(tmp_path / "runtime-home"),
                    "TAIJI_WORKSPACE": str(tmp_path / "workspace"),
                    "TAIJI_AGENT_LOG_DIR": str(tmp_path / "logs"),
                    "TAIJI_AGENT_TMP_DIR": str(tmp_path / "tmp"),
                    "TAIJI_AGENT_RUNTIME_ENV": str(tmp_path / "runtime.env"),
                }
            )
            isolated_runtime_env = _script_without_account_resolvers(RUNTIME_ENV, tmp_path)
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    'set -e; source "$1"',
                    "bash",
                    str(isolated_runtime_env),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(RESOLUTION_ERROR, result.stderr)

    def test_health_check_overrides_poisoned_parent_dotenv_and_runtime_env(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            runtime_home = tmp_path / "runtime-home"
            runtime_env = tmp_path / "runtime.env"
            capture_file = tmp_path / "license-env.json"
            _write_poisoned_env_files(runtime_home, runtime_env, tmp_path)
            python_stub = tmp_path / "python-stub"
            python_stub.write_text(
                textwrap.dedent(
                    """\
                    #!/bin/bash
                    /usr/bin/printf '{"TAIJI_ACCOUNT_HOME":"%s","TAIJI_LICENSE_FILE":"%s","TAIJI_LICENSE_STATE_FILE":"%s"}\\n' \
                      "$TAIJI_ACCOUNT_HOME" "$TAIJI_LICENSE_FILE" "$TAIJI_LICENSE_STATE_FILE" \
                      > "$TAIJI_TEST_CAPTURE_FILE"
                    if [ "${1:-}" = "-" ]; then
                      /usr/bin/printf 'missing|license_missing|-|-\\n'
                    elif [ "${*: -1}" = "--version" ]; then
                      /usr/bin/printf 'Taiji Agent test\\n'
                    fi
                    exit 0
                    """
                ),
                encoding="utf-8",
            )
            python_stub.chmod(0o755)

            env = _poisoned_env(tmp_path)
            env.update(
                {
                    "TAIJI_AGENT_USE_USER_DIRS": "1",
                    "TAIJI_RUNTIME_HOME": str(runtime_home),
                    "TAIJI_WORKSPACE": str(tmp_path / "workspace"),
                    "TAIJI_AGENT_LOG_DIR": str(tmp_path / "logs"),
                    "TAIJI_AGENT_TMP_DIR": str(tmp_path / "tmp"),
                    "TAIJI_AGENT_RUNTIME_ENV": str(runtime_env),
                    "TAIJI_AGENT_PYTHON": str(python_stub),
                    "TAIJI_TEST_CAPTURE_FILE": str(capture_file),
                    "AGENT_API_PORT": "9",
                    "WEBUI_PORT": "10",
                    "RUN_MODEL_TEST": "0",
                }
            )
            subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    (
                        "printf() { /bin/echo poisoned-parent-printf; }; "
                        "export -f printf; "
                        'exec /bin/bash "$1"'
                    ),
                    "bash",
                    str(HEALTH_CHECK),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )

            self.assertTrue(capture_file.is_file())
            _assert_canonical_license_environment(
                self,
                json.loads(capture_file.read_text(encoding="utf-8")),
            )

    def test_health_check_fails_closed_when_system_account_home_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            env = _poisoned_env(tmp_path)
            env.update(
                {
                    "TAIJI_AGENT_USE_USER_DIRS": "1",
                    "TAIJI_RUNTIME_HOME": str(tmp_path / "runtime-home"),
                    "TAIJI_WORKSPACE": str(tmp_path / "workspace"),
                    "TAIJI_AGENT_LOG_DIR": str(tmp_path / "logs"),
                    "TAIJI_AGENT_TMP_DIR": str(tmp_path / "tmp"),
                    "TAIJI_AGENT_RUNTIME_ENV": str(tmp_path / "runtime.env"),
                    "AGENT_API_PORT": "9",
                    "WEBUI_PORT": "10",
                }
            )
            isolated_health_check = _script_without_account_resolvers(HEALTH_CHECK, tmp_path)
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    'source "$1"',
                    "bash",
                    str(isolated_health_check),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(RESOLUTION_ERROR, result.stderr)

    def test_desktop_runtime_env_ignores_poisoned_home_and_license_variables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            env = _poisoned_env(tmp_path)
            env["TAIJI_DESKTOP_MAIN"] = str(DESKTOP_MAIN)
            result = subprocess.run(
                ["node", "-e", self._desktop_runtime_probe()],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            _assert_canonical_license_environment(self, json.loads(result.stdout))

    def test_desktop_runtime_env_fails_closed_when_user_info_has_no_home(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            env = _poisoned_env(tmp_path)
            env.update(
                {
                    "TAIJI_DESKTOP_MAIN": str(DESKTOP_MAIN),
                    "TAIJI_TEST_INVALID_USER_INFO": "1",
                }
            )
            result = subprocess.run(
                ["node", "-e", self._desktop_runtime_probe()],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(RESOLUTION_ERROR, result.stderr)

    @staticmethod
    def _desktop_runtime_probe() -> str:
        return textwrap.dedent(
            """\
            const fs = require("fs");
            const path = require("path");
            const vm = require("vm");
            const mainPath = process.env.TAIJI_DESKTOP_MAIN;
            const source = fs.readFileSync(mainPath, "utf8");
            const realOs = require("os");
            const testOs = process.env.TAIJI_TEST_INVALID_USER_INFO === "1"
              ? { ...realOs, userInfo: () => ({ homedir: "" }) }
              : realOs;
            const noop = () => {};
            const electron = {
              app: {
                isPackaged: false,
                setPath: noop,
                requestSingleInstanceLock: () => false,
                getAppPath: () => path.dirname(mainPath),
                quit: noop,
                exit: noop,
                on: noop,
                whenReady: () => ({ then: noop })
              },
              BrowserWindow: {},
              Menu: {},
              shell: {},
              dialog: {},
              systemPreferences: {},
              ipcMain: {},
              clipboard: {}
            };
            const fsStub = {
              existsSync: () => false,
              mkdirSync: noop,
              appendFileSync: noop
            };
            const localRequire = (name) => {
              if (name === "electron") return electron;
              if (name === "fs") return fsStub;
              if (name === "os") return testOs;
              if (name === "./external-link-policy") {
                return {
                  createExternalWindowOpenHandler: noop,
                  normalizeTrustedExternalOrigins: () => []
                };
              }
              return require(name);
            };
            const sandbox = {
              require: localRequire,
              module: { exports: {} },
              exports: {},
              __dirname: path.dirname(mainPath),
              __filename: mainPath,
              process,
              console,
              Buffer,
              URL,
              Date,
              setTimeout,
              clearTimeout
            };
            try {
              vm.runInNewContext(
                source + "\\nmodule.exports = { createRuntimeEnv };",
                sandbox,
                { filename: mainPath }
              );
              const runtimeEnv = sandbox.module.exports.createRuntimeEnv(
                "/tmp/taiji-test-lab",
                18642,
                18787,
                "/tmp/taiji-test-logs"
              );
              process.stdout.write(JSON.stringify({
                TAIJI_ACCOUNT_HOME: runtimeEnv.TAIJI_ACCOUNT_HOME,
                TAIJI_LICENSE_FILE: runtimeEnv.TAIJI_LICENSE_FILE,
                TAIJI_LICENSE_STATE_FILE: runtimeEnv.TAIJI_LICENSE_STATE_FILE
              }));
            } catch (error) {
              process.stderr.write(String(error && error.message ? error.message : error));
              process.exitCode = 1;
            }
            """
        )


if __name__ == "__main__":
    unittest.main()
