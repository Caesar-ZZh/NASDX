function Get-NasdxQuotedPath {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return ""
    }

    $Trimmed = $Value.Trim()
    if ($Trimmed.StartsWith('"')) {
        $EndQuote = $Trimmed.IndexOf('"', 1)
        if ($EndQuote -gt 1) {
            return $Trimmed.Substring(1, $EndQuote - 1)
        }
    }

    return ($Trimmed -split ",")[0].Trim('"')
}

function Get-NasdxInnoSetupCandidates {
    $Candidates = @(
        (Get-Command "ISCC.exe" -ErrorAction SilentlyContinue).Source,
        (Get-Command "iscc" -ErrorAction SilentlyContinue).Source
    )

    $RegistryKeys = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    foreach ($Key in $RegistryKeys) {
        Get-ItemProperty $Key -ErrorAction SilentlyContinue |
            Where-Object {
                $_.DisplayName -match "Inno Setup" -or
                $_.InstallLocation -match "Inno Setup" -or
                $_.DisplayIcon -match "Inno Setup"
            } |
            ForEach-Object {
                if (-not [string]::IsNullOrWhiteSpace($_.InstallLocation)) {
                    $Candidates += (Join-Path $_.InstallLocation "ISCC.exe")
                }
                foreach ($Value in @($_.DisplayIcon, $_.UninstallString)) {
                    $PathValue = Get-NasdxQuotedPath -Value $Value
                    if (-not [string]::IsNullOrWhiteSpace($PathValue)) {
                        $Parent = Split-Path -Parent $PathValue -ErrorAction SilentlyContinue
                        if (-not [string]::IsNullOrWhiteSpace($Parent)) {
                            $Candidates += (Join-Path $Parent "ISCC.exe")
                        }
                    }
                }
            }
    }

    $Candidates += @(
        "C:\Program Files\Inno Setup 7\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
    )

    return $Candidates |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Select-Object -Unique
}

function Get-NasdxIsccPath {
    param([string]$ConfiguredPath)

    if (-not [string]::IsNullOrWhiteSpace($ConfiguredPath)) {
        if (-not (Test-Path -LiteralPath $ConfiguredPath)) {
            throw "ISCC.exe does not exist: $ConfiguredPath"
        }
        return [System.IO.Path]::GetFullPath($ConfiguredPath)
    }

    foreach ($Candidate in Get-NasdxInnoSetupCandidates) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate)) {
            return [System.IO.Path]::GetFullPath($Candidate)
        }
    }

    return ""
}
