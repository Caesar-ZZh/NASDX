param(
    [string]$InstallerPath = "",
    [string]$InstallDir = "",
    [string]$ProofPath = "",
    [string]$PythonExe = "",
    [int]$Timeout = 60,
    [switch]$AllowInstall,
    [switch]$CheckShortcuts,
    [switch]$RequireVenv,
    [switch]$KeepInstalled
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$RepoRootPath = [System.IO.Path]::GetFullPath($RepoRoot)
$InstalledSmokeScript = Join-Path $ScriptDir "smoke_installed.ps1"
. (Join-Path $ScriptDir "hash_utils.ps1")

function Resolve-AbsolutePath {
    param([Parameter(Mandatory = $true)][string]$PathValue)

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $PathValue))
}

function Invoke-ProcessChecked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $Process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -Wait -PassThru -WindowStyle Hidden
    if ($Process.ExitCode -ne 0) {
        throw "Command failed with exit code $($Process.ExitCode): $FilePath $($Arguments -join ' ')"
    }
}

function Invoke-PowerShellChecked {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    & powershell @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PowerShell command failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$PathValue)

    return Get-NasdxSha256 -PathValue $PathValue
}

function Assert-SafeInstallDir {
    param([Parameter(Mandatory = $true)][string]$PathValue)

    $FullPath = [System.IO.Path]::GetFullPath($PathValue)
    $RootPath = [System.IO.Path]::GetPathRoot($FullPath)
    if ($FullPath.TrimEnd("\") -eq $RootPath.TrimEnd("\")) {
        throw "Refusing to use drive root as install directory: $FullPath"
    }
    if ($FullPath -eq $RepoRootPath -or $RepoRootPath.StartsWith($FullPath.TrimEnd("\") + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to install over the repository or its parent: $FullPath"
    }
    foreach ($ForbiddenName in @("Windows", "Program Files", "Program Files (x86)", "Users")) {
        if ([System.IO.Path]::GetFileName($FullPath.TrimEnd("\")) -eq $ForbiddenName) {
            throw "Refusing high-risk install directory: $FullPath"
        }
    }
}

if ([string]::IsNullOrWhiteSpace($InstallerPath)) {
    $InstallerPath = Join-Path $RepoRootPath "dist\installer\NASDX-Desktop-Setup.exe"
}
$InstallerFullPath = Resolve-AbsolutePath -PathValue $InstallerPath
if ([string]::IsNullOrWhiteSpace($ProofPath)) {
    $ProofPath = Join-Path (Split-Path -Parent $InstallerFullPath) "NASDX-Desktop-roundtrip-proof.json"
}
$ProofFullPath = Resolve-AbsolutePath -PathValue $ProofPath

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    $InstallDir = Join-Path ([System.IO.Path]::GetTempPath()) ("nasdx-installer-roundtrip-" + [guid]::NewGuid().ToString("N"))
    $UsingDefaultTempInstallDir = $true
} else {
    $UsingDefaultTempInstallDir = $false
}
$InstallPath = Resolve-AbsolutePath -PathValue $InstallDir
Assert-SafeInstallDir -PathValue $InstallPath

if (-not $AllowInstall) {
    Write-Host "NASDX installer roundtrip is in plan-only mode."
    Write-Host "Installer path: $InstallerFullPath"
    Write-Host "Install directory: $InstallPath"
    Write-Host "Proof path: $ProofFullPath"
    Write-Host "Pass -AllowInstall in a disposable Windows profile or VM to run install, smoke, and uninstall."
    if (-not (Test-Path -LiteralPath $InstallerFullPath)) {
        Write-Host "Installer file is not present yet. Build it with build_installer.ps1 after Inno Setup 6 is installed."
    }
    return
}

if (-not (Test-Path -LiteralPath $InstallerFullPath)) {
    throw "Installer file does not exist: $InstallerFullPath"
}
if (-not (Test-Path -LiteralPath $InstalledSmokeScript)) {
    throw "Installed smoke script does not exist: $InstalledSmokeScript"
}
if ((Test-Path -LiteralPath $InstallPath) -and (Get-ChildItem -LiteralPath $InstallPath -Force | Select-Object -First 1)) {
    throw "Install directory already exists and is not empty: $InstallPath"
}

$InstallArgs = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/DIR=$InstallPath"
)

Invoke-ProcessChecked -FilePath $InstallerFullPath -Arguments $InstallArgs

$SmokeArgs = @(
    "-ExecutionPolicy",
    "Bypass",
    "-File",
    $InstalledSmokeScript,
    "-InstallDir",
    $InstallPath,
    "-Timeout",
    [string]$Timeout
)
if (-not [string]::IsNullOrWhiteSpace($PythonExe)) {
    $SmokeArgs += @("-PythonExe", $PythonExe)
}
if ($CheckShortcuts) {
    $SmokeArgs += "-CheckShortcuts"
}
if ($RequireVenv) {
    $SmokeArgs += "-RequireVenv"
}
Invoke-PowerShellChecked -Arguments $SmokeArgs

if ($KeepInstalled) {
    Write-Host "NASDX installer roundtrip install and smoke passed; install kept: $InstallPath"
    Write-Host "Roundtrip proof was not written because -KeepInstalled skips uninstall."
    return
}

$Uninstallers = Get-ChildItem -LiteralPath $InstallPath -Filter "unins*.exe" -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending
if (-not $Uninstallers) {
    throw "No Inno Setup uninstaller found under installed directory: $InstallPath"
}
$Uninstaller = $Uninstallers[0].FullName
$UninstallArgs = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART"
)
Invoke-ProcessChecked -FilePath $Uninstaller -Arguments $UninstallArgs

$AppPy = Join-Path $InstallPath "app.py"
if (Test-Path -LiteralPath $AppPy) {
    throw "Uninstall finished but app.py still exists in install directory: $AppPy"
}
if ($CheckShortcuts) {
    $StartMenuShortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\NASDX Desktop\NASDX Desktop.lnk"
    if (Test-Path -LiteralPath $StartMenuShortcut) {
        throw "Start Menu shortcut still exists after uninstall: $StartMenuShortcut"
    }
}
if ($UsingDefaultTempInstallDir -and (Test-Path -LiteralPath $InstallPath)) {
    $Remaining = @(Get-ChildItem -LiteralPath $InstallPath -Force -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $Remaining) {
        Remove-Item -LiteralPath $InstallPath -Force
    }
}

$Proof = [ordered]@{
    schema = "nasdx_installer_roundtrip_proof.v1"
    generated_at = (Get-Date).ToString("s")
    installer_path = $InstallerFullPath
    installer_sha256 = Get-Sha256 -PathValue $InstallerFullPath
    installer_size_bytes = (Get-Item -LiteralPath $InstallerFullPath).Length
    install_dir = $InstallPath
    require_venv = [bool]$RequireVenv
    check_shortcuts = [bool]$CheckShortcuts
    installed_smoke = "passed"
    uninstall = "passed"
    kept_installed = [bool]$KeepInstalled
}
$ProofParent = Split-Path -Parent $ProofFullPath
if (-not [string]::IsNullOrWhiteSpace($ProofParent)) {
    New-Item -ItemType Directory -Force -Path $ProofParent | Out-Null
}
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ProofFullPath, ($Proof | ConvertTo-Json -Depth 4), $Utf8NoBom)

Write-Host "NASDX installer roundtrip passed: $InstallerFullPath"
Write-Host "Install directory checked and uninstalled: $InstallPath"
Write-Host "Roundtrip proof written: $ProofFullPath"
