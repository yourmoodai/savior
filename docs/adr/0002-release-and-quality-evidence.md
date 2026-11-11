# 2. Release integrity and quality evidence

Date: 2026-10-20

## Status

Accepted for release qualification and default promotion. This record does not
promote a model, backend, or judge.
[ADR 5](0005-sign-binaries-before-release.md) proposes additional platform
signing and promotion requirements that are not yet implemented.

## Context

Artifact integrity, exact replacement, agreement with reference labels, and
agreement between judges answer different questions. A successful package or a
fast experiment cannot establish all of them.

## Decision

Distribute immutable versioned bundles. The installed plugin pins an Ed25519
verification key and signed runtime manifest; setup verifies matching platform
artifacts and separately pinned models before execution. Bundled Node supplies
bootstrap execution without a user compiler, GitHub CLI, or PATH Node.

Promote a new default only with complete evidence for its applicable safety,
parity, quality, and performance gates. Report provider-policy work as
**measured policy alignment**.

## Invariants & Abstention

- Runtime inference remains offline; downloads belong to setup.
- Tag builds require the protected production signing key. Qualification builds
  use ephemeral keys and cannot be published as releases.
- Marketplace promotion requires the real published-download matrix to pass.
- Exact-output checks establish replacement behavior; reference-label agreement
  depends on the corpus; judge agreement describes judges, not ground truth.
- Missing or uncertain evidence holds the existing default. Measurements do not
  establish certification, provider approval, or enforcement guarantees.

## Consequences

Releases and claims retain a provenance trail readers can inspect. Qualification
requires more than one reassuring score and can withhold a fast candidate.

The required reviewed corpus, backend coverage, and installed-host coverage
remain open questions for claims beyond the existing evidence.
[Evaluation](../evaluation.md) owns recorded findings,
[benchmarking](../benchmarking.md) owns reproduction, and
[release preparation](../releases.md) owns operational procedure.
