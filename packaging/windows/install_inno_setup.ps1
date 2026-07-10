param(
    [string]$PackageId = "JRSoftware.InnoSetup",
    [switch]$Install,
    [switch]$AcceptAgreements
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InnoPathsScript = Join-Path $ScriptDir "inno_paths.ps1"
. $InnoPathsScript

$ExistingIscc = Get-NasdxIsccPath
if (-not [string]::IsNullOrWhiteSpace($ExistingIscc)) {
    Write-Host "Inno Setup compiler found: $ExistingIscc"
    return
}

$Winget = Get-Command "winget" -ErrorAction SilentlyContinue
if (-not $Winget) {
    throw "winget was not found. Install Inno Setup 7 or 6 manually, then rerun build_installer.ps1."
}

$WingetArgs = @(
    "install",
    "--id",
    $PackageId,
    "-e",
    "--silent"
)
if ($AcceptAgreements) {
    $WingetArgs += @("--accept-package-agreements", "--accept-source-agreements")
}

if (-not $Install) {
    Write-Host "NASDX Inno Setup bootstrap is in plan-only mode."
    Write-Host "winget path: $($Winget.Source)"
    Write-Host "Package id: $PackageId"
    Write-Host "Would run: winget $($WingetArgs -join ' ')"
    Write-Host "Pass -Install -AcceptAgreements to install Inno Setup, then run build_installer.ps1."
    return
}

if (-not $AcceptAgreements) {
    throw "Pass -AcceptAgreements with -Install so winget can run non-interactively."
}

& $Winget.Source @WingetArgs
if ($LASTEXITCODE -ne 0) {
    throw "winget failed with exit code ${LASTEXITCODE}: winget $($WingetArgs -join ' ')"
}

$InstalledIscc = Get-NasdxIsccPath
if ([string]::IsNullOrWhiteSpace($InstalledIscc)) {
    throw "winget finished, but ISCC.exe was not found. Open a new shell or pass -IsccPath to build_installer.ps1."
}

Write-Host "Inno Setup compiler installed: $InstalledIscc"
