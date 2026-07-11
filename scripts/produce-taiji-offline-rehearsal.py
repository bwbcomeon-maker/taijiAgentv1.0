#!/usr/bin/env python3
"""Produce challenge-bound offline install lifecycle evidence in Docker."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate-taiji-release-evidence.py"
PUBLIC_KEY = ROOT / "tools" / "taiji-release-evidence" / "signing-public.pem"
PUBLIC_KEY_FINGERPRINT = "839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"
IMAGE_ROLE_LABEL = "offline-rehearsal-v1"
SESSION_BASENAME = "offline-install-rehearsal-session.json"
EVIDENCE_BASENAME = "offline-install-rehearsal.json"
CHALLENGE_RE = re.compile(r"^[0-9a-f]{64,128}$")
SESSION_RE = re.compile(r"^[0-9a-f]{32}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,40}$")
OFFLINE_SESSION_KEYS = {
    "schema",
    "generated_at_utc",
    "rehearsal_session_id",
    "challenge_nonce",
    "source_commit",
    "deb_basename",
    "deb_sha256",
    "platform",
    "environment",
    "os_id",
    "os_version",
    "network",
    "checks",
    "desktop_app_verified",
    "target_verified",
}


class ProducerError(RuntimeError):
    pass


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProducerError(f"JSON 含重复字段：{key}")
        result[key] = value
    return result


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise ProducerError(f"{label} 不存在或不可读取：{path}: {exc}") from exc
    if not stat.S_ISREG(file_stat.st_mode) or path.is_symlink() or file_stat.st_nlink != 1:
        raise ProducerError(f"{label} 必须是单链接普通文件：{path}")
    if file_stat.st_size <= 0 or file_stat.st_size > 1024 * 1024:
        raise ProducerError(f"{label} 大小不合法：{path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object)
    except (OSError, UnicodeError, json.JSONDecodeError, ProducerError) as exc:
        raise ProducerError(f"{label} 无法严格解析：{exc}") from exc
    if type(payload) is not dict:
        raise ProducerError(f"{label} 顶层必须是 JSON object")
    return payload


def load_validator() -> ModuleType:
    if not VALIDATOR.is_file():
        raise ProducerError(f"缺少 release evidence validator：{VALIDATOR}")
    spec = importlib.util.spec_from_file_location("taiji_release_evidence_validator", VALIDATOR)
    if spec is None or spec.loader is None:
        raise ProducerError(f"无法载入 release evidence validator：{VALIDATOR}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_command(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if check and result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise ProducerError(f"命令失败（exit={result.returncode}）：{' '.join(args[:3])}: {details}")
    return result


def docker_json(docker: str, args: list[str], label: str) -> dict[str, Any]:
    result = run_command([docker, *args])
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProducerError(f"{label} 不是合法 JSON") from exc
    if type(payload) is not list or len(payload) != 1 or type(payload[0]) is not dict:
        raise ProducerError(f"{label} 返回结构不合法")
    return payload[0]


def current_source_commit() -> str:
    commit = run_command(
        ["git", "-C", str(ROOT), "rev-parse", "--short=8", "HEAD"]
    ).stdout.strip()
    if not COMMIT_RE.fullmatch(commit):
        raise ProducerError(f"当前源码 commit 格式不合法：{commit!r}")
    return commit


def resolve_directory(path: Path, label: str) -> Path:
    if path.is_symlink():
        raise ProducerError(f"{label} 不能是符号链接：{path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ProducerError(f"{label} 不存在：{path}") from exc
    if not resolved.is_dir():
        raise ProducerError(f"{label} 不是目录：{resolved}")
    if "," in str(resolved):
        raise ProducerError(f"{label} 路径不能包含逗号，Docker --mount 无法安全表达：{resolved}")
    return resolved


def resolve_output(path: Path) -> Path:
    if path.exists() or path.is_symlink():
        raise ProducerError(f"证据输出目录已存在，拒绝覆盖历史证据：{path}")
    parent = resolve_directory(path.parent, "证据输出父目录")
    output = parent / path.name
    if not path.name or path.name in {".", ".."} or "," in path.name:
        raise ProducerError(f"证据输出目录名称不合法：{path.name!r}")
    return output


def discover_release_inputs(delivery: Path, validator: ModuleType) -> dict[str, Any]:
    commit = current_source_commit()
    package_dir = delivery / "生成的安装包"
    offline_repo = delivery / "离线依赖"
    debs = sorted(package_dir.glob("taiji-agent_*_amd64.deb"))
    if len(debs) != 1:
        raise ProducerError(f"生成的安装包目录必须且只能包含一个 amd64 DEB，实际为 {len(debs)}")
    deb = debs[0]
    checksum = Path(f"{deb}.sha256")
    manifest = package_dir / "taiji-package-manifest.json"
    build_marker = package_dir / ".build-success"
    source_archive = delivery / f"taiji-agentv1.0-kylin-build-src-{commit}.tar.gz"
    packages = offline_repo / "Packages"
    packages_gz = offline_repo / "Packages.gz"
    binding_args = argparse.Namespace(
        source_commit=commit,
        deb=deb,
        checksum=checksum,
        manifest=manifest,
        build_marker=build_marker,
        source_archive=source_archive,
        packages=packages,
        packages_gz=packages_gz,
        delivery_dir=delivery,
    )
    try:
        binding = validator.validate_build_binding(binding_args)
    except Exception as exc:
        raise ProducerError(f"交付物与当前源码/manifest 绑定校验失败：{exc}") from exc
    if type(binding) is not tuple or len(binding) < 3:
        raise ProducerError("release evidence validator 返回了不兼容的 build binding")
    deb_hash, version, release_hash = binding[:3]
    return {
        "source_commit": commit,
        "deb": deb,
        "checksum": checksum,
        "manifest": manifest,
        "build_marker": build_marker,
        "source_archive": source_archive,
        "packages": packages,
        "packages_gz": packages_gz,
        "deb_sha256": deb_hash,
        "version": version,
        "release_artifacts_sha256": release_hash,
    }


def verify_container_inspect(
    inspect: dict[str, Any],
    *,
    expected_image_id: str,
    delivery: Path,
    evidence_dir: Path,
) -> None:
    host_config = inspect.get("HostConfig")
    if type(host_config) is not dict or host_config.get("NetworkMode") != "none":
        raise ProducerError("Docker inspect 的 HostConfig.NetworkMode 不是 none")
    if inspect.get("Image") != expected_image_id:
        raise ProducerError("Docker inspect 的容器镜像与预检镜像 ID 不一致")
    mounts = inspect.get("Mounts")
    if type(mounts) is not list:
        raise ProducerError("Docker inspect 缺少 Mounts")
    by_destination = {
        item.get("Destination"): item
        for item in mounts
        if type(item) is dict and type(item.get("Destination")) is str
    }
    expected_destinations = {"/delivery-ro", "/evidence"}
    if set(by_destination) != expected_destinations:
        unexpected = sorted(set(by_destination) - expected_destinations)
        raise ProducerError(f"Docker inspect 出现未授权挂载：{unexpected}")
    delivery_mount = by_destination.get("/delivery-ro")
    evidence_mount = by_destination.get("/evidence")
    if type(delivery_mount) is not dict:
        raise ProducerError("Docker inspect 缺少 /delivery-ro 挂载")
    if type(evidence_mount) is not dict:
        raise ProducerError("Docker inspect 缺少 /evidence 挂载")
    if delivery_mount.get("Type") != "bind" or Path(str(delivery_mount.get("Source"))).resolve() != delivery:
        raise ProducerError("Docker inspect 的交付目录挂载源不一致")
    if delivery_mount.get("RW") is not False:
        raise ProducerError("Docker inspect 显示交付目录不是只读挂载")
    if evidence_mount.get("Type") != "bind" or Path(str(evidence_mount.get("Source"))).resolve() != evidence_dir:
        raise ProducerError("Docker inspect 的证据目录挂载源不一致")
    if evidence_mount.get("RW") is not True:
        raise ProducerError("Docker inspect 显示证据目录不是可写挂载")


def remove_container(docker: str, container_id: str) -> None:
    result = run_command([docker, "rm", "--force", container_id], check=False)
    if result.returncode != 0:
        raise ProducerError(
            f"Docker rehearsal 容器清理失败，残留 container={container_id}: "
            f"{(result.stderr or result.stdout).strip()}"
        )


def run_lifecycle_container(
    *,
    docker: str,
    image: str,
    delivery: Path,
    evidence_dir: Path,
    challenge: str,
    release: dict[str, Any],
) -> None:
    image_info = docker_json(docker, ["image", "inspect", image], "Docker image inspect")
    if image_info.get("Os") != "linux" or image_info.get("Architecture") != "amd64":
        raise ProducerError("演练镜像必须是 linux/amd64")
    image_config = image_info.get("Config")
    labels = image_config.get("Labels") if type(image_config) is dict else None
    entrypoint = image_config.get("Entrypoint") if type(image_config) is dict else None
    if type(labels) is not dict or labels.get("io.taiji.release-evidence.role") != IMAGE_ROLE_LABEL:
        raise ProducerError("演练镜像不是仓库定义的专用离线演练镜像")
    if entrypoint != ["/usr/local/bin/run-lifecycle.sh"]:
        raise ProducerError("演练镜像入口不是固定 lifecycle runner")
    image_id = image_info.get("Id")
    if type(image_id) is not str or not image_id.startswith("sha256:"):
        raise ProducerError("Docker image inspect 缺少不可变镜像 ID")

    name = f"taiji-offline-rehearsal-{uuid.uuid4().hex[:12]}"
    create = run_command(
        [
            docker,
            "create",
            "--platform",
            "linux/amd64",
            "--pull=never",
            "--network",
            "none",
            "--name",
            name,
            "--mount",
            f"type=bind,src={delivery},dst=/delivery-ro,readonly",
            "--mount",
            f"type=bind,src={evidence_dir},dst=/evidence",
            "--env",
            f"TAIJI_OFFLINE_REHEARSAL_CHALLENGE={challenge}",
            "--env",
            f"TAIJI_EXPECTED_SOURCE_COMMIT={release['source_commit']}",
            "--env",
            f"TAIJI_EXPECTED_DEB_BASENAME={release['deb'].name}",
            "--env",
            f"TAIJI_EXPECTED_DEB_SHA256={release['deb_sha256']}",
            image,
        ]
    )
    container_id = create.stdout.strip()
    if not container_id:
        raise ProducerError("docker create 未返回 container ID")

    try:
        inspect = docker_json(docker, ["inspect", container_id], "Docker container inspect")
        verify_container_inspect(
            inspect,
            expected_image_id=image_id,
            delivery=delivery,
            evidence_dir=evidence_dir,
        )
        started = run_command([docker, "start", "--attach", container_id], check=False)
        if started.stdout:
            print(started.stdout, end="")
        if started.stderr:
            print(started.stderr, end="", file=sys.stderr)
        finished = docker_json(docker, ["inspect", container_id], "Docker completed container inspect")
        verify_container_inspect(
            finished,
            expected_image_id=image_id,
            delivery=delivery,
            evidence_dir=evidence_dir,
        )
        state = finished.get("State")
        exit_code = state.get("ExitCode") if type(state) is dict else None
        if started.returncode != 0 or exit_code != 0:
            raise ProducerError(
                f"离线生命周期容器失败：docker start exit={started.returncode}, container exit={exit_code}"
            )
    except Exception as exc:
        try:
            remove_container(docker, container_id)
        except ProducerError as cleanup_exc:
            raise ProducerError(f"{exc}；{cleanup_exc}") from exc
        raise
    remove_container(docker, container_id)


def validate_session(session: dict[str, Any], release: dict[str, Any], challenge: str) -> None:
    if set(session) != OFFLINE_SESSION_KEYS:
        missing = sorted(OFFLINE_SESSION_KEYS - set(session))
        extra = sorted(set(session) - OFFLINE_SESSION_KEYS)
        raise ProducerError(f"离线会话字段集合不合法：missing={missing}, extra={extra}")
    exact = {
        "schema": "taiji.offline-install-rehearsal.v1",
        "challenge_nonce": challenge,
        "source_commit": release["source_commit"],
        "deb_basename": release["deb"].name,
        "deb_sha256": release["deb_sha256"],
        "platform": "linux/amd64",
        "environment": "container",
        "network": "none",
        "desktop_app_verified": False,
        "target_verified": False,
    }
    for key, expected in exact.items():
        if type(session.get(key)) is not type(expected) or session.get(key) != expected:
            raise ProducerError(f"离线会话字段 {key} 与预期不一致")
    if not SESSION_RE.fullmatch(str(session.get("rehearsal_session_id", ""))):
        raise ProducerError("离线会话 rehearsal_session_id 格式不合法")
    if type(session.get("generated_at_utc")) is not str or not session["generated_at_utc"].endswith("Z"):
        raise ProducerError("离线会话 generated_at_utc 格式不合法")
    if type(session.get("os_id")) is not str or not session["os_id"].strip():
        raise ProducerError("离线会话 os_id 不能为空")
    if type(session.get("os_version")) is not str or not session["os_version"].strip():
        raise ProducerError("离线会话 os_version 不能为空")
    checks = session.get("checks")
    expected_checks = {"install": True, "uninstall": True, "reinstall": True}
    if checks != expected_checks or any(type(value) is not bool for value in checks.values()):
        raise ProducerError("离线会话必须记录 install/uninstall/reinstall 三段真实通过")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_pre_sign(evidence: Path, release: dict[str, Any], challenge: str, delivery: Path) -> None:
    args = [
        sys.executable,
        str(VALIDATOR),
        "offline",
        "--evidence",
        str(evidence),
        "--source-commit",
        release["source_commit"],
        "--deb",
        str(release["deb"]),
        "--checksum",
        str(release["checksum"]),
        "--manifest",
        str(release["manifest"]),
        "--build-marker",
        str(release["build_marker"]),
        "--source-archive",
        str(release["source_archive"]),
        "--packages",
        str(release["packages"]),
        "--packages-gz",
        str(release["packages_gz"]),
        "--delivery-dir",
        str(delivery),
        "--attestation-public-key",
        str(PUBLIC_KEY),
        "--attestation-public-key-fingerprint",
        PUBLIC_KEY_FINGERPRINT,
        "--challenge",
        challenge,
        "--pre-sign",
    ]
    run_command(args)


def produce(delivery_arg: Path, output_arg: Path, image: str, challenge: str) -> Path:
    if not CHALLENGE_RE.fullmatch(challenge):
        raise ProducerError("challenge 必须是 64-128 位小写十六进制")
    if not image.strip() or any(character.isspace() for character in image):
        raise ProducerError("Docker image 名称不能为空或包含空白")
    if not PUBLIC_KEY.is_file() or PUBLIC_KEY.is_symlink():
        raise ProducerError(f"缺少固定 release evidence 验签公钥：{PUBLIC_KEY}")
    docker = shutil.which("docker")
    if docker is None:
        raise ProducerError("缺少 docker 命令")

    delivery = resolve_directory(delivery_arg, "交付目录")
    output = resolve_output(output_arg)
    validator = load_validator()
    release = discover_release_inputs(delivery, validator)

    output.mkdir(mode=0o700)
    published = False
    try:
        run_lifecycle_container(
            docker=docker,
            image=image,
            delivery=delivery,
            evidence_dir=output,
            challenge=challenge,
            release=release,
        )
        session_path = output / SESSION_BASENAME
        session = load_json(session_path, "离线生命周期结构化会话")
        validate_session(session, release, challenge)

        current_release = discover_release_inputs(delivery, validator)
        if current_release["release_artifacts_sha256"] != release["release_artifacts_sha256"]:
            raise ProducerError("交付目录在 Docker 演练期间发生变化")
        if current_release["deb_sha256"] != release["deb_sha256"]:
            raise ProducerError("DEB 在 Docker 演练期间发生变化")

        evidence = {
            "schema_version": 1,
            "evidence_type": "offline-install-rehearsal",
            "generated_at_utc": session["generated_at_utc"],
            "rehearsal_session_id": session["rehearsal_session_id"],
            "challenge_nonce": challenge,
            "release_artifacts_sha256": release["release_artifacts_sha256"],
            "source_commit": release["source_commit"],
            "deb_basename": release["deb"].name,
            "deb_sha256": release["deb_sha256"],
            "platform": "linux/amd64",
            "environment": "container",
            "os_id": session["os_id"],
            "os_version": session["os_version"],
            "network": "none",
            "install": True,
            "uninstall": True,
            "reinstall": True,
            "desktop_app_verified": False,
            "target_verified": False,
            "log_basename": SESSION_BASENAME,
            "log_sha256": sha256_file(session_path),
        }
        evidence_path = output / EVIDENCE_BASENAME
        atomic_write_json(evidence_path, evidence)
        validate_pre_sign(evidence_path, release, challenge, delivery)
        published = True
        return evidence_path
    finally:
        if not published:
            try:
                if output.exists():
                    shutil.rmtree(output)
            except OSError as exc:
                raise ProducerError(f"失败证据清理失败，残留路径：{output}: {exc}") from exc
            if output.exists():
                raise ProducerError(f"失败证据清理后仍有残留路径：{output}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="在 --network none 的 linux/amd64 Docker 中生成太极离线安装生命周期证据。"
    )
    parser.add_argument("--delivery-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--challenge", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        evidence = produce(args.delivery_dir, args.output_dir, args.image, args.challenge)
    except (ProducerError, OSError, ValueError, TypeError) as exc:
        print(f"offline-rehearsal-producer-failed\t{exc}", file=sys.stderr)
        return 1
    print(f"offline-rehearsal-produced\t{evidence}")
    print("offline-rehearsal-signature\t未生成；请由发布负责人离线复核后单独签名")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
