# SPDX-License-Identifier: MPL-2.0
param(
  [string]$Destination = "",
  [switch]$Clean,
  [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Confirm-Operation([string]$Message) {
  if ($Force -or $env:AUTO_APPROVE -eq "1") { return $true }
  if ($env:NONINTERACTIVE -eq "1" -or -not [string]::IsNullOrWhiteSpace($env:CI) -or -not [Environment]::UserInteractive) { return $false }
  $answer = (Read-Host "$Message [y/N]").Trim().ToLowerInvariant()
  return ($answer -in @("y", "yes", "j", "ja"))
}

function Test-Python([string]$Command, [string[]]$PrefixArgs) {
  try {
    & $Command @($PrefixArgs + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3,12) else 1)")) *> $null
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  }
}

function Select-Python {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($selector in @("-3.13", "-3.14", "-3.12")) {
      if (Test-Python -Command "py" -PrefixArgs @($selector)) {
        return @{ Command = "py"; PrefixArgs = @($selector); Label = "py $selector" }
      }
    }
  }
  foreach ($candidate in @("python3.13", "python3.14", "python3.12", "python")) {
    $resolved = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($resolved -and (Test-Python -Command $resolved.Source -PrefixArgs @())) {
      return @{ Command = $resolved.Source; PrefixArgs = @(); Label = $resolved.Source }
    }
  }
  return $null
}

function Run-SelectedPython($Selection, [string[]]$Arguments) {
  & $Selection.Command @($Selection.PrefixArgs + $Arguments)
}

Set-Location (Join-Path $PSScriptRoot "..")
if ($env:OS -ne "Windows_NT") { throw "This wheelhouse must be prepared on Windows." }

$python = Select-Python
if (-not $python) { throw "Python 3.12+ was not found. Install Python 3.13 (preferred) and retry." }
$pythonTag = ((Run-SelectedPython -Selection $python -Arguments @("-c", "import sys; print(f'py{sys.version_info.major}{sys.version_info.minor}')")) | Select-Object -Last 1).Trim()
if (-not $pythonTag) { throw "Could not determine the selected Python version." }

$wheelhouse = if ($Destination) { $Destination } else { "wheelhouse-v2\windows-$pythonTag" }
if ($Clean -and (Test-Path $wheelhouse)) {
  if (-not (Confirm-Operation -Message "Delete and recreate existing wheelhouse '$wheelhouse'?")) {
    throw "Existing wheelhouse was not deleted. Rerun without -Clean, approve the prompt, or add -Force."
  }
  Remove-Item -Recurse -Force $wheelhouse
}
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

Write-Host "Preparing V2 Windows wheelhouse with $($python.Label) at $wheelhouse" -ForegroundColor Cyan
Run-SelectedPython -Selection $python -Arguments @(
  "-m", "pip", "download",
  "--disable-pip-version-check",
  "--only-binary=:all:",
  "--dest", $wheelhouse,
  "-r", "requirements-v2-release.txt"
)
if ($LASTEXITCODE -ne 0) {
  throw "Wheelhouse download failed. Check DNS/proxy access to PyPI and retry on an internet-connected Windows machine."
}

$wheels = @(Get-ChildItem -Path $wheelhouse -Filter "*.whl" -File)
if ($wheels.Length -eq 0) { throw "No wheels were downloaded to $wheelhouse." }
$manifest = Join-Path $wheelhouse "SHA256SUMS"
Get-FileHash -Algorithm SHA256 -Path $wheels.FullName | ForEach-Object {
  "$($_.Hash.ToLower()) *$([System.IO.Path]::GetFileName($_.Path))"
} | Set-Content -Encoding ascii $manifest

Write-Host "Wheelhouse ready: $($wheels.Length) wheels" -ForegroundColor Green
Write-Host "Copy this directory with the project to the offline Windows build machine: $wheelhouse"
