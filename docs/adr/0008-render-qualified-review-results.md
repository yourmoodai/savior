# 8. Render qualified review results deterministically

Date: 2026-11-12

## Status

Proposed for the development reviewer. Implemented in the reporting follow-up;
activation requires review and merge. Supersedes only ADR 6's Elephant prose
reconciliation and prose-synthesis approval dependency. ADR 7 is reserved by the
separate development-prerelease proposal.

## Context

Three independent reviewers and a fresh finding verifier produce structured
assessment results. Model-generated reconciliation cannot discard a verified
finding, so it adds prose rather than additional decision authority. Nevertheless,
failed prose synthesis currently prevents a complete clean review from approving.

The bounded design experiment found that deterministic reporting removed about
19% of measured baseline cost while preserving the same assessments. That result
supports removing reporting calls; it does not qualify a cheaper reviewer or
prove the correctness of the underlying findings. Packing and CPU routing have
not demonstrated sufficient quality and cost improvements for promotion.

## Decision

Keep three independent correctness, security and compatibility assessments and
fresh finding verification. Reconcile results with deterministic code, deduplicating
only identical finding objects. Preserve distinct findings at the same location
and every role's judgment, including abstentions. Continue collecting independent
role results when another role is incomplete, subject to the existing budget.

Generate the overview and final disposition from structured results. A complete,
qualified, clean assessment produces automated approval. Prose length and model
synthesis success have no authority over that decision. The publisher requires
`reporting_mode=deterministic` and `reporting_complete=true`; the legacy
`synthesis_complete` field is false and cannot authorize approval.

Quotes no longer reserve reconciliation or overview calls. Qualification caches
invalidate for this behavior change. All other ADR 6 decisions remain in force.

## Invariants & Abstention

- Every verified finding, reviewer judgment, context gap and abstention remains
  in the full report. The bounded GitHub comment links to that artifact.
- Missing qualification, incomplete coverage, context gaps, unsupported evidence,
  exhausted budgets and reviewer abstention cannot produce automated LGTM.
- Exact duplicate removal cannot merge differing explanations at one location.
- Trusted-base execution, model routing, reservation accounting, PR/daily caps,
  current-revision publication and explicit admin-waiver policy are unchanged.
- Rendering does not execute model output or call inference.

## Consequences

Approval no longer depends on paid prose generation. Reports are less narrative,
but retain role-level conclusions for inspection. This change reduces reporting
expense without reducing review roles or proving model equivalence. Invalid
findings still require better verification and evaluation; deterministic rendering
does not turn model agreement into proof.

Unit tests cover duplicate and differing findings, incomplete roles, context gaps,
publisher approval, and absence of reporting calls. Live model qualification is
still required before this changed harness can issue production approvals.
