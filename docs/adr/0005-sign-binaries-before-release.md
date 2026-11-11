# 5. Sign platform binaries before release promotion

Date: 2026-10-29

## Status

Accepted for implementation. Extends [ADR 2](0002-release-and-quality-evidence.md).
Production identities remain to be provisioned; implementation and synthetic
tests do not establish credentialed OS acceptance.

## Context

An Ed25519 manifest establishes integrity relative to the installed plugin's
trusted key. Operating systems separately assess publisher identity and
downloaded executable trust. Savior needs both boundaries to accept the same
final bytes, including its native addon and bundled Node runtime.

Standalone macOS executables cannot carry stapled notarization tickets. A
notarized ZIP alone leaves first launch dependent on Apple's network services.
A complete application bundle can carry a ticket and retain Node's upstream
signature. Windows requires its own trusted Authenticode signatures and secure
timestamps. Sigstore provenance could supplement these checks, but would not
replace either platform's publisher identity.

## Decision

Build native artifacts on their supported hosts. A pinned Linux signing toolchain
consumes all three stages from one revision, signs Savior's native components,
preserves and verifies upstream Node signatures, and notarizes and staples the
complete macOS application bundle. Its headless launcher forwards arguments,
streams, signals, and exit behavior to the bundled Node process.

Manifest schema 2 includes the complete macOS bundle inventory inside the ZIP;
Windows and Linux runtime payloads retain detached, verified setup downloads.
Schema 1 remains readable for existing releases. Calculate hashes after all
platform signing and stapling, then sign both the runtime manifest and an
inventory covering the final ZIP, marketplace entry, detached runtime files,
and pinned model hashes.

Static verification produces a candidate. Fresh native hosts must independently
check OS trust and execute that exact candidate. macOS must pass its first
quarantined launch without external networking after all model files have been
downloaded. Windows must validate expected Authenticode identities and timestamps.
Installed-host tests and the public-download matrix remain required.
Unmodified-candidate evidence binds the final archive bytes. A fault-injected
copy is labeled separately and retains its source candidate hash; its results
cannot stand in for executing the signed candidate unchanged.

Publish immutable assets without advertising them as latest. A separate,
maintainer-approved workflow verifies the completed release run and signed
assets, then atomically advances the protected marketplace branch. It records
the revision, run attempt, and hashes. Approval binds to the reviewed marketplace
head, so a stale promotion cannot overwrite a newer decision.

## Invariants & Abstention

- Signing and stapling precede final hashes. Any byte change requires renewed
  signatures and evidence, with a new version after publication.
- Qualification uses ephemeral keys without production credentials.
- Missing credentials, unexpected identities, failed notarization or timestamps,
  integrity failures, incomplete native coverage, stale run attempts, and changed
  marketplace heads stop publication or promotion at the applicable boundary.
- Preserve OS protections and quarantine. A runtime that cannot be verified or
  launched remains unavailable; failures never forward the original prompt.
- Runtime inference remains offline. Release services receive software artifacts,
  never user prompts.
- A failed public-download check leaves the existing marketplace unchanged.
- Signing establishes scoped integrity and publisher evidence, not replacement
  correctness, provider approval, model quality, or universal OS acceptance.

## Consequences

Users receive one verified macOS bundle supporting an offline first launch
after setup. The larger ZIP still must fit the host's archive size limit.
A Linux toolchain centralizes signing interfaces, but native OS checks remain
mandatory because cross-platform verification has narrower coverage.

Maintainers must own enrollment, renewal, credential custody, rotation,
revocation, and provider availability. Node certificate updates require explicit
identity review. Windows reputation and managed-machine policies can still
block an otherwise valid signed release. Support claims must state conditions
actually verified.

[Release preparation](../releases.md) owns provisioning, evidence, and recovery.
[ADR 1](0001-local-span-replacement.md) retains the transformation boundary;
ADR 2 continues to govern quality evidence independently.
