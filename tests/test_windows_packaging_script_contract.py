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
        "InputArchiveBasename", "InputArchiveBytes", "InputArchiveSha256",
        "InputManifestBasename", "InputManifestBytes", "InputManifestSha256",
        "InputSidecarBasename", "InputSidecarBytes", "InputSidecarSha256",
        "CacheRoot", "CacheRequirementsPath", "ExpectedCacheRequirementsSha256",
        "ExpectedCacheObservationSha256", "PowerShellPath", "TarPath", "NodePath",
        "NpmPath", "PythonPath", "IsccPath", "SafeTarPath", "ExpectedSafeTarSha256",
        "ExpectedTargetConfigSha256", "ExpectedAssetProvenanceSha256",
        "ExpectedHostFactsSha256", "Version",
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
                self.assertTrue(path.read_bytes().isascii())
                self.assertEqual(parameter_names(text), expected)

    def test_initialize_allows_ntfs_hardlinks_only_for_pinned_tools(self):
        initialize = read_script(INITIALIZE)
        self.assertIn("[switch]$AllowHardLink", initialize)
        self.assertIn("[string]$item.LinkType -cne 'HardLink'", initialize)
        self.assertIn(
            'Assert-RegularFile $Path "tool $Name" -AllowHardLink', initialize
        )
        self.assertEqual(initialize.count("-AllowHardLink"), 1)

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
            "remote-build.log",
        ):
            self.assertIn(basename, build)
        self.assertEqual("".join(chr(value) for value in (0x6784, 0x5EFA, 0x62A5, 0x544A)) + ".txt", "构建报告.txt")
        for codepoint in ("0x6784", "0x5efa", "0x62a5", "0x544a"):
            self.assertIn(codepoint, build.lower())
        self.assertIn("$ReportBasename", build)

    def test_formal_checks_are_single_ordered_and_fail_closed(self):
        text = read_script(BUILD)
        self.assertIn("function Invoke-FormalCheck", text)
        self.assertIn("$LASTEXITCODE -ne 0", text)
        self.assertIn("throw", text)
        self.assertNotIn("'01 source-session-identity PASS exit=0'", text)
        self.assertNotIn("'07 installer-pe-version-authenticode PASS exit=0'", text)
        self.assertIn("Append-Utf8Line", text)
        self.assertIn("Write-Utf8Text", text)
        self.assertNotIn("Add-Content", text)
        self.assertNotIn("WriteAllLines", text)
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
        self.assertIn("$session.tools.npm.path ci --offline --ignore-scripts --no-audit", text)
        self.assertIn("PAYLOAD_MENU_POLICY_OK", text)
        self.assertIn("& $payloadPython -I -B $GatePath", text)
        self.assertIn("ELECTRON_RUN_AS_NODE", text)
        self.assertIn("win32 x64", text)
        self.assertIn("import taiji_runtime.main", text)
        self.assertIn("from api.config import get_ui_visibility", text)
        self.assertIn('assert nav == {"chat", "tasks", "writing", "settings"}', text)

    def test_review_exact_set_and_separate_log_are_explicit(self):
        text = read_script(BUILD)
        for literal in (
            "TaijiAgent-Setup-$Version-win-x64.exe",
            "TaijiAgent-Setup-$Version-win-x64.exe.sha256",
            "taiji-package-manifest.json",
            "formal-build-tests.log",
            "$ReportBasename",
            ".build-success",
            "run-state.json",
            "logs\\remote-build.log",
            "fetch-review",
            "fetch-log",
        ):
            self.assertIn(literal, text)
        self.assertIn("review exact set", text.lower())
        self.assertIn("ReviewExpectedBeforeMarker", text)
        self.assertIn("ReviewExpectedAfterMarker", text)
        self.assertNotIn(".build-success',\n  'run-state.json'\n)", text)

    def test_offline_cache_and_payload_hygiene_contracts_are_explicit(self):
        initialize = read_script(INITIALIZE)
        stage = read_script(STAGE)
        build = read_script(BUILD)
        shared_literals = (
            "ExpectedCacheRequirementsSha256",
            "ExpectedCacheObservationSha256",
            "cache-observation.json",
            "staging\\cache",
            "payload-hygiene-closure",
            ".git",
            ".env",
            "*.db",
            "*.sqlite",
            "__pycache__",
        )
        for literal in shared_literals:
            with self.subTest(literal=literal):
                self.assertIn(literal, initialize + stage + build)
        self.assertIn("ci --offline --ignore-scripts --no-audit", stage + build)
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

    def test_stage_payload_contract_is_windows_candidate_specific(self):
        stage = read_script(STAGE)
        for literal in (
            "TaijiAgent.exe",
            "resources\\app\\src",
            "resources\\app\\package.json",
            "hermes-local-lab\\sources\\hermes-agent",
            "hermes-local-lab\\sources\\hermes-webui",
            "hermes-local-lab\\config\\taiji-default-config.yaml",
            "diagnose.ps1",
            "python311._pth",
            "menu gate",
            "desktop-npm-check",
            "package-lock.json",
            "Lib\\site-packages",
            "taiji_runtime_profile",
            "taiji_license",
            "aiohttp",
            "fastapi",
            "uvicorn",
            "cryptography",
            "psutil",
            "ELECTRON_RUN_AS_NODE",
            "win32 x64",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, stage)
        self.assertIn("$session.tools.npm.path", stage)
        self.assertIn("$session.tools.python.path", stage)
        self.assertNotIn("& $session.tools.npm ci", stage)
        self.assertNotIn("Copy-Item -LiteralPath $pythonSource -Destination $pythonDestination -Recurse -Force", stage)
        self.assertNotIn("Push-Location $desktopRoot", stage)
        self.assertIn("Push-Location $desktopNpmCheckRoot", stage)
        self.assertIn("foreach ($entry in @($requirements.entries))", stage)
        self.assertIn("Join-PathText $sharedCacheRoot $entry.relative_path.Replace('/', '\\')", stage)
        self.assertIn("Join-PathText $stagingCacheRoot $entry.relative_path.Replace('/', '\\')", stage)
        self.assertIn("Get-CacheEntry", stage)
        self.assertIn("staging observation identity drifted before payload assembly", stage)
        self.assertIn("__pycache__", stage)
        self.assertIn(".pyc", stage)
        self.assertIn(".pyo", stage)
        self.assertNotIn("..\\..\\..\\Agent", stage)
        self.assertNotIn("..\\..\\..\\WebUI", stage)
        self.assertIn("..\\..\\sources\\hermes-agent", stage)
        self.assertIn("..\\..\\sources\\hermes-webui", stage)
        self.assertIn("python311.zip", stage)
        self.assertIn("import site", stage)
        self.assertIn("[char]10", stage)
        self.assertIn("import taiji_runtime.main", stage)
        self.assertIn("from api.config import get_ui_visibility", stage)
        self.assertIn('assert nav == {"chat", "tasks", "writing", "settings"}', stage)
        self.assertIn("-m taiji_runtime.main --help", stage)
        self.assertNotIn("[Environment]::NewLine", stage)
        for forbidden in ("resources\\app\\tests", "payloadRoot 'Agent'", "payloadRoot 'WebUI'"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, stage)

    def test_stage_uses_extended_paths_for_remote_working_trees(self):
        stage = read_script(STAGE)
        self.assertIn("function ConvertTo-ExtendedPath", stage)
        self.assertIn("function Join-PathText", stage)
        self.assertIsNone(re.search(r"\bJoin-Path\s", stage))
        for assignment in (
            "$sourceRoot = ConvertTo-ExtendedPath ([string]$session.paths.source_root)",
            "$stagingRoot = ConvertTo-ExtendedPath ([string]$session.paths.staging_root)",
            "$stagingCacheRoot = ConvertTo-ExtendedPath ([string]$session.paths.staging_cache_root)",
            "$payloadRoot = ConvertTo-ExtendedPath ([string]$session.paths.payload_root)",
        ):
            with self.subTest(assignment=assignment):
                self.assertIn(assignment, stage)
        self.assertNotIn(
            "$sharedCacheRoot = ConvertTo-ExtendedPath", stage
        )

    def test_stage_cache_revalidation_preserves_bound_root_and_canonical_member_order(self):
        stage = read_script(STAGE)
        self.assertIn("$observation.cache_root", stage)
        self.assertIn("$sharedCacheRoot", stage)
        self.assertNotIn("$Stream.Position = 0", stage)
        self.assertIn("cache observation root drifted before staging", stage)
        self.assertIn("cache_root = [string]$observation.cache_root", stage)
        sort_index = stage.index("$members = Sort-MembersByUtf8 $members")
        digest_index = stage.index("$entry.sha256 = Get-CanonicalHash (,$members)")
        self.assertLess(sort_index, digest_index)
        self.assertIn("$entry.members = Sort-MembersByUtf8 $members", stage)
        self.assertNotIn("@(Sort-MembersByUtf8", stage)
        self.assertTrue(
            ".ComputeHash($Stream)" in stage
            or ".CopyTo(" in stage
            or ("while (" in stage and "$stream.Read(" in stage),
            "ZIP member hashing must consume the complete stream",
        )

    def test_payload_manifest_entries_use_the_same_utf8_byte_order_as_local_review(self):
        stage = read_script(STAGE)
        self.assertIn("$unsortedPayloadEntries = @(", stage)
        self.assertIn("$payloadEntries = Sort-MembersByUtf8 $unsortedPayloadEntries", stage)
        self.assertNotIn("| Sort-Object path", stage)
        chinese_paths = ["构建报告.txt", "z.txt", "é.txt"]
        self.assertEqual(
            sorted(chinese_paths, key=lambda value: value.encode("utf-8")),
            ["z.txt", "é.txt", "构建报告.txt"],
        )
        self.assertIn("[Text.Encoding]::UTF8.GetBytes", stage)

    def test_initialize_session_captures_tool_identities_not_only_paths(self):
        initialize = read_script(INITIALIZE)
        for literal in (
            "host_facts_sha256",
            "cache_observation_sha256",
            "taiji-safe-tar/v1",
            "bytes",
            "sha256",
            "version",
            "ExpectedTargetConfigSha256",
            "ExpectedAssetProvenanceSha256",
            "ExpectedHostFactsSha256",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, initialize)

    def test_initialize_reuses_transport_reserved_root_and_preserves_transferred_observation(self):
        initialize = read_script(INITIALIZE)
        self.assertNotIn("run root already exists", initialize.lower())
        self.assertNotIn("Write-AtomicJson (Join-Path $RunRoot 'input\\cache-observation.json')", initialize)
        self.assertIn("cache-observation.json", initialize)
        self.assertIn("host-facts-sha256.txt", initialize)
        self.assertIn("input\\cache-observation.json", initialize)

    def test_build_review_contract_uses_output_base_without_double_exe(self):
        build = read_script(BUILD)
        self.assertIn('OutputBaseFilename=$OutputBaseName', build)
        self.assertIn('$ArtifactBasename = "$OutputBaseName.exe"', build)
        self.assertNotIn('OutputBaseFilename=$ArtifactBasename', build)

    def test_build_manifest_and_marker_bind_remote_state_and_formal_checks_exactly(self):
        build = read_script(BUILD)
        for literal in (
            "host_facts_sha256",
            "remote_state_basename",
            "remote_state_bytes",
            "remote_state_sha256",
            "taiji-package-remote-run/v1",
            "SUMMARY PASS checks=7",
            "'{0:d2} {1} PASS exit=0' -f $formalIndex, [string]$check.id",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, build)
        self.assertNotIn("started_at = $session.source.commit", build)

    def test_build_review_verifies_pre_marker_six_files_and_pe_signature_bytes(self):
        build = read_script(BUILD)
        self.assertIn("ReviewExpectedBeforeMarker", build)
        self.assertIn("ReviewExpectedAfterMarker", build)
        self.assertIn("Compare-ByteArrays", build)
        self.assertNotIn("[Text.Encoding]::ASCII.GetString($bytes, $peOffset, 4)", build)
        self.assertIn("Move-Item -LiteralPath $OutputArtifactPath -Destination $ArtifactPath", build)
        self.assertNotIn("[Environment]::NewLine", build)
        self.assertIn("[char]10", build)
        self.assertIn("desktop-npm-check", build)
        self.assertIn("ELECTRON_RUN_AS_NODE", build)
        self.assertIn("win32 x64", build)

    def test_build_review_durably_rereads_six_files_before_atomic_marker(self):
        build = read_script(BUILD)
        self.assertIn("Flush($true)", build)
        self.assertIn("[IO.FileMode]::CreateNew", build)
        reread_start = build.index("function ReReadAndVerifyReview")
        reread_end = build.index("function Write-PackageManifest", reread_start)
        reread_body = build[reread_start:reread_end]
        self.assertIn("ReadAllBytes", reread_body)
        self.assertIn("Get-Sha256", reread_body)
        marker_call = build.rindex("Write-SuccessMarker")
        pre_marker_verify = build.rindex("ReReadAndVerifyReview", 0, marker_call)
        self.assertLess(pre_marker_verify, marker_call)
        atomic_start = build.index("function Write-AtomicJson")
        atomic_end = build.index("function Append-Utf8Line", atomic_start)
        self.assertIn("Move-Item", build[atomic_start:atomic_end])
        marker_writer_start = build.index("function Write-SuccessMarker")
        marker_writer_end = build.index("Assert-RegularFile $SessionPath", marker_writer_start)
        self.assertIn("Write-AtomicJson", build[marker_writer_start:marker_writer_end])

    def test_all_three_scripts_share_recursive_canonical_json_and_utf8_lf(self):
        for path in (INITIALIZE, STAGE, BUILD):
            text = read_script(path)
            with self.subTest(path=str(path)):
                self.assertIn("function ConvertTo-CanonicalValue", text)
                self.assertIn("function ConvertTo-CanonicalJson", text)
                self.assertIn("[Text.UTF8Encoding]::new($false)", text)
                self.assertIn("[char]10", text)
                self.assertNotIn("[Environment]::NewLine", text)


if __name__ == "__main__":
    unittest.main()
