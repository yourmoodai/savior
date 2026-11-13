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

A live comparison remains pending. It must use the existing evidence validation
and finding verification rules, disable assessment-cache reuse, compare matched
cases, and retain unresolved labels separately. Any additional missed defect or
false positive blocks recommendation. It needs a separately authorized ceiling
and persistent ledger; the completed one-pass $4 trial is not reopened.

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
