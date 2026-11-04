# 4. Retain local replacement traces only with consent

Date: 2026-10-21

## Status

Accepted for implementation following the explicit choice of opt-in full traces,
local-only storage, seven-day retention, and nonblocking recording failures.
Supersedes only [ADR 1's](0001-local-span-replacement.md) blanket prohibition on
persistence. Offline inference, deterministic replacement, and no-original-fallback
rules remain.

## Context

Counts cannot explain a disputed edit. Full traces retain the original context
and exact output, which may contain secrets; excerpts can omit decisive context.
Existing metrics consent authorizes neither form of text retention.

## Decision

Rust owns trace admission, span validation, consent generations, and retention.
Records reuse actual detector spans. The adapter separately reports delivery;
human annotations distinguish profanity removal, targeted abuse, and preservation.

A private SQLite journal is authoritative. The loopback inspector uses a
per-launch token and an atomically replaced, revision-bound Parquet projection
queried through embedded DuckDB. The initial single snapshot is invalidated by
annotation changes; the journal supports recovery and regeneration. Downloads
use cache storage; retained records use private, profile-isolated state storage.

## Invariants & Abstention

- Full-text recording defaults off and requires independent consent. Revocation
  wins over work admitted under an earlier consent generation.
- Metadata excludes originals, output, custom markers, and unkeyed prompt hashes.
  Logs and errors never contain prompt content.
- Invalid spans, unsafe paths, and incompatible schemas are rejected. Recording
  failures warn without blocking successful filtering. Processing failures still
  block submission; recording cannot create an original-text fallback.
- Retention expires at seven days within a 256 MiB ceiling; conservative capacity
  reservations can evict earlier. Expiry runs on capture and inspection, not
  while Savior is stopped.
- Deletion disables capture and removes managed records, annotations, and
  projections. User exports and backups are outside deletion.
- Capture grants no cloud telemetry, training import, or Claude-history import.
  Missing model or outcome evidence remains unavailable; unresolved judgments
  remain unresolved. Adapter delivery evidence does not prove provider receipt.

## Consequences

Users can inspect exact edits and retain context for later review.
Retained files are private, not encrypted; same-user access, administrators,
backups, or compromise can expose them. Database contention can cause recording
gaps. Full traces do not automatically become benchmark fixtures.

[Inspection](../inspection.md) owns commands, storage details, and export format.
