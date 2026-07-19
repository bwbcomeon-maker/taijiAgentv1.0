from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from textwrap import dedent

import yaml


_REPO_ROOT = Path(__file__).resolve().parents[1]
_NIX_ROOT = _REPO_ROOT / "nix"


def _source(name: str) -> str:
    return (_NIX_ROOT / name).read_text(encoding="utf-8")


def _writer_python_source() -> str:
    nix_source = _source("configMergeScript.nix")
    body = nix_source.split(
        'pkgs.writeScript "hermes-config-merge" \'\'\n',
        maxsplit=1,
    )[1].rsplit("\n''", maxsplit=1)[0]
    return dedent(body)


def _run_writer(
    arguments: Sequence[Path | str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            _writer_python_source(),
            *(str(argument) for argument in arguments),
        ],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _read_yaml(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    assert isinstance(loaded, dict)
    return loaded


def test_nix_writer_uses_packaged_python_and_canonical_transactions() -> None:
    writer = _source("configMergeScript.nix")

    assert "#!${python}" in writer
    assert "credential_transaction" in writer
    assert "mutate_config_strict" in writer
    assert "replace_config_env_payload_strict" in writer
    assert "reconcile_capability_config_epochs" in writer
    assert "capability_epochs_for_secret_env" in writer
    assert "secret_only_capabilities" in writer
    assert 'open(config_path, "w")' not in writer


def test_nixos_activation_routes_config_and_env_through_one_writer() -> None:
    module = _source("nixosModules.nix")

    assert 'python = "${effectivePackage.hermesVenv}/bin/python3";' in module
    assert "--replace" in module
    assert "--env-json" in module
    assert "--env-file" in module
    assert "cat > \"$ENV_FILE\"" not in module
    assert "/dev/null \"$ENV_FILE\"" not in module
    assert (
        "${configFile} ${cfg.stateDir}/.hermes/config.yaml"
        not in module
    )


def test_nixos_all_credential_participants_use_group_shared_policy() -> None:
    module = _source("nixosModules.nix")
    writer = _source("configMergeScript.nix")

    assert "HERMES_CREDENTIAL_GROUP_SHARED=1" in module
    assert 'HERMES_CREDENTIAL_GROUP_SHARED = "1";' in module
    assert "--run-as-uid" in module
    assert "--run-as-gid" in module
    assert "--run-as-uid" in writer
    assert "--run-as-gid" in writer
    assert "with credential_transaction(args.config_path):" in writer
    assert "drop_privileges(args.run_as_uid, args.run_as_gid)" in writer
    assert ".hermes/profiles 2770" in module
    assert "schema = 5;" in module


def test_nixos_activation_never_recursively_chmods_profile_credentials() -> None:
    module = _source("nixosModules.nix")
    activation = module.split(
        'system.activationScripts."hermes-agent-setup"',
        maxsplit=1,
    )[1].split(
        "# Publish declarative config",
        maxsplit=1,
    )[0]

    assert "cron sessions logs memories plugins profiles" not in module
    assert (
        'find "${cfg.stateDir}/.hermes/profiles" -type f'
        not in module
    )
    recursive_chmod_loops = []
    for loop in re.finditer(
        (
            r"for\s+(?P<variable>_[A-Za-z0-9_]+)\s+in\s+"
            r"(?P<directories>[^;]+);\s*do"
            r"(?P<body>.*?)\bdone\b"
        ),
        activation,
        flags=re.DOTALL,
    ):
        variable = loop.group("variable")
        if re.search(
            (
                rf"\bfind\s+.*\${re.escape(variable)}.*"
                r"-type\s+f\b.*\bchmod\b"
            ),
            loop.group("body"),
            flags=re.DOTALL,
        ):
            recursive_chmod_loops.append(loop)

    assert recursive_chmod_loops
    for loop in recursive_chmod_loops:
        assert "profiles" not in loop.group("directories").split()
    assert not re.search(
        r"\bfind\s+(?:\"[^\"]*profiles[^\"]*\"|\S*profiles\S*)"
        r"\s+-type\s+f\b.*\bchmod\b",
        activation,
        flags=re.DOTALL,
    )


def test_container_entrypoint_preserves_live_credential_transaction_owners() -> None:
    module = _source("nixosModules.nix")

    for canonical_name in (
        ".taiji-credential-transaction.lock",
        ".taiji-credential-pair-intent.json",
        ".taiji-credential-pair-abort.json",
        ".taiji-credential-*.stage",
    ):
        assert canonical_name in module


def test_nix_checks_cover_metadata_pair_commit_and_failure_idempotency() -> None:
    checks = _source("checks.nix")

    for scenario in (
        "Scenario H: Capability metadata",
        "Scenario I: Replace preserves incarnation",
        "Scenario J: Declarative env pair commit",
        "Scenario K: Secret idempotency",
        "Scenario L: Failed publish is not torn",
    ):
        assert scenario in checks


def test_writer_merge_and_replace_reconcile_capability_metadata(
    tmp_path: Path,
) -> None:
    current = {
        "auxiliary": {
            "vision": {"provider": "alibaba", "model": "qwen-vl-max"}
        },
        "image_gen": {"provider": "dashscope", "model": "wanx-v1"},
        "_taiji_capability_epochs": {
            "vision": 4,
            "image_generation": 7,
        },
        "_taiji_profile_incarnation": "live-incarnation",
        "user_only": {"preserved": True},
    }
    declared = {
        "auxiliary": {
            "vision": {"provider": "zai", "model": "glm-4.5v"}
        },
        "image_gen": {"provider": "zhipu-image", "model": "cogview-4"},
        "_taiji_capability_epochs": {
            "vision": 1,
            "image_generation": 2,
        },
        "_taiji_profile_incarnation": "stale-incarnation",
    }
    source = tmp_path / "declared.yaml"
    source.write_text(
        yaml.safe_dump(declared, sort_keys=False),
        encoding="utf-8",
    )

    merge_home = tmp_path / "merge"
    merge_home.mkdir()
    merge_config = merge_home / "config.yaml"
    merge_config.write_text(
        yaml.safe_dump(current, sort_keys=False),
        encoding="utf-8",
    )
    merged = _run_writer([source, merge_config])
    assert merged.returncode == 0, merged.stderr
    merged_config = _read_yaml(merge_config)
    assert merged_config["user_only"] == {"preserved": True}
    assert merged_config["_taiji_profile_incarnation"] == "live-incarnation"
    assert merged_config["_taiji_capability_epochs"] == {
        "vision": 5,
        "image_generation": 8,
    }

    replace_home = tmp_path / "replace"
    replace_home.mkdir()
    replace_config = replace_home / "config.yaml"
    replace_config.write_text(
        yaml.safe_dump(current, sort_keys=False),
        encoding="utf-8",
    )
    replaced = _run_writer(["--replace", source, replace_config])
    assert replaced.returncode == 0, replaced.stderr
    replaced_config = _read_yaml(replace_config)
    assert "user_only" not in replaced_config
    assert replaced_config["_taiji_profile_incarnation"] == "live-incarnation"
    assert replaced_config["_taiji_capability_epochs"] == {
        "vision": 5,
        "image_generation": 8,
    }


def test_writer_pair_commit_bumps_secret_epochs_once_and_is_idempotent(
    tmp_path: Path,
) -> None:
    current = {
        "auxiliary": {
            "vision": {"provider": "alibaba", "model": "qwen-vl-max"}
        },
        "image_gen": {"provider": "dashscope", "model": "wanx-v1"},
        "_taiji_capability_epochs": {
            "vision": 10,
            "image_generation": 20,
        },
        "_taiji_profile_incarnation": "pair-incarnation",
    }
    source = tmp_path / "declared.yaml"
    source.write_text(
        yaml.safe_dump(current, sort_keys=False),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(current, sort_keys=False),
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DASHSCOPE_API_KEY=old-secret\nOLD_ONLY=remove-me\n",
        encoding="utf-8",
    )
    env_json = tmp_path / "environment.json"
    env_json.write_text(
        json.dumps({"NON_SECRET": "managed value"}),
        encoding="utf-8",
    )
    env_file = tmp_path / "secrets.env"
    env_file.write_text(
        "DASHSCOPE_API_KEY=new-secret\n",
        encoding="utf-8",
    )
    arguments = [
        "--env-json",
        env_json,
        "--env-file",
        env_file,
        source,
        config_path,
    ]

    first = _run_writer(arguments)
    assert first.returncode == 0, first.stderr
    first_config = _read_yaml(config_path)
    assert first_config["_taiji_capability_epochs"] == {
        "vision": 11,
        "image_generation": 21,
    }
    assert env_path.read_text(encoding="utf-8") == (
        "DASHSCOPE_API_KEY=new-secret\n"
        "NON_SECRET='managed value'\n"
    )
    first_config_bytes = config_path.read_bytes()
    first_env_bytes = env_path.read_bytes()

    second = _run_writer(arguments)
    assert second.returncode == 0, second.stderr
    assert config_path.read_bytes() == first_config_bytes
    assert env_path.read_bytes() == first_env_bytes


def test_writer_rejects_missing_or_invalid_env_before_any_publish(
    tmp_path: Path,
) -> None:
    current = {
        "model": "current",
        "_taiji_profile_incarnation": "failure-incarnation",
    }
    declared = {"model": "would-change"}
    source = tmp_path / "declared.yaml"
    source.write_text(
        yaml.safe_dump(declared, sort_keys=False),
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(current, sort_keys=False),
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text("DASHSCOPE_API_KEY=old-secret\n", encoding="utf-8")
    env_json = tmp_path / "environment.json"
    env_json.write_text("{}", encoding="utf-8")
    config_before = config_path.read_bytes()
    env_before = env_path.read_bytes()

    missing = _run_writer(
        [
            "--env-json",
            env_json,
            "--env-file",
            tmp_path / "missing.env",
            source,
            config_path,
        ]
    )
    assert missing.returncode != 0
    assert config_path.read_bytes() == config_before
    assert env_path.read_bytes() == env_before

    invalid_env = tmp_path / "invalid.env"
    invalid_env.write_text(
        "this is not an env assignment\n",
        encoding="utf-8",
    )
    invalid = _run_writer(
        [
            "--env-json",
            env_json,
            "--env-file",
            invalid_env,
            source,
            config_path,
        ]
    )
    assert invalid.returncode != 0
    assert config_path.read_bytes() == config_before
    assert env_path.read_bytes() == env_before
