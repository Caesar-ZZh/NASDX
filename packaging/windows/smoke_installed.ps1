param(
    [string]$InstallDir = "",
    [string]$PythonExe = "",
    [int]$Timeout = 60,
    [switch]$NoClean,
    [switch]$CheckShortcuts,
    [switch]$RequireVenv
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    $InstallDir = Join-Path $env:LOCALAPPDATA "Programs\NASDX Desktop"
}

if ([System.IO.Path]::IsPathRooted($InstallDir)) {
    $InstallPath = [System.IO.Path]::GetFullPath($InstallDir)
} else {
    $InstallPath = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $InstallDir))
}

if (-not (Test-Path -LiteralPath $InstallPath)) {
    throw "Installed NASDX directory does not exist: $InstallPath"
}

function Remove-PythonCacheArtifacts {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootPath
    )

    if (-not (Test-Path -LiteralPath $RootPath -PathType Container)) {
        return
    }
    $ResolvedRoot = [System.IO.Path]::GetFullPath($RootPath)
    $RootPrefix = $ResolvedRoot.TrimEnd("\") + "\"

    Get-ChildItem -LiteralPath $ResolvedRoot -Directory -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -eq "__pycache__" -and
            [System.IO.Path]::GetFullPath($_.FullName).StartsWith($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)
        } |
        Sort-Object { $_.FullName.Length } -Descending |
        ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }

    foreach ($Pattern in @("*.pyc", "*.pyo")) {
        Get-ChildItem -LiteralPath $ResolvedRoot -File -Recurse -Force -Filter $Pattern -ErrorAction SilentlyContinue |
            Where-Object {
                [System.IO.Path]::GetFullPath($_.FullName).StartsWith($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)
            } |
            ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
    }
}

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $VenvPython = Join-Path $InstallPath ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $VenvPython) {
        $PythonExe = $VenvPython
    } else {
        $PythonExe = "python"
    }
}

if ($RequireVenv) {
    $VenvPython = [System.IO.Path]::GetFullPath((Join-Path $InstallPath ".venv\Scripts\python.exe"))
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        throw "Installed .venv Python is required but missing: $VenvPython"
    }
    if ([System.IO.Path]::GetFullPath($PythonExe) -ne $VenvPython) {
        throw "Installed smoke must use bundled .venv Python when -RequireVenv is set: $PythonExe"
    }
}

$RequiredFiles = @(
    "app.py",
    "desktop\control.py",
    "desktop\control_panel.py",
    "desktop\launcher.py",
    "desktop\runtime.py",
    "desktop\paths.py",
    "desktop\config.py",
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
    $FullPath = Join-Path $InstallPath $RelativePath
    if (-not (Test-Path -LiteralPath $FullPath)) {
        throw "Installed NASDX directory is missing required file: $RelativePath"
    }
}

foreach ($Forbidden in @("config.toml", ".env", "nasdx_history.db", "reports")) {
    if (Test-Path -LiteralPath (Join-Path $InstallPath $Forbidden)) {
        throw "Installed app directory contains runtime/user artifact: $Forbidden"
    }
}

if ($CheckShortcuts) {
    $StartMenuShortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\NASDX Desktop\NASDX Desktop.lnk"
    if (-not (Test-Path -LiteralPath $StartMenuShortcut)) {
        throw "Start Menu shortcut is missing: $StartMenuShortcut"
    }
}

$SmokeRuntime = [System.IO.Path]::GetFullPath((Join-Path ([System.IO.Path]::GetTempPath()) ("nasdx-installed-smoke-" + [guid]::NewGuid().ToString("N"))))
New-Item -ItemType Directory -Force -Path $SmokeRuntime | Out-Null
if (-not $NoClean) {
    Remove-PythonCacheArtifacts -RootPath $InstallPath
}

$OldRuntimeDir = $env:NASDX_RUNTIME_DIR
$OldHistoryDb = $env:NASDX_HISTORY_DB
$OldReportsDir = $env:NASDX_REPORTS_DIR

try {
    $env:NASDX_RUNTIME_DIR = $SmokeRuntime
    $env:NASDX_HISTORY_DB = Join-Path $SmokeRuntime "nasdx_history.db"
    $env:NASDX_REPORTS_DIR = Join-Path $SmokeRuntime "reports"

    Push-Location $InstallPath
    try {
        $LauncherOutput = & $PythonExe -B desktop\launcher.py --dry-run --page plan
        if ($LASTEXITCODE -ne 0) {
            throw "Installed launcher dry-run failed"
        }
        $LauncherDryRun = $LauncherOutput | ConvertFrom-Json
        if ([System.IO.Path]::GetFullPath([string]$LauncherDryRun.root) -ne $InstallPath) {
            throw "Installed launcher root is not the install directory: $($LauncherDryRun.root)"
        }
        if ([System.IO.Path]::GetFullPath([string]$LauncherDryRun.runtime_dir) -ne $SmokeRuntime) {
            throw "Installed launcher runtime_dir is not the smoke runtime: $($LauncherDryRun.runtime_dir)"
        }

        $ControlOutput = & $PythonExe -B desktop\control_panel.py --dry-run --page plan
        if ($LASTEXITCODE -ne 0) {
            throw "Installed control panel dry-run failed"
        }
        $ControlDryRun = $ControlOutput | ConvertFrom-Json
        foreach ($Action in @("Start", "Stop", "Open App", "Settings", "Logs", "Data Refresh")) {
            if (-not ($ControlDryRun.actions -contains $Action)) {
                throw "Installed control panel is missing action: $Action"
            }
        }

        $BatchPath = Join-Path $InstallPath "启动NASDX桌面.bat"
        $BatchOutput = & $BatchPath --dry-run --page plan
        if ($LASTEXITCODE -ne 0) {
            throw "Installed desktop batch dry-run failed"
        }
        $BatchDryRun = $BatchOutput | ConvertFrom-Json
        if ([System.IO.Path]::GetFullPath([string]$BatchDryRun.root) -ne $InstallPath) {
            throw "Installed desktop batch dry-run root is not the install directory: $($BatchDryRun.root)"
        }
        foreach ($Action in @("Start", "Stop", "Open App", "Settings", "Logs", "Data Refresh")) {
            if (-not ($BatchDryRun.actions -contains $Action)) {
                throw "Installed desktop batch dry-run is missing action: $Action"
            }
        }

        $DoctorOutput = & $PythonExe -B run_desktop_doctor.py --json
        if ($LASTEXITCODE -ne 0) {
            throw "Installed desktop doctor failed"
        }
        $DoctorChecks = @($DoctorOutput | ConvertFrom-Json)
        $DoctorFailures = @($DoctorChecks | Where-Object { $_.status -eq "FAIL" })
        if ($DoctorFailures) {
            $Labels = ($DoctorFailures | ForEach-Object { $_.label }) -join ", "
            throw "Installed desktop doctor reported failures: $Labels"
        }
        foreach ($RequiredLabel in @("required_files", "desktop_env", "launch_plan")) {
            if (-not ($DoctorChecks | Where-Object { $_.label -eq $RequiredLabel })) {
                throw "Installed desktop doctor did not report required label: $RequiredLabel"
            }
        }

        $CompletionOutput = & $PythonExe -B run_desktop_completion_audit.py --json
        if ($LASTEXITCODE -ne 0) {
            throw "Installed desktop completion audit failed"
        }
        $CompletionChecks = @($CompletionOutput | ConvertFrom-Json)
        $CompletionFailures = @($CompletionChecks | Where-Object { $_.status -eq "FAIL" })
        if ($CompletionFailures) {
            $Labels = ($CompletionFailures | ForEach-Object { $_.label }) -join ", "
            throw "Installed desktop completion audit reported failures: $Labels"
        }
        foreach ($RequiredLabel in @("preserved_entrypoints", "desktop_launcher_mvp", "installer_roundtrip")) {
            if (-not ($CompletionChecks | Where-Object { $_.label -eq $RequiredLabel })) {
                throw "Installed desktop completion audit did not report required label: $RequiredLabel"
            }
        }

        $ShortcutScript = Join-Path $InstallPath "packaging\windows\create_shortcuts.ps1"
        $ShortcutOutput = & powershell -ExecutionPolicy Bypass -File $ShortcutScript -AppDir $InstallPath -Desktop
        if ($LASTEXITCODE -ne 0) {
            throw "Installed shortcut plan-only check failed"
        }
        $ShortcutText = $ShortcutOutput -join [Environment]::NewLine
        if (-not $ShortcutText.Contains("plan-only mode") -or -not $ShortcutText.Contains("启动NASDX桌面.bat")) {
            throw "Installed shortcut plan-only output did not describe NASDX desktop shortcut targets"
        }

        $SmokeOutput = & $PythonExe -B desktop\launcher.py --headless-smoke --timeout $Timeout --no-browser --page plan 2>&1
        if ($LASTEXITCODE -ne 0) {
            $SmokeText = $SmokeOutput -join [Environment]::NewLine
            throw "Installed launcher headless smoke failed:`n$SmokeText"
        }
    } finally {
        Pop-Location
    }

    $AppPy = Join-Path $InstallPath "app.py"
    $Orphans = Get-CimInstance Win32_Process |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine.Contains("streamlit") -and
            $_.CommandLine.Contains($AppPy)
        }
    if ($Orphans) {
        $Ids = ($Orphans | Select-Object -ExpandProperty ProcessId) -join ", "
        throw "Streamlit process still running after installed smoke: $Ids"
    }

    if (Test-Path -LiteralPath (Join-Path $InstallPath "_smoke_runtime")) {
        throw "Installed app directory should not contain _smoke_runtime"
    }

    Write-Host "NASDX installed smoke passed: $InstallPath"
    Write-Host "Smoke python: $PythonExe"
    Write-Host "Smoke runtime: $SmokeRuntime"
} finally {
    $env:NASDX_RUNTIME_DIR = $OldRuntimeDir
    $env:NASDX_HISTORY_DB = $OldHistoryDb
    $env:NASDX_REPORTS_DIR = $OldReportsDir

    if ((Test-Path -LiteralPath $SmokeRuntime) -and -not $NoClean) {
        Remove-Item -LiteralPath $SmokeRuntime -Recurse -Force
    }
    if (-not $NoClean) {
        Remove-PythonCacheArtifacts -RootPath $InstallPath
    }
}
