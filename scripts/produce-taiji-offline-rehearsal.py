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
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TRUSTED_GIT = ROOT / "scripts" / "taiji-trusted-git"
VALIDATOR = ROOT / "scripts" / "validate-taiji-release-evidence.py"
POLICY_HELPER = ROOT / "packaging" / "linux" / "compatibility_policy.py"
RELEASE_PUBLIC_KEY = ROOT / "tools" / "taiji-release-evidence" / "signing-public.pem"
PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT = (
    "839b6c589f74bda533f54b660d977e6757ccc86f73554e10647d5f72d51ec1da"
)
TRUSTED_OPENSSL = Path("/usr/bin/openssl")
IMAGE_ROLE_LABEL = "offline-rehearsal-v1"
IMAGE_BASELINE_LABEL = "ubuntu-20.04"
IMAGE_FIXTURE_LABEL = "kylin-os-release-v1"
REHEARSAL_ENVIRONMENT = "container-kylin-policy-fixture-v1"
SESSION_BASENAME = "offline-install-rehearsal-session.json"
LIFECYCLE_BASENAME = "offline-install-rehearsal-lifecycle.json"
EVIDENCE_BASENAME = "offline-install-rehearsal.json"
CHALLENGE_RE = re.compile(r"^[0-9a-f]{64,128}$")
SESSION_RE = re.compile(r"^[0-9a-f]{32}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
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


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def resolve_trusted_openssl() -> str:
    """Resolve only the root-managed fixed OpenSSL admission executable."""

    for directory in (Path("/usr"), Path("/usr/bin")):
        try:
            metadata = directory.lstat()
        except OSError as exc:
            raise ProducerError(f"可信 openssl 目录不可读取：{directory}: {exc}") from exc
        if (
            directory.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_mode & 0o022
        ):
            raise ProducerError(f"可信 openssl 目录不是 root 管理的只读系统目录：{directory}")

    try:
        alias = TRUSTED_OPENSSL.lstat()
    except OSError as exc:
        raise ProducerError(f"缺少固定可信 openssl：{TRUSTED_OPENSSL}: {exc}") from exc
    if alias.st_uid != 0 or alias.st_nlink != 1:
        raise ProducerError(f"固定 openssl 必须是 root-owned 单链接入口：{TRUSTED_OPENSSL}")
    if not (stat.S_ISREG(alias.st_mode) or stat.S_ISLNK(alias.st_mode)):
        raise ProducerError(f"固定 openssl 入口类型不可信：{TRUSTED_OPENSSL}")
    if stat.S_ISREG(alias.st_mode) and alias.st_mode & 0o022:
        raise ProducerError(f"固定 openssl 可被组或其他用户写入：{TRUSTED_OPENSSL}")
    try:
        resolved = TRUSTED_OPENSSL.resolve(strict=True)
        resolved_metadata = resolved.lstat()
    except (OSError, RuntimeError) as exc:
        raise ProducerError(f"固定 openssl 无法解析到可信实体：{exc}") from exc
    if resolved.parent != Path("/usr/bin"):
        raise ProducerError(f"固定 openssl 解析后逃离 /usr/bin：{resolved}")
    if (
        resolved.is_symlink()
        or not stat.S_ISREG(resolved_metadata.st_mode)
        or resolved_metadata.st_uid != 0
        or resolved_metadata.st_nlink != 1
        or not resolved_metadata.st_mode & 0o111
        or resolved_metadata.st_mode & 0o022
    ):
        raise ProducerError(f"固定 openssl 实体不是 root 管理的单链接可执行普通文件：{resolved}")
    return str(resolved)


def read_regular_bytes(path: Path, label: str, *, max_bytes: int = 1024 * 1024) -> bytes:
    """Read one bounded regular file without switching pathnames mid-read."""

    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ProducerError(f"{label} 必须是单链接普通文件：{path}")
        if before.st_size <= 0 or before.st_size > max_bytes:
            raise ProducerError(f"{label} 大小不合法：{path}")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except ProducerError:
        raise
    except OSError as exc:
        raise ProducerError(f"{label} 不存在或不可读取：{path}: {exc}") from exc

    try:
        opened = os.fstat(descriptor)
        if _file_identity(opened) != _file_identity(before):
            raise ProducerError(f"{label} 在打开时发生替换：{path}")
        remaining = before.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise ProducerError(f"{label} 读取期间被截断：{path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ProducerError(f"{label} 读取期间大小发生变化：{path}")
        after = os.fstat(descriptor)
        current = path.lstat()
        if (
            _file_identity(after) != _file_identity(before)
            or _file_identity(current) != _file_identity(before)
        ):
            raise ProducerError(f"{label} 读取期间发生变化：{path}")
        return b"".join(chunks)
    except OSError as exc:
        raise ProducerError(f"{label} 读取失败：{path}: {exc}") from exc
    finally:
        os.close(descriptor)


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = read_regular_bytes(path, label)
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_object)
    except (UnicodeError, json.JSONDecodeError, ProducerError) as exc:
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
    if not TRUSTED_GIT.is_file() or TRUSTED_GIT.is_symlink():
        raise ProducerError(f"缺少可信 Git 边界：{TRUSTED_GIT}")
    commit = run_command(
        [str(TRUSTED_GIT), "-C", str(ROOT), "rev-parse", "HEAD"]
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


def resolve_regular_file(path: Path, label: str) -> Path:
    """Resolve one immutable, single-link regular input file."""

    try:
        info = path.lstat()
    except OSError as exc:
        raise ProducerError(f"{label} 不存在或不可读取：{path}: {exc}") from exc
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ProducerError(f"{label} 必须是单链接普通文件：{path}")
    if info.st_size <= 0:
        raise ProducerError(f"{label} 不能为空：{path}")
    return path.resolve()


def load_policy_identity(path: Path) -> tuple[str, str]:
    """Load the checked-in compatibility policy and return its immutable identity."""

    policy_path = resolve_regular_file(path, "compatibility policy")
    if not POLICY_HELPER.is_file() or POLICY_HELPER.is_symlink():
        raise ProducerError(f"缺少 compatibility policy helper：{POLICY_HELPER}")
    spec = importlib.util.spec_from_file_location("taiji_offline_compatibility_policy", POLICY_HELPER)
    if spec is None or spec.loader is None:
        raise ProducerError(f"无法载入 compatibility policy helper：{POLICY_HELPER}")
    helper = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = helper
    spec.loader.exec_module(helper)
    try:
        policy = helper.load_and_validate(policy_path)
        policy_id = policy["policy_id"]
        policy_sha256 = helper.canonical_sha256(policy)
    except Exception as exc:
        raise ProducerError(f"compatibility policy 校验失败：{exc}") from exc
    if not isinstance(policy_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]+", policy_id):
        raise ProducerError("compatibility policy_id 格式不合法")
    if not re.fullmatch(r"[0-9a-f]{64}", policy_sha256):
        raise ProducerError("compatibility policy SHA256 格式不合法")
    return policy_id, policy_sha256


def parse_sha256_sidecar(path: Path, expected_file: Path, expected_hash: str) -> None:
    sidecar = resolve_regular_file(path, "previous DEB SHA256 sidecar")
    try:
        text = read_regular_bytes(
            sidecar, "previous DEB SHA256 sidecar", max_bytes=4096
        ).decode("ascii")
    except (ProducerError, UnicodeError) as exc:
        raise ProducerError(f"previous DEB SHA256 sidecar 无法读取：{sidecar}") from exc
    match = re.fullmatch(r"([0-9a-f]{64})[ \t]+\*?([^/\s]+)\n?", text)
    if not match or match.group(1) != expected_hash or match.group(2) != expected_file.name:
        raise ProducerError("previous DEB SHA256 sidecar 未准确绑定 previous DEB basename 和内容")


def verify_previous_detached_signature(previous: Path, signature: Path) -> str:
    """Verify the supplied N-1 signature against the tracked release trust root."""

    previous = resolve_regular_file(previous, "previous DEB")
    signature = resolve_regular_file(signature, "previous DEB detached signature")
    expected_signature_name = f"{previous.name}.sig"
    if signature.name != expected_signature_name:
        raise ProducerError(
            "previous DEB detached signature basename 必须是 "
            f"{expected_signature_name}"
        )
    public_key = resolve_regular_file(RELEASE_PUBLIC_KEY, "tracked release public key")
    signature_payload = read_regular_bytes(
        signature,
        "previous DEB detached signature",
        max_bytes=64 * 1024,
    )
    public_key_payload = read_regular_bytes(
        public_key,
        "tracked release public key",
        max_bytes=64 * 1024,
    )
    openssl = resolve_trusted_openssl()

    try:
        before = previous.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size <= 0:
            raise ProducerError("previous DEB 必须是非空单链接普通文件")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(previous, flags)
    except ProducerError:
        raise
    except OSError as exc:
        raise ProducerError(f"previous DEB 无法打开以执行 detached signature 验签：{exc}") from exc

    try:
        opened = os.fstat(descriptor)
        if _file_identity(opened) != _file_identity(before):
            raise ProducerError("previous DEB 在验签打开时发生替换")
        with tempfile.TemporaryDirectory(prefix="taiji-previous-signature-") as temporary:
            temporary_root = Path(temporary)
            public_snapshot = temporary_root / "signing-public.pem"
            signature_snapshot = temporary_root / "previous.deb.sig"
            public_snapshot.write_bytes(public_key_payload)
            signature_snapshot.write_bytes(signature_payload)
            public_snapshot.chmod(0o600)
            signature_snapshot.chmod(0o600)
            derived = subprocess.run(
                [
                    openssl,
                    "pkey",
                    "-pubin",
                    "-in",
                    str(public_snapshot),
                    "-outform",
                    "DER",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if derived.returncode != 0:
                raise ProducerError("tracked release public key 不是有效 PEM 公钥")
            actual_fingerprint = hashlib.sha256(derived.stdout).hexdigest()
            if actual_fingerprint != PINNED_SIGNING_PUBLIC_KEY_FINGERPRINT:
                raise ProducerError(
                    "tracked release public key fingerprint 与产品固定信任锚不一致"
                )
            result = subprocess.run(
                [
                    openssl,
                    "dgst",
                    "-sha256",
                    "-verify",
                    str(public_snapshot),
                    "-signature",
                    str(signature_snapshot),
                ],
                stdin=descriptor,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        after = os.fstat(descriptor)
        current = previous.lstat()
        if (
            _file_identity(after) != _file_identity(before)
            or _file_identity(current) != _file_identity(before)
        ):
            raise ProducerError("previous DEB 在 detached signature 验签期间发生变化")
        if result.returncode != 0:
            raise ProducerError("previous DEB detached signature 未通过固定发布公钥验证")
    except OSError as exc:
        raise ProducerError(f"previous DEB detached signature 验证失败：{exc}") from exc
    finally:
        os.close(descriptor)
    return hashlib.sha256(signature_payload).hexdigest()


def _split_debian_version(version: str) -> tuple[int, str, str]:
    if type(version) is not str or not version:
        raise ProducerError("Debian version 不能为空")
    epoch = 0
    remainder = version
    if ":" in remainder:
        epoch_text, remainder = remainder.split(":", 1)
        if not epoch_text.isdigit() or not remainder:
            raise ProducerError(f"Debian version epoch 不合法：{version}")
        epoch = int(epoch_text, 10)
    if not re.fullmatch(r"[0-9A-Za-z.+~:-]+", version):
        raise ProducerError(f"Debian version 包含不合法字符：{version}")
    if "-" in remainder:
        upstream, revision = remainder.rsplit("-", 1)
        if not revision:
            raise ProducerError(f"Debian version revision 不合法：{version}")
    else:
        upstream, revision = remainder, "0"
    if not upstream or not upstream[0].isdigit():
        raise ProducerError(f"Debian upstream version 不合法：{version}")
    return epoch, upstream, revision


def _debian_character_order(character: str) -> int:
    if character == "~":
        return -1
    if not character:
        return 0
    if character.isalpha():
        return ord(character)
    return ord(character) + 256


def _compare_debian_part(left: str, right: str) -> int:
    left_index = 0
    right_index = 0
    while left_index < len(left) or right_index < len(right):
        while (
            (left_index < len(left) and not left[left_index].isdigit())
            or (right_index < len(right) and not right[right_index].isdigit())
        ):
            left_character = left[left_index] if left_index < len(left) and not left[left_index].isdigit() else ""
            right_character = right[right_index] if right_index < len(right) and not right[right_index].isdigit() else ""
            left_order = _debian_character_order(left_character)
            right_order = _debian_character_order(right_character)
            if left_order != right_order:
                return -1 if left_order < right_order else 1
            if left_character:
                left_index += 1
            if right_character:
                right_index += 1
        while left_index < len(left) and left[left_index] == "0":
            left_index += 1
        while right_index < len(right) and right[right_index] == "0":
            right_index += 1
        left_end = left_index
        right_end = right_index
        while left_end < len(left) and left[left_end].isdigit():
            left_end += 1
        while right_end < len(right) and right[right_end].isdigit():
            right_end += 1
        left_digits = left[left_index:left_end]
        right_digits = right[right_index:right_end]
        if len(left_digits) != len(right_digits):
            return -1 if len(left_digits) < len(right_digits) else 1
        if left_digits != right_digits:
            return -1 if left_digits < right_digits else 1
        left_index = left_end
        right_index = right_end
    return 0


def compare_debian_versions(left: str, right: str) -> int:
    """Return Debian/dpkg ordering for two complete package versions."""

    left_epoch, left_upstream, left_revision = _split_debian_version(left)
    right_epoch, right_upstream, right_revision = _split_debian_version(right)
    if left_epoch != right_epoch:
        return -1 if left_epoch < right_epoch else 1
    upstream_order = _compare_debian_part(left_upstream, right_upstream)
    if upstream_order:
        return upstream_order
    return _compare_debian_part(left_revision, right_revision)


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
    debs = sorted(package_dir.glob("taiji-agent_*_amd64.deb"))
    if len(debs) != 1:
        raise ProducerError(f"生成的安装包目录必须且只能包含一个 amd64 DEB，实际为 {len(debs)}")
    deb = debs[0]
    checksum = Path(f"{deb}.sha256")
    manifest = package_dir / "taiji-package-manifest.json"
    build_marker = package_dir / ".build-success"
    source_archive = delivery / f"taiji-agentv1.0-kylin-build-src-{commit}.tar.gz"
    binding_args = argparse.Namespace(
        source_commit=commit,
        deb=deb,
        checksum=checksum,
        manifest=manifest,
        build_marker=build_marker,
        source_archive=source_archive,
        delivery_dir=delivery,
    )
    try:
        binding = validator.validate_build_binding(binding_args)
    except Exception as exc:
        raise ProducerError(f"交付物与当前源码/manifest 绑定校验失败：{exc}") from exc
    if not isinstance(binding, validator.BuildBinding):
        raise ProducerError("release evidence validator 未返回当前 v3 BuildBinding")
    return {
        "source_commit": binding.source_commit,
        "deb": deb,
        "checksum": checksum,
        "manifest": manifest,
        "deb_sha256": binding.deb_sha256,
        "version": binding.version,
        "architecture": binding.architecture,
        "compatibility_policy_id": binding.compatibility_policy_id,
        "compatibility_policy_sha256": binding.compatibility_policy_sha256,
        "build_marker": build_marker,
        "source_archive": source_archive,
        "delivery_inventory_sha256": binding.delivery_inventory_sha256,
        "binding": binding,
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
    previous: dict[str, Any] | None = None,
    policy: dict[str, str] | None = None,
    expanded: bool = False,
) -> None:
    image_info = docker_json(docker, ["image", "inspect", image], "Docker image inspect")
    if image_info.get("Os") != "linux" or image_info.get("Architecture") != "amd64":
        raise ProducerError("演练镜像必须是 linux/amd64")
    image_config = image_info.get("Config")
    labels = image_config.get("Labels") if type(image_config) is dict else None
    entrypoint = image_config.get("Entrypoint") if type(image_config) is dict else None
    if type(labels) is not dict or labels.get("io.taiji.release-evidence.role") != IMAGE_ROLE_LABEL:
        raise ProducerError("演练镜像不是仓库定义的专用离线演练镜像")
    if labels.get("io.taiji.release-evidence.baseline") != IMAGE_BASELINE_LABEL:
        raise ProducerError("演练镜像兼容基线不是 ubuntu-20.04")
    if labels.get("io.taiji.release-evidence.fixture") != IMAGE_FIXTURE_LABEL:
        raise ProducerError("演练镜像不是固定的 Kylin policy fixture")
    if entrypoint != ["/usr/local/bin/run-lifecycle.sh"]:
        raise ProducerError("演练镜像入口不是固定 lifecycle runner")
    image_id = image_info.get("Id")
    if type(image_id) is not str or not image_id.startswith("sha256:"):
        raise ProducerError("Docker image inspect 缺少不可变镜像 ID")

    name = f"taiji-offline-rehearsal-{uuid.uuid4().hex[:12]}"
    create_args = [
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
            "--env",
            f"TAIJI_EXPECTED_CANDIDATE_VERSION={release['version']}",
            "--env",
            f"TAIJI_REHEARSAL_FIXTURE_ID={IMAGE_FIXTURE_LABEL}",
    ]
    if expanded:
        if previous is None or policy is None:
            raise ProducerError("扩展生命周期演练缺少 previous DEB 或 compatibility policy")
        create_args.extend(
            [
                "--env",
                "TAIJI_REHEARSAL_EXPANDED=1",
                "--env",
                f"TAIJI_EXPECTED_PREVIOUS_DEB_BASENAME={previous['deb'].name}",
                "--env",
                f"TAIJI_EXPECTED_PREVIOUS_DEB_SHA256={previous['sha256']}",
                "--env",
                f"TAIJI_EXPECTED_PREVIOUS_VERSION={previous['version']}",
                "--env",
                "TAIJI_PREVIOUS_DEB_RELATIVE=.rehearsal-inputs/previous/"
                f"{previous['deb'].name}",
                "--env",
                f"TAIJI_EXPECTED_PREVIOUS_SIGNATURE_BASENAME={previous['signature'].name}",
                "--env",
                f"TAIJI_EXPECTED_PREVIOUS_SIGNATURE_SHA256={previous['signature_sha256']}",
                "--env",
                "TAIJI_PREVIOUS_SIGNATURE_RELATIVE=.rehearsal-inputs/previous/"
                f"{previous['signature'].name}",
                "--env",
                f"TAIJI_COMPATIBILITY_POLICY_ID={policy['id']}",
                "--env",
                f"TAIJI_COMPATIBILITY_POLICY_SHA256={policy['sha256']}",
                "--env",
                "TAIJI_TRANSACTION_HELPER_RELATIVE=.rehearsal-inputs/upgrade_transaction.py",
                "--env",
                "TAIJI_TRANSACTION_CONTRACT_RELATIVE=.rehearsal-inputs/upgrade-data-contract.json",
                "--env",
                "TAIJI_SILENT_DEPLOY_RELATIVE=.rehearsal-inputs/taiji-silent-deploy.sh",
                "--env",
                "TAIJI_BUILD_MANIFEST_RELATIVE=生成的安装包/taiji-package-manifest.json",
                "--env",
                "TAIJI_POLICY_RELATIVE=.rehearsal-inputs/compatibility-policy.json",
                "--env",
                "TAIJI_RELEASE_PUBLIC_KEY_RELATIVE=.rehearsal-inputs/signing-public.pem",
                "--env",
                f"TAIJI_EXPECTED_RELEASE_PUBLIC_KEY_SHA256={sha256_file(RELEASE_PUBLIC_KEY)}",
            ]
        )
    create_args.append(image)
    create = run_command(create_args)
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
    if set(session) != OFFLINE_SESSION_KEYS and not OFFLINE_SESSION_KEYS.issubset(session):
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
        "environment": REHEARSAL_ENVIRONMENT,
        "os_id": "ubuntu",
        "os_version": "20.04",
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
    checks = session.get("checks")
    expected_checks = {"install": True, "uninstall": True, "reinstall": True}
    if not isinstance(checks, dict) or any(
        checks.get(key) is not True or type(checks.get(key)) is not bool
        for key in expected_checks
    ):
        raise ProducerError("离线会话必须记录 install/uninstall/reinstall 三段真实通过")
    if set(session) != OFFLINE_SESSION_KEYS:
        steps = session.get("steps")
        if steps != [
            "fresh_install_n",
            "same_version_reinstall_n",
            "seed_n_minus_one",
            "upgrade_n_minus_one_to_n",
            "data_manifest_after_upgrade",
            "inject_postinst_failure_same_candidate",
            "automatic_rollback_to_n_minus_one",
            "upgrade_n_again",
            "remove_preserves_user_data",
            "purge_clears_root_state_only",
        ]:
            raise ProducerError("扩展离线会话 lifecycle steps 不完整或顺序错误")
        receipts = session.get("receipts")
        if not isinstance(receipts, list) or len(receipts) < 4:
            raise ProducerError("扩展离线会话缺少足够的 candidate receipts")
        manifests = session.get("data_manifests")
        if not isinstance(manifests, dict) or not {
            "before_upgrade", "after_upgrade", "after_rollback"
        }.issubset(manifests):
            raise ProducerError("扩展离线会话缺少数据 manifest 对账")


def sha256_file(path: Path) -> str:
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size <= 0:
            raise ProducerError(f"待摘要文件必须是非空单链接普通文件：{path}")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
    except ProducerError:
        raise
    except OSError as exc:
        raise ProducerError(f"待摘要文件不存在或不可读取：{path}: {exc}") from exc

    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if _file_identity(opened) != _file_identity(before):
            raise ProducerError(f"待摘要文件在打开时发生替换：{path}")
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise ProducerError(f"待摘要文件读取期间被截断：{path}")
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ProducerError(f"待摘要文件读取期间大小发生变化：{path}")
        after = os.fstat(descriptor)
        current = path.lstat()
        if (
            _file_identity(after) != _file_identity(before)
            or _file_identity(current) != _file_identity(before)
        ):
            raise ProducerError(f"待摘要文件读取期间发生变化：{path}")
        return digest.hexdigest()
    except OSError as exc:
        raise ProducerError(f"待摘要文件读取失败：{path}: {exc}") from exc
    finally:
        os.close(descriptor)


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


def copy_new_evidence_file(source: Path, destination: Path) -> str:
    source = resolve_regular_file(source, "offline lifecycle identity input")
    try:
        before = source.lstat()
        source_descriptor = os.open(
            source,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise ProducerError(f"N-1 生命周期身份文件不可安全打开：{source}: {exc}") from exc
    destination_descriptor = -1
    succeeded = False
    try:
        opened = os.fstat(source_descriptor)
        if _file_identity(opened) != _file_identity(before):
            raise ProducerError(f"N-1 生命周期身份文件在打开时发生替换：{source}")
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        digest = hashlib.sha256()
        remaining = opened.st_size
        copied = 0
        while remaining:
            chunk = os.read(source_descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise ProducerError(f"N-1 生命周期身份文件在归档时被截断：{source}")
            digest.update(chunk)
            copied += len(chunk)
            remaining -= len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    raise ProducerError("无法流式归档 N-1 生命周期身份文件")
                view = view[written:]
        if os.read(source_descriptor, 1):
            raise ProducerError(f"N-1 生命周期身份文件在归档时增长：{source}")
        os.fsync(destination_descriptor)
        after = os.fstat(source_descriptor)
        current = source.lstat()
        if (
            _file_identity(after) != _file_identity(opened)
            or _file_identity(current) != _file_identity(opened)
        ):
            raise ProducerError(f"N-1 生命周期身份文件在归档期间发生变化：{source}")
        archived = os.fstat(destination_descriptor)
        archived_current = destination.lstat()
        if (
            not stat.S_ISREG(archived.st_mode)
            or archived.st_nlink != 1
            or archived.st_size != copied
            or archived.st_mode & 0o077
            or _file_identity(archived_current) != _file_identity(archived)
        ):
            raise ProducerError(f"N-1 生命周期身份归档快照元数据不可信：{destination}")
        succeeded = True
        return digest.hexdigest()
    except ProducerError:
        raise
    except OSError as exc:
        raise ProducerError(f"N-1 生命周期身份文件归档失败：{exc}") from exc
    finally:
        if destination_descriptor >= 0:
            os.close(destination_descriptor)
        os.close(source_descriptor)
        if not succeeded and os.path.lexists(destination):
            try:
                destination.unlink()
            except OSError:
                pass


def validate_current_offline(
    evidence: Path, release: dict[str, Any], challenge: str
) -> None:
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
        "--delivery-dir",
        str(release["source_archive"].parent),
        "--challenge",
        challenge,
    ]
    run_command(args)


@contextmanager
def staged_expanded_delivery(delivery: Path, previous: dict[str, Any], policy_source: Path):
    """Expose previous-release and transaction inputs without mutating delivery."""

    with tempfile.TemporaryDirectory(prefix=".taiji-offline-rehearsal-", dir=delivery.parent) as temporary:
        staged = Path(temporary) / delivery.name
        shutil.copytree(delivery, staged, symlinks=True)
        input_root = staged / ".rehearsal-inputs"
        previous_dir = input_root / "previous"
        previous_dir.mkdir(mode=0o700, parents=True)
        previous_deb = previous["deb"]
        previous_target = previous_dir / previous_deb.name
        shutil.copy2(previous_deb, previous_target)
        shutil.copy2(previous["checksum"], Path(f"{previous_target}.sha256"))
        shutil.copy2(previous["signature"], previous_dir / previous["signature"].name)
        shutil.copy2(previous["manifest"], previous_dir / "previous-release-manifest.json")
        for source in (
            ROOT / "packaging" / "linux" / "deb" / "taiji-silent-deploy.sh",
            ROOT / "packaging" / "linux" / "deployment_receipt.py",
            ROOT / "packaging" / "linux" / "upgrade_transaction.py",
            ROOT / "packaging" / "linux" / "upgrade-data-contract.json",
            ROOT / "packaging" / "linux" / "compatibility_policy.py",
            policy_source,
        ):
            shutil.copy2(source, input_root / source.name)
        shutil.copy2(RELEASE_PUBLIC_KEY, input_root / "signing-public.pem")
        yield staged


@contextmanager
def unmodified_delivery(delivery: Path):
    yield delivery


def prepare_explicit_inputs(
    *,
    delivery: Path,
    validator: ModuleType,
    deb_arg: Path,
    previous_arg: Path,
    previous_signature_arg: Path,
    previous_manifest_arg: Path,
    manifest_arg: Path,
    policy_arg: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    """Validate the fixed explicit CLI contract against the current v3 release."""

    release = discover_release_inputs(delivery, validator)
    deb = resolve_regular_file(deb_arg, "candidate DEB")
    manifest = resolve_regular_file(manifest_arg, "build manifest")
    if deb != release["deb"].resolve():
        raise ProducerError("candidate DEB 必须是交付目录生成的安装包中的唯一 amd64 DEB")
    if manifest.name != release["manifest"].name:
        raise ProducerError("build manifest basename 必须是 taiji-package-manifest.json")
    if read_regular_bytes(manifest, "build manifest") != read_regular_bytes(
        release["manifest"], "交付目录 build manifest"
    ):
        raise ProducerError("build manifest 内容必须与交付目录中的 candidate manifest 完全一致")
    release["manifest"] = manifest
    previous = resolve_regular_file(previous_arg, "previous DEB")
    if previous == deb:
        raise ProducerError("previous DEB 不能与 candidate DEB 是同一文件")
    previous_hash = sha256_file(previous)
    previous_checksum = resolve_regular_file(Path(f"{previous}.sha256"), "previous DEB checksum")
    parse_sha256_sidecar(previous_checksum, previous, previous_hash)
    previous_signature = resolve_regular_file(
        previous_signature_arg,
        "previous DEB detached signature",
    )
    previous_signature_sha256 = verify_previous_detached_signature(
        previous,
        previous_signature,
    )
    previous_manifest = resolve_regular_file(previous_manifest_arg, "previous build manifest")
    previous_manifest_payload = load_json(previous_manifest, "previous build manifest")
    required_previous_manifest = {
        "schema": "taiji-package-manifest/v3",
        "package": "taiji-agent",
        "architecture": "amd64",
        "deb_basename": previous.name,
        "deb_sha256": previous_hash,
    }
    for key, expected in required_previous_manifest.items():
        if previous_manifest_payload.get(key) != expected:
            raise ProducerError(f"previous build manifest {key} 与 previous DEB 不一致")
    previous_source_commit = previous_manifest_payload.get("source_commit")
    previous_version = previous_manifest_payload.get("version")
    previous_policy_id = previous_manifest_payload.get("compatibility_policy_id")
    previous_policy_sha256 = previous_manifest_payload.get("compatibility_policy_sha256")
    if type(previous_source_commit) is not str or not re.fullmatch(r"[0-9a-f]{40}", previous_source_commit):
        raise ProducerError("previous build manifest source_commit 不合法")
    if type(previous_version) is not str or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}", previous_version):
        raise ProducerError("previous build manifest version 不合法")
    if type(previous_policy_id) is not str or not previous_policy_id:
        raise ProducerError("previous build manifest compatibility_policy_id 不合法")
    if type(previous_policy_sha256) is not str or not SHA256_RE.fullmatch(previous_policy_sha256):
        raise ProducerError("previous build manifest compatibility_policy_sha256 不合法")
    previous_name = re.fullmatch(r"taiji-agent_(.+)_amd64\.deb", previous.name)
    if previous_name is None:
        raise ProducerError("previous DEB basename 不符合 taiji-agent_<version>_amd64.deb")
    if previous_name.group(1) != previous_version:
        raise ProducerError("previous DEB basename version 与 previous build manifest 不一致")
    candidate_version = release["version"]
    if compare_debian_versions(previous_version, candidate_version) >= 0:
        raise ProducerError(
            "previous DEB must be strictly older than candidate under Debian version rules"
        )
    policy_id, policy_sha256 = load_policy_identity(policy_arg)
    manifest_payload = load_json(manifest, "build manifest")
    manifest_policy_id = manifest_payload.get("compatibility_policy_id")
    manifest_policy_sha256 = manifest_payload.get("compatibility_policy_sha256")
    if manifest_policy_id != policy_id or manifest_policy_sha256 != policy_sha256:
        raise ProducerError("显式 policy identity 与 candidate build manifest 不一致")
    return (
        release,
        {
            "deb": previous,
            "sha256": previous_hash,
            "checksum": previous_checksum,
            "checksum_sha256": sha256_file(previous_checksum),
            "signature": previous_signature,
            "signature_sha256": previous_signature_sha256,
            "signature_verification": "PASS",
            "manifest": previous_manifest,
            "manifest_sha256": sha256_file(previous_manifest),
            "source_commit": previous_source_commit,
            "version": previous_version,
            "compatibility_policy_id": previous_policy_id,
            "compatibility_policy_sha256": previous_policy_sha256,
        },
        {"id": policy_id, "sha256": policy_sha256, "path": str(Path(policy_arg).resolve())},
    )


def produce(
    delivery_arg: Path | None,
    output_arg: Path,
    image: str,
    challenge: str,
    *,
    deb_arg: Path | None = None,
    previous_deb_arg: Path | None = None,
    previous_signature_arg: Path | None = None,
    previous_manifest_arg: Path | None = None,
    build_manifest_arg: Path | None = None,
    policy_arg: Path | None = None,
) -> Path:
    if not CHALLENGE_RE.fullmatch(challenge):
        raise ProducerError("challenge 必须是 64-128 位小写十六进制")
    if not image.strip() or any(character.isspace() for character in image):
        raise ProducerError("Docker image 名称不能为空或包含空白")
    docker = shutil.which("docker")
    if docker is None:
        raise ProducerError("缺少 docker 命令")

    explicit = any(
        value is not None
        for value in (
            deb_arg,
            previous_deb_arg,
            previous_signature_arg,
            previous_manifest_arg,
            build_manifest_arg,
            policy_arg,
        )
    )
    if explicit and any(
        value is None
        for value in (
            deb_arg,
            previous_deb_arg,
            previous_signature_arg,
            previous_manifest_arg,
            build_manifest_arg,
            policy_arg,
        )
    ):
        raise ProducerError(
            "显式生命周期入口必须同时提供 --deb、--previous-deb、--previous-signature、"
            "--previous-manifest、--build-manifest、--policy"
        )
    if not explicit and delivery_arg is None:
        raise ProducerError("必须提供 --delivery-dir，或完整的显式 DEB/previous/manifest/policy 输入")
    if explicit and delivery_arg is not None:
        raise ProducerError("--delivery-dir 与显式 --deb 输入不能混用")

    if delivery_arg is not None:
        delivery = resolve_directory(delivery_arg, "交付目录")
    else:
        candidate = resolve_regular_file(Path(deb_arg), "candidate DEB")
        # The explicit controlled lifecycle is intentionally scoped to the
        # canonical delivery layout so source/manifest/inventory binding stays stable.
        expected_delivery = candidate.parent.parent
        delivery = resolve_directory(expected_delivery, "candidate DEB 所属交付目录")
    output = resolve_output(output_arg)
    validator = load_validator()
    previous: dict[str, Any] | None = None
    policy: dict[str, str] | None = None
    expanded = explicit
    if expanded:
        release, previous, policy = prepare_explicit_inputs(
            delivery=delivery,
            validator=validator,
            deb_arg=Path(deb_arg),
            previous_arg=Path(previous_deb_arg),
            previous_signature_arg=Path(previous_signature_arg),
            previous_manifest_arg=Path(previous_manifest_arg),
            manifest_arg=Path(build_manifest_arg),
            policy_arg=Path(policy_arg),
        )
    else:
        release = discover_release_inputs(delivery, validator)

    output.mkdir(mode=0o700)
    published = False
    try:
        with (
            staged_expanded_delivery(delivery, previous, Path(policy["path"]))
            if expanded and previous
            else unmodified_delivery(delivery)
        ) as container_delivery:
            run_lifecycle_container(
                docker=docker,
                image=image,
                delivery=container_delivery,
                evidence_dir=output,
                challenge=challenge,
                release=release,
                previous=previous,
                policy=policy,
                expanded=expanded,
            )
        session_path = output / SESSION_BASENAME
        lifecycle_path = output / LIFECYCLE_BASENAME
        if not session_path.is_file() and lifecycle_path.is_file():
            lifecycle_session = load_json(lifecycle_path, "扩展离线生命周期会话")
            base_session = {
                key: lifecycle_session[key]
                for key in OFFLINE_SESSION_KEYS
                if key in lifecycle_session
            }
            atomic_write_json(session_path, base_session)
        session = load_json(session_path, "离线生命周期结构化会话")
        validate_session(session, release, challenge)
        lifecycle_session = (
            load_json(lifecycle_path, "扩展离线生命周期会话")
            if lifecycle_path.is_file()
            else session
        )
        if expanded:
            validate_session(lifecycle_session, release, challenge)
            if lifecycle_session.get("compatibility_policy_id") != policy["id"]:
                raise ProducerError("lifecycle evidence compatibility_policy_id 与固定 policy 不一致")
            if lifecycle_session.get("compatibility_policy_sha256") != policy["sha256"]:
                raise ProducerError("lifecycle evidence compatibility_policy_sha256 与固定 policy 不一致")
            for key, expected in {
                "previous_deb_basename": previous["deb"].name,
                "previous_deb_sha256": previous["sha256"],
                "previous_version": previous["version"],
                "previous_signature_basename": previous["signature"].name,
                "previous_signature_sha256": previous["signature_sha256"],
                "previous_signature_verification": previous["signature_verification"],
            }.items():
                if lifecycle_session.get(key) != expected:
                    raise ProducerError(f"lifecycle evidence {key} 与固定 previous DEB 不一致")

        current_release = discover_release_inputs(delivery, validator)
        if (
            current_release["binding"] != release["binding"]
            or current_release["delivery_inventory_sha256"]
            != release["delivery_inventory_sha256"]
        ):
            raise ProducerError("当前 v3 交付目录在 Docker 演练期间发生变化")

        previous_release_evidence: dict[str, Any] | None = None
        if expanded:
            if previous is None:
                raise ProducerError("扩展生命周期演练缺少已验证的 N-1 身份")
            archived_deb_hash = copy_new_evidence_file(
                previous["deb"],
                output / previous["deb"].name,
            )
            archived_checksum_hash = copy_new_evidence_file(
                previous["checksum"],
                output / previous["checksum"].name,
            )
            archived_signature_hash = copy_new_evidence_file(
                previous["signature"],
                output / previous["signature"].name,
            )
            previous_manifest_basename = "previous-release-manifest.json"
            archived_manifest_hash = copy_new_evidence_file(
                previous["manifest"],
                output / previous_manifest_basename,
            )
            if (
                archived_deb_hash != previous["sha256"]
                or archived_checksum_hash != previous["checksum_sha256"]
                or archived_signature_hash != previous["signature_sha256"]
                or archived_manifest_hash != previous["manifest_sha256"]
            ):
                raise ProducerError("归档后的 N-1 身份与已验证输入不一致")
            previous_release_evidence = {
                "source_commit": previous["source_commit"],
                "version": previous["version"],
                "deb_basename": previous["deb"].name,
                "deb_sha256": previous["sha256"],
                "checksum_basename": previous["checksum"].name,
                "checksum_sha256": previous["checksum_sha256"],
                "signature_basename": previous["signature"].name,
                "signature_sha256": previous["signature_sha256"],
                "signature_verification": previous["signature_verification"],
                "manifest_basename": previous_manifest_basename,
                "manifest_sha256": previous["manifest_sha256"],
                "compatibility_policy_id": previous["compatibility_policy_id"],
                "compatibility_policy_sha256": previous["compatibility_policy_sha256"],
            }

        evidence = {
            "schema": "taiji.offline-install-rehearsal.v1",
            "status": "PASS",
            "generated_at_utc": session["generated_at_utc"],
            "rehearsal_session_id": session["rehearsal_session_id"],
            "challenge_nonce": challenge,
            "source_commit": release["source_commit"],
            "version": release["version"],
            "architecture": release["architecture"],
            "deb_basename": release["deb"].name,
            "deb_sha256": release["deb_sha256"],
            "compatibility_policy_id": release["compatibility_policy_id"],
            "compatibility_policy_sha256": release["compatibility_policy_sha256"],
            "delivery_inventory_sha256": release["delivery_inventory_sha256"],
            "platform": "linux/amd64",
            "environment": REHEARSAL_ENVIRONMENT,
            "os_id": session["os_id"],
            "os_version": session["os_version"],
            "network": "none",
            "checks": {"install": "PASS", "uninstall": "PASS", "reinstall": "PASS"},
            "desktop_app_verified": False,
            "target_verified": False,
            "log_basename": SESSION_BASENAME,
            "log_sha256": sha256_file(session_path),
        }
        if expanded:
            for key in (
                "steps",
                "receipts",
                "data_manifests",
                "journal",
                "package_actions",
                "previous_deb_basename",
                "previous_deb_sha256",
                "previous_version",
                "previous_signature_basename",
                "previous_signature_sha256",
                "previous_signature_verification",
            ):
                if key in lifecycle_session:
                    evidence[key] = lifecycle_session[key]
            if previous_release_evidence is None:
                raise ProducerError("扩展生命周期证据缺少 N-1 身份")
            evidence["previous_release"] = previous_release_evidence
            evidence["lifecycle_log_basename"] = LIFECYCLE_BASENAME
            evidence["lifecycle_log_sha256"] = sha256_file(lifecycle_path)
        evidence_path = output / EVIDENCE_BASENAME
        atomic_write_json(evidence_path, evidence)
        validate_current_offline(evidence_path, release, challenge)
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
    parser.add_argument(
        "--delivery-dir",
        type=Path,
        help="当前 v3 单 DEB 交付目录入口；与显式 DEB 输入互斥",
    )
    parser.add_argument("--deb", type=Path, help="本轮固定 candidate amd64 DEB")
    parser.add_argument("--previous-deb", type=Path, help="必须存在且有 .sha256 sidecar 的 N-1 DEB")
    parser.add_argument("--previous-signature", type=Path, help="由固定发布公钥验证的 N-1 DEB detached signature")
    parser.add_argument("--previous-manifest", type=Path, help="与 N-1 DEB 身份一致的发布 manifest")
    parser.add_argument("--build-manifest", type=Path, help="绑定 candidate 的 taiji-package-manifest.json")
    parser.add_argument("--policy", type=Path, help="固定 compatibility-policy.json")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--challenge", required=True)
    args = parser.parse_args()
    explicit = (
        args.deb,
        args.previous_deb,
        args.previous_signature,
        args.previous_manifest,
        args.build_manifest,
        args.policy,
    )
    if args.delivery_dir is None and any(item is None for item in explicit):
        parser.error(
            "必须提供 --delivery-dir，或完整提供 --deb --previous-deb --previous-signature "
            "--previous-manifest --build-manifest --policy"
        )
    if args.delivery_dir is not None and any(item is not None for item in explicit):
        parser.error("--delivery-dir 与显式 DEB 输入不能混用")
    return args


def main() -> int:
    args = parse_args()
    try:
        evidence = produce(
            args.delivery_dir,
            args.output_dir,
            args.image,
            args.challenge,
            deb_arg=args.deb,
            previous_deb_arg=args.previous_deb,
            previous_signature_arg=args.previous_signature,
            previous_manifest_arg=args.previous_manifest,
            build_manifest_arg=args.build_manifest,
            policy_arg=args.policy,
        )
    except (ProducerError, OSError, ValueError, TypeError) as exc:
        print(f"offline-rehearsal-producer-failed\t{exc}", file=sys.stderr)
        return 1
    print(f"offline-rehearsal-produced\t{evidence}")
    print("offline-rehearsal-signature\t未生成；请由发布负责人离线复核后单独签名")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
