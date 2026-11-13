# 11. Disclose runtime uncertainty during source review

Date: 2026-11-13

## Status

Proposed. Supersedes ADR 10 only where ordinary runtime dispatch alone prevents
source review. Immutable evidence, capacity limits and spending gates remain.

## Context

PR 21 exposed a mismatch between source coverage and runtime proof. The analyzer
rejected pathlib methods, string methods and server callbacks despite including
their enclosing source. Static syntax analysis cannot prove every runtime target.
Treating each uncertain target as missing source prevents ordinary code review.

Full compiler analysis would establish more bindings but requires additional
language environments and still cannot resolve arbitrary injected behavior.
Ignoring uncertainty would conceal a material limit from reviewers.

## Decision

Separate blocking evidence gaps from disclosed runtime uncertainty. Preserve
receiver and callback locations, expressions and reasons in the evidence plan.
Include enclosing declarations and the existing transitive source dependencies.
Present limitations in both assessment and verification context. The reviewer
must abstain when a judgment depends on unavailable runtime behavior.

Use a compact, lossless representation in prompts and structured records in
artifacts. Source coverage is not a complete runtime call graph. Plan schema 2
and implementation fingerprints invalidate earlier plans and cached judgments.

## Invariants & Abstention

- Missing local imports, ambiguous definitions, syntax errors, source limits,
  reflective loading, dynamic module loading and unexpanded macros still block.
- Ordinary object calls and injected callbacks are visible analysis limitations,
  not automatically missing source. No runtime target is asserted without evidence.
- No PR code executes during planning. Source coordinates, immutable revisions,
  exact changed-text coverage and request capacity limits remain enforced.
- Qualification, verification, spending caps and reviewer abstentions remain
  independent gates. A planning success is not an automated approval.

## Consequences

Ordinary code can be assessed without pretending static analysis proves runtime
behavior. Review prompts become larger and model judgment carries more explicit
responsibility. Unsupported loading and genuinely missing source still require
manual investigation. Artwork exclusions remain visible and require visual review.
