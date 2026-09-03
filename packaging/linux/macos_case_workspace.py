#!/usr/bin/env python3
"""Run physical package verification in a private case-sensitive Mac volume.

The caller remains the authoritative verifier. This helper only owns its new
image and mount; a failed detach retains both and makes the invocation fail.
"""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import plistlib
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile


HDIUTIL = "/usr/bin/hdiutil"
MIN_HOST_FREE = 4 * 1024 ** 3


class WorkspaceError(RuntimeError):
    pass


def validate_parent(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink():
        raise WorkspaceError("temporary parent must be an absolute non-symlink directory")
    physical = path.resolve(strict=True)
    for entry in (physical, *physical.parents):
        metadata = entry.lstat()
        sticky_root = metadata.st_uid == 0 and stat.S_IMODE(metadata.st_mode) == 0o1777
        if (not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid not in (0, os.getuid())
                or (metadata.st_mode & 0o022 and not sticky_root)):
            raise WorkspaceError("unsafe temporary parent: " + str(entry))
    return physical


def case_sensitive(parent):
    parent = validate_parent(parent)
    with tempfile.TemporaryDirectory(prefix="taiji-case-probe-", dir=str(parent)) as temporary:
        directory = Path(temporary)
        (directory / "A").write_bytes(b"upper")
        try:
            descriptor = os.open(str(directory / "a"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        else:
            os.close(descriptor)
        return (directory / "A").stat().st_ino != (directory / "a").stat().st_ino


def disk_command(runner, *arguments):
    result = runner([HDIUTIL, *arguments], check=True, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, timeout=180,
                    env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
    return result.stdout


def attached_devices(runner, image, mount):
    payload = plistlib.loads(disk_command(runner, "info", "-plist"))
    devices = []
    for record in payload.get("images", []):
        if record.get("image-path") != str(image):
            continue
        for entity in record.get("system-entities", []):
            device = entity.get("dev-entry", "")
            if not re.fullmatch(r"/dev/disk[0-9]+(?:s[0-9]+)*", device):
                raise WorkspaceError("unexpected attached device identity")
            target = entity.get("mount-point")
            if target is not None and target != str(mount):
                raise WorkspaceError("image mounted outside its private mount point")
            devices.append(device)
        # Detach the whole image device, or the sole volume for a flat image.
        whole = [device for device in devices if re.fullmatch(r"/dev/disk[0-9]+", device)]
        return whole[:1] or devices[:1]
    return []


@contextmanager
def workspace(parent, runner=subprocess.run):
    parent = validate_parent(parent)
    if shutil.disk_usage(parent).free < MIN_HOST_FREE:
        raise WorkspaceError("Mac verification requires at least 4 GiB of real host free space")
    base = Path(tempfile.mkdtemp(prefix="taiji-case-workspace-", dir=str(parent)))
    identity = base.stat()
    image = base / "verification.sparseimage"
    mount = base / "volume"
    mount.mkdir(mode=0o700)
    print("[INFO] Mac verification workspace: " + str(base), flush=True)
    try:
        disk_command(runner, "create", "-size", "8g", "-type", "SPARSE",
                     "-fs", "Case-sensitive APFS", "-volname", "TaijiVerification",
                     "-nospotlight", str(image))
        metadata = image.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
            raise WorkspaceError("unsafe verification image")
        image.chmod(0o600)
        payload = plistlib.loads(disk_command(
            runner, "attach", "-plist", "-nobrowse", "-noautoopen", "-owners", "on",
            "-mountpoint", str(mount), str(image)))
        mounted = [entry for entry in payload.get("system-entities", [])
                   if entry.get("mount-point") == str(mount)]
        if len(mounted) != 1 or not attached_devices(runner, image, mount):
            raise WorkspaceError("attachment did not confirm the private mount point")
        temporary = mount / "tmp"
        temporary.mkdir(mode=0o700)
        if not case_sensitive(temporary):
            raise WorkspaceError("verification volume does not preserve case-distinct names")
        print("[INFO] Mac verification uses a private case-sensitive APFS workspace", flush=True)
        yield temporary
    finally:
        try:
            current = base.lstat()
            if (current.st_dev, current.st_ino, current.st_uid, stat.S_IMODE(current.st_mode)) != (
                    identity.st_dev, identity.st_ino, os.getuid(), 0o700):
                raise WorkspaceError("workspace identity changed")
            for device in attached_devices(runner, image, mount):
                disk_command(runner, "detach", device)
            if attached_devices(runner, image, mount) or mount.is_mount() or mount.is_symlink():
                raise WorkspaceError("verification image is still attached")
            # Never recursively delete a mount or sweep unknown entries.
            mount.rmdir()
            if image.exists():
                image.unlink()
            base.rmdir()
        except Exception as error:
            raise WorkspaceError("workspace cleanup failed; retained {}: {}".format(base, error)) from error


def run_child(command, temporary):
    environment = dict(os.environ)
    environment.update({"TMPDIR": str(temporary), "TMP": str(temporary), "TEMP": str(temporary)})
    child = subprocess.Popen(command, env=environment, start_new_session=True)
    try:
        return child.wait()
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--parent", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.probe is not None:
        return 0 if case_sensitive(args.probe) else 1
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if sys.platform != "darwin" or args.parent is None or not command:
        parser.error("workspace execution requires Darwin, --parent and a command after --")

    def interrupted(number, frame):
        for value in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(value, signal.SIG_IGN)
        raise InterruptedError(number)

    previous = {number: signal.signal(number, interrupted)
                for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        with workspace(args.parent) as temporary:
            status = run_child(command, temporary)
        return status if status >= 0 else 128 - status
    except InterruptedError as error:
        return 128 + error.args[0]
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, WorkspaceError, subprocess.SubprocessError) as error:
        print("[FAIL] Mac verification workspace: " + str(error), file=sys.stderr)
        sys.exit(2)
