"""Exercise the real Windows license resource writer in disposable state."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


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
    public_key = license_module._load_production_public_key(
        license_module.runtime_license_policy())
    assert license_module._public_key_fingerprint(public_key) == (
        license_module.PRODUCTION_PUBLIC_KEY_FINGERPRINT)
    expected_version = expected_version_path.read_text(encoding='utf-8').strip()
    assert license_module._load_production_version() == expected_version
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
    if sys.argv[1] == '--read':
        print(json.dumps(check_resources(Path(sys.argv[2]), Path(sys.argv[3]), create=False)))
    else:
        main(Path(sys.argv[1]), Path(sys.argv[2]))
