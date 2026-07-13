function Get-NasdxSha256 {
    param([Parameter(Mandatory = $true)][string]$PathValue)

    $Stream = [System.IO.File]::OpenRead([System.IO.Path]::GetFullPath($PathValue))
    $Sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $HashBytes = $Sha256.ComputeHash($Stream)
        return ([System.BitConverter]::ToString($HashBytes)).Replace("-", "").ToLowerInvariant()
    } finally {
        $Sha256.Dispose()
        $Stream.Dispose()
    }
}
