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
Here, a verified finding means one retained by the model's verification request;
executable evidence is a separate check.

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

The `review-designs-v1` pass completed on 2026-11-12 with **22 matched cases**:
ten defective and twelve benign. All labels resolved before paid calls. The
172 invocations cost **$0.16335005** at frozen prices, with **$0 outstanding**
and **$3.83664995 unused** from the $4 ceiling. No qualification calls ran.

| Arm | Complete cases | Benign cases with findings | Defective cases without findings |
| --- | ---: | ---: | ---: |
| Current | 21 / 22 | 0 / 12 | 0 / 10 |
| Three roles, deterministic reporting | 22 / 22 | 0 / 12 | 0 / 10 |
| Single role, deterministic reporting | 22 / 22 | 0 / 12 | 0 / 10 |

These are case-level detection counts, not proof that the findings are correct.
The counterexample replay below identifies a finding-quality failure that these
counts would conceal. No unresolved fixture labels were excluded; the current
arm's one incomplete case was benign consent refactoring. All three assessments
approved it, but both overview attempts had fewer than 50 words. Production would
request changes because synthesis failed, despite having no code findings. The
deterministic report preserved the complete assessments.

| Arm | Calls | Usage-priced cost | Input tokens | Output tokens | Transport seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| Current | 140 | $0.12784807 | 150,811 | 18,565 | 568.85 |
| Three-role shared assessments | 94 reused | $0.10303738 shared | 123,884 shared | 14,178 shared | 420.89 shared |
| Single role | 32 | $0.03550198 | 42,879 | 4,820 | 147.35 |

The three-role candidate incurred **zero additional calls or charges**. The
shared row is a subset of current-arm usage, so it must not be added to the
experiment total. Current reporting consumed 46 calls and $0.02481069, or 19.41%
of the current arm's charge. Removing those calls would avoid that measured
reporting cost for these fixed assessment results; it does not establish savings
on other PRs. Transport seconds sum invocation wall time and exclude parts of
local planning and rendering.

The current arm produced three responses that failed overview validation, including
the two short responses that exhausted retries on the consent case. The
single-role arm produced none. Both paid arms had zero explicit abstentions,
zero truncations and zero unknown-usage calls. Deterministic reporting lost no
recorded finding, coverage gap or unresolved judgment. The
[measurements](reviewer-designs-results.json) retain every case outcome and finding.

### Counterexample replay

The single reviewer flagged the defective protected-span change, but its verified
example used `text='abcde'`, `start=1`, `end=4`, `protected=[(2,3)]` and `marker='X'`.
The old overlap check and the new containment check both raise
`ValueError('protected')` for that input. The finding claimed that only the old
code raises. Its verifier retained a counterexample that does not demonstrate a
regression.

The current arm's example used `text='abcdef'`, `start=3`, `end=4` and
`protected=[(2,5)]`. It does reproduce the regression: the old code raises and the
new code returns `abcXef`. Its finding incorrectly wrote `abXef`, so even the
supported verdict contains an inaccurate result string. The
[executable replay observations](reviewer-designs-counterexamples.json) preserve
both claims and actual outcomes. This was a targeted post-trial check, not
exhaustive adjudication of all findings. It changed no label or paid request.

The single-role candidate therefore has a known additional failure to demonstrate
the seeded defect, despite its positive case-level flag. It cannot be recommended
from this trial. The current arm's inaccurate result string also warrants better
verification; model agreement does not resolve either issue.

### Follow-up ranking

1. Propose three-role deterministic reporting in a separate production review.
   It preserved the recorded evidence and eliminated the observed prose-only
   failure. Keep every existing coverage and abstention gate.
2. Strengthen finding verification against executable counterexamples where
   practical. The protected-span replay shows why a retained finding is not
   execution evidence, even when its overall verdict matches the fixture label.
3. Bind quotes to the cheapest qualified plan over equivalent evidence and
   review the qualification-cache contract described below.
4. Keep single-role review exploratory. Its lower measured cost does not overcome
   the unsupported counterexample, and this small sample cannot establish
   equivalence even if that example is corrected later.

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
fixture labels passed their bounded executable checks. Before inference,
`npm run build`, `npm run verify` and the reviewer suite passed. The complete
reviewer suite passed 177 tests with the pinned signing dependency available,
including 20 research tests. Runtime inference was exercised only through the
synthetic research runner; production CI qualification and live PR approval remain
unverified by this experiment.

The execution source is recorded in commit `fb5f1b1`, and the frozen manifest
digest is `43e37c14eca0c585ba01ab44130406b6063b2baefeb4331e4d5cd531e8f41e4d`.
After the pass, the offline metrics decoder was corrected to recognize JSON
fences accepted by the transport. This changed the invalid-response count from
one to three. Case results and ledger accounting were compared before and after
and remained identical. The measurements record both execution and analysis
source fingerprints; the original source and raw responses remain available.
This correction did not alter prompts, fixtures, labels, routing or any paid call.

The [research commands](https://github.com/yourmoodai/savior/blob/main/tools/adversarial_reviewer/README.md#bounded-design-research)
own initialization, resumption, debugging and allocation profiling. The journal
contains synthetic requests, responses and call-level accounting; the generated
`report.json` preserves full findings and pairwise results. The checked-in
measurements omit duplicate presentation bodies and raw call responses; the
complete artifacts remain under `reviewer-research/review-designs-v1` in the shared
Git directory. Retain that directory and its
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
