# Reviewer verification and import repairs

PR #10 combines the existing development-prerelease work with the reviewer
repairs requested after #19 merged. Release behavior remains governed by ADR 7;
this work neither creates a tag nor publishes a release.

## Immutable finding verification

The saved qualification failure from
[run 43364160786](https://github.com/yourmoodai/savior/actions/runs/43364160786)
changed “will cause” to “causes” inside a candidate scenario. Exact finding
comparison rejected that rewritten response. Removing the equality check would
also permit substantive allegation changes.

The repaired verifier returns decisions by candidate ID. IDs bind original
finding fields and source anchors. Code reconstructs retained findings, so the
model can explain its decision without editing the allegation. Every unique
candidate requires one decision; unknown IDs, duplicates, omissions and invalid
verdicts fail closed. Abstention preserves retained findings and prevents a
complete review. The saved response is retained as a synthetic regression fixture.

## Source dependencies

Python and Rust imports now use parser nodes rather than textual import patterns.
Python parenthesized names, comments and aliases retain the original bindings.
Rust nested groups expand into explicit paths, including aliases and module
`self` imports. Glob imports, missing modules and ambiguous resolution remain
unresolved.

An additional regression showed that Python import nodes with a `name` field
could be mistaken for declarations. A caller-only change then lost its unchanged
aliased import. Imports now remain required module context. Independent expected
symbol sets cover this behavior, nested groups, relative paths and cycles.

The frozen #19 replay preserves all 8,860 primary locations exactly once. Source
baseline/adaptive plans contain 49/48 chunks, with 34 incomplete chunks in each.
The earlier plans contained 48/47 chunks and the same 34 incomplete chunks.
Verification headroom changed alongside import analysis, so this is not an
isolated packing-cost comparison. Dynamic dispatch and oversized closures still
block the large review; these repairs do not establish whole-program completeness.

## Validation and trial

Local validation passed: 216 reviewer tests, 29 research tests, 75 release tests
(two platform-tool-dependent skips), 29 offline packing cases, `npm run build`,
`npm run verify`, workflow lint and documentation link checks. These establish
implementation behavior; platform installation and live model quality require
separate evidence.

The manual calibration uses the existing `review-cost-calibration-v1` identity,
GLM 4.7 Flash, twelve synthetic fixtures, three assessment roles and finding
verification under a $1 cumulative ceiling. Its protocol freezes the response
schema, analyzer versions, implementation, prompts, labels, routing and prices.
Assessment caching is disabled. Missing usage retains reservations; restarts
cannot reset the allowance or replay started assessments.

The pass completed all twelve cases with 55 invocations. All six defective cases
were flagged, as was one of the six benign cases. There were no invalid responses,
abstentions, truncations, missing usage reports or incomplete cases.

| Measure | Completed pass |
| --- | ---: |
| Usage-priced cost | $0.00688799 |
| Outstanding reservations | $0 |
| Matched request-size estimates | $0.05029424 |
| Matched full-context reservations, summed across calls | $0.82672640 |
| Request estimate / usage-priced cost | 7.30× |
| Reservation / usage-priced cost | 120.02× |
| Input / output tokens | 60,697 / 6,598 |
| Summed provider latency | 115.82 seconds |

The reservation sum measures the conservative allowances made before the same
55 calls. Each known charge settled its allowance, so the sum is neither actual
spend nor a remaining hold. Request-size estimates price UTF-8 byte allowances
and bounded output, not predicted token counts. Usage-priced cost applies frozen
prices to reported tokens; it has not been reconciled to a provider invoice.
These ratios describe short synthetic requests and cannot predict a large PR's
cost. The completed identity cannot run another pass despite its unused ceiling.

The benign interrupted-accounting case exposes a verification-quality failure.
The reviewer claimed that `committed(10, None)` changed from `10` to `None`, and
verification retained that claim. Executing both trusted fixture versions returns
`10`; both also pass the existing invariant oracle. The preserved finding points
to the function signature. Its demonstrated behavioral claim is false; a retained
decision establishes neither correctness nor adequate source attachment.

Seventeen findings were reported across the cases. The counterexample above
resolves one as false. The other sixteen have not been independently adjudicated;
finding-level precision and source-attachment accuracy remain unknown. A correct
case-level flag does not establish that every finding describes a real defect.
No result promotes a model or populates production qualification.

## Follow-up priorities

1. Keep the stable-ID contract and parser repairs, subject to normal PR review.
   The captured qualification failure replays successfully and every paid response
   satisfied the new contract. Semantic verification remains a separate problem.
2. Adjudicate stored findings offline and preserve the confirmed false positive
   as a future qualification case. Improve rejection of false counterexamples
   before recommending GLM 4.7 Flash; do not spend again under this identity.
3. Address the remaining dependency gaps with targeted offline fixtures before
   another large review or routing experiment. The frozen replay still has 34
   incomplete chunks. A small trial with no missed case flags establishes neither
   equivalent review quality nor complete repository coverage.

## Evidence

The [frozen protocol](reviewer-repairs-frozen.json) binds fixture labels, prompts,
source digests, analyzers, routing and prices to fingerprint
`02f30240fc7a9a0bb74ad0291ab4a0461843f44d83b464e254d432e07fada3aa`.
The [unaltered trial report](reviewer-repairs-results.json) retains per-call usage
and original findings. Its generated interpretation marks findings unadjudicated;
the subsequent offline counterexample above is additional evidence. The
[context replay](reviewer-repairs-context.json) records primary ownership and
remaining gaps without provider calls. Historical experiment artifacts remain
unchanged.
