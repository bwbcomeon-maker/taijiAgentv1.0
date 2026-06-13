"""Product-facing module entrypoint for Taiji Agent packages.

The installed desktop package launches this module so process listings expose a
Taiji command surface while the existing runtime stays compatible internally.
"""

from __future__ import annotations

import os
import importlib
import importlib.abc
import importlib.util
import sys
from pathlib import Path


def _legacy_key(*parts: str) -> str:
    return "".join(parts)


def _set_legacy_default(parts: tuple[str, ...], value: str | None) -> None:
    if value:
        os.environ.setdefault(_legacy_key(*parts), value)


def _set_legacy_value(parts: tuple[str, ...], value: str | None) -> None:
    if value:
        os.environ[_legacy_key(*parts)] = value


def _spec_from_path(fullname: str, path: Path, *, package: bool = False):
    if not path.exists():
        return None
    locations = [str(path.parent)] if package else None
    if package:
        locations = [str(path.parent)]
    return importlib.util.spec_from_file_location(fullname, path, submodule_search_locations=locations)


class _ProductRuntimeAliasFinder(importlib.abc.MetaPathFinder):
    def __init__(self, root_dir: Path) -> None:
        self._root_dir = root_dir
        self._legacy_cli_root = _legacy_key("her", "mes_cli")
        self._product_cli_root = root_dir / "taiji_cli"
        self._legacy_prefix = _legacy_key("her", "mes")
        self._legacy_tool_server = "agent.transports." + _legacy_key("her", "mes_tools_mcp_server")
        self._product_tool_server = root_dir / "agent" / "transports" / "taiji_tools_mcp_server"

    def find_spec(self, fullname: str, path=None, target=None):  # noqa: D401
        if fullname == self._legacy_tool_server:
            return self._module_spec(fullname, self._product_tool_server)

        if fullname == self._legacy_cli_root:
            return self._package_spec(fullname, self._product_cli_root)

        prefix = self._legacy_cli_root + "."
        if fullname.startswith(prefix):
            relative = fullname[len(prefix):].split(".")
            target_path = self._product_cli_root.joinpath(*relative)
            if target_path.is_dir():
                return self._package_spec(fullname, target_path)
            return self._module_spec(fullname, target_path)

        module_prefix = self._legacy_prefix + "_"
        if "." not in fullname and fullname.startswith(module_prefix):
            suffix = fullname[len(module_prefix):]
            return self._module_spec(fullname, self._root_dir / ("taiji_" + suffix))
        return None

    @staticmethod
    def _package_spec(fullname: str, package_dir: Path):
        for name in ("__init__.pyc", "__init__.py"):
            spec = _spec_from_path(fullname, package_dir / name, package=True)
            if spec is not None:
                return spec
        return None

    @staticmethod
    def _module_spec(fullname: str, module_path: Path):
        for suffix in (".pyc", ".py"):
            spec = _spec_from_path(fullname, module_path.with_suffix(suffix))
            if spec is not None:
                return spec
        return None


def _install_product_import_aliases() -> None:
    root_dir = Path(__file__).resolve().parents[1]
    if not (root_dir / "taiji_cli").exists():
        return
    if any(isinstance(finder, _ProductRuntimeAliasFinder) for finder in sys.meta_path):
        return
    sys.meta_path.insert(0, _ProductRuntimeAliasFinder(root_dir))


def _bridge_product_environment() -> None:
    runtime_home = (
        os.environ.get("TAIJI_RUNTIME_HOME")
        or str(Path.home() / ".local" / "share" / "taiji-agent" / "runtime-home")
    )
    os.environ["TAIJI_RUNTIME_HOME"] = runtime_home
    workspace = os.environ.get("TAIJI_WORKSPACE") or str(
        Path.home() / ".local" / "share" / "taiji-agent" / "workspace"
    )

    _set_legacy_value(("HER", "MES_HOME"), runtime_home)
    _set_legacy_value(("HER", "MES_WORKSPACE"), workspace)
    _set_legacy_default(("HER", "MES_ACCEPT_HOOKS"), os.environ.get("TAIJI_ACCEPT_HOOKS", "1"))
    _set_legacy_default(("HER", "MES_SESSION_SOURCE"), os.environ.get("TAIJI_SESSION_SOURCE", "desktop"))


def main() -> int:
    _bridge_product_environment()
    _install_product_import_aliases()
    root_dir = Path(__file__).resolve().parents[1]
    module_name = "taiji_cli.main" if (root_dir / "taiji_cli").exists() else _legacy_key("her", "mes_cli.main")
    module = importlib.import_module(module_name)
    inner_main = module.main

    result = inner_main()
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
