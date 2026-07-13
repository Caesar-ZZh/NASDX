param(
    [string]$ZipPath = "",
    [string]$ChecksumPath = "",
    [string]$ManifestPath = "",
    [string]$ExtractRoot = "",
    [int]$Timeout = 60,
    [switch]$RequireVenv,
    [switch]$NoClean
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
. (Join-Path $ScriptDir "hash_utils.ps1")

function Resolve-NasdxPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PathValue
    )

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $PathValue))
}

function Get-PortableZipHash {
    if ($script:PortableZipHash) {
        return $script:PortableZipHash
    }
    $script:PortableZipHash = Get-NasdxSha256 -PathValue $ResolvedZip
    return $script:PortableZipHash
}

function Get-ForbiddenPackageArtifacts {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RootPath
    )

    $ForbiddenDirectoryNames = @(
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
    $ForbiddenFilePatterns = @(
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

    $Findings = New-Object System.Collections.Generic.List[string]
    Get-ChildItem -LiteralPath $RootPath -Directory -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object { $ForbiddenDirectoryNames -contains $_.Name } |
        ForEach-Object {
            $Findings.Add($_.FullName.Substring($RootPath.Length).TrimStart("\").Replace("\", "/"))
        }
    foreach ($Pattern in $ForbiddenFilePatterns) {
        Get-ChildItem -LiteralPath $RootPath -File -Recurse -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like $Pattern } |
            ForEach-Object {
                $Findings.Add($_.FullName.Substring($RootPath.Length).TrimStart("\").Replace("\", "/"))
            }
    }
    $GeneratedModel = Join-Path $RootPath "models\signal_confidence.json"
    if (Test-Path -LiteralPath $GeneratedModel) {
        $Findings.Add("models/signal_confidence.json")
    }
    return @($Findings | Sort-Object -Unique)
}

if ([string]::IsNullOrWhiteSpace($ZipPath)) {
    $ZipPath = Join-Path $RepoRoot "dist\NASDX-Desktop-portable.zip"
}
$ChecksumPathProvided = -not [string]::IsNullOrWhiteSpace($ChecksumPath)
$ManifestPathProvided = -not [string]::IsNullOrWhiteSpace($ManifestPath)

$ResolvedZip = Resolve-NasdxPath $ZipPath
if (-not (Test-Path -LiteralPath $ResolvedZip)) {
    throw "Portable zip does not exist: $ResolvedZip"
}
if (-not $ChecksumPathProvided) {
    $ChecksumPath = "$ResolvedZip.sha256"
}
if (-not $ManifestPathProvided) {
    $ManifestLeaf = [System.IO.Path]::GetFileNameWithoutExtension($ResolvedZip) + ".manifest.json"
    $ManifestPath = Join-Path (Split-Path -Parent $ResolvedZip) $ManifestLeaf
}
$ResolvedChecksumPath = Resolve-NasdxPath $ChecksumPath
$ResolvedManifestPath = Resolve-NasdxPath $ManifestPath

if (Test-Path -LiteralPath $ResolvedChecksumPath) {
    $ChecksumText = Get-Content -Raw -Encoding UTF8 $ResolvedChecksumPath
    $ChecksumMatch = [System.Text.RegularExpressions.Regex]::Match($ChecksumText, "[A-Fa-f0-9]{64}")
    if (-not $ChecksumMatch.Success) {
        throw "Checksum sidecar does not contain a SHA256 value: $ResolvedChecksumPath"
    }
    $ExpectedHash = $ChecksumMatch.Value.ToLowerInvariant()
    $ActualHash = Get-PortableZipHash
    if ($ActualHash -ne $ExpectedHash) {
        throw "Portable zip checksum mismatch: expected $ExpectedHash but got $ActualHash"
    }
    Write-Host "Portable zip checksum verified: $ActualHash"
} elseif ($ChecksumPathProvided) {
    throw "Checksum sidecar does not exist: $ResolvedChecksumPath"
}

if (Test-Path -LiteralPath $ResolvedManifestPath) {
    $ReleaseManifest = Get-Content -Raw -Encoding UTF8 $ResolvedManifestPath | ConvertFrom-Json
    if ($ReleaseManifest.schema -ne "nasdx_portable_release.v1") {
        throw "Portable zip manifest schema is invalid: $($ReleaseManifest.schema)"
    }
    $ActualHash = Get-PortableZipHash
    if ([string]$ReleaseManifest.zip_sha256 -ne $ActualHash) {
        throw "Portable zip manifest hash mismatch: expected $($ReleaseManifest.zip_sha256) but got $ActualHash"
    }
    $ActualSize = (Get-Item -LiteralPath $ResolvedZip).Length
    if ([int64]$ReleaseManifest.zip_size_bytes -ne [int64]$ActualSize) {
        throw "Portable zip manifest size mismatch: expected $($ReleaseManifest.zip_size_bytes) but got $ActualSize"
    }
    if ($RequireVenv -and -not [bool]$ReleaseManifest.require_venv) {
        throw "Portable zip manifest does not prove a dependency-contained package"
    }
    Write-Host "Portable zip manifest verified: $ResolvedManifestPath"
} elseif ($ManifestPathProvided) {
    throw "Portable zip manifest does not exist: $ResolvedManifestPath"
}

if ([string]::IsNullOrWhiteSpace($ExtractRoot)) {
    $ExtractRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("nasdx-portable-zip-smoke-" + [guid]::NewGuid().ToString("N"))
}
$ExtractPath = Resolve-NasdxPath $ExtractRoot

if ((Test-Path -LiteralPath $ExtractPath) -and -not $NoClean) {
    Remove-Item -LiteralPath $ExtractPath -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $ExtractPath | Out-Null

try {
    $TarExe = Get-Command tar.exe -ErrorAction SilentlyContinue
    if ($TarExe) {
        & $TarExe.Source -xf $ResolvedZip -C $ExtractPath
        if ($LASTEXITCODE -ne 0) {
            throw "tar.exe failed to extract portable zip with exit code ${LASTEXITCODE}"
        }
    } else {
        Expand-Archive -LiteralPath $ResolvedZip -DestinationPath $ExtractPath -Force
    }

    $PackageRoot = Join-Path $ExtractPath "NASDX-Desktop"
    if (-not (Test-Path -LiteralPath (Join-Path $PackageRoot "启动NASDX桌面.bat"))) {
        $PackageCandidates = @(
            Get-ChildItem -LiteralPath $ExtractPath -Directory -Force -ErrorAction SilentlyContinue |
                Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "启动NASDX桌面.bat") }
        )
        if ($PackageCandidates.Count -eq 1) {
            $PackageRoot = $PackageCandidates[0].FullName
        } else {
            $PackageRoot = $ExtractPath
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $PackageRoot "启动NASDX桌面.bat"))) {
        throw "Extracted zip does not contain 启动NASDX桌面.bat"
    }

    $ForbiddenArtifacts = @(Get-ForbiddenPackageArtifacts -RootPath $PackageRoot)
    if ($ForbiddenArtifacts.Count -gt 0) {
        throw "Extracted package contains forbidden runtime/cache/build artifact: $($ForbiddenArtifacts -join ', ')"
    }

    $SmokeScript = Join-Path $PackageRoot "packaging\windows\smoke_installed.ps1"
    if (-not (Test-Path -LiteralPath $SmokeScript)) {
        throw "Extracted package is missing smoke_installed.ps1"
    }

    $SmokeArgs = @(
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $SmokeScript,
        "-InstallDir",
        $PackageRoot,
        "-Timeout",
        [string]$Timeout
    )
    if ($RequireVenv) {
        $SmokeArgs += "-RequireVenv"
    }
    & powershell @SmokeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Extracted portable zip smoke failed"
    }

    Write-Host "NASDX portable zip smoke passed: $ResolvedZip"
    Write-Host "Extracted package: $PackageRoot"
    Write-Host "Extract engine: $(if ($TarExe) { 'tar.exe' } else { 'Expand-Archive' })"
} finally {
    if ((Test-Path -LiteralPath $ExtractPath) -and -not $NoClean) {
        Remove-Item -LiteralPath $ExtractPath -Recurse -Force
    }
}
