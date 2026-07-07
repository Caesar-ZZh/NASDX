param(
    [string]$OutputDir = "",
    [switch]$IncludeWebView,
    [int]$PipTimeout = 60,
    [int]$PipRetries = 2,
    [string]$PipIndexUrl = "",
    [string]$ConstraintsFile = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")
$RepoRootPath = [System.IO.Path]::GetFullPath($RepoRoot)

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $RepoRootPath "wheelhouse\nasdx-win-py311"
}

if ([System.IO.Path]::IsPathRooted($OutputDir)) {
    $Target = [System.IO.Path]::GetFullPath($OutputDir)
} else {
    $Target = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputDir))
}

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

New-Item -ItemType Directory -Force -Path $Target | Out-Null

$WheelArgs = @(
    "-m",
    "pip",
    "wheel",
    "--prefer-binary",
    "--disable-pip-version-check",
    "--timeout",
    [string]$PipTimeout,
    "--retries",
    [string]$PipRetries,
    "--wheel-dir",
    $Target
)

if (-not [string]::IsNullOrWhiteSpace($ConstraintsFile)) {
    $WheelArgs += @("--constraint", $ConstraintsFile)
}
if (-not [string]::IsNullOrWhiteSpace($PipIndexUrl)) {
    $WheelArgs += @("--index-url", $PipIndexUrl)
}

Invoke-Checked -FilePath "python" -Arguments ($WheelArgs + @("-r", (Join-Path $RepoRootPath "requirements_nasdx.txt")))
if ($IncludeWebView) {
    Invoke-Checked -FilePath "python" -Arguments ($WheelArgs + @("-r", (Join-Path $RepoRootPath "requirements_desktop.txt")))
}

Write-Host "NASDX wheelhouse prepared: $Target"
Write-Host "WebView dependency included: $([bool]$IncludeWebView)"
