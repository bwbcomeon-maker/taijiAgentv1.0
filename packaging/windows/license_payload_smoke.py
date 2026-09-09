"""Exercise the real Windows license resource writer in disposable state."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile


_INSTALL_TREE_LOADER_TIMEOUT_SECONDS = 30


def check_resources(agent: Path, root: Path, *, create: bool) -> dict:
    sys.path.insert(0, str(agent))
    import taiji_license as license_module

    if Path(license_module.__file__).resolve() != (agent / 'taiji_license.py').resolve():
        raise RuntimeError('License module outside payload')
    payload = agent.parents[2]
    expected_key_path = payload / 'resources/license/signing-public.pem'
    expected_version_path = payload / 'resources/license/VERSION'
    assert license_module.PRODUCTION_INSTALL_ROOT == payload
    assert license_module.PRODUCTION_INSTALL_TRUST_ROOT == Path(payload.anchor)
    assert license_module.PRODUCTION_PUBLIC_KEY_PATH == expected_key_path
    assert license_module.PRODUCTION_VERSION_PATH == expected_version_path
    device_path = root / '.config/taiji-agent/license-device.json'
    # Redirect only canonical paths in this disposable process; keep the real
    # request, device creation and secure reader/writer implementations intact.
    license_module.PRODUCTION_USER_HOME = root
    license_module.PRODUCTION_LICENSE_DEVICE_PATH = device_path
    license_module.PRODUCTION_LICENSE_PATH = root / '.config/taiji-agent/licenses/active-license.jwt'
    license_module.PRODUCTION_LICENSE_STATE_PATH = root / '.local/state/taiji-agent/license-state.json'
    if not create:
        assert device_path.is_file()
    request = license_module.build_machine_request()
    assert request['fingerprint_quality'] == 'strong'
    assert 'no_stable_hardware' not in request['risk_flags']
    raw = license_module._secure_read_runtime_resource(
        path=device_path, profile_root=root, required=True)
    device = license_module._parse_license_device(raw)
    assert device is not None
    owner, current, entries = license_module._windows_security_snapshot(device_path)
    assert owner == current
    import win32security
    descriptor = win32security.GetFileSecurity(
        str(device_path), win32security.DACL_SECURITY_INFORMATION)
    assert descriptor.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED
    assert license_module._windows_acl_is_trusted(
        owner_sid=owner, current_user_sid=current, entries=entries,
        require_current_user_owner=True)
    license_path = license_module.runtime_license_path()
    state_path = license_module.runtime_license_state_path()
    if create:
        for content in ('first', 'replacement'):
            license_module._secure_atomic_write_runtime_resource(
                path=license_path, text=content, profile_root=root)
        for stamp in (1700000000, 1700010000):
            license_module._write_license_state(
                path=state_path, now_ts=stamp, license_id='smoke',
                token='replacement', secure_root=root)
    assert license_module._secure_read_runtime_resource(
        path=license_path, profile_root=root, required=True) == 'replacement'
    state = json.loads(license_module._secure_read_runtime_resource(
        path=state_path, profile_root=root, required=True))
    assert state['last_successful_validation_at'] == 1700010000
    return {'machine_code': request['machine_code']}


def check_payload_verification_material(agent: Path) -> str:
    """Check immutable bytes and layout without treating staging as installed."""
    sys.path.insert(0, str(agent))
    import taiji_license as license_module

    payload = agent.parents[2]
    expected = {
        payload / 'resources/license/signing-public.pem',
        payload / 'resources/license/VERSION',
    }
    for path in expected:
        file_stat = path.lstat()
        assert stat.S_ISREG(file_stat.st_mode)
        assert not stat.S_ISLNK(file_stat.st_mode)
        assert file_stat.st_nlink == 1
        assert not (
            getattr(file_stat, 'st_file_attributes', 0)
            & license_module._WINDOWS_REPARSE_POINT_ATTRIBUTE
        )
    public_key = license_module.PRODUCTION_PUBLIC_KEY_PATH.read_text(
        encoding='utf-8').strip()
    assert license_module._public_key_fingerprint(public_key) == (
        license_module.PRODUCTION_PUBLIC_KEY_FINGERPRINT)
    version = license_module.PRODUCTION_VERSION_PATH.read_text(
        encoding='utf-8').strip()
    assert re.fullmatch(
        r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)',
        version,
    )
    return version


def _set_protected_install_acl(path: Path) -> None:
    """Give a disposable production-shaped path an installed-style ACL."""
    import ntsecuritycon
    import win32con
    import win32security

    administrators = win32security.ConvertStringSidToSid('S-1-5-32-544')
    system = win32security.ConvertStringSidToSid('S-1-5-18')
    ace_flags = 0
    if path.is_dir():
        ace_flags = win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE
    acl = win32security.ACL()
    for principal in (administrators, system):
        acl.AddAccessAllowedAceEx(
            win32security.ACL_REVISION,
            ace_flags,
            ntsecuritycon.FILE_ALL_ACCESS,
            principal,
        )
    descriptor = win32security.SECURITY_DESCRIPTOR()
    descriptor.SetSecurityDescriptorOwner(administrators, False)
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    descriptor.SetSecurityDescriptorControl(
        win32security.SE_DACL_PROTECTED,
        win32security.SE_DACL_PROTECTED,
    )
    win32security.SetFileSecurity(
        str(path),
        win32security.OWNER_SECURITY_INFORMATION
        | win32security.DACL_SECURITY_INFORMATION
        | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        descriptor,
    )


def check_disposable_install_tree(root: Path, expected_version: str) -> None:
    """Load verification materials through the actual production validators."""
    root = root.resolve(strict=True)
    agent = root / 'hermes-local-lab/sources/hermes-agent'
    sys.path.insert(0, str(agent))
    import taiji_license as license_module

    assert Path(license_module.__file__).resolve() == (agent / 'taiji_license.py').resolve()
    assert license_module.taiji_runtime_profile.installation_profile() == (
        license_module.taiji_runtime_profile.WINDOWS_CANDIDATE_PROFILE)
    assert license_module.PRODUCTION_INSTALL_ROOT == root
    assert license_module.PRODUCTION_INSTALL_TRUST_ROOT == Path(root.anchor)
    public_key = license_module._load_production_public_key(
        license_module.runtime_license_policy())
    assert license_module._public_key_fingerprint(public_key) == (
        license_module.PRODUCTION_PUBLIC_KEY_FINGERPRINT)
    assert license_module._load_production_version() == expected_version
    print('WINDOWS_LICENSE_INSTALL_TREE_OK')


def _process_detail(stdout: object, stderr: object) -> str:
    parts = []
    for value in (stderr, stdout):
        if isinstance(value, bytes):
            value = value.decode('utf-8', 'replace')
        text = str(value or '').strip()
        if text:
            parts.append(text)
    return ' | '.join(parts) or 'no diagnostic'


def _run_install_tree_loader(probe_root: Path, expected_version: str) -> None:
    command = [
        sys.executable,
        '-I',
        '-B',
        str(Path(__file__).resolve()),
        '--verify-install-tree',
        str(probe_root),
        expected_version,
    ]
    child_env = dict(os.environ)
    child_env['TAIJI_WINDOWS_CANDIDATE'] = '1'
    try:
        child = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_INSTALL_TREE_LOADER_TIMEOUT_SECONDS,
            env=child_env,
        )
    except subprocess.TimeoutExpired as exc:
        detail = _process_detail(exc.stdout, exc.stderr)
        raise RuntimeError(
            f'Installed verification-material loader timed out after '
            f'{_INSTALL_TREE_LOADER_TIMEOUT_SECONDS}s: {detail}'
        ) from exc
    detail = _process_detail(child.stdout, child.stderr)
    if child.returncode != 0:
        raise RuntimeError(
            f'Installed verification-material loader failed '
            f'(exit={child.returncode}): {detail}'
        )
    if child.stdout.strip() != 'WINDOWS_LICENSE_INSTALL_TREE_OK':
        raise RuntimeError(
            f'Installed verification-material loader returned no success marker: '
            f'{detail}'
        )


def _finish_probe_cleanup(probe_root: Path, primary_error: BaseException | None) -> None:
    try:
        shutil.rmtree(probe_root)
    except Exception as cleanup_error:
        cleanup_detail = f'{type(cleanup_error).__name__}: {cleanup_error}'
        if primary_error is not None:
            raise RuntimeError(
                f'{primary_error}; probe cleanup also failed: {cleanup_detail}'
            ) from primary_error
        raise RuntimeError(
            f'Protected install probe cleanup failed: {cleanup_detail}'
        ) from cleanup_error
    if primary_error is not None:
        raise primary_error


def verify_disposable_install_tree(payload: Path, expected_version: str) -> None:
    """Build a protected probe on the system volume, invoke a fresh loader, clean it."""
    system_drive = os.environ.get('SystemDrive', '').strip()
    if len(system_drive) != 2 or system_drive[1] != ':':
        raise RuntimeError('SystemDrive is unavailable')
    system_root = Path(system_drive + '\\')
    probe_root = Path(tempfile.mkdtemp(
        prefix='taiji-license-install-probe-', dir=system_root))
    primary_error: BaseException | None = None
    try:
        agent = probe_root / 'hermes-local-lab/sources/hermes-agent'
        resources = probe_root / 'resources/license'
        agent.mkdir(parents=True)
        resources.mkdir(parents=True)
        source_agent = payload / 'hermes-local-lab/sources/hermes-agent'
        shutil.copyfile(source_agent / 'taiji_license.py', agent / 'taiji_license.py')
        shutil.copyfile(
            source_agent / 'taiji_runtime_profile.py', agent / 'taiji_runtime_profile.py')
        shutil.copyfile(
            source_agent / 'taiji-runtime-profile.json',
            agent / 'taiji-runtime-profile.json')
        shutil.copyfile(
            payload / 'resources/license/signing-public.pem',
            resources / 'signing-public.pem',
        )
        shutil.copyfile(
            payload / 'resources/license/VERSION', resources / 'VERSION')
        paths = [probe_root, *sorted(
            probe_root.rglob('*'), key=lambda item: (len(item.parts), str(item)))]
        for path in paths:
            _set_protected_install_acl(path)
        _run_install_tree_loader(probe_root, expected_version)
    except BaseException as exc:
        primary_error = exc
    _finish_probe_cleanup(probe_root, primary_error)


def check_rejections(root: Path) -> None:
    import taiji_license as license_module
    import win32security

    path = root / 'negative.json'
    license_module._secure_atomic_write_runtime_resource(
        path=path, text='test', profile_root=root)

    def rejected(candidate: Path) -> None:
        try:
            license_module._secure_read_runtime_resource(
                path=candidate, profile_root=root, required=True)
        except license_module._LicenseUserResourceError:
            return
        raise AssertionError('Untrusted license resource was accepted')

    link = root / 'hardlink.json'
    os.link(path, link)
    rejected(path)
    link.unlink()
    descriptor = win32security.GetFileSecurity(
        str(path), win32security.DACL_SECURITY_INFORMATION)
    acl = descriptor.GetSecurityDescriptorDacl()
    acl.AddAccessAllowedAceEx(
        win32security.ACL_REVISION, 0, 0x0002,
        win32security.ConvertStringSidToSid('S-1-1-0'))
    descriptor.SetSecurityDescriptorDacl(True, acl, False)
    win32security.SetFileSecurity(
        str(path), win32security.DACL_SECURITY_INFORMATION, descriptor)
    rejected(path)
    junction = root / 'junction'
    subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(junction), str(root)],
                   check=True, capture_output=True)
    try:
        rejected(junction / '.config/taiji-agent/license-device.json')
    finally:
        os.rmdir(junction)


def main(payload: Path, scratch: Path) -> None:
    payload = payload.resolve(strict=True)
    scratch = scratch.resolve(strict=True)
    if scratch == payload or scratch.is_relative_to(payload):
        raise ValueError('License scratch must be outside payload')
    agent = payload / 'hermes-local-lab/sources/hermes-agent'
    expected_version = check_payload_verification_material(agent)
    verify_disposable_install_tree(payload, expected_version)
    with tempfile.TemporaryDirectory(prefix='license-smoke-', dir=scratch) as temporary:
        root = Path(temporary)
        # The build scratch can inherit broad ACLs. Establish only this disposable
        # profile root; production parents and payload are never changed.
        sys.path.insert(0, str(agent))
        import taiji_license as license_module
        import win32security
        attributes = license_module._windows_runtime_security_attributes()
        win32security.SetFileSecurity(
            str(root), win32security.OWNER_SECURITY_INFORMATION
            | win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            attributes.SECURITY_DESCRIPTOR)
        first = check_resources(agent, root, create=True)
        child = subprocess.run(
            [sys.executable, '-I', '-B', str(Path(__file__).resolve()),
             '--read', str(agent), str(root)],
            check=True, capture_output=True, text=True)
        assert json.loads(child.stdout) == first
        check_rejections(root)
    print('WINDOWS_PAYLOAD_LICENSE_OK')


if __name__ == '__main__':
    if sys.argv[1] == '--verify-install-tree':
        check_disposable_install_tree(Path(sys.argv[2]), sys.argv[3])
    elif sys.argv[1] == '--read':
        print(json.dumps(check_resources(Path(sys.argv[2]), Path(sys.argv[3]), create=False)))
    else:
        main(Path(sys.argv[1]), Path(sys.argv[2]))
