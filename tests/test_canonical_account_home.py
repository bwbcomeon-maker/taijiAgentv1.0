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
START_AGENT = ROOT / "hermes-local-lab" / "scripts" / "start-agent.sh"
START_WEBUI = ROOT / "hermes-local-lab" / "scripts" / "start-webui.sh"
DESKTOP_MAIN = ROOT / "apps" / "taiji-desktop" / "src" / "main.js"
RESOLUTION_ERROR = (
    "Taiji Agent could not resolve the current account home "
    "from the system account database."
)
EXPORTED_FUNCTION_ERROR = (
    "Taiji Agent refuses to run with exported shell functions "
    "in the environment."
)
READONLY_BOUNDARY_ERROR = (
    "Taiji Agent could not establish the canonical license path "
    "readonly boundary."
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
        "LD_PRELOAD",
        "DYLD_INSERT_LIBRARIES",
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
                'TAIJI_TEST_DOTENV_QUOTED="dot env value with spaces"',
                "TAIJI_TEST_API_KEY='sk-test.key value'",
                f"TAIJI_TEST_LITERAL_COMMAND=$(/usr/bin/touch {tmp_path / 'command-expanded'})",
                f"TAIJI_TEST_LITERAL_BACKTICK=`/usr/bin/touch {tmp_path / 'backtick-expanded'}`",
                "TAIJI_TEST_LITERAL_PARAMETER=${HOME}/not-expanded",
                f"LD_PRELOAD={tmp_path / 'untrusted-preload.so'}",
                f"DYLD_INSERT_LIBRARIES={tmp_path / 'untrusted-dyld.dylib'}",
                (
                    "_taiji_resolve_system_account_home() "
                    f"{{ printf '%s\\n' '{tmp_path / 'poisoned-dotenv-function'}'; }}"
                ),
                (
                    "mkdir() { "
                    f"TAIJI_ACCOUNT_HOME='{tmp_path / 'poisoned-dotenv-mkdir'}'; "
                    f"TAIJI_LICENSE_FILE='{tmp_path / 'poisoned-dotenv-mkdir.jwt'}'; "
                    f"TAIJI_LICENSE_STATE_FILE='{tmp_path / 'poisoned-dotenv-mkdir.json'}'; "
                    '/bin/mkdir "$@"; }'
                ),
                (
                    "printf() { "
                    f"TAIJI_ACCOUNT_HOME='{tmp_path / 'poisoned-dotenv-printf'}'; "
                    f"TAIJI_LICENSE_FILE='{tmp_path / 'poisoned-dotenv-printf.jwt'}'; "
                    f"TAIJI_LICENSE_STATE_FILE='{tmp_path / 'poisoned-dotenv-printf.json'}'; "
                    'builtin printf "$@"; }'
                ),
                (
                    "taiji_unlisted_env_hook() { "
                    f"TAIJI_ACCOUNT_HOME='{tmp_path / 'poisoned-dotenv-unlisted'}'; "
                    "}"
                ),
                "export -f mkdir printf taiji_unlisted_env_hook",
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
                "TAIJI_TEST_RUNTIME_QUOTED='runtime env value with spaces'",
                (
                    "_taiji_resolve_system_account_home() "
                    f"{{ printf '%s\\n' '{tmp_path / 'poisoned-runtime-function'}'; }}"
                ),
                (
                    "mkdir() { "
                    f"TAIJI_ACCOUNT_HOME='{tmp_path / 'poisoned-runtime-mkdir'}'; "
                    f"TAIJI_LICENSE_FILE='{tmp_path / 'poisoned-runtime-mkdir.jwt'}'; "
                    f"TAIJI_LICENSE_STATE_FILE='{tmp_path / 'poisoned-runtime-mkdir.json'}'; "
                    '/bin/mkdir "$@"; }'
                ),
                (
                    "printf() { "
                    f"TAIJI_ACCOUNT_HOME='{tmp_path / 'poisoned-runtime-printf'}'; "
                    f"TAIJI_LICENSE_FILE='{tmp_path / 'poisoned-runtime-printf.jwt'}'; "
                    f"TAIJI_LICENSE_STATE_FILE='{tmp_path / 'poisoned-runtime-printf.json'}'; "
                    'builtin printf "$@"; }'
                ),
                (
                    "taiji_unlisted_env_hook() { "
                    f"TAIJI_ACCOUNT_HOME='{tmp_path / 'poisoned-runtime-unlisted'}'; "
                    "}"
                ),
                "export -f mkdir printf taiji_unlisted_env_hook",
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


def _script_with_failed_function_scan(
    script: Path, tmp_path: Path, failure_mode: str
) -> Path:
    scan_command = "/usr/bin/env | /usr/bin/grep -q '^BASH_FUNC_'"
    source = script.read_text(encoding="utf-8")
    if source.count(scan_command) != 1:
        raise AssertionError(f"exported function scan contract missing from {script}")
    if failure_mode == "env":
        replacement = "/bin/sh -c 'exit 2' | /usr/bin/grep -q '^BASH_FUNC_'"
    elif failure_mode == "grep":
        replacement = "/usr/bin/env | /bin/sh -c 'exit 2'"
    else:
        raise AssertionError(f"unsupported failure mode: {failure_mode}")
    isolated_script = tmp_path / f"{failure_mode}-{script.name}"
    isolated_script.write_text(
        source.replace(scan_command, replacement, 1),
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
                        'set -e; source "$1"; '
                        "/usr/bin/printf 'TAIJI_TEST_LD_PRELOAD_SET=%s\\n"
                        "TAIJI_TEST_DYLD_SET=%s\\n' "
                        '"${LD_PRELOAD+x}" "${DYLD_INSERT_LIBRARIES+x}"; '
                        "/usr/bin/env"
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
                "TAIJI_TEST_DOTENV_QUOTED",
                "TAIJI_TEST_API_KEY",
                "TAIJI_TEST_RUNTIME_QUOTED",
                "TAIJI_TEST_LITERAL_COMMAND",
                "TAIJI_TEST_LITERAL_BACKTICK",
                "TAIJI_TEST_LITERAL_PARAMETER",
                "LD_PRELOAD",
                "DYLD_INSERT_LIBRARIES",
                "HOME",
                "PATH",
                "TAIJI_TEST_LD_PRELOAD_SET",
                "TAIJI_TEST_DYLD_SET",
            }
            values = {
                name: value
                for line in result.stdout.splitlines()
                if "=" in line
                for name, value in (line.split("=", 1),)
                if name in expected_names
            }
            _assert_canonical_license_environment(self, values)
            self.assertEqual(
                values["TAIJI_TEST_DOTENV_QUOTED"],
                "dot env value with spaces",
            )
            self.assertEqual(values["TAIJI_TEST_API_KEY"], "sk-test.key value")
            self.assertEqual(
                values["TAIJI_TEST_RUNTIME_QUOTED"],
                "runtime env value with spaces",
            )
            self.assertEqual(
                values["TAIJI_TEST_LITERAL_COMMAND"],
                f"$(/usr/bin/touch {tmp_path / 'command-expanded'})",
            )
            self.assertEqual(
                values["TAIJI_TEST_LITERAL_BACKTICK"],
                f"`/usr/bin/touch {tmp_path / 'backtick-expanded'}`",
            )
            self.assertEqual(
                values["TAIJI_TEST_LITERAL_PARAMETER"],
                "${HOME}/not-expanded",
            )
            self.assertFalse((tmp_path / "command-expanded").exists())
            self.assertFalse((tmp_path / "backtick-expanded").exists())
            self.assertNotIn("LD_PRELOAD", values)
            self.assertNotIn("DYLD_INSERT_LIBRARIES", values)
            self.assertEqual(values["TAIJI_TEST_LD_PRELOAD_SET"], "")
            self.assertEqual(values["TAIJI_TEST_DYLD_SET"], "")
            self.assertEqual(values["HOME"], str(tmp_path / "poisoned-parent-home"))
            self.assertNotEqual(values["PATH"], str(tmp_path / "poisoned-runtime-path"))

    def test_runtime_env_rejects_any_inherited_exported_function(self):
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
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    (
                        "taiji_never_listed_canary() { /bin/echo should-not-run; }; "
                        "export -f taiji_never_listed_canary; "
                        'source "$1"'
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

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(EXPORTED_FUNCTION_ERROR, result.stderr)

    def test_exported_control_builtins_cannot_bypass_function_rejection(self):
        for function_name in ("return", "exit", "command", "builtin"):
            with self.subTest(function_name=function_name):
                with tempfile.TemporaryDirectory() as temp_dir:
                    tmp_path = Path(temp_dir)
                    env = _poisoned_env(tmp_path)
                    result = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            (
                                f"function {function_name} "
                                "{ /bin/echo should-not-run; }; "
                                f"export -f {function_name}; "
                                'source "$1"'
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

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(EXPORTED_FUNCTION_ERROR, result.stderr)

    def test_start_agent_rejects_inherited_exported_function_before_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            env = _poisoned_env(tmp_path)
            env.update(
                {
                    "TAIJI_AGENT_USE_USER_DIRS": "0",
                    "TAIJI_AGENT_SYNC_PACKAGED_CONFIG": "0",
                    "TAIJI_RUNTIME_HOME": str(tmp_path / "runtime-home"),
                    "TAIJI_WORKSPACE": str(tmp_path / "workspace"),
                    "TAIJI_AGENT_LOG_DIR": str(tmp_path / "logs"),
                    "TAIJI_AGENT_TMP_DIR": str(tmp_path / "tmp"),
                    "TAIJI_AGENT_RUNTIME_ENV": str(tmp_path / "runtime.env"),
                }
            )
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    (
                        "taiji_never_listed_canary() { /bin/echo should-not-run; }; "
                        "export -f taiji_never_listed_canary; "
                        'exec /bin/bash "$1"'
                    ),
                    "bash",
                    str(START_AGENT),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(EXPORTED_FUNCTION_ERROR, result.stderr)

    def test_start_scripts_accept_readonly_canonical_paths(self):
        scenarios = (
            (
                START_AGENT,
                {
                    "TAIJI_AGENT_AGENT_DIR": "missing-agent",
                },
                "Taiji Agent Python runtime not found",
            ),
            (
                START_WEBUI,
                {
                    "TAIJI_AGENT_AGENT_DIR": "missing-agent",
                    "TAIJI_AGENT_WEBUI_DIR": "missing-webui",
                    "API_SERVER_KEY": "unit-test-api-key",
                },
                "Taiji web service entrypoint not found",
            ),
        )
        for script, extra_env, expected_error in scenarios:
            with self.subTest(script=script.name):
                with tempfile.TemporaryDirectory() as temp_dir:
                    tmp_path = Path(temp_dir)
                    env = _poisoned_env(tmp_path)
                    env.update(
                        {
                            "TAIJI_AGENT_USE_USER_DIRS": "0",
                            "TAIJI_AGENT_SYNC_PACKAGED_CONFIG": "0",
                            "TAIJI_RUNTIME_HOME": str(tmp_path / "runtime-home"),
                            "TAIJI_WORKSPACE": str(tmp_path / "workspace"),
                            "TAIJI_AGENT_LOG_DIR": str(tmp_path / "logs"),
                            "TAIJI_AGENT_TMP_DIR": str(tmp_path / "tmp"),
                            "TAIJI_AGENT_RUNTIME_ENV": str(tmp_path / "runtime.env"),
                        }
                    )
                    env.update(
                        {
                            name: str(tmp_path / value)
                            if value.startswith("missing-")
                            else value
                            for name, value in extra_env.items()
                        }
                    )
                    result = subprocess.run(
                        ["/bin/bash", str(script)],
                        cwd=ROOT,
                        env=env,
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=30,
                    )

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected_error, result.stderr)
                    self.assertNotIn("readonly variable", result.stderr)

    def test_runtime_env_fails_closed_when_function_scan_is_unavailable(self):
        for failure_mode in ("env", "grep"):
            with self.subTest(failure_mode=failure_mode):
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
                    isolated_runtime_env = _script_with_failed_function_scan(
                        RUNTIME_ENV, tmp_path, failure_mode
                    )
                    result = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            'source "$1"',
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
                    self.assertIn(
                        "could not verify the exported shell function boundary",
                        result.stderr,
                    )

    def test_runtime_env_makes_canonical_paths_readonly_against_local_functions(self):
        for function_name in ("mkdir", "printf"):
            with self.subTest(function_name=function_name):
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
                            "TAIJI_TEST_FUNCTION_NAME": function_name,
                            "TAIJI_TEST_POISONED_HOME": str(tmp_path / "local-function"),
                        }
                    )
                    result = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            textwrap.dedent(
                                """\
                                set -e
                                TAIJI_TEST_ARM_LOCAL_FUNCTION=0
                                if [ "$TAIJI_TEST_FUNCTION_NAME" = "mkdir" ]; then
                                  mkdir() {
                                    if [ "$TAIJI_TEST_ARM_LOCAL_FUNCTION" = "1" ]; then
                                      TAIJI_ACCOUNT_HOME="$TAIJI_TEST_POISONED_HOME"
                                    fi
                                    /bin/mkdir "$@"
                                  }
                                else
                                  printf() {
                                    if [ "$TAIJI_TEST_ARM_LOCAL_FUNCTION" = "1" ]; then
                                      TAIJI_ACCOUNT_HOME="$TAIJI_TEST_POISONED_HOME"
                                    fi
                                    builtin printf "$@"
                                  }
                                fi
                                source "$1"
                                declare -p TAIJI_ACCOUNT_HOME TAIJI_LICENSE_FILE TAIJI_LICENSE_STATE_FILE
                                TAIJI_TEST_ARM_LOCAL_FUNCTION=1
                                if [ "$TAIJI_TEST_FUNCTION_NAME" = "mkdir" ]; then
                                  mkdir -p "$TAIJI_TEST_POISONED_HOME/after-resolution"
                                else
                                  printf '%s\\n' after-resolution
                                fi
                                """
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

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("declare -rx TAIJI_ACCOUNT_HOME=", result.stdout)
                    self.assertIn("readonly variable", result.stderr)

    def test_runtime_env_bypasses_unexported_readonly_function_when_sourced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            canary = tmp_path / "post-canonical-mkdir-ran"
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
                    "TAIJI_TEST_EXPECTED_ACCOUNT_HOME": str(_system_account_home()),
                    "TAIJI_TEST_POISONED_HOME": str(tmp_path / "poisoned"),
                    "TAIJI_TEST_CANARY": str(canary),
                }
            )
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    textwrap.dedent(
                        """\
                        readonly() { :; }
                        mkdir() {
                          if [ "${TAIJI_ACCOUNT_HOME:-}" = "$TAIJI_TEST_EXPECTED_ACCOUNT_HOME" ]; then
                            TAIJI_ACCOUNT_HOME="$TAIJI_TEST_POISONED_HOME"
                            TAIJI_LICENSE_FILE="$TAIJI_TEST_POISONED_HOME/license.jwt"
                            TAIJI_LICENSE_STATE_FILE="$TAIJI_TEST_POISONED_HOME/state.json"
                            /usr/bin/touch "$TAIJI_TEST_CANARY"
                          fi
                          /bin/mkdir "$@"
                        }
                        source "$1"
                        declare -p TAIJI_ACCOUNT_HOME TAIJI_LICENSE_FILE TAIJI_LICENSE_STATE_FILE
                        """
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
            self.assertFalse(canary.exists())
            self.assertIn("declare -rx TAIJI_ACCOUNT_HOME=", result.stdout)
            self.assertIn("declare -rx TAIJI_LICENSE_FILE=", result.stdout)
            self.assertIn("declare -rx TAIJI_LICENSE_STATE_FILE=", result.stdout)
            self.assertIn(str(_system_account_home()), result.stdout)

    def test_runtime_env_fails_closed_when_unexported_builtin_hides_readonly(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            canary = tmp_path / "post-canonical-mkdir-ran"
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
                    "TAIJI_TEST_EXPECTED_ACCOUNT_HOME": str(_system_account_home()),
                    "TAIJI_TEST_CANARY": str(canary),
                }
            )
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    textwrap.dedent(
                        """\
                        readonly() { :; }
                        builtin() { :; }
                        mkdir() {
                          if [ "${TAIJI_ACCOUNT_HOME:-}" = "$TAIJI_TEST_EXPECTED_ACCOUNT_HOME" ]; then
                            /usr/bin/touch "$TAIJI_TEST_CANARY"
                          fi
                          /bin/mkdir "$@"
                        }
                        source "$1"
                        """
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

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(READONLY_BOUNDARY_ERROR, result.stderr)
            self.assertFalse(canary.exists())

    def test_runtime_env_fails_closed_when_readonly_paths_are_not_exported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            env = _poisoned_env(tmp_path)
            for name in (
                "TAIJI_ACCOUNT_HOME",
                "TAIJI_LICENSE_FILE",
                "TAIJI_LICENSE_STATE_FILE",
            ):
                env.pop(name, None)
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
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    textwrap.dedent(
                        """\
                        builtin() {
                          case "$1" in
                            export) return 0 ;;
                            readonly)
                              shift
                              readonly "$@"
                              ;;
                          esac
                        }
                        source "$1"
                        """
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

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(READONLY_BOUNDARY_ERROR, result.stderr)

    def test_runtime_env_handles_unexported_readonly_from_bash_env(self):
        for shadow_builtin, expected_returncode in ((False, 0), (True, None)):
            with self.subTest(shadow_builtin=shadow_builtin):
                with tempfile.TemporaryDirectory() as temp_dir:
                    tmp_path = Path(temp_dir)
                    canary = tmp_path / "post-canonical-mkdir-ran"
                    bash_env = tmp_path / "bash-env.sh"
                    bash_env.write_text(
                        textwrap.dedent(
                            f"""\
                            readonly() {{ :; }}
                            {"builtin() { :; }" if shadow_builtin else ""}
                            mkdir() {{
                              if [ "${{TAIJI_ACCOUNT_HOME:-}}" = "$TAIJI_TEST_EXPECTED_ACCOUNT_HOME" ]; then
                                /usr/bin/touch "$TAIJI_TEST_CANARY"
                              fi
                              /bin/mkdir "$@"
                            }}
                            """
                        ),
                        encoding="utf-8",
                    )
                    env = _poisoned_env(tmp_path)
                    env.update(
                        {
                            "BASH_ENV": str(bash_env),
                            "TAIJI_AGENT_ROOT": str(tmp_path / "lab"),
                            "TAIJI_AGENT_USE_USER_DIRS": "0",
                            "TAIJI_AGENT_SYNC_PACKAGED_CONFIG": "0",
                            "TAIJI_RUNTIME_HOME": str(tmp_path / "runtime-home"),
                            "TAIJI_WORKSPACE": str(tmp_path / "workspace"),
                            "TAIJI_AGENT_LOG_DIR": str(tmp_path / "logs"),
                            "TAIJI_AGENT_TMP_DIR": str(tmp_path / "tmp"),
                            "TAIJI_AGENT_RUNTIME_ENV": str(tmp_path / "runtime.env"),
                            "TAIJI_TEST_EXPECTED_ACCOUNT_HOME": str(
                                _system_account_home()
                            ),
                            "TAIJI_TEST_CANARY": str(canary),
                        }
                    )
                    result = subprocess.run(
                        ["/bin/bash", str(RUNTIME_ENV)],
                        cwd=ROOT,
                        env=env,
                        text=True,
                        capture_output=True,
                        check=False,
                    )

                    if expected_returncode is None:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn(READONLY_BOUNDARY_ERROR, result.stderr)
                    else:
                        self.assertEqual(
                            result.returncode,
                            expected_returncode,
                            result.stdout + result.stderr,
                        )
                    self.assertFalse(canary.exists())

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
            result = subprocess.run(
                ["/bin/bash", str(HEALTH_CHECK)],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )

            self.assertTrue(capture_file.is_file())
            self.assertNotIn("readonly variable", result.stderr)
            _assert_canonical_license_environment(
                self,
                json.loads(capture_file.read_text(encoding="utf-8")),
            )

    def test_health_check_rejects_any_inherited_exported_function(self):
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
                }
            )
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    (
                        "taiji_never_listed_canary() { /bin/echo should-not-run; }; "
                        "export -f taiji_never_listed_canary; "
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

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(EXPORTED_FUNCTION_ERROR, result.stderr)

    def test_health_check_readonly_boundary_blocks_later_local_function_rewrite(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            canary = tmp_path / "health-printf-rewrite-ran"
            env = _poisoned_env(tmp_path)
            env.update(
                {
                    "TAIJI_AGENT_USE_USER_DIRS": "1",
                    "TAIJI_RUNTIME_HOME": str(tmp_path / "runtime-home"),
                    "TAIJI_WORKSPACE": str(tmp_path / "workspace"),
                    "TAIJI_AGENT_LOG_DIR": str(tmp_path / "logs"),
                    "TAIJI_AGENT_TMP_DIR": str(tmp_path / "tmp"),
                    "TAIJI_AGENT_RUNTIME_ENV": str(tmp_path / "runtime.env"),
                    "TAIJI_TEST_EXPECTED_ACCOUNT_HOME": str(_system_account_home()),
                    "TAIJI_TEST_POISONED_HOME": str(tmp_path / "poisoned"),
                    "TAIJI_TEST_CANARY": str(canary),
                    "AGENT_API_PORT": "9",
                    "WEBUI_PORT": "10",
                    "RUN_MODEL_TEST": "0",
                }
            )
            result = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    textwrap.dedent(
                        """\
                        readonly() { :; }
                        printf() {
                          if [ "${TAIJI_ACCOUNT_HOME:-}" = "$TAIJI_TEST_EXPECTED_ACCOUNT_HOME" ]; then
                            TAIJI_ACCOUNT_HOME="$TAIJI_TEST_POISONED_HOME"
                            /usr/bin/touch "$TAIJI_TEST_CANARY"
                          fi
                          builtin printf "$@"
                        }
                        source "$1"
                        """
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

            self.assertIn("readonly variable", result.stderr)
            self.assertFalse(canary.exists())

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
