# 2. Release integrity and quality evidence

Date: 2026-10-20

## Status

Accepted for release qualification and default promotion. This record does not
promote a model, backend, or judge.

## Context

Artifact integrity means the downloaded bytes match the signed inventory.
Replacement correctness means selected spans change while surrounding text is
preserved. Reference labels are judgments attached to evaluation examples;
judges are models that assess those examples. Their agreement depends on what
was tested and how the labels were reviewed.

Artifact integrity, exact replacement, agreement with reference labels, and
agreement between judges answer different questions. A successful package or a
fast experiment cannot establish all of them.

## Decision

Distribute immutable versioned bundles. The installed plugin pins an Ed25519
verification key and signed runtime manifest; setup verifies matching platform
artifacts and separately pinned models before execution. Bundled Node supplies
bootstrap execution without a user compiler, GitHub CLI, or Node on `PATH`.

Promote a new default only with complete evidence for its applicable safety,
parity, quality, and performance gates. Report provider-policy work as
**measured policy alignment**.

### Interception evidence

Keep three interception evidence classes separate. Handler tests use the engine
test kit to check continuation, refusal, cancellation, and draft races.
Installed-candidate tests run the complete, unmodified release archive in each
qualified native Claude client. Instrumented-failure tests use a complete disposable
copy of that candidate, replacing only the bridge entry with a command-aware
test shim. Normal startup and inspection delegate to the candidate; rewrite
faults are armed after readiness. A separate case fails startup readiness.
Source-checkout runs are development evidence and cannot satisfy the installed
candidate gate.

Every required case binds the candidate archive, native client, model files,
harness, instrumentation, platform, and replacement strategy. The release gate
requires success, unchanged input, protected refusal, startup failure, missing
model, malformed output, immediate timeout response, worker exit, and a stalled
worker reaching the production deadline. Recovery checks retained drafts and
the exact subsequent receiver payload. The startup failure case checks refusal
and draft retention only. [Release preparation](../releases.md#interception-evidence)
owns the executable matrix and evidence locations.

### Adapter distribution

For multiple mod APIs, distribute immutable adapter-specific bundles sharing the
same Rust protocol. A future installer must resolve the client selected by the
active profile, then select exactly one published compatibility entry binding
client versions, adapter ID, Savior version, type snapshot, platform, and archive
hash. Preserve the chosen client and profile; never upgrade, downgrade, or reroute
them implicitly. Unknown clients, overlapping entries, and unavailable compatible
artifacts stop installation. Retain an older bundle only for its qualified client
versions; do not use it as a fallback for a newer API.

That distribution selector is not released. Current packaging has one adapter.
Additional clients may share it after review and qualification; a different API
entry needs its own bundle and selection verification before distribution.

### Rolling client qualification

Assess every stable Claude Code release published in the preceding 90 days,
using inclusive UTC publication timestamps. Exclude drafts and prereleases.
Freeze the GitHub release identity and each native npm artifact's URL and SHA-512
integrity before executing the matrix on `darwin-arm64`, `linux-x64`, and
`win32-x64`. Discovery does not change the reviewed compatibility catalog.

Run native version, manifest, and handler probes for each available client.
Clients with a reviewed adapter additionally require its pinned type checks and
all 18 installed cases. Record `passed`, `failed`, `unsupported_api`, or
`unavailable_artifact` for every client/platform pair. `unsupported_api` means
Savior has no reviewed adapter; it does not assert that the upstream API is broken.
Only complete passing evidence can support a catalog entry. Unmapped gaps remain
visible in the compatibility table; missing assessments block qualification.

Candidate builds backfill the whole window. Daily discovery reuses the unchanged
candidate at the same source revision and assesses new releases. Reuse requires
the same candidate, client artifact, adapter, and qualification inputs, and the
aggregator revalidates the original event evidence. Changed candidate, adapter,
model contract, types, or harness requires fresh evidence for every advertised
combination. Run at most four native assessment jobs concurrently and retain
failed attempts and partial events independently of passing results.

Published compatibility metadata names the candidate, adapter, exact clients,
platforms, verification dates, and gaps. Ageing out preserves historical evidence
and never remotely disables an installed mod. A new Savior candidate must remove
out-of-window support claims through review before its release gate can pass.

## Invariants & Abstention

- Runtime inference remains offline; downloads belong to setup.
- Tag builds require the protected production signing key. Qualification builds
  use ephemeral keys and cannot be published as releases.
- Marketplace promotion requires the real published-download matrix to pass.
- Missing, duplicate, reordered, stale, mixed-identity, or incomplete interception
  evidence blocks the installed gate. Preserve content-free partial events and
  failure phases in fresh run directories; never convert a timeout into a pass.
- An injected timeout response proves error handling only. Deadline evidence
  requires a real stalled worker, elapsed time, and observed worker termination.
- Receiver evidence covers the configured loopback destination. Disabling
  nonessential host traffic is a test condition, not evidence of whole-host
  privacy. A continuation or saved trace is not proof of provider receipt.
- The compatibility catalog and selected entry are covered by the release
  inventory. Cache and installation identity must include the adapter selection;
  install only one active Savior adapter per session.
- Exact-output checks establish replacement behavior; reference-label agreement
  depends on the corpus; judge agreement describes judges, not ground truth.
- Missing or uncertain evidence holds the existing default. Measurements do not
  establish certification, provider approval, or enforcement guarantees.

## Consequences

Releases and claims retain a provenance trail readers can inspect. Qualification
requires more than one reassuring score and can withhold a fast candidate.
The rolling window increases native execution and artifact-storage costs. It
also exposes older releases without a compatible Savior adapter instead of
silently treating patch releases as interchangeable.

### Open questions

Reviewed corpus coverage, additional backends, and host routes beyond this
matrix remain open questions for claims beyond the existing evidence.

[Evaluation](../evaluation.md) owns recorded findings,
[benchmarking](../benchmarking.md) owns reproduction, and
[release preparation](../releases.md) owns operational procedure.
