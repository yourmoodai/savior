param([Parameter(Mandatory=$true)][string]$Path, [string]$ExpectedFingerprint)
$ErrorActionPreference = 'Stop'
$file = (Resolve-Path -LiteralPath $Path).Path
$signature = Get-AuthenticodeSignature -LiteralPath $file
if ($signature.Status -ne 'Valid' -or $null -eq $signature.TimeStamperCertificate) {
    throw 'invalid_authenticode_signature_or_timestamp'
}
$certificate = $signature.SignerCertificate
$fingerprint = $certificate.GetCertHashString('SHA256').ToLowerInvariant()
$publisher = $certificate.GetNameInfo('SimpleName', $false)
$expectedProfile = $null
if ($ExpectedFingerprint) {
    if ($ExpectedFingerprint -cne $fingerprint) { throw 'unexpected_upstream_signer' }
} else {
    $expectedProfile = ($env:SAVIOR_RELEASE_CONFIG | ConvertFrom-Json).identities.windows_profile_eku
    if (!$expectedProfile -or $publisher -cne 'Your Mood, Inc.' -or
        $certificate.Extensions['2.5.29.37'].EnhancedKeyUsages.Value -notcontains $expectedProfile) {
        throw 'unexpected_savior_signer'
    }
}
$sdk = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/bin'
$signtool = Get-ChildItem "$sdk/*/x64/signtool.exe" | Sort-Object FullName -Descending | Select-Object -First 1
$verification = & $signtool.FullName verify /pa /v /tw $file
if ($LASTEXITCODE -ne 0) { throw 'windows_timestamp_verification_failed' }
$timestamp = $verification | Select-String '^\s*The signature is timestamped:\s*(.+)$'
if (@($timestamp).Count -ne 1) { throw 'missing_verified_timestamp' }
@{
    publisher = $publisher
    profile_eku = $expectedProfile
    signer_sha256 = $fingerprint
    timestamp = $timestamp.Matches[0].Groups[1].Value
    timestamp_signer_sha256 = $signature.TimeStamperCertificate.GetCertHashString('SHA256').ToLowerInvariant()
} | ConvertTo-Json -Compress
