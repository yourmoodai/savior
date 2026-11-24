$ErrorActionPreference = 'Stop'
$file = (Resolve-Path 'target/release/savior.exe').Path
$evidence = & "$PSScriptRoot/inspect_windows_signature.ps1" -Path $file | ConvertFrom-Json
$profile = ($env:SAVIOR_RELEASE_CONFIG | ConvertFrom-Json).identities.windows_profile_eku

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
    "Publisher $($evidence.publisher)"
    "Profile EKU $profile"
    "Signer certificate SHA-256 $($evidence.signer_sha256)"
    "Verified timestamp $($evidence.timestamp)"
    "Modified copy rejected with HashMismatch"
) -join "`n`n" | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
