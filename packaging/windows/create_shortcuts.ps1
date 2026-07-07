param(
    [string]$AppDir = "",
    [string]$Name = "NASDX Desktop",
    [switch]$Desktop,
    [switch]$Remove,
    [switch]$Apply
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DefaultAppDir = Resolve-Path (Join-Path $ScriptDir "..\..")

function Resolve-AbsolutePath {
    param([Parameter(Mandatory = $true)][string]$PathValue)

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $PathValue))
}

function Assert-AppDir {
    param([Parameter(Mandatory = $true)][string]$PathValue)

    foreach ($RelativePath in @("app.py", "desktop\control_panel.py", "desktop\launcher.py", "启动NASDX桌面.bat")) {
        $FullPath = Join-Path $PathValue $RelativePath
        if (-not (Test-Path -LiteralPath $FullPath)) {
            throw "NASDX app directory is missing required file: $RelativePath"
        }
    }
}

function New-NasdxShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$ShortcutPath,
        [Parameter(Mandatory = $true)][string]$TargetPath,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$Description
    )

    $ShortcutDir = Split-Path -Parent $ShortcutPath
    New-Item -ItemType Directory -Force -Path $ShortcutDir | Out-Null

    $Shell = New-Object -ComObject WScript.Shell
    $Shortcut = $Shell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = $TargetPath
    $Shortcut.WorkingDirectory = $WorkingDirectory
    $Shortcut.Description = $Description
    $Shortcut.Save()
}

if ([string]::IsNullOrWhiteSpace($AppDir)) {
    $AppDir = [string]$DefaultAppDir
}
$AppPath = Resolve-AbsolutePath -PathValue $AppDir
Assert-AppDir -PathValue $AppPath

$LaunchBat = Join-Path $AppPath "启动NASDX桌面.bat"
$StartMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\NASDX Desktop"
$StartMenuShortcut = Join-Path $StartMenuDir "$Name.lnk"
$ShortcutTargets = @($StartMenuShortcut)

if ($Desktop) {
    $DesktopDir = [Environment]::GetFolderPath("Desktop")
    if ([string]::IsNullOrWhiteSpace($DesktopDir)) {
        throw "Cannot resolve current user's Desktop folder."
    }
    $ShortcutTargets += (Join-Path $DesktopDir "$Name.lnk")
}

if (-not $Apply) {
    Write-Host "NASDX shortcut setup is in plan-only mode."
    Write-Host "App directory: $AppPath"
    Write-Host "Launch target: $LaunchBat"
    if ($Remove) {
        Write-Host "Would remove shortcuts:"
    } else {
        Write-Host "Would create shortcuts:"
    }
    foreach ($ShortcutPath in $ShortcutTargets) {
        Write-Host "- $ShortcutPath"
    }
    Write-Host "Pass -Apply to write shortcuts for the current Windows user."
    return
}

if ($Remove) {
    foreach ($ShortcutPath in $ShortcutTargets) {
        if (Test-Path -LiteralPath $ShortcutPath) {
            Remove-Item -LiteralPath $ShortcutPath -Force
            Write-Host "Removed shortcut: $ShortcutPath"
        } else {
            Write-Host "Shortcut already absent: $ShortcutPath"
        }
    }
    if ((Test-Path -LiteralPath $StartMenuDir) -and -not (Get-ChildItem -LiteralPath $StartMenuDir -Force | Select-Object -First 1)) {
        Remove-Item -LiteralPath $StartMenuDir -Force
        Write-Host "Removed empty Start Menu folder: $StartMenuDir"
    }
    return
}

foreach ($ShortcutPath in $ShortcutTargets) {
    New-NasdxShortcut `
        -ShortcutPath $ShortcutPath `
        -TargetPath $LaunchBat `
        -WorkingDirectory $AppPath `
        -Description "Launch NASDX Desktop"
    Write-Host "Created shortcut: $ShortcutPath"
}
