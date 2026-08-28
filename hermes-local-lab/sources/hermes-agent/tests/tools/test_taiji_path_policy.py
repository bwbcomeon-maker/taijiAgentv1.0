from __future__ import annotations

import pytest

from tools.taiji_path_policy import (
    posix_directory_security_reason,
    windows_directory_security_reason,
)


@pytest.mark.parametrize("mode", [0o700, 0o755, 0o775])
def test_owned_posix_normal_directories_are_usable(mode):
    assert posix_directory_security_reason(
        owner_uid=1000,
        mode=mode,
        current_uid=1000,
    ) is None


def test_posix_world_writable_owner_and_symlink_have_distinct_reasons():
    assert posix_directory_security_reason(
        owner_uid=1000, mode=0o777, current_uid=1000
    ) == "posix_world_writable"
    assert posix_directory_security_reason(
        owner_uid=1001, mode=0o755, current_uid=1000
    ) == "posix_owner_untrusted"
    assert posix_directory_security_reason(
        owner_uid=1000, mode=0o755, current_uid=1000, is_symlink=True
    ) == "posix_symlink_escape"


def test_windows_inherited_dacl_is_allowed_but_owner_acl_and_reparse_are_distinct():
    inherited = [(0, 0x10, 0x001200A9, "S-1-5-11")]
    assert windows_directory_security_reason(
        owner_sid="S-1-5-21-current",
        current_user_sid="S-1-5-21-current",
        entries=inherited,
    ) is None
    assert windows_directory_security_reason(
        owner_sid="S-1-5-21-other",
        current_user_sid="S-1-5-21-current",
        entries=[],
    ) == "windows_owner_untrusted"
    assert windows_directory_security_reason(
        owner_sid="S-1-5-21-current",
        current_user_sid="S-1-5-21-current",
        entries=[(0, 0, 0x40000000, "S-1-5-21-other")],
    ) == "windows_acl_untrusted"
    assert windows_directory_security_reason(
        owner_sid="S-1-5-21-current",
        current_user_sid="S-1-5-21-current",
        entries=[],
        is_reparse_point=True,
    ) == "windows_reparse_escape"
