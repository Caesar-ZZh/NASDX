param(
    [string]$PackageDir = "",
    [string]$InstallerOutputDir = "",
    [string]$IsccPath = "",
    [switch]$SkipPortableBuild,
    [switch]$SkipCompile,
    [switch]$IncludeWebView,
    [int]$PipTimeout = 60,
    [int]$PipRetries = 2,
    [string]$WheelhouseDir = "",
    [switch]$OnlyBinary
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$RepoRootPath = [System.IO.Path]::GetFullPath($RepoRoot)
$PortableBuildScript = Join-Path $ScriptDir "build_portable.ps1"
$InstallerScript = Join-Path $ScriptDir "NASDX-Desktop.iss"
$InnoPathsScript = Join-Path $ScriptDir "inno_paths.ps1"
. $InnoPathsScript

if ([string]::IsNullOrWhiteSpace($PackageDir)) {
    $PackageDir = Join-Path $RepoRootPath "dist\NASDX-Desktop"
}
if ([string]::IsNullOrWhiteSpace($InstallerOutputDir)) {
    $InstallerOutputDir = Join-Path $RepoRootPath "dist\installer"
}

function Resolve-AbsolutePath {
    param([Parameter(Mandatory = $true)][string]$PathValue)

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $PathValue))
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

function Get-IsccPath {
    param([string]$ConfiguredPath)

    $Resolved = Get-NasdxIsccPath -ConfiguredPath $ConfiguredPath
    if (-not [string]::IsNullOrWhiteSpace($Resolved)) {
        return $Resolved
    }
    throw "Inno Setup compiler was not found. Install Inno Setup 7/6 or pass -IsccPath. Use -SkipCompile for a non-compiling validation run."
}

function Assert-PortablePackage {
    param([Parameter(Mandatory = $true)][string]$PathValue)

    if (-not (Test-Path -LiteralPath $PathValue)) {
        throw "Portable package directory does not exist: $PathValue"
    }
    foreach ($RelativePath in @(
        "app.py",
        "desktop\control_panel.py",
        "desktop\launcher.py",
        "requirements_nasdx.txt",
        "启动NASDX桌面.bat"
    )) {
        $FullPath = Join-Path $PathValue $RelativePath
        if (-not (Test-Path -LiteralPath $FullPath)) {
            throw "Portable package is missing required file: $RelativePath"
        }
    }
}

$PackagePath = Resolve-AbsolutePath -PathValue $PackageDir
$InstallerOut = Resolve-AbsolutePath -PathValue $InstallerOutputDir

if (-not $SkipPortableBuild) {
    $BuildArgs = @(
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $PortableBuildScript,
        "-OutputDir",
        $PackagePath,
        "-PipTimeout",
        [string]$PipTimeout,
        "-PipRetries",
        [string]$PipRetries
    )
    if ($IncludeWebView) {
        $BuildArgs += "-IncludeWebView"
    }
    if (-not [string]::IsNullOrWhiteSpace($WheelhouseDir)) {
        $BuildArgs += @("-WheelhouseDir", $WheelhouseDir)
    }
    if ($OnlyBinary) {
        $BuildArgs += "-OnlyBinary"
    }
    Invoke-Checked -FilePath "powershell" -Arguments $BuildArgs
}

Assert-PortablePackage -PathValue $PackagePath
New-Item -ItemType Directory -Force -Path $InstallerOut | Out-Null

if ($SkipCompile) {
    Write-Host "NASDX installer validation passed."
    Write-Host "Portable package: $PackagePath"
    Write-Host "Installer output: $InstallerOut"
    Write-Host "Installer compile skipped."
    return
}

$Compiler = Get-IsccPath -ConfiguredPath $IsccPath
$CompilerArgs = @(
    "/DPortableDir=$PackagePath",
    "/DInstallerOutputDir=$InstallerOut",
    $InstallerScript
)
Invoke-Checked -FilePath $Compiler -Arguments $CompilerArgs

$ExpectedInstaller = Join-Path $InstallerOut "NASDX-Desktop-Setup.exe"
if (-not (Test-Path -LiteralPath $ExpectedInstaller)) {
    throw "Installer build finished but expected output is missing: $ExpectedInstaller"
}

Write-Host "NASDX installer built: $ExpectedInstaller"
