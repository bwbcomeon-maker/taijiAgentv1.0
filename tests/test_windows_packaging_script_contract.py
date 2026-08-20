"""Static contracts for the parameterized Windows candidate scripts."""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_ROOT = ROOT / "packaging/windows"
INITIALIZE = WINDOWS_ROOT / "Initialize-CandidateSession.ps1"
STAGE = WINDOWS_ROOT / "Stage-CandidatePayload.ps1"
BUILD = WINDOWS_ROOT / "Build-CandidateReview.ps1"
INNO = WINDOWS_ROOT / "TaijiAgent.iss"


EXPECTED_PARAMETERS = {
    INITIALIZE: {
        "RunRoot", "RunId", "SourceRoot", "SourceBranch", "SourceCommit", "SourceTree",
        "InputManifestPath", "TargetConfigPath", "AssetProvenancePath",
        "CacheRoot", "CacheRequirementsPath", "ExpectedCacheRequirementsSha256",
        "ExpectedCacheObservationSha256", "PowerShellPath", "TarPath", "NodePath",
        "NpmPath", "PythonPath", "IsccPath", "SafeTarPath", "ExpectedSafeTarSha256",
        "Version",
    },
    STAGE: {"SessionPath"},
    BUILD: {"SessionPath"},
}


def read_script(path):
    if not path.is_file():
        raise AssertionError("missing Windows script: {}".format(path))
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        raise AssertionError("Windows script contains a UTF-8 BOM: {}".format(path))
    try:
        return data.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise AssertionError("Windows script is not strict UTF-8: {}".format(exc))


def parameter_names(text):
    match = re.search(r"(?is)\bparam\s*\(\n(.*?)\n\)", text)
    if match is None:
        raise AssertionError("script has no parameter block")
    return set(re.findall(r"\[string\]\$([A-Za-z][A-Za-z0-9_]*)", match.group(1)))


class WindowsPackagingScriptContractTests(unittest.TestCase):
    def test_all_scripts_are_strict_utf8_and_have_exact_parameters(self):
        for path, expected in EXPECTED_PARAMETERS.items():
            with self.subTest(path=str(path)):
                text = read_script(path)
                self.assertEqual(parameter_names(text), expected)

    def test_scripts_never_derive_source_from_git_or_worktree(self):
        forbidden = (
            "RepositoryRoot", "ProductRepository", "GitPath", "git archive",
            "git rev-parse", "git checkout", "Invoke-WebRequest", "Start-BitsTransfer",
            "npm install", "npm ci --prefer-online", "Install-Module",
        )
        for path in (INITIALIZE, STAGE, BUILD):
            text = read_script(path).lower()
            for literal in forbidden:
                with self.subTest(path=str(path), literal=literal):
                    self.assertNotIn(literal.lower(), text)

    def test_session_and_review_schema_contracts_are_literal(self):
        initialize = read_script(INITIALIZE)
        stage = read_script(STAGE)
        build = read_script(BUILD)
        for literal in (
            "taiji-windows-candidate-session/v1",
            "taiji-windows-cache-observation/v1",
            "taiji-package-manifest/v2",
            "taiji-package-remote-run/v1",
            "taiji-package-build-success/v1",
            "SourceRoot",
            "SourceBranch",
            "SourceCommit",
            "SourceTree",
            "asset_provenance_sha256",
            "cache_requirements_sha256",
            "cache_observation_sha256",
            "boundaries",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, initialize + stage + build)
        for basename in (
            "taiji-package-manifest.json",
            "formal-build-tests.log",
            ".build-success",
            "run-state.json",
            "构建报告.txt",
            "remote-build.log",
        ):
            self.assertIn(basename, build)

    def test_formal_checks_are_single_ordered_and_fail_closed(self):
        text = read_script(BUILD)
        self.assertIn("function Invoke-FormalCheck", text)
        self.assertIn("$LASTEXITCODE -ne 0", text)
        self.assertIn("throw", text)
        ids = re.findall(
            r'Invoke-FormalCheck\s+-Id\s+["\']([^"\']+)["\']\s+-Action\s+\{',
            text,
        )
        self.assertEqual(
            ids,
            [
                "source-session-identity",
                "offline-npm-ci",
                "electron-win32-x64",
                "payload-import-menu-policy",
                "payload-hygiene-closure",
                "inno-compile",
                "installer-pe-version-authenticode",
            ],
        )
        summary_index = text.index("SUMMARY PASS checks=7")
        self.assertGreater(text.index("Write-PackageManifest", summary_index), summary_index)
        self.assertGreater(text.index("Write-SuccessMarker", summary_index), summary_index)

    def test_review_exact_set_and_separate_log_are_explicit(self):
        text = read_script(BUILD)
        for literal in (
            "TaijiAgent-Setup-$Version-win-x64.exe",
            "TaijiAgent-Setup-$Version-win-x64.exe.sha256",
            "taiji-package-manifest.json",
            "formal-build-tests.log",
            "构建报告.txt",
            ".build-success",
            "run-state.json",
            "logs\\remote-build.log",
            "fetch-review",
            "fetch-log",
        ):
            self.assertIn(literal, text)
        self.assertIn("review exact set", text.lower())

    def test_offline_cache_and_payload_hygiene_contracts_are_explicit(self):
        initialize = read_script(INITIALIZE)
        stage = read_script(STAGE)
        build = read_script(BUILD)
        for literal in (
            "ExpectedCacheRequirementsSha256",
            "ExpectedCacheObservationSha256",
            "cache-observation.json",
            "staging\\cache",
            "npm ci --offline --ignore-scripts --no-audit",
            "payload-hygiene-closure",
            ".git",
            ".env",
            "*.db",
            "*.sqlite",
            "__pycache__",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, initialize + stage + build)
        for forbidden in (
            "Invoke-WebRequest", "Start-BitsTransfer", "DownloadFile", "Set-AuthenticodeSignature",
            "Install-Module", "winget", "choco", "publish-to-customer", "Start-Process",
        ):
            self.assertNotIn(forbidden.lower(), (initialize + stage + build).lower())

    def test_binary_version_and_authenticode_contract_is_explicit(self):
        build = read_script(BUILD)
        inno = read_script(INNO)
        for literal in (
            "Get-AuthenticodeSignature",
            "NotSigned",
            "0x8664",
            "0x20b",
            "FileVersion",
            "ProductVersion",
            "PE\\0\\0",
            "MZ",
        ):
            self.assertIn(literal, build)
        self.assertIn("ArchitecturesAllowed=x64compatible", inno)
        self.assertIn("ArchitecturesInstallIn64BitMode=x64compatible", inno)
        for define in ("MyAppVersion", "PayloadRoot", "OutputDir", "OutputBaseFilename"):
            self.assertIn(define, inno)
        self.assertIn("VersionInfoVersion={#MyAppVersion}.0", inno)
        self.assertIn("VersionInfoProductName", inno)

    def test_marker_is_last_and_inno_accepts_only_four_defines(self):
        build = read_script(BUILD)
        inno = read_script(INNO)
        marker_index = build.index("Write-SuccessMarker")
        self.assertLess(build.index("ReReadAndVerifyReview"), marker_index)
        defines = re.findall(r"/D([A-Za-z][A-Za-z0-9_]*)", inno)
        self.assertEqual(set(defines), {"MyAppVersion", "PayloadRoot", "OutputDir", "OutputBaseFilename"})
        self.assertIn(".build-success", build[marker_index:])


if __name__ == "__main__":
    unittest.main()
