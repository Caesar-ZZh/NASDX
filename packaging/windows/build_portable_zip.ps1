param(
    [string]$PackageDir = "",
    [string]$OutputZip = "",
    [string]$ChecksumPath = "",
    [string]$ManifestPath = "",
    [switch]$RequireVenv
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

if ([string]::IsNullOrWhiteSpace($PackageDir)) {
    $PackageDir = Join-Path $RepoRoot "dist\NASDX-Desktop"
}
if ([string]::IsNullOrWhiteSpace($OutputZip)) {
    $OutputZip = Join-Path $RepoRoot "dist\NASDX-Desktop-portable.zip"
}

$PackagePath = Resolve-NasdxPath $PackageDir
$ZipPath = Resolve-NasdxPath $OutputZip
if ([string]::IsNullOrWhiteSpace($ChecksumPath)) {
    $ChecksumPath = "$ZipPath.sha256"
}
if ([string]::IsNullOrWhiteSpace($ManifestPath)) {
    $ManifestLeaf = [System.IO.Path]::GetFileNameWithoutExtension($ZipPath) + ".manifest.json"
    $ManifestPath = Join-Path (Split-Path -Parent $ZipPath) $ManifestLeaf
}
$ResolvedChecksumPath = Resolve-NasdxPath $ChecksumPath
$ResolvedManifestPath = Resolve-NasdxPath $ManifestPath

if (-not (Test-Path -LiteralPath $PackagePath)) {
    throw "Package directory does not exist: $PackagePath"
}

$RequiredFiles = @(
    "app.py",
    "启动NASDX桌面.bat",
    "PACKAGING_MANIFEST.json",
    "desktop\control_panel.py",
    "desktop\launcher.py",
    "packaging\windows\smoke_installed.ps1"
)
foreach ($RelativePath in $RequiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $PackagePath $RelativePath))) {
        throw "Package is missing required file before zip: $RelativePath"
    }
}

if ($RequireVenv -and -not (Test-Path -LiteralPath (Join-Path $PackagePath ".venv\Scripts\python.exe"))) {
    throw "Package .venv Python is required before zip but missing"
}

$ForbiddenFiles = @("config.toml", ".env", "nasdx_history.db")
foreach ($RelativePath in $ForbiddenFiles) {
    if (Test-Path -LiteralPath (Join-Path $PackagePath $RelativePath)) {
        throw "Package contains forbidden runtime/user artifact before zip: $RelativePath"
    }
}
$ForbiddenArtifacts = @(Get-ForbiddenPackageArtifacts -RootPath $PackagePath)
if ($ForbiddenArtifacts.Count -gt 0) {
    throw "Package contains forbidden artifact before zip: $($ForbiddenArtifacts -join ', ')"
}

$PackageWithSlash = $PackagePath.TrimEnd("\") + "\"
foreach ($OutputPath in @($ZipPath, $ResolvedChecksumPath, $ResolvedManifestPath)) {
    if ($OutputPath.StartsWith($PackageWithSlash, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to write zip inside package directory or sidecar inside package directory: $OutputPath"
    }
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ZipPath) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ResolvedChecksumPath) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $ResolvedManifestPath) | Out-Null
if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
if (Test-Path -LiteralPath $ResolvedChecksumPath) {
    Remove-Item -LiteralPath $ResolvedChecksumPath -Force
}
if (Test-Path -LiteralPath $ResolvedManifestPath) {
    Remove-Item -LiteralPath $ResolvedManifestPath -Force
}

$TarExe = Get-Command tar.exe -ErrorAction SilentlyContinue
if ($TarExe) {
    $PackageParent = Split-Path -Parent $PackagePath
    $PackageLeaf = Split-Path -Leaf $PackagePath
    & $TarExe.Source -a -cf $ZipPath -C $PackageParent $PackageLeaf
    if ($LASTEXITCODE -ne 0) {
        throw "tar.exe failed with exit code ${LASTEXITCODE}"
    }
    $ZipEngine = "tar.exe"
} else {
    Compress-Archive -LiteralPath $PackagePath -DestinationPath $ZipPath -CompressionLevel Optimal -Force
    $ZipEngine = "Compress-Archive"
}
if (-not (Test-Path -LiteralPath $ZipPath)) {
    throw "Portable zip was not created: $ZipPath"
}
$ZipItem = Get-Item -LiteralPath $ZipPath
if ($ZipItem.Length -le 0) {
    throw "Portable zip is empty: $ZipPath"
}

$ZipHash = Get-NasdxSha256 -PathValue $ZipPath
$ZipName = Split-Path -Leaf $ZipPath
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    $ResolvedChecksumPath,
    "SHA256  $ZipHash  $ZipName$([Environment]::NewLine)",
    $Utf8NoBom
)

$SourceManifestPath = Join-Path $PackagePath "PACKAGING_MANIFEST.json"
$SourceManifest = Get-Content -Raw -Encoding UTF8 $SourceManifestPath | ConvertFrom-Json
$ReleaseManifest = [ordered]@{
    schema = "nasdx_portable_release.v1"
    generated_at = (Get-Date).ToUniversalTime().ToString("o")
    package_dir = $PackagePath
    zip_path = $ZipPath
    zip_name = $ZipName
    zip_size_bytes = [int64]$ZipItem.Length
    zip_sha256 = $ZipHash
    checksum_path = $ResolvedChecksumPath
    manifest_path = $ResolvedManifestPath
    require_venv = [bool]$RequireVenv
    zip_engine = $ZipEngine
    source_packaging_manifest = [ordered]@{
        name = $SourceManifest.name
        generated_at = $SourceManifest.generated_at
        path_policy = $SourceManifest.path_policy
        source_root = $SourceManifest.source_root
        package_root = $SourceManifest.package_root
        skip_dependency_install = $SourceManifest.skip_dependency_install
        include_webview = $SourceManifest.include_webview
        only_binary = $SourceManifest.only_binary
        included_directories = @($SourceManifest.included_directories)
        included_files = @($SourceManifest.included_files)
        excluded_patterns = @($SourceManifest.excluded_patterns)
        scrubbed_patterns = @($SourceManifest.scrubbed_patterns)
    }
}
[System.IO.File]::WriteAllText(
    $ResolvedManifestPath,
    ($ReleaseManifest | ConvertTo-Json -Depth 8),
    $Utf8NoBom
)

Write-Host "NASDX portable zip prepared: $ZipPath"
Write-Host "Source package: $PackagePath"
Write-Host "Zip engine: $ZipEngine"
Write-Host "Zip SHA256: $ZipHash"
Write-Host "Checksum sidecar: $ResolvedChecksumPath"
Write-Host "Release manifest: $ResolvedManifestPath"
Write-Host "Require venv: $([bool]$RequireVenv)"
