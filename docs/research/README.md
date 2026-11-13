# Reviewer experiments

The objective is an affordable, complete review that can issue automated
approval. Model agreement, successful JSON parsing, and a correct case-level
flag do not establish finding correctness. The experiment separates those
signals before comparing expense.

## Commands

`npm run reviewer:experiment -- replay` replays trusted counterexample oracles
and grades stored results without inference. `npm run reviewer:experiment --
freeze --output .cache/review-experiment/frozen.json` freezes the next offline
protocol, including verification envelopes and family-level held-out cases.
An oversized verification envelope makes a case ineligible for a paid trial.
Run `npm run reviewer:experiment:test` for mocked transport and grading tests;
`:debug` and `:profile` variants support debugging and Python allocation profiling.
Python has no separate release compilation mode. Native CPU experiments retain
their existing explicit debug/release build commands.

Both original paid grants are closed. Their command-line runners permit reporting
only. No new paid execution command is enabled. A new trial needs an authorized
ceiling, a reviewed frozen protocol, verified account routing and durable history.

## Historical evidence

- [Design comparison](reviewer-designs.md): completed `review-designs-v1`,
  $0.16335005 charged, no outstanding reservations.
- [Packing and CPU routing](cpu-review-routing.md): completed
  `packing-quality-v1`, $0.63815688 charged, no outstanding reservations;
  the CPU experiment made no provider calls.

Stored result JSON files are preserved byte-for-byte. They describe the original
harness, not this repaired implementation. Historical preparation/run commands in
those reports are archival and now refuse further spending. Reproduction requires
the original revision and does not grant permission to run inference again.

The source PRs were #14 (`033fa4dda2d60378c3f690707123e3e1555f583d`) and
#15 (`1f4856aa7e161bf45f54707c689c6d757a11632c`). Their branches and discussions
remain available. PR #13's reviewer clarification is incorporated into the harness
foundation. PR #10 and release-test repairs are separate.

Durable ledgers remain under the shared Git directory's `reviewer-research/`
directory, with adjacent anchor files. Never recreate, relocate, or reset them to
obtain spending authority. The replay report records hashes of historical results.

## Decisions informed by literature

Chip Huyen's *AI Engineering*, pp. 126–127 and 200–208, motivates executable
correctness checks, component-level evaluation, and evaluation of the grader
itself. The protected-span replay exercises those checks: a plausible defect
description can still contain a false output or invalid input.

[Anthropic's evaluation guidance](https://www.anthropic.com/research/demystifying-evals-for-ai-agents)
distinguishes transcripts from actual outcomes. Preserve both and grade behavior,
source attachment, coverage, and final disposition independently. Unknown finding
labels remain excluded from accuracy denominators.

[Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/) motivates varying helper
position and distracting context. It does not establish the cause of our model's
misses. Smaller chunks must preserve dependencies and outperform the baseline in
paired measurements before promotion.

[RouteLLM](https://arxiv.org/abs/2406.18665) motivates learning from paired model
outcomes. Simulated routing failures are infrastructure tests, not training evidence
for deployment. [SelectiveNet](https://proceedings.mlr.press/v97/geifman19a.html)
motivates reporting error and coverage together: universal escalation or abstention
does not demonstrate useful selection.

## Follow-up order

First review the consolidated harness fix, including deterministic reporting.
Then compare dependency-preserving packing, followed by qualified cheaper models.
Only then reconsider CPU routing using paired outcomes and held-out families.
Report matched costs, reservations, latency, misses, false positives, invalid
findings and abstentions separately. New regressions block recommendation; a small
passing sample cannot establish equivalence. None of these experiments promotes
a model, changes production limits, waives review, or merges a PR.

The historical prose arm is isolated in `research_prose_baseline.py` for mocked
regression replay. Production review does not import it.
