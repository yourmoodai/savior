# Reviewer harness contracts

A completed, qualified review with no findings can approve a revision. The
harness must preserve the distinction between successful review, verified
defects, and incomplete evidence. Spending limits do not change that distinction.

## Requests and evidence

Packing uses UTF-8 bytes plus template allowances, not measured model tokens.
Initial requests leave 12 kB for a bounded candidate and verification instructions.
Verification batches split between findings without truncating source context;
an indivisible oversized request remains incomplete. A retained finding must be
an unchanged candidate, including its original evidence location. The verifier
must check preconditions, compared outcomes, and whether the cited operation
causes the alleged defect. This is model verification, not executable proof.

Split units can retain their siblings as supporting context. Omitted related
context is recorded as a gap and prevents complete review. This deliberately
prefers an explicit incomplete result over an unsupported coverage claim.

## Quotes and qualification

Schema 3 quotes bind revisions, configuration, routing, prices, qualification,
and the exact selected evidence plan. Only strategies that passed qualification
are candidates for selection; minimum reservation cost breaks the choice, then
the strategy name breaks ties. Execution checks the binding again before calls.

Without qualification, the quote covers qualification only and has no executable
review strategy. Successful qualification requires a new review quote. Whole-plan
projections are informational: they sum worst-case reservations, including retries,
and are neither expected charges nor evidence of spending. Preflight checks the
next call; the ledger atomically enforces the remaining PR and daily allowances
before every actual invocation. Cache loss can increase work without changing the
bound evidence plan; each replacement call still needs its own reservation.

As proposed in [ADR 9](adr/0009-reserve-review-calls.md), admission requires settled
usage cost plus outstanding reservations plus the next full-context reservation
to fit the cap. Missing usage retains its hold. Reports separate usage-priced cost
from outstanding reservations; neither establishes invoice reconciliation. An
exhausted allowance leaves review incomplete. Whole-plan projections alone cannot
authorize a cost waiver. Verification projections include two bounded finding
batches per assessment, including retries.

Behavior fingerprints include runtime prompts, validators, packing, models,
fixtures and spending policy. Python comments are excluded through syntax-tree
serialization; literals and docstrings remain significant. Research modules do
not invalidate production qualification. Historic qualification does not qualify
this changed harness.

## Verification

Run `npm run test:reviewer`, `npm run build`, and `npm run verify`. Reviewer tests
use mocked transport and exercise binding changes, verification capacity,
rewritten allegations, and persistent accounting without paid inference.

Runtime model quality remains unverified until a separately authorized frozen
trial completes. This reporting follow-up removes paid reconciliation and overview
calls as proposed in [ADR 8](adr/0008-render-qualified-review-results.md). The full
artifact preserves role judgments, findings and gaps; the publisher derives
automated approval from complete, qualified structured results.
