# Reviewer design comparison

A review has two kinds of work: assessing source evidence and explaining the
result. This experiment tests whether the explanation can be generated
deterministically without losing verified findings or uncertainty. It separately
explores whether one assessment can cover correctness, security and compatibility.
Neither experiment changes the production reviewer described by
[ADR 6](../adr/0006-qualify-review-model-routing.md).

## Protocol

The current arm uses three independent role assessments, finding verification,
Elephant reconciliation and a model-generated overview. The three-role candidate
renders the current arm's assessment results directly; sharing those results
isolates reporting changes and incurs no duplicate assessment charges. The
single-role candidate makes a fresh general assessment, verifies findings and
uses the same deterministic renderer. Every call uses the configured DeepSeek
routing and the existing transport, evidence and verification rules.

Twelve new fixtures pair a defect with a benign change for consent revocation,
protected spans, profile isolation, protocol compatibility, spending boundaries
and interrupted-call accounting. They run before the ten existing qualification
fixtures. Bounded executable oracles check old and new behavior before inference;
unresolved labels are excluded from accuracy comparisons. Arm execution order
alternates by fixture. Case-level labels do not independently adjudicate the
semantic accuracy of every reported finding.

The authorized ceiling is $4 for one pass, including retries and reporting. It is
a research-only exemption from the production $2 PR and $5 daily limits. Full
model context plus bounded output is reserved before each request. Missing usage
and interrupted calls retain their reservations. One fixed experiment identity
and an append-only journal in the shared Git directory survive worktree changes
and restarts. An adjacent checkpoint detects missing or rolled-back history;
configuration changes stop execution without resetting the allowance.

The frozen manifest binds fixtures, oracle outcomes, prompts, source fingerprints,
runtime probes, order, prices, account and routing. Assessment-cache reuse is
disabled. Research does not invoke the full qualification suite or write to its
cache. Production qualification is assumed only to exercise the reporting stages;
the experiment cannot establish a production approval.

## Results

Implementation and offline validation are complete. Paid trial results will be
recorded here after the frozen protocol runs. No quality or savings claim follows
from the reservation projections below.

## Offline reservations

The replay of [PR 13](https://github.com/yourmoodai/savior/pull/13) has one chunk
under both baseline and adaptive packing. With existing retry bounds, both plans
produce these reservations at the frozen catalog prices:

| Design | Review reservation |
| --- | ---: |
| Current | $1.945206 |
| Three roles, deterministic reporting | $1.309117 |
| One role, verification and deterministic reporting | $0.436372 |

Full production qualification separately reserves $40.075085. These reproduce
the prepared plan's rounded $1.95, $1.31, $0.44 and $40.08 scenarios. They are not
charges or measured savings. The research runner reserves each next call within
its own cumulative ceiling rather than invoking that qualification program.

Replaying the 22 synthetic files together produces 22 baseline chunks and three
adaptive chunks with identical primary evidence coordinates and text:

| Packing | Current | Three roles | Single role |
| --- | ---: | ---: | ---: |
| Baseline | $38.401582 | $28.800584 | $9.600195 |
| Adaptive | $5.417242 | $3.927352 | $1.309117 |

This establishes equivalent primary evidence coverage in the offline replay,
not equivalent model judgments. Grouping changes context, and supporting evidence
is not additional primary coverage. The paid trial assesses each case separately
to preserve its labels and isolate reporting. It does not measure adaptive
packing quality.

## Proposed production follow-ups

Deterministic reporting is the first candidate for separate review. It retains
every distinct verified finding, every role outcome, every coverage gap and every
abstention without prose synthesis. Exact duplicates collapse; two different
findings at the same location remain separate. Any observed loss disqualifies
this candidate. Missing evidence remains incomplete review.

The existing approval path depends on two successful prose stages. `egm.review`
returns incomplete review when Elephant reconciliation fails. Then
`reporting.finalize` requires a successful overview for approval and can request
changes when synthesis fails despite having no code findings. Research reports
that case as incomplete and preserves the production disposition separately.
A future change should separate assessment completeness from prose availability
while retaining finding, coverage and abstention gates. This experiment does not
implement that production change.

A future quote should compare only qualified plans over the same canonical
primary evidence and select the smallest full reservation, with a deterministic
tie-break. Bind the chosen strategy and exact plan digest to repository, base and
head revisions, model routing, prices, prompts, configuration, qualification
evidence, cache assumptions, output bounds and retry limits. Execution must
reconstruct and verify that same plan and reserve each call atomically. Missing
qualification, changed evidence or inadequate remaining allowance must block
execution; it must not silently pick another plan. The current quote selects the
most expensive feasible plan before qualification, then follows qualification's
preferred strategy, subject to its chunk bound. It does not establish the
cheapest qualified plan for every PR.
Execution currently recomputes the strategy from qualification. A future executor
must validate and use the quote's chosen strategy rather than select it again.

Qualification-cache invalidation needs separate review. Its current key covers
the selected routing manifest, fixture definitions, prompt profile and temperature,
plus a fingerprint of listed reviewer source files. The cache lasts 24 hours;
assessment entries last seven days. Even comment-only edits to fingerprinted
files invalidate evidence. The qualification key does not explicitly bind the
live AWS account or observed Node runtime-probe results. A future cache contract
should bind those boundaries and each review design's prompts, verification,
packing and oracle versions. Research outcomes must remain outside production
qualification namespaces. Reducing invalidation requires evidence that the
excluded changes cannot affect behavior.

Single-role review remains exploratory. Any additional missed defect on matched
cases prevents a recommendation; a small passing sample cannot establish
equivalence. Correlated roles using one model and synthetic fixtures limit the
strength of every quality conclusion.

## Verification and evidence

Mocked checks cover exact and concurrent reservations, restart recovery, missing
or conflicting history, unknown usage, interruption, truncation retries, frozen
routing, account mismatch, synthesis failures and lossless rendering. All 22
fixture labels passed their bounded executable checks. The repository requires
reviewer tests, `npm run build` and `npm run verify` before the paid trial.

The [research commands](https://github.com/yourmoodai/savior/blob/main/tools/adversarial_reviewer/README.md#bounded-design-research)
own initialization, resumption, debugging and allocation profiling. The journal
contains synthetic requests, responses and call-level accounting; the generated
`report.json` preserves full findings and pairwise results. Retain it and its
checkpoint together. Usage-priced charges are computed from provider-reported
tokens, not reconciled to an AWS invoice. No automatic promotion, waiver, PR merge
or production-policy change follows from this research.

The source inspection used these boundaries from the experiment's production base:

| Boundary | Source |
| --- | --- |
| Reconciliation failure | [`egm.py:40`](https://github.com/yourmoodai/savior/blob/4b509fca4782eca06af31716e8d52cb9df9a33f3/tools/adversarial_reviewer/egm.py#L40) |
| Approval requires successful synthesis | [`reporting.py:98`](https://github.com/yourmoodai/savior/blob/4b509fca4782eca06af31716e8d52cb9df9a33f3/tools/adversarial_reviewer/reporting.py#L98) |
| Qualification key and lifetime | [`qualify.py:22`](https://github.com/yourmoodai/savior/blob/4b509fca4782eca06af31716e8d52cb9df9a33f3/tools/adversarial_reviewer/qualify.py#L22) |
| Cache source fingerprint | [`result_cache.py:10`](https://github.com/yourmoodai/savior/blob/4b509fca4782eca06af31716e8d52cb9df9a33f3/tools/adversarial_reviewer/result_cache.py#L10) |
| Quote strategy selection | [`quote.py:64`](https://github.com/yourmoodai/savior/blob/4b509fca4782eca06af31716e8d52cb9df9a33f3/tools/adversarial_reviewer/quote.py#L64) |
| Execution strategy selection | [`run_ci.py:64`](https://github.com/yourmoodai/savior/blob/4b509fca4782eca06af31716e8d52cb9df9a33f3/tools/adversarial_reviewer/run_ci.py#L64) |
