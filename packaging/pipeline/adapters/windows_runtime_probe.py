"""Lightweight, read-only executable checks before expensive cache observation."""

import base64
import json
import re

from ..core.errors import PipelineError


RUNTIME_SCHEMA = "taiji-windows-runtime-probe/v1"
PYTHON_IMPORTS = ("aiohttp", "fastapi", "uvicorn", "yaml", "cryptography", "psutil", "pypdf",
                  "win32api", "win32profile", "win32security", "win32file")


def runtime_probe_script(target):
    defaults = {
        "node": r"C:\Program Files\nodejs\node.exe",
        "npm": r"C:\Program Files\nodejs\npm.cmd",
        "python": r"D:\tw\cache\python-runtime\python.exe",
        "iscc": r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    }
    paths = "\n".join(
        "$path_{} = '{}'".format(name, str(target.get(name, path)).replace("'", "''"))
        for name, path in defaults.items()
    )
    python_code = """import importlib, json, struct, sys
imports = {}
for name in %r:
    try:
        importlib.import_module(name)
        imports[name] = True
    except Exception:
        imports[name] = False
print(json.dumps(dict(version=list(sys.version_info[:3]), bits=struct.calcsize('P') * 8, imports=imports)))
""" % (PYTHON_IMPORTS,)
    encoded = base64.b64encode(python_code.encode("utf-8")).decode("ascii")
    # No embedded double quotes: Windows PowerShell 5.1 removes them in native argv.
    loader = "exec(__import__('base64').b64decode('{}'))".format(encoded)
    return paths + "\n" + r"""
function Invoke-RuntimeProbe {
  param([string]$Path, [string[]]$Arguments)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    return [ordered]@{ exit_code = -1; output = 'missing executable' }
  }
  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = $null
    $captured = @(& $Path @Arguments 2>&1)
    $code = $global:LASTEXITCODE
    return [ordered]@{ exit_code = $code; output = ($captured -join "`n").Trim() }
  } catch {
    return [ordered]@{ exit_code = -1; output = $_.Exception.Message }
  } finally {
    $ErrorActionPreference = $previousPreference
  }
}

$checks = [ordered]@{
  schema = 'taiji-windows-runtime-probe/v1'
  node = Invoke-RuntimeProbe -Path $path_node -Arguments @('-p', '[process.version,process.arch].join()')
  npm = Invoke-RuntimeProbe -Path $path_npm -Arguments @('--version')
  python = Invoke-RuntimeProbe -Path $path_python -Arguments @('-I', '-B', '-c', '__PYTHON_LOADER__')
  iscc = Invoke-RuntimeProbe -Path $path_iscc -Arguments @('/?')
}
$checks | ConvertTo-Json -Depth 8 -Compress
""".replace("__PYTHON_LOADER__", loader.replace("'", "''"))


def parse_runtime_probe(payload):
    def blocked(message):
        raise PipelineError(message, category="WINDOWS_RUNTIME_NOT_READY")

    try:
        result = json.loads(payload)
    except (ValueError, TypeError, UnicodeError):
        blocked("Windows runtime probe did not return valid JSON")
    tools = ("node", "npm", "python", "iscc")
    if not isinstance(result, dict) or set(result) != {"schema", *tools} or result["schema"] != RUNTIME_SCHEMA:
        blocked("Windows runtime probe fields are invalid")
    failures = []
    for name in tools:
        check = result[name]
        inno_help = (
            name == "iscc" and isinstance(check, dict)
            and check.get("exit_code") == 1
            and isinstance(check.get("output"), str)
            and re.search(r"Inno Setup (?:6|7)\b", check["output"]) is not None
            and "Usage:" in check["output"] and "iscc [options]" in check["output"]
        )
        if (
            not isinstance(check, dict) or set(check) != {"exit_code", "output"}
            or type(check["exit_code"]) is not int or (check["exit_code"] != 0 and not inno_help)
            or not isinstance(check["output"], str) or not check["output"].strip()
        ):
            failures.append("{} executable probe failed".format(name))
    if failures:
        blocked("; ".join(failures))
    node = result["node"]["output"].strip()
    if re.fullmatch(r"v(?:22|24)\.\d+\.\d+,x64", node) is None:
        failures.append("Node must be 22/24 x64; observed {}".format(node[:100]))
    if re.fullmatch(r"\d+\.\d+\.\d+", result["npm"]["output"].strip()) is None:
        failures.append("npm version probe failed")
    if re.search(r"Inno Setup (?:6|7)\b", result["iscc"]["output"]) is None:
        failures.append("Inno Setup 6/7 compiler probe failed")
    try:
        python = json.loads(result["python"]["output"])
    except (ValueError, TypeError):
        python = None
    if not isinstance(python, dict) or set(python) != {"version", "bits", "imports"}:
        failures.append("private Python facts are invalid")
    else:
        version = python["version"]
        if not isinstance(version, list) or len(version) != 3 or version[:2] != [3, 11] or python["bits"] != 64:
            failures.append("private Python must be 3.11 x64")
        imports = python["imports"]
        missing = [name for name in PYTHON_IMPORTS if not isinstance(imports, dict) or imports.get(name) is not True]
        if missing:
            failures.append("private Python imports failed: {}".format(", ".join(missing)))
    if failures:
        blocked("; ".join(failures))
    return result
