param(
    [string]$PackageDir = "",
    [string]$ZipPath = "",
    [string]$ChecksumPath = "",
    [string]$ManifestPath = "",
    [string]$InstallerOutputDir = "",
    [string]$IsccPath = "",
    [switch]$RequireVenv,
    [switch]$Strict
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$RepoRootPath = [System.IO.Path]::GetFullPath($RepoRoot)
$InnoPathsScript = Join-Path $ScriptDir "inno_paths.ps1"
. $InnoPathsScript
. (Join-Path $ScriptDir "hash_utils.ps1")

function Resolve-NasdxPath {
    param([Parameter(Mandatory = $true)][string]$PathValue)

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $PathValue))
}

function Get-IsccPath {
    param([string]$ConfiguredPath)

    return Get-NasdxIsccPath -ConfiguredPath $ConfiguredPath
}

function Add-Check {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$Detail
    )

    $script:Checks += [pscustomobject]@{
        status = $Status
        label = $Label
        detail = $Detail
    }
}

if ([string]::IsNullOrWhiteSpace($PackageDir)) {
    $PackageDir = Join-Path $RepoRootPath "dist\NASDX-Desktop"
}
if ([string]::IsNullOrWhiteSpace($ZipPath)) {
    $ZipPath = Join-Path $RepoRootPath "dist\NASDX-Desktop-portable.zip"
}
if ([string]::IsNullOrWhiteSpace($InstallerOutputDir)) {
    $InstallerOutputDir = Join-Path $RepoRootPath "dist\installer"
}

$PackagePath = Resolve-NasdxPath $PackageDir
$ResolvedZipPath = Resolve-NasdxPath $ZipPath
$ResolvedInstallerOutput = Resolve-NasdxPath $InstallerOutputDir
if ([string]::IsNullOrWhiteSpace($ChecksumPath)) {
    $ChecksumPath = "$ResolvedZipPath.sha256"
}
if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
    $ManifestLeaf = [System.IO.Path]::GetFileNameWithoutExtension($ResolvedZipPath) + ".manifest.json"
    $ManifestPath = Join-Path (Split-Path -Parent $ResolvedZipPath) $ManifestLeaf
}
$ResolvedChecksumPath = Resolve-NasdxPath $ChecksumPath
$ResolvedManifestPath = Resolve-NasdxPath $ManifestPath

$Checks = @()

if (-not (Test-Path -LiteralPath $PackagePath)) {
    Add-Check "INCOMPLETE" "portable_package" "missing: $PackagePath"
} else {
    $RequiredPackageFiles = @(
        "app.py",
        "desktop\control_panel.py",
        "desktop\launcher.py",
        "requirements_nasdx.txt",
        "启动NASDX桌面.bat",
        "PACKAGING_MANIFEST.json"
    )
    $MissingPackageFiles = @(
        $RequiredPackageFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $PackagePath $_)) }
    )
    if ($MissingPackageFiles) {
        Add-Check "FAIL" "portable_package" ("missing required files: " + ($MissingPackageFiles -join ", "))
    } else {
        Add-Check "PASS" "portable_package" "required package files present: $PackagePath"
    }

    $PackageManifestPath = Join-Path $PackagePath "PACKAGING_MANIFEST.json"
    if (Test-Path -LiteralPath $PackageManifestPath) {
        $PackageManifest = Get-Content -Raw -Encoding UTF8 $PackageManifestPath | ConvertFrom-Json
        if ($RequireVenv -and [bool]$PackageManifest.skip_dependency_install) {
            Add-Check "FAIL" "portable_manifest" "manifest was built with -SkipDependencyInstall"
        } else {
            Add-Check "PASS" "portable_manifest" "skip_dependency_install=$($PackageManifest.skip_dependency_install)"
        }
    }

    $VenvPython = Join-Path $PackagePath ".venv\Scripts\python.exe"
    if ($RequireVenv) {
        if (Test-Path -LiteralPath $VenvPython) {
            Add-Check "PASS" "portable_venv" "bundled Python present: $VenvPython"
        } else {
            Add-Check "FAIL" "portable_venv" "bundled Python missing: $VenvPython"
        }
    } elseif (Test-Path -LiteralPath $VenvPython) {
        Add-Check "PASS" "portable_venv" "bundled Python present: $VenvPython"
    } else {
        Add-Check "WARN" "portable_venv" "bundled Python not required for this preflight"
    }
}

if (-not (Test-Path -LiteralPath $ResolvedZipPath)) {
    Add-Check "INCOMPLETE" "portable_zip" "zip not found: $ResolvedZipPath"
} else {
    $ZipItem = Get-Item -LiteralPath $ResolvedZipPath
    Add-Check "PASS" "portable_zip" "zip exists; bytes=$($ZipItem.Length)"
    $ActualHash = Get-NasdxSha256 -PathValue $ResolvedZipPath

    if (Test-Path -LiteralPath $ResolvedChecksumPath) {
        $ChecksumText = Get-Content -Raw -Encoding UTF8 $ResolvedChecksumPath
        $ChecksumMatch = [System.Text.RegularExpressions.Regex]::Match($ChecksumText, "[A-Fa-f0-9]{64}")
        if ($ChecksumMatch.Success -and $ChecksumMatch.Value.ToLowerInvariant() -eq $ActualHash) {
            Add-Check "PASS" "portable_zip_checksum" "SHA256 verified: $ActualHash"
        } else {
            Add-Check "FAIL" "portable_zip_checksum" "checksum sidecar does not match zip hash"
        }
    } else {
        Add-Check "INCOMPLETE" "portable_zip_checksum" "missing: $ResolvedChecksumPath"
    }

    if (Test-Path -LiteralPath $ResolvedManifestPath) {
        $ReleaseManifest = Get-Content -Raw -Encoding UTF8 $ResolvedManifestPath | ConvertFrom-Json
        $ManifestProblems = @()
        if ($ReleaseManifest.schema -ne "nasdx_portable_release.v1") {
            $ManifestProblems += "schema=$($ReleaseManifest.schema)"
        }
        if ([string]$ReleaseManifest.zip_sha256 -ne $ActualHash) {
            $ManifestProblems += "zip_sha256 mismatch"
        }
        if ([int64]$ReleaseManifest.zip_size_bytes -ne [int64]$ZipItem.Length) {
            $ManifestProblems += "zip_size_bytes mismatch"
        }
        if ($RequireVenv -and -not [bool]$ReleaseManifest.require_venv) {
            $ManifestProblems += "require_venv=false"
        }
        if ($ManifestProblems) {
            Add-Check "FAIL" "portable_zip_manifest" ($ManifestProblems -join "; ")
        } else {
            Add-Check "PASS" "portable_zip_manifest" "manifest verified: $ResolvedManifestPath"
        }
    } else {
        Add-Check "INCOMPLETE" "portable_zip_manifest" "missing: $ResolvedManifestPath"
    }
}

$Compiler = Get-IsccPath -ConfiguredPath $IsccPath
if ([string]::IsNullOrWhiteSpace($Compiler)) {
    Add-Check "INCOMPLETE" "inno_setup" "ISCC.exe not found; run install_inno_setup.ps1 or pass -IsccPath"
} else {
    Add-Check "PASS" "inno_setup" "ISCC found: $Compiler"
}

if (Test-Path -LiteralPath (Join-Path $ScriptDir "NASDX-Desktop.iss")) {
    Add-Check "PASS" "installer_script" "NASDX-Desktop.iss present"
} else {
    Add-Check "FAIL" "installer_script" "NASDX-Desktop.iss missing"
}

Write-Host "NASDX installer release preflight"
foreach ($Check in $Checks) {
    Write-Host "[$($Check.status)] $($Check.label): $($Check.detail)"
}

Write-Host "Next compile command:"
Write-Host "powershell -ExecutionPolicy Bypass -File packaging\windows\build_installer.ps1 -SkipPortableBuild"
Write-Host "Next roundtrip command:"
Write-Host "powershell -ExecutionPolicy Bypass -File packaging\windows\smoke_installer_roundtrip.ps1 -InstallerPath dist\installer\NASDX-Desktop-Setup.exe -AllowInstall -CheckShortcuts -RequireVenv -Timeout 60"
Write-Host "Expected roundtrip proof:"
Write-Host "dist\installer\NASDX-Desktop-roundtrip-proof.json"
Write-Host "Installer output directory: $ResolvedInstallerOutput"

$HasFail = @($Checks | Where-Object { $_.status -eq "FAIL" }).Count -gt 0
$HasIncomplete = @($Checks | Where-Object { $_.status -eq "INCOMPLETE" }).Count -gt 0
if ($HasFail -or ($Strict -and $HasIncomplete)) {
    exit 1
}
