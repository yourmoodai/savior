# 1. User-controlled prompt boundary

Date: 2026-10-12

## Status

Accepted. Savior's local transformation core is the product boundary; Claude
Code is the current adapter. [ADR 4](0004-consented-local-traces.md) supersedes
only this record's original prohibition on persistence, permitting explicitly
consented local inspection. [ADR 2](0002-release-and-quality-evidence.md) owns
release evidence; [ADR 3](0003-future-egress-enforcement.md) owns proposed proxy research.

## Context

A draft is unsent text in the host's prompt box. Prompt input is user-authored
material prepared for an AI request. An adapter connects that host or transport
to Savior's local replacement core before a submission continues to a provider.
A span is a range of source text selected for replacement. Protected text, such
as code or quoted material, can make that edit unsafe even when detection found
an offensive phrase.

```text
host prompt box → adapter → Rust detection and replacement → adapter → provider
```

This picture follows a supported submission. It does not account for other
traffic the host may send.

Users need control at that boundary without depending on one host forever.
Savior cannot establish control over unsubmitted drafts outside its process,
host telemetry, or provider policy.

## Decision

Rust owns detection, span normalization, protected-text checks, replacement, and
content-free errors. Adapters transport requests and results without duplicating
text semantics. The Claude Code integration supplies the current submission
path; future adapters must preserve the same contract.

The supported path is a non-slash submission whose `prompt.submit` origin is
`composer`, the host's name for the prompt box. The handler calls
`next({...e, text: processed})` after successful processing and returns `drop`
on refusal. This is the **verified mod boundary**: evidence about this handler
and its continuation, scoped to the client, artifact, and cases tested.
Slash commands, programmatic origins, tools, and earlier host records are outside
it. A Claude mod is packaged and installed through Claude's `plugin` commands;
these names do not describe two different Savior integrations.

Do not broaden this decision into `session.append` rewriting. That event includes
conversation content beyond the user's draft, including protected material.
Filtering it needs a separate field and origin contract, fidelity tests, and
compatibility with managed host policies. A network proxy and whole-host egress
control also require their own evidence; neither follows from a successful mod.

### Client API compatibility

Treat the mod API as a versioned adapter dependency. The
[upstream interface](https://github.com/anthropics/claude-code/blob/main/mods/README.md)
can change between client releases. Patch-version similarity does not establish
compatibility: draft recovery already changes behavior without changing the
replacement protocol.

`hooks/compatibility-catalog.ts` maps exact client versions to an adapter ID,
entry module, draft-recovery owner, and pinned type snapshot. The current entry
is `composer_2_1_322`. `session.version()` selects its contract once per session;
unknown versions, ambiguous matches, and a failed version probe refuse supported
submissions before processing. The loaded entry accepts only its own adapter ID.
Keep model inference and replacement in Rust when adding another API adapter.

Discovery is separate from this reviewed mapping. The rolling release inventory
in ADR 2 lists clients that need assessment; it never adds versions to the runtime
allowlist. Group clients under one adapter only after qualifying the same API and
draft lifecycle. Add an entry module and type snapshot when that contract differs,
rather than branching Rust replacement behavior on a client version.

Select different API modules at installation/package selection, before an
incompatible module can be parsed or registered. Do not import every API variant
and hope runtime feature checks hide incompatible code. The catalog and read-only
selector are implemented; distribution of multiple adapter bundles is future
work under ADR 2. Only the current adapter exists.

If a changed client cannot load the mod or register its refusal handler, the mod
cannot block anything. A version guard inside a loaded mod is defense at that
boundary, not independent egress control. Unknown clients must never be described
as protected merely because a plugin installation command succeeded.

## Invariants & Abstention

- Forward transformed content only after successful processing. Protected-span
  conflicts, malformed requests, incompatible artifacts, and processing failures
  block the attempt; none permits an original-text fallback.
- Preserve unmatched text exactly. Runtime inference stays local and offline.
- Leave draft recovery to the qualified host. Savior must not read or fill the
  prompt box on refusal, overwrite newer input, or revive cancelled work.
  Verify retention and an exact retry in the actual client; `drop` alone does
  not establish draft retention or provider receipt.
- Original prompts never enter logs, errors, or training data. Persistence is
  prohibited except under ADR 4's separate consent and retention contract.
- A disabled or bypassed adapter provides no protection. Detection can miss
  unwanted language or select harmless text; the core cannot guarantee delivery,
  complete host privacy, or provider acceptance.

## Consequences

Visible refusal gives the user a chance to resolve a failed edit. Keeping text
semantics in Rust lets another adapter replace the host integration without
changing that behavior. It also limits claims to the paths actually tested.

### Open questions

Which other hosts can preserve this boundary? What reviewed evidence supports
stronger detection claims? Can independent egress control resist bypass across
platform failures? These questions remain unresolved.

### Related mod designs

Qualification on 2026-11-11 found that Claude 2.1.322 restores a refused composer
draft itself. Filling the draft before returning `drop` duplicated the retry.
This host behavior is part of the tested compatibility boundary; requalify it
when changing the client pin. Handler mocks do not model that terminal lifecycle.

Anthropic's [mod interface](https://github.com/anthropics/claude-code/blob/main/mods/README.md)
provides continuation and refusal hooks plus an engine test kit. Those tests
exercise handler contracts; actual-client delivery needs a receiver observation.
[Redact](https://github.com/karanb192/claude-code-redact) additionally rewrites
conversation rows and documents records outside that interception, including
headless queue records created before `prompt.submit`.
[Managed security](https://github.com/anthropics/claude-code/blob/main/mods/sec-default/README.md)
also constrains user-mod access to some appended rows. These designs inform the
boundary; their tests cannot qualify Savior or establish coverage of every route.
