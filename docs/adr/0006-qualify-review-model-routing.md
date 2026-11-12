# 6. Qualify model routing before automated review

Date: 2026-11-11

## Status

Accepted for the development reviewer. Live activation remains gated by account
access, approved spending, and qualification. This does not change offline
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
resources, and provider offers. Qualification has a separate spending quote.

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

## Invariants & Abstention

- Runtime prompt processing remains offline. Only repository review evidence
  and synthetic qualification fixtures reach AWS.
- PR content is data. Only trusted-base code executes with AWS credentials.
- CI runs require revision-bound allowances. Explicitly authorized local diagnostic
  loops may span configuration changes only within one persistent cumulative
  ceiling, recording each attempt's configuration. New grants cannot reset it.
  Reserve full context and bounded output before each call, including retries;
  settle once at that call's frozen prices.
- Old charges are never repriced. Missing usage and interrupted calls retain
  their reservations. Lost spending history blocks subsequent CI spending.
- Automatic cache reads may use a documented standard-input upper bound when
  the catalog opts in; a presumed discount cannot release reserved funds.
- Model framing is explicit per catalog entry. Reasoning removal and bounded
  regeneration never waive final JSON, evidence, or completion validation.
- Missing reviewers, invalid evidence, refusals, unavailable endpoints, exhausted
  allowance, or failed reconciliation cannot produce approval.
- Provider terms and one-time use-case submissions require a concrete onboarding
  approval. Account access is not runtime qualification.
- Use one repository enable flag, captured before entering the GitHub environment.
  Spending approval cannot turn that flag on.
- Resource-scoped automated onboarding currently supports the runtime endpoint.
  Mantle transport exists, but its different IAM boundary requires a separately
  reviewed policy before activation; do not substitute wildcard permissions.

## Consequences

Routing can change without rewriting budgeting or review coverage. Independent
contexts expose assumptions, while explicit roles make cost and failure evidence
inspectable. More calls increase worst-case reservations and qualification cost.
Conservative reconciliation can retain false positives for human adjudication.

AWS APIs and model cards remain the authority for access and compatibility.
[PR-Agent's explicit model configuration](https://github.com/The-PR-Agent/pr-agent/blob/main/docs/docs/usage-guide/changing_a_model.md)
provides public corroboration for explicit model selection; it is not a dependency
or evidence that a candidate passes this reviewer.

The [reviewer guide](https://github.com/yourmoodai/savior/blob/main/tools/adversarial_reviewer/README.md) owns commands,
activation prerequisites, qualification evidence, and disabling procedure.
