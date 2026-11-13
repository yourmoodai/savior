# 9. Reserve review spending one call at a time

Date: 2026-11-12

## Status

Proposed. Supersedes ADR 6's requirement that the whole remaining worst-case
projection fit before inference. Activation requires review and merge.

## Context

A reservation holds enough allowance to cover a call before its usage is known.
Settlement replaces that hold with cost calculated from provider-reported tokens
at frozen prices. A whole-plan projection sums every possible full-context call,
including retries. It is useful for comparing plans but does not predict actual
spending. Requiring its entire sum upfront can block work whose calls would settle
for much less, preventing observation of both cost and quality.

Removing spending checks entirely would admit unbounded concurrent work. The
existing atomic ledger already provides a narrower boundary: each invocation
must fit before it reaches the provider. That boundary also covers retries.

## Decision

Make whole-plan projections informational. Preflight displays the next-call
reservation separately; the transport's ledger owns authoritative admission:

```text
settled usage cost + outstanding reservations + next reservation <= cap
```

Keep the $2 PR lifetime and $5 repository UTC-day caps. Reserve full model context
and bounded output before each call. Settle reported usage at that reservation's
prices. Report usage-priced cost and outstanding reservations separately; billing
reconciliation requires provider billing evidence.

Quotes still bind the revision, evidence plan, configuration, routing, prices and
qualification. Cache availability can change remaining work without altering that
evidence binding. A high whole-plan projection alone cannot establish a cost block
or permit an admin waiver. The existing explicit waiver remains available only
when the next call cannot fit the remaining allowance.

## Invariants & Abstention

- Concurrent calls and retries must reserve atomically before invocation.
- Missing usage and interrupted calls retain their full reservation. Missing or
  conflicting history blocks spending; restarts never reset an allowance.
- Changed routing, stale prices, missing qualification and changed evidence
  bindings still prevent their dependent operations.
- Exhausting the allowance preserves partial results and leaves review incomplete.
  Cost never supplies evidence of code quality or an automated assessment pass.
- Research uses separate explicit ceilings and cannot publish qualification or
  promote a model based on a small calibration sample.

## Consequences

Reviews can make progress within the fixed caps even when their worst-case sum
exceeds them. Some runs may spend part of the allowance without finishing, so
partial coverage and outstanding holds must remain visible. A manual synthetic
trial can compare reservation size, request-size estimates and usage-priced cost;
it cannot establish model equivalence or invoice accuracy.
