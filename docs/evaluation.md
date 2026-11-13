# Evidence and limitations

Savior first detects phrases, then replaces them while preserving surrounding
text. Testing the replacement checks whether the edit is exact. Evaluating the
detector checks which phrases it selects. A correct edit can still miss language
the user wanted removed.

Reference labels are the expected judgments attached to evaluation examples;
their review status matters. A judge is a separate model used to assess an
example. Agreement with reference labels and agreement between models are
separate results. Neither establishes account protection, provider approval, or
complete control of traffic leaving the host.

## Read the results by type

Exact-output checks compare rewritten text with an expected result. Parity
checks compare implementations against a reference implementation. Integration
tests follow the submission through the adapter that connects Claude Code to
Savior; their conclusions depend on which parts of that path were observed.

| Evidence | Supports | Does not support |
| --- | --- | --- |
| Exact-output and parity checks | Replacement behavior | Detection coverage |
| Reviewed labels | Agreement with that corpus | General accuracy |
| Local judge comparison | Agreement between judges | Ground truth or certification |
| Handler test | Handler continuation and refusal contract | Actual-client delivery |
| Source-checkout host test | Observed development integration | Packaged release qualification |
| Installed-candidate test | Observed path for the identified archive and client | Absence of every host bypass |
| Instrumented failure test | Failure and recovery of a modified disposable candidate | Unmodified artifact execution |

Missing, refused, malformed, or uncertain judgments remain unresolved.
Provider-policy evaluations use **measured policy alignment**; provisional
labels and uncalibrated judges produce `inconclusive`.

The default detector runs locally on the CPU using 32-bit floating-point
arithmetic (FP32). English social-media training does not establish accuracy
across developer prompts or other languages. Lexical and accelerated backends
remain experimental until their applicable parity,
quality, and performance evidence is complete. Personal review is opt-in and
does not alter successful replacements; its stored metrics contain no text.

## Reading dated evidence

The observations below retain their original dates, source identities, and
limitations. They describe the recorded builds and conditions, not a fresh
qualification of the current checkout. Failed measurements remain visible.

### Claude 2.1.322 interception check

The 2026-11-11 macOS arm64 source-checkout run passed 16 cases: eight scenarios
under each of `fixed_phrase` and `omit`. Both stalled workers reached the
45-second process deadline, stopped, retained the draft, and completed an exact
retry. Rewritten and unchanged submissions, protected refusal, missing models,
malformed output, immediate timeout responses, and worker exits also passed.
The [content-free evidence](../benchmarks/results/interception-2026-11-11.json)
records client, adapter, model, harness, and per-case report identities.

The investigation found that filling a refused draft in the adapter duplicated
the host's own restoration. The adapter now leaves recovery to the host. Terminal
editing also needs a fresh redraw before the harness retries. Earlier failed
attempts remain identified in the evidence; they are not release qualifications.

This is source-checkout evidence for one native platform. Packaged startup
failure, the final release archive, Linux/Windows native runs, and whole-host
egress coverage remain unverified. The page and installation path remain
unreleased.

## Inspection evidence

Recorded on 2026-10-21. These checks concern signed setup and retained inspection;
they do not qualify later changes, including the live watcher.

| New boundary | Local verification |
| --- | --- |
| Signed bootstrap | Signature rejection, interrupted download, concurrent setup, crash recovery, and offline reuse |
| Real compiled runtime | macOS ARM64 binaries and native addon exercised with an empty `PATH` |
| Trace store | Six checks covering consent, exact edits, retention, annotations, recovery, and Parquet |
| Browser inspector | 12 checks passed: consent, Unicode edits, annotations, literal markup, access controls, export, deletion, and nonblocking recording failure |

The public GitHub download matrix and Windows/Linux runtime checks remain CI
release gates, not locally established claims. Timing screens vary substantially
with machine load and do not qualify latency.

The final five-sample short-input screen measured 24.3–32.7 ms without capture,
24.8–31.1 ms with metadata, and 25.2–26.1 ms with full traces. Cold/warm Parquet
export took 31.1/13.3 ms for ten synthetic records; peak parent-process RSS was
about 480 MiB including the detector. A separate busy-machine run reached
432 ms, so these figures are observations, not overhead guarantees.

[Browser evidence](../benchmarks/results/inspection-e2e-2026-10-21.json) ·
[Final timing screen](../benchmarks/results/inspection-final-2026-10-21.json) ·
[Busy-machine screen](../benchmarks/results/inspection-2026-10-21.json) ·
[Inspection guide and reproduction commands](benchmarking.md#inspection-and-live-feed-checks).

## Boundaries still unverified

Synthetic and provisional labels do not estimate real-world accuracy,
provider-policy compliance, or judge calibration. The Claude Code integration
does not prove the absence of host telemetry, other plugin behavior, or voice
uploads before filtering. The proposed proxy remains unimplemented; see
[ADR 3](adr/0003-future-egress-enforcement.md).

## Detailed results

The [benchmark results](#benchmark-results) below preserve the dated snapshot,
including failed measurements and corpus limitations. Exact replacement checks,
reference-label agreement, and timing screens answer separate questions; their
counts must not be combined into an overall quality score.

<!-- benchmark-results:start -->
## Benchmark results

Savior’s replacement engine is checked for exact preservation outside selected spans. Detection is measured separately against public reference labels.

| Check | Observed result |
| --- | --- |
| Known-span replacement and abstention | 42/42 checks passed |
| Rust and Node CLI end-to-end checks | 36/36 checks passed |
| Installed Claude Code delivery and recovery | 15/15 requested checks passed |
| Scunthorpe flag agreement | 74.5% on 188/200 sampled cases; F1 0.739; 12 abstentions |
| HateCheck hate-label agreement | 62.2% on 193/200 sampled cases; F1 0.757; 7 abstentions |
| Unmatched text: `fixed_phrase` | 381/381 successful outputs preserved; 19 abstentions |
| Unmatched text: `custom_phrase` | 381/381 successful outputs preserved; 19 abstentions |
| Unmatched text: `omit` | 381/381 successful outputs preserved; 19 abstentions |

Measured 2026-10-21 UTC on Apple M5 (darwin, arm64), CPU FP32, pinned `mudes/en-base`.
The external screen samples English cases by category and label; it is not a population accuracy estimate. HateCheck labels hate speech, which differs from Savior’s offensive-span policy.
Reviewed external replacement spans are unavailable. These classification figures do not establish successful removal of targeted abuse.

| Resident rewrite fixture | Native maximum | Node-API maximum | Failed combinations |
| --- | ---: | ---: | ---: |
| short | 39.88 ms | 37.62 ms | 0/4 |
| medium | 174.36 ms | 183.93 ms | 0/4 |
| long | 862.72 ms | 888.88 ms | 0/4 |
| maximum | 16.39 s | 17.03 s | 0/4 |
| dense | 23.09 s | 23.69 s | 0/4 |
| unicode | 27.72 s | 28.61 s | 0/4 |
| protected | unavailable | unavailable | 4/4 |
| token_dense | unavailable | unavailable | 4/4 |
| window_boundary | 1.78 s | 1.83 s | 0/4 |
| tokens_509 | 607.60 ms | 581.08 ms | 0/4 |
| tokens_510 | 582.60 ms | 600.22 ms | 0/4 |
| tokens_511 | 757.13 ms | 758.90 ms | 0/4 |
| tokens_892 | 1.17 s | 1.20 s | 0/4 |
| tokens_893 | 1.35 s | 1.34 s | 0/4 |
| punctuation_dense | unavailable | unavailable | 4/4 |

Performance coverage: 48/60 measurements completed successfully; 12 failed. Maxima above combine strategies only within the same fixture; failed timings are not success samples.
Recorded configuration: 1 run(s), 1 measured sample(s) and 0 warmup(s) per fixture, strategy, and boundary; 45 s worker deadline.
This is a screen, not latency qualification. The all-input maximum-below-500-ms screen did not pass. Cold load, process startup, per-fixture timing, and peak RSS are recorded separately in the evidence.
Latency runs exclude stage profiling. Matched per-fixture strategy comparisons and explicit failure causes are retained in the snapshot.
Failure classifications: 12 `processing_timeout`.
Machine context: 10 logical CPUs; start/end 1-, 5-, and 15-minute load averages 7.09 / 7.84 / 7.25 → 6.82 / 7.47 / 7.38.
Per-measurement 1-minute load ranged from 6.29 to 8.94. Background work was not controlled; load alone does not establish the cause of a timeout.

[Evidence snapshot](../benchmarks/results/latest.json) · [Reproduce the checks](benchmarking.md)
<!-- benchmark-results:end -->

## Reproduce or investigate

[Benchmarking](benchmarking.md) owns corpus provenance, commands, measurement
protocols, and report publishing. [Contributing](../CONTRIBUTING.md#development)
covers implementation verification, and [release preparation](releases.md)
covers distribution checks. Use synthetic or licensed evaluation inputs and
new evidence paths; never turn private prompts into test or benchmark data.

[ADR 1](adr/0001-local-span-replacement.md) defines the transformation boundary.
[ADR 2](adr/0002-release-and-quality-evidence.md) defines the evidence needed to
promote a release or default.
