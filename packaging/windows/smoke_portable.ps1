param(
    [string]$PackageDir = "",
    [string]$PythonExe = "",
    [int]$Timeout = 45,
    [switch]$NoClean,
    [switch]$RequireVenv
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
if ([string]::IsNullOrWhiteSpace($PackageDir)) {
    $PackageDir = Join-Path $RepoRoot "dist\NASDX-Desktop"
}

if ([System.IO.Path]::IsPathRooted($PackageDir)) {
    $PackagePath = [System.IO.Path]::GetFullPath($PackageDir)
} else {
    $PackagePath = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $PackageDir))
}

if (-not (Test-Path -LiteralPath $PackagePath)) {
    throw "Package directory does not exist: $PackagePath"
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $VenvPython = Join-Path $PackagePath ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $VenvPython) {
        $PythonExe = $VenvPython
    } else {
        $PythonExe = "python"
    }
}

if ($RequireVenv) {
    $VenvPython = [System.IO.Path]::GetFullPath((Join-Path $PackagePath ".venv\Scripts\python.exe"))
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        throw "Package .venv Python is required but missing: $VenvPython"
    }
    if ([System.IO.Path]::GetFullPath($PythonExe) -ne $VenvPython) {
        throw "Package smoke must use bundled .venv Python when -RequireVenv is set: $PythonExe"
    }
}

$RequiredFiles = @(
    "app.py",
    "desktop\control.py",
    "desktop\control_panel.py",
    "desktop\launcher.py",
    "desktop\runtime.py",
    "desktop\paths.py",
    "desktop\doctor.py",
    "static\style.css",
    "requirements_nasdx.txt",
    "run_desktop_doctor.py",
    "run_desktop_completion_audit.py",
    "packaging\windows\create_shortcuts.ps1",
    "packaging\windows\smoke_installer_roundtrip.ps1",
    "启动NASDX桌面.bat"
)

foreach ($RelativePath in $RequiredFiles) {
    $FullPath = Join-Path $PackagePath $RelativePath
    if (-not (Test-Path -LiteralPath $FullPath)) {
        throw "Required package file is missing: $RelativePath"
    }
}

$StylePath = Join-Path $PackagePath "static\style.css"
if ((Get-Item -LiteralPath $StylePath).Length -le 0) {
    throw "static\style.css is empty in package"
}

$SmokeRuntime = [System.IO.Path]::GetFullPath((Join-Path $PackagePath "_smoke_runtime"))
if ((Test-Path -LiteralPath $SmokeRuntime) -and -not $NoClean) {
    Remove-Item -LiteralPath $SmokeRuntime -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $SmokeRuntime | Out-Null

$OldRuntimeDir = $env:NASDX_RUNTIME_DIR
$OldHistoryDb = $env:NASDX_HISTORY_DB
$OldReportsDir = $env:NASDX_REPORTS_DIR

try {
    $env:NASDX_RUNTIME_DIR = $SmokeRuntime
    $env:NASDX_HISTORY_DB = Join-Path $SmokeRuntime "nasdx_history.db"
    $env:NASDX_REPORTS_DIR = Join-Path $SmokeRuntime "reports"

    Push-Location $PackagePath
    try {
        $DryRunOutput = & $PythonExe -B desktop\launcher.py --dry-run --page plan
        if ($LASTEXITCODE -ne 0) {
            throw "Launcher dry-run failed"
        }
        $DryRun = $DryRunOutput | ConvertFrom-Json

        if ([System.IO.Path]::GetFullPath([string]$DryRun.root) -ne $PackagePath) {
            throw "Launcher root is not the package directory: $($DryRun.root)"
        }
        if ([System.IO.Path]::GetFullPath([string]$DryRun.runtime_dir) -ne $SmokeRuntime) {
            throw "Launcher runtime_dir is not the smoke runtime: $($DryRun.runtime_dir)"
        }
        if ([string]$DryRun.page -ne "plan") {
            throw "Launcher dry-run did not preserve plan page"
        }

        $ControlOutput = & $PythonExe -B desktop\control_panel.py --dry-run --page plan
        if ($LASTEXITCODE -ne 0) {
            throw "Control panel dry-run failed"
        }
        $ControlDryRun = $ControlOutput | ConvertFrom-Json
        if (-not ($ControlDryRun.actions -contains "Start") -or -not ($ControlDryRun.actions -contains "Data Refresh")) {
            throw "Control panel dry-run is missing required desktop actions"
        }

        $BatchPath = Join-Path $PackagePath "启动NASDX桌面.bat"
        $BatchOutput = & $BatchPath --dry-run --page plan
        if ($LASTEXITCODE -ne 0) {
            throw "Desktop batch dry-run failed"
        }
        $BatchDryRun = $BatchOutput | ConvertFrom-Json
        if ([System.IO.Path]::GetFullPath([string]$BatchDryRun.root) -ne $PackagePath) {
            throw "Desktop batch dry-run root is not the package directory: $($BatchDryRun.root)"
        }
        if (-not ($BatchDryRun.actions -contains "Start") -or -not ($BatchDryRun.actions -contains "Data Refresh")) {
            throw "Desktop batch dry-run is missing required desktop actions"
        }

        $DoctorOutput = & $PythonExe -B run_desktop_doctor.py --json
        if ($LASTEXITCODE -ne 0) {
            throw "Desktop doctor failed"
        }
        $DoctorChecks = @($DoctorOutput | ConvertFrom-Json)
        $DoctorFailures = @($DoctorChecks | Where-Object { $_.status -eq "FAIL" })
        if ($DoctorFailures) {
            $Labels = ($DoctorFailures | ForEach-Object { $_.label }) -join ", "
            throw "Desktop doctor reported failures: $Labels"
        }
        foreach ($RequiredLabel in @("required_files", "desktop_env", "launch_plan")) {
            if (-not ($DoctorChecks | Where-Object { $_.label -eq $RequiredLabel })) {
                throw "Desktop doctor did not report required label: $RequiredLabel"
            }
        }

        $CompletionOutput = & $PythonExe -B run_desktop_completion_audit.py --json
        if ($LASTEXITCODE -ne 0) {
            throw "Desktop completion audit failed"
        }
        $CompletionChecks = @($CompletionOutput | ConvertFrom-Json)
        $CompletionFailures = @($CompletionChecks | Where-Object { $_.status -eq "FAIL" })
        if ($CompletionFailures) {
            $Labels = ($CompletionFailures | ForEach-Object { $_.label }) -join ", "
            throw "Desktop completion audit reported failures: $Labels"
        }
        foreach ($RequiredLabel in @("preserved_entrypoints", "desktop_launcher_mvp", "installer_roundtrip")) {
            if (-not ($CompletionChecks | Where-Object { $_.label -eq $RequiredLabel })) {
                throw "Desktop completion audit did not report required label: $RequiredLabel"
            }
        }

        $ShortcutScript = Join-Path $PackagePath "packaging\windows\create_shortcuts.ps1"
        $ShortcutOutput = & powershell -ExecutionPolicy Bypass -File $ShortcutScript -AppDir $PackagePath -Desktop
        if ($LASTEXITCODE -ne 0) {
            throw "Shortcut plan-only check failed"
        }
        $ShortcutText = $ShortcutOutput -join [Environment]::NewLine
        if (-not $ShortcutText.Contains("plan-only mode") -or -not $ShortcutText.Contains("启动NASDX桌面.bat")) {
            throw "Shortcut plan-only output did not describe NASDX desktop shortcut targets"
        }

        $SmokeOutput = & $PythonExe -B desktop\launcher.py --headless-smoke --timeout $Timeout --no-browser --page plan 2>&1
        if ($LASTEXITCODE -ne 0) {
            $SmokeText = $SmokeOutput -join [Environment]::NewLine
            throw "Launcher headless smoke failed:`n$SmokeText"
        }
    } finally {
        Pop-Location
    }

    $AppPy = Join-Path $PackagePath "app.py"
    $Orphans = Get-CimInstance Win32_Process |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine.Contains("streamlit") -and
            $_.CommandLine.Contains($AppPy)
        }
    if ($Orphans) {
        $Ids = ($Orphans | Select-Object -ExpandProperty ProcessId) -join ", "
        throw "Streamlit process still running after smoke: $Ids"
    }

    Write-Host "NASDX portable smoke passed: $PackagePath"
    Write-Host "Smoke python: $PythonExe"
    Write-Host "Smoke runtime: $SmokeRuntime"
} finally {
    $env:NASDX_RUNTIME_DIR = $OldRuntimeDir
    $env:NASDX_HISTORY_DB = $OldHistoryDb
    $env:NASDX_REPORTS_DIR = $OldReportsDir

    if ((Test-Path -LiteralPath $SmokeRuntime) -and -not $NoClean) {
        Remove-Item -LiteralPath $SmokeRuntime -Recurse -Force
    }
}
