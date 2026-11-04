# 1. User-controlled prompt boundary

Date: 2026-10-12

## Status

Accepted. Savior's local transformation core is the product boundary; Claude
Code is the current adapter. [ADR 4](0004-consented-local-traces.md) supersedes
only this record's original prohibition on persistence, permitting explicitly
consented local inspection. [ADR 2](0002-release-and-quality-evidence.md) owns
release evidence; [ADR 3](0003-future-egress-enforcement.md) owns the planned proxy.

## Context

A draft is unsent text in the host's prompt box. Prompt input is user-authored
material prepared for an AI request. An adapter connects that host or transport
to Savior's local replacement core before a submission continues to a provider.

Users need control at that boundary without depending on one host forever.
Savior cannot establish control over unsubmitted drafts outside its process,
host telemetry, or provider policy.

## Decision

Rust owns detection, span normalization, protected-text checks, replacement, and
content-free errors. Adapters transport requests and results without duplicating
text semantics. The Claude Code integration supplies the current submission
path; future adapters must preserve the same contract.

## Invariants & Abstention

- Forward transformed content only after successful processing. Protected-span
  conflicts, malformed requests, incompatible artifacts, and processing failures
  block the attempt; none permits an original-text fallback.
- Preserve unmatched text exactly. Runtime inference stays local and offline.
- Original prompts never enter logs, errors, or training data. Persistence is
  prohibited except under ADR 4's separate consent and retention contract.
- A disabled or bypassed adapter provides no protection. Detection can miss
  unwanted language or select harmless text; the core cannot guarantee delivery,
  complete host privacy, or provider acceptance.

## Consequences

Visible refusal gives the user a chance to resolve a failed edit. Keeping text
semantics in Rust lets another adapter replace the host integration without
changing that behavior. It also limits claims to the paths actually tested.

Open questions remain: which other hosts can preserve this boundary, which
reviewed evidence supports stronger detection claims, and whether independent
egress control can resist bypass across platform failures.
