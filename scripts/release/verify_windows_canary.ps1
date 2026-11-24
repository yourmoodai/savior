$ErrorActionPreference = 'Stop'
$file = (Resolve-Path 'target/release/savior.exe').Path
$signature = Get-AuthenticodeSignature -LiteralPath $file
$config = $env:SAVIOR_RELEASE_CONFIG | ConvertFrom-Json
$profile = $config.identities.windows_profile_eku
$publisher = $signature.SignerCertificate.GetNameInfo('SimpleName', $false)
if ($signature.Status -ne 'Valid' -or $publisher -cne 'Your Mood, Inc.' -or
    $null -eq $signature.TimeStamperCertificate -or !$profile -or
    $signature.SignerCertificate.Extensions['2.5.29.37'].EnhancedKeyUsages.Value -notcontains $profile) {
    throw 'invalid_windows_canary_signature'
}
$sdk = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits/10/bin'
$signtool = Get-ChildItem "$sdk/*/x64/signtool.exe" | Sort-Object FullName -Descending | Select-Object -First 1
$verification = & $signtool.FullName verify /pa /v /tw $file
if ($LASTEXITCODE -ne 0) { throw 'windows_timestamp_verification_failed' }
$timestamp = $verification | Select-String '^\s*The signature is timestamped:\s*(.+)$'
if (@($timestamp).Count -ne 1) { throw 'missing_verified_timestamp' }

$copy = Join-Path $env:RUNNER_TEMP 'savior-tampered.exe'
try {
    Copy-Item -LiteralPath $file -Destination $copy
    $stream = [IO.File]::Open($copy, 'Open', 'ReadWrite')
    try {
        # DOS stub bytes are covered by the PE digest.
        $stream.Position = 64
        $byte = $stream.ReadByte()
        $stream.Position = 64
        $stream.WriteByte($byte -bxor 1)
    } finally { $stream.Dispose() }
    if ((Get-AuthenticodeSignature -LiteralPath $copy).Status -ne 'HashMismatch') {
        throw 'expected_tampered_signature_hash_mismatch'
    }
} finally { Remove-Item -LiteralPath $copy -ErrorAction SilentlyContinue }

@(
    "Reviewed commit $env:GITHUB_SHA"
    "Signed binary SHA-256 $((Get-FileHash $file -Algorithm SHA256).Hash)"
    "Publisher $publisher"
    "Profile EKU $profile"
    "Signer certificate SHA-256 $($signature.SignerCertificate.GetCertHashString('SHA256'))"
    "Verified timestamp $($timestamp.Matches[0].Groups[1].Value)"
    "Modified copy rejected with HashMismatch"
) -join "`n`n" | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
