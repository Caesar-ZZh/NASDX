param([string]$UvVersion = "0.10.2")

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\..")

$ActualUv = (& uv --version).Split()[1]
if ($LASTEXITCODE -ne 0 -or $ActualUv -ne $UvVersion) {
    throw "uv $UvVersion is required to refresh Windows lockfiles; found $ActualUv"
}

& uv pip compile (Join-Path $RepoRoot "requirements_nasdx.txt") `
    --generate-hashes --python-version 3.11 --python-platform windows `
    --output-file (Join-Path $ScriptDir "requirements-win-core.lock")
if ($LASTEXITCODE -ne 0) { throw "core lock refresh failed" }

& uv pip compile (Join-Path $RepoRoot "requirements_desktop.txt") `
    --generate-hashes --python-version 3.11 --python-platform windows `
    --output-file (Join-Path $ScriptDir "requirements-win-webview.lock")
if ($LASTEXITCODE -ne 0) { throw "WebView lock refresh failed" }

& python (Join-Path $RepoRoot "run_dependency_lock_check.py") --static-only
if ($LASTEXITCODE -ne 0) { throw "lock validation failed" }
