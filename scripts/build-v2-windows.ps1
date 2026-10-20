# SPDX-License-Identifier: MPL-2.0
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProductionRelease = if ([string]::IsNullOrWhiteSpace($env:PRODUCTION_RELEASE)) {
  $true
} elseif ($env:PRODUCTION_RELEASE -in @("0", "1")) {
  $env:PRODUCTION_RELEASE -eq "1"
} else {
  throw "PRODUCTION_RELEASE must be '0' or '1'."
}
$ReleaseSigning = if ([string]::IsNullOrWhiteSpace($env:RELEASE_SIGNING)) { "unsigned" } else { $env:RELEASE_SIGNING.Trim().ToLowerInvariant() }
if ($ReleaseSigning -notin @("unsigned", "signed")) { throw "RELEASE_SIGNING must be 'unsigned' or 'signed'." }
$SignedRelease = ($ReleaseSigning -eq "signed")

function Write-Step([string]$Message) {
  Write-Host ""
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Test-NonInteractive {
  return (
    $env:NONINTERACTIVE -eq "1" -or
    -not [string]::IsNullOrWhiteSpace($env:CI) -or
    -not [Environment]::UserInteractive
  )
}

function Confirm-Operation {
  param(
    [Parameter(Mandatory = $true)][string]$Message,
    [bool]$DefaultYes = $true
  )
  if ($env:AUTO_APPROVE -eq "1") {
    Write-Host "AUTO_APPROVE=1: $Message" -ForegroundColor DarkGray
    return $true
  }
  if (Test-NonInteractive) { return $false }
  $choice = if ($DefaultYes) { "[Y/n]" } else { "[y/N]" }
  while ($true) {
    $answer = (Read-Host "$Message $choice").Trim().ToLowerInvariant()
    if (-not $answer) { return $DefaultYes }
    if ($answer -in @("y", "yes", "j", "ja")) { return $true }
    if ($answer -in @("n", "no", "nein")) { return $false }
    Write-Host "Please answer yes or no." -ForegroundColor Yellow
  }
}

function Assert-ReleaseSource([string]$Root) {
  $physicalRoot = [System.IO.Path]::GetFullPath($Root)
  if ($physicalRoot -match '(?i)[\\/](\$Recycle\.Bin|Recycler|Trash)[\\/]') {
    throw "The release script is running from a recycle-bin/trash path: $physicalRoot. Restore or freshly extract the project, open a new PowerShell window in that directory, and retry."
  }
  $required = @(
    "requirements-v2-release.txt",
    "scripts\prebuild_v2.py",
    "scripts\linux-v2-runtime-launcher.sh",
    "ship-simulator-v2.spec",
    "src\maritime_simulator\__init__.py"
  )
  foreach ($relative in $required) {
    if (-not (Test-Path (Join-Path $physicalRoot $relative))) {
      throw "Incomplete or stale V2 release source: missing '$relative'. Extract a complete current source package and retry."
    }
  }
  $launcher = Get-Content -Raw (Join-Path $physicalRoot "scripts\linux-v2-runtime-launcher.sh")
  if ($launcher -notmatch "required Ubuntu system libraries") {
    throw "Stale V2 release source detected: the Linux runtime launcher does not contain the required dependency diagnostic. Use the current complete source package."
  }
}

function Remove-ReleaseArtifact([string]$Path) {
  if (-not (Test-Path $Path)) { return }
  if (-not (Confirm-Operation -Message "Release artifact already exists. Replace '$Path'?" -DefaultYes $true)) {
    throw "Existing release artifact was not replaced: $Path"
  }
  Remove-Item -Force $Path
}

function Invoke-External {
  param(
    [Parameter(Mandatory = $true)][string]$Description,
    [Parameter(Mandatory = $true)][scriptblock]$Command
  )
  Write-Step $Description
  & $Command
  if ($LASTEXITCODE -ne 0) {
    throw "$Description failed (exit code $LASTEXITCODE)."
  }
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
  $localCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python314\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe")
  )
  foreach ($candidate in @("python3.13", "python3.14", "python3.12", "python") + $localCandidates) {
    if (-not $candidate) { continue }
    $resolved = Get-Command $candidate -ErrorAction SilentlyContinue
    $command = if ($resolved) { $resolved.Source } elseif (Test-Path $candidate) { $candidate } else { $null }
    if ($command -and (Test-Python -Command $command -PrefixArgs @())) {
      return @{ Command = $command; PrefixArgs = @(); Label = $command }
    }
  }
  return $null
}

function Run-SelectedPython($Selection, [string[]]$Arguments) {
  & $Selection.Command @($Selection.PrefixArgs + $Arguments)
}

function Test-V2Dependencies([string]$PythonPath) {
  try {
    & $PythonPath -c "from importlib.metadata import version; from pathlib import Path; expected=dict(line.split('==', 1) for line in Path('requirements-v2-release.txt').read_text(encoding='utf-8').splitlines() if line and not line.startswith('#')); raise SystemExit(0 if all(version(name) == wanted for name, wanted in expected.items()) else 1)" *> $null
    if ($LASTEXITCODE -ne 0) { return $false }
    & $PythonPath -m pip check *> $null
    return ($LASTEXITCODE -eq 0)
  } catch {
    return $false
  }
}

function Test-PyPiDns {
  try {
    $addresses = @([System.Net.Dns]::GetHostAddresses("pypi.org"))
    return ($addresses.Length -gt 0)
  } catch {
    return $false
  }
}

function Find-Wheelhouse([string]$PythonTag) {
  $candidates = @()
  if ($env:V2_WHEELHOUSE) { $candidates += $env:V2_WHEELHOUSE }
  $candidates += (Join-Path (Get-Location) "wheelhouse-v2\windows-$PythonTag")
  $candidates += (Join-Path (Get-Location) "wheelhouse-v2\windows")
  foreach ($candidate in $candidates) {
    if (-not $candidate -or -not (Test-Path $candidate -PathType Container)) { continue }
    $wheels = @(Get-ChildItem -Path $candidate -Filter "*.whl" -File -ErrorAction SilentlyContinue)
    if ($wheels.Length -gt 0) { return (Resolve-Path $candidate).Path }
  }
  return $null
}

function Clear-ReadonlyTree([string]$Path) {
  if (Test-Path $Path) {
    cmd.exe /c "attrib -R -S -H `"$Path\*`" /S /D" *> $null
  }
}

function Remove-PathRobust([string]$Path) {
  if (-not (Test-Path $Path)) { return }
  for ($attempt = 1; $attempt -le 4; $attempt++) {
    try {
      Clear-ReadonlyTree -Path $Path
      Remove-Item -Recurse -Force $Path -ErrorAction Stop
      return
    } catch {
      if ($attempt -eq 4) { throw }
      Start-Sleep -Milliseconds (250 * $attempt)
    }
  }
}

function Register-InnoCompiler([string]$Path) {
  if (-not $Path -or -not (Test-Path $Path -PathType Leaf)) { return $null }
  $resolved = (Resolve-Path $Path).Path
  $directory = Split-Path -Parent $resolved
  $pathEntries = @($env:Path -split ";" | Where-Object { $_ })
  $alreadyRegistered = $pathEntries | Where-Object {
    [string]::Equals($_.TrimEnd([char]92), $directory.TrimEnd([char]92), [System.StringComparison]::OrdinalIgnoreCase)
  }
  if (-not $alreadyRegistered) { $env:Path = "$directory;$env:Path" }
  return $resolved
}

function Get-ExecutablePathFromRegistryValue([string]$Value) {
  if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
  $text = $Value.Trim()
  if ($text.StartsWith('"')) {
    $closingQuote = $text.IndexOf('"', 1)
    if ($closingQuote -gt 1) { return $text.Substring(1, $closingQuote - 1) }
  }
  $match = [regex]::Match($text, '^(?<path>.+?\.exe)(?:\s|,|$)', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
  if ($match.Success) { return $match.Groups['path'].Value.Trim('"') }
  return $text.Trim('"')
}

function Find-InnoCompiler {
  if ($env:INNO_ISCC) {
    $override = $env:INNO_ISCC.Trim().Trim('"')
    if (Test-Path $override -PathType Container) { $override = Join-Path $override "ISCC.exe" }
    $resolvedOverride = Register-InnoCompiler -Path $override
    if ($resolvedOverride) { return $resolvedOverride }
    Write-Warning "INNO_ISCC does not point to a usable ISCC.exe: $override"
  }

  $pathCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
  if ($pathCommand) { return (Register-InnoCompiler -Path $pathCommand.Source) }

  $candidates = @()
  if ($env:LOCALAPPDATA) {
    $candidates += Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
    $candidates += Join-Path $env:LOCALAPPDATA "Inno Setup 6\ISCC.exe"
  }
  if (${env:ProgramFiles(x86)}) {
    $candidates += Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
  }
  if ($env:ProgramFiles) {
    $candidates += Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"
  }
  if ($env:USERPROFILE) {
    $candidates += Join-Path $env:USERPROFILE "scoop\apps\innosetup\current\ISCC.exe"
  }

  $registryRoots = @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall",
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
  )
  foreach ($root in $registryRoots) {
    if (-not (Test-Path $root)) { continue }
    foreach ($key in @(Get-ChildItem -Path $root -ErrorAction SilentlyContinue)) {
      try { $entry = Get-ItemProperty -Path $key.PSPath -ErrorAction Stop } catch { continue }
      $displayNameProperty = $entry.PSObject.Properties["DisplayName"]
      if (-not $displayNameProperty -or [string]$displayNameProperty.Value -notlike "Inno Setup 6*") { continue }
      $installLocationProperty = $entry.PSObject.Properties["InstallLocation"]
      if ($installLocationProperty -and $installLocationProperty.Value) {
        $candidates += Join-Path ([string]$installLocationProperty.Value) "ISCC.exe"
      }
      foreach ($propertyName in @("DisplayIcon", "UninstallString")) {
        $property = $entry.PSObject.Properties[$propertyName]
        if (-not $property -or -not $property.Value) { continue }
        $registeredExecutable = Get-ExecutablePathFromRegistryValue -Value ([string]$property.Value)
        if ($registeredExecutable) {
          $registeredDirectory = Split-Path -Parent $registeredExecutable
          if ($registeredDirectory) { $candidates += Join-Path $registeredDirectory "ISCC.exe" }
        }
      }
    }
  }

  if ($env:LOCALAPPDATA) {
    $wingetPackages = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path $wingetPackages -PathType Container) {
      foreach ($package in @(Get-ChildItem -Path $wingetPackages -Directory -Filter "JRSoftware.InnoSetup*" -ErrorAction SilentlyContinue)) {
        $compiler = Get-ChildItem -Path $package.FullName -File -Filter "ISCC.exe" -Recurse -ErrorAction SilentlyContinue |
          Select-Object -First 1
        if ($compiler) { $candidates += $compiler.FullName }
      }
    }
  }

  foreach ($candidate in @($candidates | Select-Object -Unique)) {
    $resolved = Register-InnoCompiler -Path $candidate
    if ($resolved) { return $resolved }
  }

  # A per-user or WinGet installation can use a versioned/custom subdirectory
  # and omit InstallLocation. Search only the bounded application roots after
  # all cheap/direct probes have failed.
  $searchRoots = @()
  if ($env:LOCALAPPDATA) {
    $searchRoots += Join-Path $env:LOCALAPPDATA "Programs"
    $searchRoots += Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
  }
  if ($env:USERPROFILE) {
    $searchRoots += Join-Path $env:USERPROFILE "scoop\apps\innosetup"
  }
  foreach ($searchRoot in @($searchRoots | Select-Object -Unique)) {
    if (-not (Test-Path $searchRoot -PathType Container)) { continue }
    $compiler = Get-ChildItem -Path $searchRoot -File -Filter "ISCC.exe" -Recurse -ErrorAction SilentlyContinue |
      Where-Object { $_.FullName -match '(?i)(inno|JRSoftware\.InnoSetup)' } |
      Select-Object -First 1
    if ($compiler) {
      $resolved = Register-InnoCompiler -Path $compiler.FullName
      if ($resolved) { return $resolved }
    }
  }
  return $null
}

function Request-InnoCompilerPath {
  if (Test-NonInteractive) { return $null }
  $manualPath = (Read-Host "Enter the full path to ISCC.exe (or its Inno Setup 6 folder), or press Enter to cancel").Trim().Trim('"')
  if (-not $manualPath) { return $null }
  if (Test-Path $manualPath -PathType Container) { $manualPath = Join-Path $manualPath "ISCC.exe" }
  $resolved = Register-InnoCompiler -Path $manualPath
  if (-not $resolved) {
    Write-Warning "No usable ISCC.exe was found at '$manualPath'."
    return $null
  }
  $env:INNO_ISCC = $resolved
  return $resolved
}

function Find-SignTool {
  $pathCommand = Get-Command signtool.exe -ErrorAction SilentlyContinue
  if ($pathCommand) { return $pathCommand.Source }
  $roots = @(
    (Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"),
    (Join-Path $env:ProgramFiles "Windows Kits\10\bin")
  ) | Where-Object { $_ -and (Test-Path $_) }
  $matches = @(
    foreach ($root in $roots) {
      Get-ChildItem -Path $root -Filter "signtool.exe" -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '(?i)\\x64\\signtool\.exe$' }
    }
  )
  if ($matches.Length -gt 0) {
    return ($matches | Sort-Object FullName -Descending | Select-Object -First 1).FullName
  }
  return $null
}

function Assert-WindowsSigningCredentials {
  if ($env:WINDOWS_SIGN_CERT_SHA1) { return }
  if ($env:WINDOWS_SIGN_PFX -and $env:WINDOWS_SIGN_PFX_PASSWORD) { return }
  throw @"
RELEASE_SIGNING=signed requires one of:
 - WINDOWS_SIGN_CERT_SHA1 for a certificate in the Windows certificate store
 - WINDOWS_SIGN_PFX and WINDOWS_SIGN_PFX_PASSWORD for a PFX file
"@
}

function Sign-WindowsArtifact {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$SignTool
  )
  if (-not (Test-Path $Path -PathType Leaf)) { throw "Cannot sign missing file: $Path" }
  $timestampUrl = if ($env:WINDOWS_TIMESTAMP_URL) { $env:WINDOWS_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
  Write-Host "Signing $Path" -ForegroundColor DarkGray
  if ($env:WINDOWS_SIGN_CERT_SHA1) {
    & $SignTool sign /fd SHA256 /td SHA256 /tr $timestampUrl /sha1 $env:WINDOWS_SIGN_CERT_SHA1 $Path
  } else {
    & $SignTool sign /fd SHA256 /td SHA256 /tr $timestampUrl /f $env:WINDOWS_SIGN_PFX /p $env:WINDOWS_SIGN_PFX_PASSWORD $Path
  }
  if ($LASTEXITCODE -ne 0) { throw "Authenticode signing failed for $Path." }
  & $SignTool verify /pa /all /v $Path
  if ($LASTEXITCODE -ne 0) { throw "Authenticode verification failed for $Path." }
}

Set-Location (Join-Path $PSScriptRoot "..")
if ($env:OS -ne "Windows_NT") { throw "This script must run on Windows 11/Windows." }
Assert-ReleaseSource -Root (Get-Location).Path

$python = Select-Python
if (-not $python -and ($env:AUTO_INSTALL_TOOLS -ne "0") -and (Get-Command winget -ErrorAction SilentlyContinue)) {
  if (Confirm-Operation -Message "Python 3.12+ is missing. Install Python 3.13 with winget now?" -DefaultYes $true) {
    Write-Step "Installing Python 3.13 through winget"
    & winget install --id Python.Python.3.13 -e --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Write-Warning "Automatic Python installation failed; checking again for another interpreter." }
    $python = Select-Python
  }
}
if (-not $python) {
  throw "Python 3.12+ was not found. Install Python 3.13 (preferred) or 3.14, then rerun this script."
}

Write-Step "Using interpreter: $($python.Label)"
Run-SelectedPython -Selection $python -Arguments @("-c", "import sys; print(sys.version)")
if ($LASTEXITCODE -ne 0) { throw "Selected Python could not be started." }
$pythonTag = ((Run-SelectedPython -Selection $python -Arguments @("-c", "import sys; print(f'py{sys.version_info.major}{sys.version_info.minor}')")) | Select-Object -Last 1).Trim()
$selectedMinor = ((Run-SelectedPython -Selection $python -Arguments @("-c", "import sys; print(sys.version_info.minor)")) | Select-Object -Last 1).Trim()
if (-not $pythonTag -or -not $selectedMinor) { throw "Could not determine the selected Python version." }

$venvDir = if ($env:V2_VENV_DIR) { $env:V2_VENV_DIR } else { "venv-v2-windows" }
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvHealthy = $false
if (Test-Path $venvPython) {
  try {
    & $venvPython -c "import sys; expected='$pythonTag'; actual=f'py{sys.version_info.major}{sys.version_info.minor}'; raise SystemExit(0 if sys.version_info >= (3,12) and actual == expected else 1)" *> $null
    $venvHealthy = ($LASTEXITCODE -eq 0)
  } catch { $venvHealthy = $false }
}

if (-not $venvHealthy) {
  if (Test-Path $venvDir) {
    if (-not (Confirm-Operation -Message "The V2 virtual environment '$venvDir' is damaged, foreign, or uses another Python version. Delete and recreate it?" -DefaultYes $true)) {
      throw "A usable V2 virtual environment is required. Move it manually or approve recreation."
    }
  }
  Write-Step "Replacing missing, foreign or damaged V2 virtual environment"
  Remove-PathRobust -Path $venvDir
  $venvArguments = @("-m", "venv")
  if ([int]$selectedMinor -ge 13) { $venvArguments += "--without-scm-ignore-files" }
  $venvArguments += $venvDir
  Run-SelectedPython -Selection $python -Arguments $venvArguments
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
    throw "Virtual environment creation failed. Check write permissions for '$venvDir'."
  }
}

$env:PIP_NO_CACHE_DIR = "1"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:PYTEST_ADDOPTS = "-p no:cacheprovider"
$requirementsFile = Join-Path (Get-Location) "requirements-v2-release.txt"
if (-not (Test-Path $requirementsFile)) { throw "Missing V2 release requirements: $requirementsFile" }

if (Test-V2Dependencies -PythonPath $venvPython) {
  Write-Step "Using the complete dependency set already installed in the V2 environment"
} elseif ($env:SKIP_DEPENDENCY_INSTALL -eq "1") {
  throw "SKIP_DEPENDENCY_INSTALL=1 was requested, but PySide6, PyInstaller or pytest is missing from '$venvDir'."
} else {
  $wheelhouse = Find-Wheelhouse -PythonTag $pythonTag
  $pypiAvailable = $true
  if (-not $wheelhouse) {
    $pypiAvailable = Test-PyPiDns
    if (-not $pypiAvailable -and -not (Test-NonInteractive)) {
      $manualWheelhouse = (Read-Host "PyPI DNS is unavailable. Enter a local $pythonTag wheelhouse directory, or press Enter to cancel").Trim()
      if ($manualWheelhouse) {
        $env:V2_WHEELHOUSE = $manualWheelhouse
        $wheelhouse = Find-Wheelhouse -PythonTag $pythonTag
        if (-not $wheelhouse) { throw "No .whl files were found in '$manualWheelhouse'." }
      }
    }
  }
  if ($wheelhouse) {
    Write-Step "Installing pinned V2 dependencies from offline wheelhouse: $wheelhouse"
    & $venvPython -m pip install --disable-pip-version-check --no-index --find-links $wheelhouse --upgrade-strategy only-if-needed -r $requirementsFile
    if ($LASTEXITCODE -ne 0) {
      throw "Offline dependency installation failed. Recreate the $pythonTag wheelhouse with scripts\prepare-v2-windows-wheelhouse.ps1 on an internet-connected Windows machine."
    }
  } else {
    if (-not $pypiAvailable) {
      throw @"
PyPI cannot be reached because pypi.org cannot be resolved. This is a Windows DNS/network problem, not an incompatible 'black' package.

For an offline build, run this once on an internet-connected Windows machine using the same Python minor version:
  .\scripts\prepare-v2-windows-wheelhouse.ps1

Then copy the generated 'wheelhouse-v2\windows-$pythonTag' directory with the project and rerun this build script.
"@
    }
    Write-Step "Installing pinned V2 release dependencies from PyPI"
    & $venvPython -m pip install --disable-pip-version-check --no-cache-dir --retries 1 --upgrade-strategy only-if-needed -r $requirementsFile
    if ($LASTEXITCODE -ne 0) {
      throw @"
V2 dependency installation failed. Check DNS, proxy/firewall and PyPI access. To build without internet, prepare and copy a local wheelhouse with scripts\prepare-v2-windows-wheelhouse.ps1.
"@
    }
  }
}
if (-not (Test-V2Dependencies -PythonPath $venvPython)) {
  throw "The V2 dependency set is incomplete after installation; PySide6, PyInstaller and pytest are required."
}

$version = (& $venvPython -c "import sys; sys.path.insert(0, 'src'); from maritime_simulator import __version__; print(__version__)").Trim()
if ($version -match '-rc') {
  $ProductionRelease = $false
  $env:PRODUCTION_RELEASE = '0'
}
if (-not $version) { throw "Could not read the V2 version." }
if ($version -notmatch '-' -and -not $ProductionRelease) {
  throw "Final version $version requires PRODUCTION_RELEASE=1. Run the documented unsigned production command, or explicitly configure signed release credentials."
}
if ($SignedRelease -and -not $ProductionRelease) { throw "RELEASE_SIGNING=signed requires PRODUCTION_RELEASE=1." }
$signTool = $null
if ($ProductionRelease) {
  if ($env:SKIP_INNO -eq "1") { throw "A production Windows release requires the Inno Setup installer; SKIP_INNO=1 is not permitted." }
}
if ($SignedRelease) {
  Assert-WindowsSigningCredentials
  $signTool = Find-SignTool
  if (-not $signTool) {
    throw "RELEASE_SIGNING=signed requires signtool.exe from the Windows SDK. Install the Windows 10/11 SDK and retry."
  }
  Write-Step "Production signing prerequisites verified"
  Write-Host "SignTool: $signTool"
} elseif ($ProductionRelease) {
  Write-Warning "Building an unsigned production release. Windows SmartScreen may require explicit user approval."
}

Invoke-External -Description "Running the complete V2 release gate" -Command { & $venvPython scripts\prebuild_v2.py --profile full }

$workRoot = Join-Path $env:TEMP "MaritimeShipSimulatorV2Build-$PID"
Remove-PathRobust -Path $workRoot
New-Item -ItemType Directory -Force -Path $workRoot | Out-Null
$env:MARITIME_SIMULATOR_V2_DIST_DIR = Join-Path $workRoot "dist-v2"
$env:MARITIME_SIMULATOR_V2_BUILD_DIR = Join-Path $workRoot "build-v2"

Invoke-External -Description "Building V2 with PyInstaller in an isolated directory" -Command { & $venvPython build_v2.py }

$distDir = Join-Path $env:MARITIME_SIMULATOR_V2_DIST_DIR "Ship Simulator V2"
$distExe = Join-Path $distDir "Ship Simulator V2.exe"
if (-not (Test-Path $distExe)) { throw "Expected executable missing: $distExe" }
if (-not (Test-Path (Join-Path $distDir "_internal\assets\app_icon.ico"))) { throw "Bundled Windows icon is missing." }
Invoke-External -Description "Auditing packaged application for embedded credentials" -Command {
  & $venvPython scripts\check_v2_release_credentials.py $distDir
}

if ($SignedRelease) {
  Write-Step "Signing bundled Windows executables"
  $bundledExecutables = @(Get-ChildItem -Path $distDir -Filter "*.exe" -File -Recurse)
  if ($bundledExecutables.Length -eq 0) { throw "No Windows executables were found for production signing." }
  foreach ($executable in $bundledExecutables) {
    Sign-WindowsArtifact -Path $executable.FullName -SignTool $signTool
  }
}

Write-Step "Running packaged offscreen startup smoke"
$env:QT_QPA_PLATFORM = "offscreen"
$env:SHIP_SIMULATOR_HEADLESS_MAP = "1"
$env:SHIP_SIMULATOR_S57_SMOKE_CELL = Join-Path (Get-Location) "tests\fixtures\s57\US4NJ1DG.zip"
$smoke = Start-Process -FilePath $distExe -ArgumentList "--smoke-test" -Wait -PassThru
if ($smoke.ExitCode -ne 0) { throw "Packaged application smoke failed (exit $($smoke.ExitCode))." }
$dialogSmoke = Start-Process -FilePath $distExe -ArgumentList "--dialog-smoke-test" -Wait -PassThru
if ($dialogSmoke.ExitCode -ne 0) { throw "Packaged dialog smoke failed (exit $($dialogSmoke.ExitCode))." }

$releaseDir = Join-Path (Get-Location) "release-v2\windows"
New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
$portable = Join-Path $releaseDir "Maritime-Ship-Simulator-V2-$version-windows-portable.zip"
Remove-ReleaseArtifact -Path $portable
Invoke-External -Description "Creating relocatable portable ZIP" -Command { Compress-Archive -Path $distDir -DestinationPath $portable -CompressionLevel Optimal }
if (-not (Test-Path $portable)) { throw "Portable ZIP was not created." }
Invoke-External -Description "Auditing portable archive for embedded credentials" -Command {
  & $venvPython scripts\check_v2_release_credentials.py $portable
}

$portableSmokeRoot = Join-Path $workRoot "Portable Smoke"
Remove-PathRobust -Path $portableSmokeRoot
New-Item -ItemType Directory -Force -Path $portableSmokeRoot | Out-Null
Invoke-External -Description "Extracting and testing the portable ZIP from a fresh path" -Command {
  Expand-Archive -Path $portable -DestinationPath $portableSmokeRoot -Force
}
$portableSmokeExecutables = @(
  Get-ChildItem -Path $portableSmokeRoot -Filter "Ship Simulator V2.exe" -File -Recurse -ErrorAction SilentlyContinue
)
if ($portableSmokeExecutables.Length -ne 1) {
  throw "Portable ZIP validation expected exactly one Ship Simulator V2.exe, found $($portableSmokeExecutables.Length)."
}
$portableSmoke = Start-Process -FilePath $portableSmokeExecutables[0].FullName -ArgumentList "--smoke-test" -Wait -PassThru
if ($portableSmoke.ExitCode -ne 0) {
  throw "Extracted portable application smoke failed (exit $($portableSmoke.ExitCode))."
}

$installer = $null
if ($env:SKIP_INNO -ne "1") {
  $iscc = Find-InnoCompiler
  if (-not $iscc -and ($env:AUTO_INSTALL_TOOLS -ne "0") -and (Get-Command winget -ErrorAction SilentlyContinue)) {
    if (Confirm-Operation -Message "Inno Setup 6 is missing. Install it with winget now?" -DefaultYes $true) {
      Write-Step "Installing Inno Setup through winget"
      & winget install --id JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements
      $wingetExitCode = $LASTEXITCODE
      $iscc = Find-InnoCompiler
      if (-not $iscc -and $wingetExitCode -ne 0) {
        Write-Warning "winget returned exit code $wingetExitCode and ISCC.exe still could not be located."
      } elseif (-not $iscc) {
        Write-Warning "winget reports Inno Setup as installed, but its compiler path is not registered. Enter the existing ISCC.exe path when prompted."
      } elseif ($iscc -and $wingetExitCode -ne 0) {
        Write-Host "winget reported a non-zero result, but the existing Inno Setup compiler was located successfully." -ForegroundColor DarkGray
      }
    }
  }
  if (-not $iscc) { $iscc = Request-InnoCompilerPath }
  if (-not $iscc) {
    if ($ProductionRelease) {
      throw @"
A production Windows release requires Inno Setup 6, but ISCC.exe could not be located.
The script checks PATH, per-user and system installations, the uninstall registry and WinGet packages.
Set INNO_ISCC to the full ISCC.exe path, or rerun interactively and enter the path when prompted.
"@
    }
    if (Confirm-Operation -Message "Inno Setup is unavailable. Continue with the portable ZIP only?" -DefaultYes $false) {
      Write-Warning "Continuing without the Windows installer."
    } else {
      throw "Inno Setup 6 was not found. Install it or set SKIP_INNO=1 for an intentional portable-only build."
    }
  }
  if ($iscc) {
    Write-Host "Using Inno Setup compiler: $iscc" -ForegroundColor DarkGray
    $issPath = Join-Path $PSScriptRoot "windows-v2-installer.iss"
    $outputName = "Maritime-Ship-Simulator-V2-$version-windows-setup"
    $installer = Join-Path $releaseDir "$outputName.exe"
    Remove-ReleaseArtifact -Path $installer
    Invoke-External -Description "Building Windows installer" -Command {
      & $iscc "/DMyAppVersion=$version" "/DMyAppSourceDir=$distDir" "/DMyOutputBaseFilename=$outputName" "/DMyOutputDir=$releaseDir" $issPath
    }
    if (-not (Test-Path $installer)) { throw "Expected installer missing: $installer" }
    Invoke-External -Description "Auditing Windows installer for embedded credential markers" -Command {
      & $venvPython scripts\check_v2_release_credentials.py $installer
    }
    if ($SignedRelease) {
      Write-Step "Signing the Windows installer"
      Sign-WindowsArtifact -Path $installer -SignTool $signTool
    }
  }
} else {
  Write-Warning "SKIP_INNO=1: only the portable ZIP was created."
}

$performanceSource = Join-Path (Get-Location) "docs\performance_v2_last_run.json"
if (Test-Path $performanceSource) { Copy-Item -Force $performanceSource (Join-Path $releaseDir "performance-windows.json") }

$releaseInfo = Join-Path $releaseDir "RELEASE_INFO.txt"
if ($SignedRelease) {
  @("Maritime Ship Simulator V2 $version", "Release type: signed production") | Set-Content -Encoding ascii $releaseInfo
} elseif ($ProductionRelease) {
  @(
    "Maritime Ship Simulator V2 $version",
    "Release type: unsigned production",
    "Embedded credentials: none; provider credentials are supplied by the operator at runtime.",
    "Platform trust warning: Windows SmartScreen may require explicit user approval."
  ) | Set-Content -Encoding ascii $releaseInfo
} else {
  @(
    "Maritime Ship Simulator V2 $version",
    "Release type: unsigned release candidate",
    "Platform trust warning: Windows SmartScreen may require explicit user approval."
  ) | Set-Content -Encoding ascii $releaseInfo
}

Write-Step "Writing SHA-256 manifest"
$artifacts = @($portable, $releaseInfo)
if ($installer) { $artifacts += $installer }
$manifest = Join-Path $releaseDir "SHA256SUMS"
Get-FileHash -Algorithm SHA256 $artifacts | ForEach-Object {
  "$($_.Hash.ToLower()) *$([System.IO.Path]::GetFileName($_.Path))"
} | Set-Content -Encoding ascii $manifest

if ($env:KEEP_BUILD_WORK -ne "1") { Remove-PathRobust -Path $workRoot }
Write-Step "Windows V2 release completed"
Get-ChildItem -Force $releaseDir
