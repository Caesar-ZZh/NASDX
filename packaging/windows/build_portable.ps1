param(
    [string]$OutputDir = "",
    [switch]$SkipDependencyInstall,
    [switch]$IncludeWebView,
    [int]$PipTimeout = 60,
    [int]$PipRetries = 2,
    [string]$PipIndexUrl = "",
    [string]$ConstraintsFile = "",
    [string]$WheelhouseDir = "",
    [switch]$OnlyBinary,
    [switch]$NoClean
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $RepoRoot "dist\NASDX-Desktop"
}

if ([System.IO.Path]::IsPathRooted($OutputDir)) {
    $Target = [System.IO.Path]::GetFullPath($OutputDir)
} else {
    $Target = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputDir))
}
$RepoRootPath = [System.IO.Path]::GetFullPath($RepoRoot)
$DefaultDistRoot = [System.IO.Path]::GetFullPath((Join-Path $RepoRootPath "dist"))
$DefaultConstraints = Join-Path $ScriptDir "constraints-win.txt"

if ([string]::IsNullOrWhiteSpace($ConstraintsFile) -and (Test-Path -LiteralPath $DefaultConstraints)) {
    $ConstraintsFile = $DefaultConstraints
}

if (-not [string]::IsNullOrWhiteSpace($ConstraintsFile)) {
    if ([System.IO.Path]::IsPathRooted($ConstraintsFile)) {
        $ConstraintsFile = [System.IO.Path]::GetFullPath($ConstraintsFile)
    } else {
        $ConstraintsFile = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $ConstraintsFile))
    }
    if (-not (Test-Path -LiteralPath $ConstraintsFile)) {
        throw "Constraints file does not exist: $ConstraintsFile"
    }
}

if (-not [string]::IsNullOrWhiteSpace($WheelhouseDir)) {
    if ([System.IO.Path]::IsPathRooted($WheelhouseDir)) {
        $WheelhouseDir = [System.IO.Path]::GetFullPath($WheelhouseDir)
    } else {
        $WheelhouseDir = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $WheelhouseDir))
    }
    if (-not (Test-Path -LiteralPath $WheelhouseDir)) {
        throw "Wheelhouse directory does not exist: $WheelhouseDir"
    }
}

$ExcludedPatterns = @(
    "reports/",
    "stock_data_*.json",
    "nasdx_history.db",
    "config.toml",
    ".env",
    "*.log",
    "*_log*.txt",
    "fetch_log.txt",
    "pip_*.txt",
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    ".git/",
    "dist/",
    "build/",
    ".venv/",
    "venv/",
    ".pytest_cache/",
    ".ruff_cache/",
    "desktop_logs/",
    "wheelhouse/",
    "models/signal_confidence.json"
)
$ScrubbedPatterns = @(
    "__pycache__/",
    "*.pyc",
    "*.pyo",
    "reports/",
    "stock_data_*.json",
    "nasdx_history.db",
    "config.toml",
    ".env",
    "*.log",
    "*_log*.txt",
    "fetch_log.txt",
    "pip_*.txt",
    ".git/",
    "dist/",
    "build/",
    ".venv/",
    "venv/",
    ".pytest_cache/",
    ".ruff_cache/",
    "desktop_logs/",
    "wheelhouse/",
    "models/signal_confidence.json"
)

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

function Convert-ToPackageManifestPath {
    param(
        [string]$PathValue,
        [string]$EmptyValue = ""
    )

    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return $EmptyValue
    }
    $FullPath = [System.IO.Path]::GetFullPath($PathValue)
    $RepoPrefix = $RepoRootPath.TrimEnd("\") + "\"
    if ($FullPath.StartsWith($RepoPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $FullPath.Substring($RepoPrefix.Length).Replace("\", "/")
    }
    return "<external-path>"
}

if ($Target -eq $RepoRootPath) {
    throw "Refusing to package into repository root: $Target"
}

if ((Test-Path -LiteralPath $Target) -and -not $NoClean) {
    $insideDefaultDist = $Target.StartsWith($DefaultDistRoot, [System.StringComparison]::OrdinalIgnoreCase)
    $hasPackageName = (Split-Path -Leaf $Target) -eq "NASDX-Desktop"
    if (-not ($insideDefaultDist -or $hasPackageName)) {
        throw "Refusing to clean output outside dist/ or NASDX-Desktop folder: $Target"
    }
    Remove-Item -LiteralPath $Target -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $Target | Out-Null

$Manifest = [ordered]@{
    name = "NASDX-Desktop"
    generated_at = (Get-Date).ToString("s")
    path_policy = "relative-or-redacted"
    source_root = "<source-checkout>"
    package_root = "."
    skip_dependency_install = [bool]$SkipDependencyInstall
    include_webview = [bool]$IncludeWebView
    constraints_file = (Convert-ToPackageManifestPath -PathValue $ConstraintsFile)
    wheelhouse_dir = (Convert-ToPackageManifestPath -PathValue $WheelhouseDir)
    only_binary = [bool]$OnlyBinary
    included_directories = @()
    included_files = @()
    excluded_patterns = $ExcludedPatterns
    scrubbed_patterns = $ScrubbedPatterns
}

function Copy-DirectoryAllowList {
    param(
        [Parameter(Mandatory = $true)][string]$Name
    )
    $Source = Join-Path $RepoRootPath $Name
    if (-not (Test-Path -LiteralPath $Source)) {
        return
    }
    $Destination = Join-Path $Target $Name
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
    $Manifest.included_directories += $Name
}

function Copy-FileIfExists {
    param(
        [Parameter(Mandatory = $true)][string]$RelativePath
    )
    $Source = Join-Path $RepoRootPath $RelativePath
    if (-not (Test-Path -LiteralPath $Source)) {
        return
    }
    $Destination = Join-Path $Target $RelativePath
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Force
    $Manifest.included_files += $RelativePath
}

function Remove-PackageExcludedArtifacts {
    param([switch]$KeepVenv)

    $DirectoryNames = @(
        "__pycache__",
        "reports",
        ".git",
        "dist",
        "build",
        ".pytest_cache",
        ".ruff_cache",
        "desktop_logs",
        "wheelhouse"
    )
    if (-not $KeepVenv) {
        $DirectoryNames += @(".venv", "venv")
    }
    foreach ($name in $DirectoryNames) {
        Get-ChildItem -LiteralPath $Target -Directory -Recurse -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq $name } |
            Sort-Object FullName -Descending |
            ForEach-Object {
                if (Test-Path -LiteralPath $_.FullName) {
                    Remove-Item -LiteralPath $_.FullName -Recurse -Force
                }
            }
    }

    $FilePatterns = @(
        "*.pyc",
        "*.pyo",
        "*.log",
        "*_log*.txt",
        "fetch_log.txt",
        "pip_*.txt",
        "stock_data_*.json",
        "nasdx_history.db",
        "config.toml",
        ".env"
    )
    foreach ($pattern in $FilePatterns) {
        Get-ChildItem -LiteralPath $Target -File -Recurse -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like $pattern } |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
    }

    $GeneratedModel = Join-Path $Target "models\signal_confidence.json"
    if (Test-Path -LiteralPath $GeneratedModel) {
        Remove-Item -LiteralPath $GeneratedModel -Force
    }
}

# The desktop allow-list keeps launcher.py, control_panel.py, and desktop\exe_launcher.py together.
foreach ($dir in @("nasdx", "quant", "desktop", "static", "docs")) {
    Copy-DirectoryAllowList -Name $dir
}

Get-ChildItem -LiteralPath $RepoRootPath -Filter "*.py" -File |
    Where-Object { $_.Name -notlike "scratch_*" } |
    ForEach-Object { Copy-FileIfExists -RelativePath $_.Name }

foreach ($file in @(
    "README.md",
    "PLANS.md",
    "AGENTS.md",
    "config.example.toml",
    "requirements_nasdx.txt",
    "requirements_desktop.txt",
    "requirements-dev.txt",
    "etf50_pool.json",
    "stocks.json",
    "启动网页.bat",
    "packaging/windows/build_launcher_exe.ps1",
    "packaging/windows/constraints-win.txt",
    "packaging/windows/create_shortcuts.ps1",
    "packaging/windows/inno_paths.ps1",
    "packaging/windows/smoke_installed.ps1",
    "packaging/windows/smoke_installer_roundtrip.ps1"
)) {
    Copy-FileIfExists -RelativePath $file
}

$LaunchBat = @"
@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -B desktop\control_panel.py %*
) else (
  python -B desktop\control_panel.py %*
)
if errorlevel 1 (
  echo NASDX control panel failed, falling back to direct launcher.
  if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -B desktop\launcher.py --webview --page plan %*
  ) else (
    python -B desktop\launcher.py --webview --page plan %*
  )
)
endlocal
"@
Set-Content -LiteralPath (Join-Path $Target "启动NASDX桌面.bat") -Value $LaunchBat -Encoding UTF8
$Manifest.included_files += "启动NASDX桌面.bat"

Remove-PackageExcludedArtifacts

if (-not $SkipDependencyInstall) {
    $VenvPath = Join-Path $Target ".venv"
    $TempVenvRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("nasdx-build-venv-" + [guid]::NewGuid().ToString("N"))
    $TempVenvPath = Join-Path $TempVenvRoot ".venv"
    try {
        New-Item -ItemType Directory -Force -Path $TempVenvRoot | Out-Null
        Invoke-Checked -FilePath "python" -Arguments @("-m", "venv", "--without-pip", $TempVenvPath)
        $PythonExe = Join-Path $TempVenvPath "Scripts\python.exe"
        Invoke-Checked -FilePath $PythonExe -Arguments @("-m", "ensurepip", "--upgrade")
        $PipInstallArgs = @(
            "-m",
            "pip",
            "install",
            "--no-user",
            "--disable-pip-version-check",
            "--prefer-binary",
            "--timeout",
            [string]$PipTimeout,
            "--retries",
            [string]$PipRetries
        )
        if (-not [string]::IsNullOrWhiteSpace($ConstraintsFile)) {
            $PipInstallArgs += @("--constraint", $ConstraintsFile)
        }
        if ($OnlyBinary) {
            $PipInstallArgs += @("--only-binary", ":all:")
        }
        if (-not [string]::IsNullOrWhiteSpace($WheelhouseDir)) {
            $PipInstallArgs += @("--no-index", "--find-links", $WheelhouseDir)
        } elseif (-not [string]::IsNullOrWhiteSpace($PipIndexUrl)) {
            $PipInstallArgs += @("--index-url", $PipIndexUrl)
        }
        Invoke-Checked -FilePath $PythonExe -Arguments ($PipInstallArgs + @("-U", "pip"))
        Invoke-Checked -FilePath $PythonExe -Arguments ($PipInstallArgs + @("-r", (Join-Path $Target "requirements_nasdx.txt")))
        if ($IncludeWebView) {
            Invoke-Checked -FilePath $PythonExe -Arguments ($PipInstallArgs + @("-r", (Join-Path $Target "requirements_desktop.txt")))
        }
        if (Test-Path -LiteralPath $VenvPath) {
            Remove-Item -LiteralPath $VenvPath -Recurse -Force
        }
        Move-Item -LiteralPath $TempVenvPath -Destination $VenvPath
        Remove-PackageExcludedArtifacts -KeepVenv
    } finally {
        if (Test-Path -LiteralPath $TempVenvRoot) {
            Remove-Item -LiteralPath $TempVenvRoot -Recurse -Force
        }
    }
}

$ManifestPath = Join-Path $Target "PACKAGING_MANIFEST.json"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ManifestPath, ($Manifest | ConvertTo-Json -Depth 4), $Utf8NoBom)

Write-Host "NASDX portable package prepared: $Target"
Write-Host "Dependency install skipped: $([bool]$SkipDependencyInstall)"
Write-Host "WebView dependency included: $([bool]$IncludeWebView)"
