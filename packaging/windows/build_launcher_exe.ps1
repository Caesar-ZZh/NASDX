param(
    [string]$PackageDir = "",
    [string]$OutputDir = "",
    [string]$WorkDir = "",
    [string]$SpecDir = "",
    [string]$Name = "NASDX-Desktop-Launcher",
    [string]$PyInstallerPath = "",
    [switch]$Windowed,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")

function Resolve-NasdxPath {
    param([Parameter(Mandatory = $true)][string]$PathValue)

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $PathValue))
}

if ([string]::IsNullOrWhiteSpace($PackageDir)) {
    $PackageDir = $RepoRoot
}
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $RepoRoot "dist\launcher-exe"
}
if ([string]::IsNullOrWhiteSpace($WorkDir)) {
    $WorkDir = Join-Path $RepoRoot "build\pyinstaller"
}
if ([string]::IsNullOrWhiteSpace($SpecDir)) {
    $SpecDir = Join-Path $RepoRoot "build\pyinstaller-spec"
}

$ResolvedPackageDir = Resolve-NasdxPath $PackageDir
$ResolvedOutputDir = Resolve-NasdxPath $OutputDir
$ResolvedWorkDir = Resolve-NasdxPath $WorkDir
$ResolvedSpecDir = Resolve-NasdxPath $SpecDir
$EntryScript = Join-Path $ResolvedPackageDir "desktop\exe_launcher.py"

if (-not (Test-Path -LiteralPath $EntryScript)) {
    throw "Launcher entry script does not exist: $EntryScript"
}

$PyInstallerArgs = @(
    "--clean",
    "--noconfirm",
    "--noupx",
    "--onefile",
    "--name",
    $Name,
    "--distpath",
    $ResolvedOutputDir,
    "--workpath",
    $ResolvedWorkDir,
    "--specpath",
    $ResolvedSpecDir
)
if ($Windowed) {
    $PyInstallerArgs += "--windowed"
}
$PyInstallerArgs += $EntryScript

Write-Host "NASDX launcher-only executable build"
Write-Host "Package root: $ResolvedPackageDir"
Write-Host "Entry script: $EntryScript"
Write-Host "Output directory: $ResolvedOutputDir"
Write-Host "This builds only the tiny desktop exe launcher; it delegates to .venv\Scripts\python.exe and does not bundle app.py or analytics dependencies."

if ($SkipBuild) {
    Write-Host "plan-only mode; pass without -SkipBuild after installing PyInstaller on a packaging machine."
    Write-Host "PyInstaller command:"
    Write-Host "python -m PyInstaller $($PyInstallerArgs -join ' ')"
    return
}

if (-not [string]::IsNullOrWhiteSpace($PyInstallerPath)) {
    $ResolvedPyInstaller = Resolve-NasdxPath $PyInstallerPath
    if (-not (Test-Path -LiteralPath $ResolvedPyInstaller)) {
        throw "PyInstaller executable does not exist: $ResolvedPyInstaller"
    }
    & $ResolvedPyInstaller @PyInstallerArgs
} else {
    & python -m PyInstaller @PyInstallerArgs
}
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code ${LASTEXITCODE}. Install PyInstaller only on the packaging machine; do not add it to the runtime package."
}

$ExePath = Join-Path $ResolvedOutputDir "$Name.exe"
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "Expected launcher executable was not created: $ExePath"
}

Write-Host "NASDX launcher executable built: $ExePath"
