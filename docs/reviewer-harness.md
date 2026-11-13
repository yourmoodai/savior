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

Schema 2 quotes bind revisions, configuration, routing, prices, qualification,
and the exact selected evidence plan. Only strategies that passed qualification
are candidates for selection; minimum reservation cost breaks the choice, then
the strategy name breaks ties. Execution checks the binding again before calls.

Without qualification, the quote covers qualification only and has no executable
review strategy. Successful qualification requires a new review quote and a new
remaining-budget check. Existing PR and daily limits, full-context reservations,
and uncertain-call accounting remain in force. Verification projections include
two bounded finding batches per assessment, including retries.

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
trial completes. The existing production prose dependency remains until the
deterministic-reporting follow-up is reviewed and merged.
