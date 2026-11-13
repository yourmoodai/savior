# 12. Approve the revision before building it

Date: 2026-11-13

## Status

Accepted for implementation. Activation requires merging the workflows and
verifying the coordinator before requiring `pr readiness` in branch protection.
Replaces independent PR build scheduling. ADR 6's model qualification and
spending rules remain unchanged.

## Context

Independent PR workflows build revisions even when source review cannot proceed.
Approval and tested artifacts can also refer to different revisions. A bot review
event alone is unsuitable for reliable workflow chaining.

## Decision

The trusted PR coordinator calls source review, validates its approval, and then
calls read-only validation workflows. GitHub job dependencies enforce ordering.
Approval binds the head, base, run, and attempt. Admission verifies the tested
merge commit's parents. The final status binds applicable checks to that approval.

Publish `pr readiness` and downstream aggregate statuses on the PR head. A failed
review skips build work and fails these aggregates. Only explicitly planned
impact skips count as successful validation. Website-only changes avoid native
qualification. Generated release PRs enter the same coordinator.

## Invariants & Abstention

- Missing, incomplete, stale, dismissed, waived, or failed review cannot admit
  builds. A complete source approval does not assert that runtime tests passed.
- New heads, changed bases, and approval dismissal invalidate readiness. The
  coordinator checks the target again before admitting builds and publishing success.
- PR code executes without review credentials or write-capable GitHub tokens.
  Publication jobs execute trusted default-branch code separately.
- Missing or failed downstream results prevent merging. Administrator enforcement
  and the existing approval requirement remain in force.

## Consequences

Rejected changes consume no application build resources. Validation starts later,
and source review must assess changes without assuming future tests will pass.
The workflow rollout needs a default-branch bootstrap before branch protection
can require the new status. Runtime failures and unsupported review evidence
remain separate defects; sequencing does not resolve them.
