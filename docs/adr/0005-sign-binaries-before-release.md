# 5. Sign platform binaries before release promotion

Date: 2026-10-29

## Status

Proposed. Extends [ADR 2](0002-release-and-quality-evidence.md) without superseding
its artifact-integrity or quality-evidence decisions. These requirements are
not implemented or evidence of platform acceptance.

## Context

The bootstrap's signed manifest establishes integrity relative to the key
trusted through the plugin. Operating systems separately evaluate publisher
identity, notarization, and reputation. Manifest-only distribution avoids
certificate enrollment but leaves platform acceptance unresolved. Platform
signing addresses publisher trust at additional operational cost. Sigstore
could supply CI provenance; adopting it is separate work and cannot replace
platform signatures.

## Decision

The release pipeline would own signing, notarization, assembly, and promotion;
the bootstrap continues to verify signatures and hashes before execution.
Neither moves detection or replacement out of Rust.

Public macOS releases would require Developer ID Application signing and Apple
notarization; Windows would require trusted Authenticode signatures with secure
timestamps. Linux retains Ed25519 manifest verification without a separate
publisher certificate. Every platform keeps the manifest signature. Coverage
includes both executables and native addons; upstream Node signatures must be
preserved and verified, and any addon-loading entitlements justified.

## Invariants & Abstention

- Finalize platform signatures and applicable notarization tickets before
  hashing runtime bytes, signing the manifest, and checksumming the archive.
  Later byte changes require renewed signatures and regenerated integrity data.
- Production signing belongs to approved jobs in a protected environment.
  Qualification uses ephemeral keys without production credentials. Verify
  expected signer identities independently of the candidate's own keys.
- Missing credentials, unexpected identities, expired or revoked certificates,
  failed notarization, incomplete platform coverage, integrity failures, or
  missing evidence block publication or promotion at the relevant gate.
- Immutable release assets must pass packaged-runtime, installed-host, and
  clean-machine checks under normal OS protections. Publish before checking
  real public downloads; promote only after those checks pass.
- Publication must not implicitly promote a candidate through a moving
  `latest` URL. Failed qualification leaves the previous promoted version in
  place. Correct failures with a new version rather than overwriting assets.
- Never require disabling Gatekeeper, Smart App Control, antivirus, or quarantine
  controls. A runtime that cannot be verified or started remains unavailable;
  setup failure cannot create an original-prompt fallback.
- Signing services receive release artifacts, never user prompts. Runtime
  inference stays offline. Signing does not guarantee OS acceptance, warning-free
  execution, provider approval, replacement correctness, or model quality.

## Consequences

Publisher verification and bootstrap integrity would cover the same final bytes.
Maintainers must manage enrollment, renewal, key backups, rotation, revocation,
and service availability. Rotation needs a new trusted plugin version and
verification against the intended identity; compromise blocks promotion until
recovery. Signing changes native bytes and adds CI dependencies.

Clean-machine evidence remains necessary: a new signed publisher may still
receive reputation warnings, and managed machines may impose stricter controls.
Support claims stay scoped to tested conditions.

The [proposed release procedure](../releases.md#proposed-platform-signing)
retains the sequence, references, and unresolved choices for the Windows signer,
Apple identity, notarization packaging, and independent promotion mechanism.
