# Reviewer harness contracts

A completed, qualified review with no findings can approve a revision. The
harness must preserve the distinction between successful review, verified
defects, and incomplete evidence. Spending limits do not change that distinction.

## Requests and evidence

Packing uses UTF-8 bytes plus template allowances, not measured model tokens.
Revision-based review freezes complete source dependencies and checks headroom
for a maximum bounded candidate and verification instructions before inference.
Verification batches split between findings without truncating source context;
an indivisible oversized request remains incomplete. A retained finding must be
an unchanged candidate, including its original evidence location. The verifier
must check preconditions, compared outcomes, and whether the cited operation
causes the alleged defect. This is model verification, not executable proof.

Assessment responses retain the existing finding schema. Verification responses
use `decisions`, each containing `finding_id`, `verdict` and `rationale`. Verdicts
are `retain`, `reject` or `abstain`. Every unique candidate needs exactly one
decision; response order is irrelevant. IDs bind the original finding and source
anchor, and remain stable across verification batches. The harness reconstructs
retained findings rather than accepting replacement text from the verifier.
Malformed decisions cannot approve; abstentions retain any verified findings
and keep the review incomplete. Diagnostics preserve the individual decisions.

The source graph reads regular Git blobs at the merge base and head. Pinned
Tree-sitter grammars cover Python, Rust, JavaScript and TypeScript; JSON records
and Markdown sections supply non-code boundaries. Recognized local dependencies
include unchanged helpers. The planner computes their transitive closure and
preserves every required node in each request without a support byte cap.
Primary diff locations retain unique ownership; support is not extra coverage.
Python import nodes preserve parenthesized multiline names, aliases and relative
paths. Rust import trees expand nested groups and aliases, including `self`,
`super` and `crate` paths. Glob imports remain unresolved. Unchanged imports stay
in the closure when only a caller changes.

Dynamic dispatch, macros, parse errors, ambiguous or unsupported imports, missing
source and oversized dependency sets remain explicit gaps. External packages and
unsupported formats are declared analysis boundaries. This is syntax analysis,
not a sound whole-program dependency proof. See [ADR 10](adr/0010-freeze-review-evidence-before-inference.md).

## Quotes and qualification

Schema 3 quotes bind revisions, configuration, routing, prices, qualification,
and the exact selected evidence plan, including its source snapshots, analyzer
versions, dependency graph and request messages. Only strategies that passed qualification
are candidates for selection; minimum reservation cost breaks the choice, then
the strategy name breaks ties. Feasibility and frozen-plan integrity are checked
before paid qualification. Execution restores the selected plan instead of
repacking; failure is reported as `planning_failed` with artifact details.

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

Behavior fingerprints include runtime prompts, validators, packing, installed
analyzer versions, pinned requirements, models,
fixtures and spending policy. Python comments are excluded through syntax-tree
serialization; literals and docstrings remain significant. Research modules do
not invalidate production qualification. Historic qualification does not qualify
this changed harness.

## Verification

Run `npm run test:reviewer`, `npm run build`, and `npm run verify`. Reviewer tests
use mocked transport and exercise binding changes, verification capacity,
rewritten allegations, and persistent accounting without paid inference.
Install `tools/adversarial_reviewer/requirements.txt` in the test environment first.
The [context replay](research/context-planning.md) adds source-dependency oracles,
ownership mutations and a reproduced PR #19 planning failure. Diff-only historical
research keeps the old planner explicitly; production CLI review supplies immutable
revision snapshots, and qualification supplies trusted miniature source snapshots.

Runtime model quality remains unverified until a separately authorized frozen
trial completes. This reporting follow-up removes paid reconciliation and overview
calls as proposed in [ADR 8](adr/0008-render-qualified-review-results.md). The full
artifact preserves role judgments, findings and gaps; the publisher derives
automated approval from complete, qualified structured results.
