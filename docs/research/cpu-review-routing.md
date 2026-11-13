# CPU review routing research

A router decides whether an initial review needs stronger assessment. This
experiment compares explicit escalation rules, a logistic classifier, and a
small local language model. All decisions are advisory. Production review,
qualification, spending limits, and the completed $4 trial remain unchanged.

The experiment also compares packing strategies. Packing determines which
changed lines and supporting context each assessment sees. Reducing chunk count
reduces repeated requests but does not by itself establish review quality.

## Protocol

The standalone harness lives in `tools/review_routing_research`. It imports the
production prompt, sizing, and reservation formulas without invoking inference.
The candidate uses the same conservative byte estimate throughout, with room
for explicit context-gap markers. It recognizes top-level Python declarations
and JSON record boundaries in complete added/deleted units. Partial hunks and
oversized records may still require row splits; those splits are marked as
missing context. It never executes code from the diff.

Packing replay binds the input diff, implementation, prompts, routing, prices,
coverage, and selected plan. Selection minimizes review reservations among
coverage-complete plans below the execution limit. These are research candidates,
not qualified production plans. Qualification costs are excluded from this
comparison; reservations are not charges or expected usage.

The routing dataset contains 22 stored single-role DeepSeek observations and 48
explicit simulations over the six new fixture families. Simulations exercise
clean responses, incomplete review, unsupported claims, and contradictory
judgments. Their labels follow executable fixture oracles or mandatory policy.
Ten observed defective cases remain unresolved because their finding semantics
were not fully adjudicated; they do not enter accuracy counts. The twelve clean
benign observations have oracle-backed labels. None is a cheaper-model trial.

The classifier uses L2-regularized logistic regression with fixed training
parameters and threshold, leaving entire families out of training. It sees only
change size, finding counts, completion and evidence signals, and a sensitive
boundary indicator. Family names, labels, expected outcomes, and stronger-review
results are excluded. Training requires both classes and four independent
families. Simulated training results cannot establish real routing quality.

The local model is publisher-provided
[Qwen3-0.6B Q8_0](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF), using pinned
llama.cpp source and a SHA-256-verified model. The runtime uses four CPU threads,
no GPU layers, an 8,192-token context, 256 output tokens, and a 60-second decision
deadline. Tokenized oversized inputs abstain without truncating evidence. Setup
downloads artifacts; inference uses only a loopback server. Runtime hashes are
recorded and checked before replay.

Mandatory rules override learned recommendations. Reports retain raw decisions,
rule-adjusted decisions, reasons, failures, fingerprints, latency, and process
peak memory. Keyword rules are heuristics, not complete boundary detection.
Missing artifacts, invalid output, unavailable classifiers and timeouts abstain.

## Run and reproduce

From the repository root:

```sh
npm run reviewer:cpu:test
npm run reviewer:cpu -- benchmark --output .cache/router-offline.json
npm run reviewer:cpu:build:release
npm run reviewer:cpu -- benchmark --local-llm --output .cache/router-live.json
npm run reviewer:cpu:profile -- --output .cache/router-memory.json
git diff BASE HEAD > .cache/review.diff
npm run reviewer:cpu -- packing --diff .cache/review.diff --output .cache/packing.json
npm run reviewer:cpu -- replay --diff .cache/review.diff --binding .cache/packing.json --output .cache/replay.json
```

Replace `BASE` and `HEAD` with the revisions under investigation. Debugging uses
`npm run reviewer:cpu:debug -- benchmark`; a debug native runtime is built with
`npm run reviewer:cpu:build:debug`. Setup selects the last built configuration.
Re-running release setup restores the release runtime. Memory profiling records
Python allocation peaks plus parent/child process RSS; native RSS is a process
high-water mark, not per-decision allocation.

The manual `reviewer cpu routing research` workflow runs on `ubuntu-24.04` with
read-only repository permission, no AWS credentials, and no PR checkout or code
execution from fixture inputs. It caches verified artifacts and uploads the
complete benchmark and setup records. Its first run requires the workflow to be
available for manual dispatch in GitHub; local results are not runner results.

## Initial results and recommendation

The accompanying [measurement summary](cpu-review-routing-results.json) records
the local CPU run and packing replay. Full per-case reports are reproducible with
the commands above and remain in the local `.cache` directory. No paid inference
ran, and no savings from switching review models have been measured.

On the frozen research PR diff with empty test-status context, all three plans
cover the same 3,627 nonblank primary evidence IDs exactly once:

| Packing | Chunks | Review reservation |
| --- | ---: | ---: |
| Baseline | 15 | $26.25 |
| Current adaptive | 53 | $92.22 |
| Semantic byte candidate | 7 | $12.36 |

The candidate reduces baseline reservations by approximately 53%, not measured
charges. This replay uses a different context/input snapshot from the earlier
CI quote and must not be presented as reproducing its $134 total.

The local CPU run evaluated 70 decisions on Apple silicon with allocation
profiling enabled. Qwen abstained on all 70 raw decisions; the classifier
escalated all 70. Neither demonstrates useful selectivity. Rules missed one
simulated protocol defect because their sensitive-boundary keyword heuristic did
not recognize it. Rules unnecessarily escalated five of twelve labeled benign
observations; the classifier escalated all twelve. Zero model-related misses
therefore do not imply equivalent quality.

Qwen median decision latency was approximately 386 ms on stored observations
and 283 ms on simulations, with respective p95 values of 543 ms and 688 ms.
The inference child-process peak RSS was approximately 2.57 GiB. These are local
CPU measurements, not results from the GitHub runner. The manual workflow has not
yet run.

### Offline packing qualification

The follow-up [context qualification](packing-quality-results.json) passes 29
offline cases: the 22 original fixtures, six new paired stress cases, and one
oversized-context control. Twenty-eight have executable labels and are eligible
for a later live comparison. The oversized control is deliberately excluded:
both strategies preserve its lines but cannot present its complete function in
one request. It must not count as a successful quality review.

This check found and repaired a candidate bug. Semantic splits shared an original
unit ID, causing sibling functions to be excluded from support without a context
gap marker. Support now considers individual pieces, prioritizes referenced
declarations, and records omitted siblings. This is a heuristic dependency aid,
not a complete call graph or semantic equivalence proof.

The helper-first and helper-last fixtures require the caller and helper contract
in the same request. The candidate now preserves that context in both defective
and benign counterparts; baseline does not. These fixtures cost three candidate
chunks versus two baseline chunks. JSON-record fixtures use three versus four.
The original research diff remains at seven candidate chunks versus fifteen
baseline chunks. Cheapest coverage alone therefore cannot qualify a plan.

Run `npm run reviewer:packing:qualify` to freeze fixture labels, required evidence
groups, per-role assessment prompts, routing, source fingerprints, and alternating
arm order in `.cache/packing-quality/frozen.json`. Repeating an identical freeze
is allowed; changed protocols require a new `--output` directory. That artifact
does not authorize inference or contain an execution command. The manual CPU
workflow also runs the offline gate and uploads its frozen artifact.

A separate $4 ceiling was explicitly authorized for the paired live comparison.
The runner uses existing evidence validation and finding verification rules,
disables assessment-cache reuse, compares matched cases, and retains unresolved
labels separately. Any additional missed defect or false positive blocks
recommendation. The completed one-pass $4 trial is not reopened.

The fixed `packing-quality-v1` identity lives under the shared Git directory in
`reviewer-research/packing-quality-v1`, with a separate external checkpoint. It
uses an isolated instance of the existing tested research-ledger implementation;
its experiment identity does not modify the previous ledger or its constants.
Each call reserves full model context plus bounded output before transport.
Uncertain usage and interrupted calls retain reservations. Missing or conflicting
history blocks execution. Restart skips started assessments rather than silently
paying to repeat them; their missing results remain incomplete.

Execution binds the reviewed prompt protocol and new execution-source fingerprint
separately. It requires `AWS_PROFILE=awill`, `AWS_REGION=us-east-1`, the configured
account identity, and the frozen DeepSeek routing. Profile, endpoint, routing,
account, pricing, or source mismatches stop spending. Research cannot run through
the production CI exemption boundary. Reporting is deterministic in both arms so
the comparison isolates packing and assessment effects.

With the authorized profile and region exported, use
`npm run reviewer:packing:live -- prepare` once, then
`npm run reviewer:packing:live -- run`. Use
`npm run reviewer:packing:live -- report` for offline reconstruction. Preparing an
existing identity is rejected; a completed pass cannot start another pass. The
ledger, frozen requests, responses, and report survive worktree changes.

### Live paired comparison

The [live measurements and targeted audit](packing-live-results.json) cover one
completed pass over 28 cases. The 296 calls cost **$0.63815688** at frozen usage
prices, with **$0 outstanding** and **$3.36184312 unused** from the separate $4
ceiling. These are usage-priced charges, not an AWS invoice reconciliation. The
allowance is closed to another pass; there was no production promotion.

Twenty-five cases completed in both arms: thirteen benign and twelve defective.
Case-level accuracy below uses only those matched cases. Incomplete reviews are
reported separately and never treated as clean reviews.

| Measurement | Baseline | Semantic byte candidate |
| --- | ---: | ---: |
| Complete cases, of 28 | 25 | 26 |
| Missed defects, of 12 matched defective cases | 2 | 2 |
| False positives, of 13 matched benign cases | 0 | 0 |
| Matched-case calls | 120 | 123 |
| Matched-case usage-priced cost | $0.19870107 | $0.22610809 |
| All calls | 146 | 150 |
| Total usage-priced cost | $0.29529469 | $0.34286219 |
| Input tokens | 415,557 | 492,052 |
| Output tokens | 20,351 | 20,427 |
| Summed call latency, seconds | 786.40 | 704.05 |

The candidate cost **13.79% more** on matched complete cases. The lower reservation
projection for the original PR diff did not predict savings on these fixtures.
Neither the cost difference nor this small quality sample establishes behavior
on other PRs. Both arms used the same three roles and deterministic reporting;
this trial did not compare cheaper models or alternative prose synthesis.

Both arms missed the large helper-first and helper-last defective fixtures.
For `current=False, saved=True`, the new `retain` function returns `persist`
although its contract requires `discard`. All three roles in both arms returned
clean assessments on these cases. Preserving the relevant lines and helper
context therefore did not establish adequate attention to the defect.

Baseline was incomplete on both benign helper cases because verification
expanded beyond the input limit. The candidate completed helper-first but had
the same verification-limit failure on helper-last. The offline gate checked
assessment prompts, not the eventual candidate-dependent verification payload;
that distinction matters for future packing qualification.

Both arms were incomplete on the defective JSON fixture. The candidate's three
roles recognized `used=5` with `cap=4`, but returned location `224`, outside the
allowed chunk-local ID range. Baseline had one invalid location and retained two
findings attached to a closing brace at source line 157. The actual defective
record occupies lines 225–227. Model verification retained these wrong source
attachments. The trial recorded four invalid responses, zero output truncations,
and zero unknown-usage calls; eight role assessments ended incomplete at the
verification input limit. None of those incomplete cases enters accuracy counts.

A targeted executable audit also found explanation errors in retained
protected-span findings. For `text='abcdef', start=3, end=6, protected=[(2,5)],
marker='X'`, the candidate claimed the new output was `abXf`; it is `abcX`.
Another example used `end=12` on ten characters, outside the fixture's valid-offset
contract, and also misstated the output. These findings flagged the correct
defective case but do not constitute fully correct explanations. Other finding
semantics have not been exhaustively adjudicated.

**Recommendation: do not promote this packing candidate.** First validate source
attachment and executable counterexamples more rigorously; then budget context
for verification as well as assessment. Evaluate smaller semantic review units
on the large-defect cases before optimizing for fewer requests. The prior
deterministic-reporting result remains a separate potential cost reduction.

1. Pursue packing qualification first. Coverage preservation supports further
   testing, but splitting and omitted context still require quality evaluation.
2. Expand adjudicated, paired cheap/strong-model evidence before training a
   production router. Keep fixture families separate and audit clean reviews.
3. Retain explicit rules as the baseline, while fixing demonstrated boundary
   omissions in a separately evaluated revision.
4. Do not promote either learned router from this sample. Report all-abstention
   and all-escalation behavior as such, even when mandatory rules prevent misses.

No automated promotion, approval, waiver, or production quote-policy change is
part of this experiment. A future execution quote must bind the exact cheapest
qualified plan, context and routing fingerprints, with no silent substitutions.
