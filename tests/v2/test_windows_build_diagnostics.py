# SPDX-License-Identifier: MPL-2.0
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/build-v2-windows.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(not POWERSHELL, reason="PowerShell is required for build-script execution tests")


def run_powershell(code, *, environment=None):
    return subprocess.run(
        [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", code],
        env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )


def load_build_functions():
    path = str(SCRIPT).replace("'", "''")
    return """
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$tokens = $null; $parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('%s', [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
foreach ($function in $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
  . ([scriptblock]::Create($function.Extent.Text))
}
""" % path


@pytest.mark.parametrize("invalid_setting", [False, True])
def test_windows_build_logs_early_failures_and_does_not_dump_credentials(tmp_path, invalid_setting):
    environment = {**os.environ, "OS": "NotWindows", "PRODUCTION_RELEASE": "bad" if invalid_setting else "1",
                   "RELEASE_SIGNING": "unsigned", "V2_BUILD_LOG_DIR": str(tmp_path),
                   "WINDOWS_SIGN_PFX_PASSWORD": "test-only-private-value"}
    result = subprocess.run([POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(SCRIPT)],
                            env=environment, capture_output=True, text=True, errors="replace", timeout=60)
    assert result.returncode != 0
    logs = list(tmp_path.glob("windows-*/build.log"))
    assert len(logs) == 1
    log = logs[0].read_text(encoding="utf-8-sig")
    assert "BUILD FAILED during: Initialization" in log
    expected_error = "PRODUCTION_RELEASE must be" if invalid_setting else "This script must run on Windows"
    assert expected_error in log
    assert "build-v2-windows.ps1" in log and "PowerShell:" in log
    assert "test-only-private-value" not in log
    assert "Build log closed:" in log


def test_windows_build_logs_native_stdout_stderr_failure_and_resets_stale_exit_status(tmp_path):
    python = sys.executable.replace("'", "''")
    code = load_build_functions() + r"""
Start-BuildDiagnostics -Root '%s'
try {
  $global:LASTEXITCODE = 23
  Invoke-External -Description 'PowerShell-only operation' -Command { Write-Output 'archive step' }
  try {
    Invoke-External -Description 'Failing native operation' -Command {
      & '%s' -c "import sys; print('native stdout'); print('native stderr', file=sys.stderr); sys.exit(7)"
    }
    throw 'A failed native command was accepted'
  } catch {
    if ($_.Exception.Message -notmatch 'Failing native operation failed \(exit code 7\)') { throw }
    Write-Host $_.Exception.Message
  }
} finally { Stop-Transcript | Out-Null }
""" % (str(tmp_path).replace("'", "''"), python)
    result = run_powershell(code, environment={**os.environ, "V2_BUILD_LOG_DIR": str(tmp_path)})
    assert result.returncode == 0, result.stdout + result.stderr
    log = next(tmp_path.glob("windows-*/build.log")).read_text(encoding="utf-8-sig")
    for expected in ("archive step", "native stdout", "native stderr", "exit code 7"):
        assert expected in log
