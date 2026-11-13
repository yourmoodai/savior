# 6. Qualify model routing before automated review

Date: 2026-11-11

## Status

[ADR 8](0008-render-qualified-review-results.md) proposes superseding the prose
reconciliation and synthesis dependency. [ADR 9](0009-reserve-review-calls.md)
proposes superseding whole-plan projection admission. Other decisions stay in force.

Accepted for the development reviewer. Live activation remains gated by account
access, repository cost limits, and qualification. This does not change offline
application inference or release qualification in ADR 2.

## Context

One hardcoded model couples review quality, API compatibility, and pricing.
Provider access does not establish that a model can find defects, and a cheap
model can increase cost through retries or missed findings. Retrying a review
inside its author's context also preserves assumptions the review should test.

A universal proxy could centralize provider routing, but would add another
credential and budget boundary. Explicit AWS adapters can reuse this repository's
existing reservations and trusted-base execution without that service.

## Decision

The CI reviewer owns a versioned model catalog and role-routing manifest. Catalog
entries bind exact invocation targets, API adapters, US resources, limits, and
dated prices. Discovery refreshes evidence; it never promotes a candidate.
Onboarding accepts only an approved plan binding account, configuration, IAM
resources, and provider offers. CI qualification consumes the same PR and daily
allowances as assessment.

Adapt [Elephant–Goldfish review](https://github.com/google/elephant-goldfish-model/blob/main/docs/designs/review.md)
to code review. Three independent Goldfish assess correctness, security, and
compatibility using source evidence and fresh requests. A fresh verifier checks
candidate findings once. The Elephant receives all resulting findings and
context, deduplicates exact duplicates, and explains the combined result. It
cannot dismiss a verified finding. This conservative reconciliation avoids an
unbounded resolution loop; unresolved findings go to human review.

Models are selected per role by explicit catalog changes followed by live
qualification of the resulting routing manifest. Model size does not define a
Goldfish, and different contexts do not establish independent model training.
Fast candidates must pass the same applicable evidence checks. There is no
implicit fallback or automatic latest-model selection.

The reviewer projects remaining inference cost before calling a model. A PR has
a $2 lifetime allowance across all revisions; the repository has a $5 daily
allowance, resetting at midnight UTC. Full context, bounded output, planned
qualification, verification, summaries, and retries contribute to the projection.
Both remaining allowances must cover it. This replaces comment-funded increases
and size-based automatic approvals: neither changed-line count nor a new run
establishes affordable work.

For a recorded cost block only, a human repository admin may comment
`/savior review skip <full-head-sha>`. Trusted-base code revalidates the current
head and base, readiness, comment integrity, permissions, and outstanding change
requests, then submits an auditable GitHub approval explicitly waiving automated
assessment. It asserts no code-review pass, spends nothing, and changes no cap.
An admin author may waive; CODEOWNER status alone is insufficient. Other required
reviews and checks still apply. Stale-approval dismissal keeps waivers bound to
their revision. This path cannot authorize its own initial rollout.

## Invariants & Abstention

- Runtime prompt processing remains offline. Only repository review evidence
  and synthetic qualification fixtures reach AWS.
- PR content is data. Only trusted-base code executes with AWS credentials.
- CI calls share repository/PR lifetime and repository/UTC-day limits. A projection
  equal to both remaining allowances is allowed; an overrun blocks before inference.
  Missing, invalid, or older-than-90-day pricing blocks calls. Explicitly authorized local diagnostic
  loops may span configuration changes only within one persistent cumulative
  ceiling, recording each attempt's configuration. New grants cannot reset it.
  Reserve full context and bounded output before each call, including retries;
  settle once at that call's frozen prices.
- Old charges are never repriced or reassigned without evidence. Missing usage
  and interrupted calls retain reservations across midnight. Lost, conflicting,
  expired, or unattributed spending history blocks subsequent CI spending.
- Automatic cache reads may use a documented standard-input upper bound when
  the catalog opts in; a presumed discount cannot release reserved funds.
- Model framing is explicit per catalog entry. Reasoning removal and bounded
  regeneration never waive final JSON, evidence, or completion validation.
- Missing reviewers, invalid evidence, refusals, unavailable endpoints, exhausted
  allowance, or failed reconciliation cannot produce an automated assessment
  approval. Only a recorded cost block permits the explicit admin waiver above.
- Provider terms and one-time use-case submissions require a concrete onboarding
  approval. Account access is not runtime qualification.
- Use one repository enable flag, captured before entering the GitHub environment.
  A waiver cannot turn that flag on.
- Resource-scoped automated onboarding currently supports the runtime endpoint.
  Mantle transport exists, but its different IAM boundary requires a separately
  reviewed policy before activation; do not substitute wildcard permissions.

## Consequences

Routing can change without rewriting budgeting or review coverage. Independent
contexts expose assumptions, while explicit roles make cost and failure evidence
inspectable. More calls increase worst-case reservations and qualification cost.
Conservative reconciliation can retain false positives for human adjudication.
Fixed caps make spending predictable but conservative projections may block most
large reviews. Admin waivers transfer that review decision to an identified
human; they do not establish code quality. Legacy pilot records need attributable
PR/date evidence before the new policy can spend. Artifact expiry requires durable
retention and recovery, not a fresh budget.

AWS APIs and model cards remain the authority for access and compatibility.
[PR-Agent's explicit model configuration](https://github.com/The-PR-Agent/pr-agent/blob/main/docs/docs/usage-guide/changing_a_model.md)
provides public corroboration for explicit model selection; it is not a dependency
or evidence that a candidate passes this reviewer.

The [reviewer guide](https://github.com/yourmoodai/savior/blob/main/tools/adversarial_reviewer/README.md) owns commands,
activation prerequisites, qualification evidence, and disabling procedure.

### Research boundary

A standalone synthetic comparison may investigate whether deterministic
reporting can preserve every verified finding, coverage gap and abstention while
removing paid prose calls. It compares the current design, deterministic reporting
over its three assessments, and a fresh general reviewer with finding verification.
The three-role candidate shares assessment evidence with the current arm so that
the reporting comparison does not confound new model responses with rendering.

The explicitly authorized research allowance is $4 across all calls, retries and
restarts, with one persistent experiment identity. This local exemption does not
alter the $2 PR or $5 daily CI limits. Freeze prompts, executable fixture labels,
runtime evidence, configuration and prices before inference. Missing or conflicting
history, identity or routing mismatch, and an unaffordable next reservation stop
the trial. Interrupted calls and missing usage retain their full reservations.

Any lost finding, gap or abstention disqualifies deterministic reporting. A single
reviewer's additional missed defect prevents recommending it; a small passing
sample cannot establish equivalence. Research produces evidence and proposals,
never automatic promotion, qualification-cache entries, a waiver or an approval.
The [research report](../research/reviewer-designs.md) owns measured outcomes,
offline reservation comparisons and the proposed quote and cache contracts.

### Proposed CPU routing research

The [CPU routing experiment](../research/cpu-review-routing.md) compares offline
packing candidates and advisory escalation rules, a logistic classifier, and a
small local language model. It replays existing evidence and explicitly labeled
simulations without new provider calls. It does not reopen the completed $4 trial.

The CI reviewer retains ownership of model selection and spending. Research
routers cannot suppress mandatory escalation, waive coverage, change qualification
or authorize approval. Missing context, invalid output, unavailable artifacts and
runtime failures produce abstention or mandatory escalation. Model artifacts and
runtime revisions are pinned; PR content remains data.

The experiment may reveal cheaper packing and useful routing signals, but its
simulated labels cannot establish savings or production review equivalence.
Promotion requires a separately approved paired-model evaluation and an explicit
production decision; it never follows automatically from a benchmark score.
