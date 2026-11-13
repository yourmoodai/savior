# 10. Freeze review evidence before inference

Date: 2026-11-12

## Status

Proposed; implemented for review in PR #19. This decision owns source evidence
and planning admission. Model qualification remains governed by ADR 6 and
per-call spending by ADR 9. No live qualification or model promotion is implied.

## Context

A review request needs both changed lines and the source that explains them.
The old planner inferred supporting context from file names and original diff
hunks. Splitting a large hunk left each piece requiring its siblings, while an
8 kB support cap discarded them. Even independent functions became incomplete.

The execution path then paid for model qualification before checking whether
either PR plan was feasible. PR #19 passed qualification but reached no PR
assessment; its eventual planning failure appeared as an infrastructure failure.
Reconstructing requests during execution also weakened the quote's evidence
binding.

Increasing the support cap preserves more context cheaply, but retains false
dependencies and makes oversized requests harder to separate. Language-specific
compiler analysis offers stronger semantics, but requires build configuration,
dependency environments and sometimes execution of untrusted project tooling.
Syntax trees provide useful source boundaries offline, provided their uncertainty
remains visible and is never represented as whole-program proof.

## Decision

Separate revision snapshots, source evidence, dependency analysis and placement
into requests. Read only regular Git blobs at the merge base and reviewed head.
Pinned Tree-sitter grammars describe Python, Rust, JavaScript and TypeScript
declarations. JSON arrays use record boundaries and Markdown uses sections;
other formats retain exact textual diff coverage without semantic analysis.
Python multiline imports and Rust grouped imports are extracted from parser
nodes, preserving original symbols and local aliases. Glob imports remain
unresolved rather than claiming a complete binding set.

For recognized imports and symbol references, compute the transitive evidence
set with a terminating worklist. Include unchanged local helpers, imports and
top-level effects. Record ambiguity, dynamic dispatch, unsupported imports,
macros and syntax failures as unresolved obligations. External package imports
are an explicit analysis boundary, not vendored evidence. This is a bounded
syntax analysis, not a sound call graph or compiler replacement.

Pack complete evidence sets with unique primary ownership and space for a bounded
verification finding. Splitting is useful only when it reduces required evidence.
An oversized indivisible set remains incomplete; there is no arbitrary support
truncation. Freeze source identities, analyzer versions, nodes, edges, primary
locations and request messages into one digest-bound plan.

Preflight rejects infeasible plans before loading qualification. Execution checks
the quoted plan and restores its requests before qualification can spend. It
reports `planning_failed` separately from provider, qualification and spending
failures. The executor consumes the selected frozen plan; a quoted request that
needs subdivision requires a fresh plan and quote.

## Invariants & Abstention

- Changed evidence has exactly one primary owner with its original coordinates.
  Supporting source does not count as additional reviewed coverage.
- Every declared dependency and its transitive dependencies remain in the request.
  Missing source, ambiguous bindings, analysis limits or unresolved dependencies
  prevent complete review. Syntax coverage does not establish runtime behavior.
- No repository module, build script, filter, symlink or submodule executes during
  source analysis. Source blobs are capped at 2 MB; modules at 4,096 declarations,
  the graph at 16,384 declarations, and each closure at 4,096 nodes.
- Request estimates and verification headroom must fit before inference. Frozen
  ownership, revisions, analyzer versions, requests and quote bindings must agree.
  Infeasibility cannot become an approval or a cost waiver.
- Analyzer versions and implementation changes invalidate qualification and result
  fingerprints. A historical qualification cannot qualify this refactor.
- Offline replay makes no provider calls and cannot populate qualification caches.
  A clean automated approval still requires a complete qualified model review.

## Consequences

Independent declarations can be separated without inventing sibling dependencies,
and infeasible PRs stop before qualification expense. The plan exposes the exact
source and unresolved judgments behind a request, enabling deterministic replay.

Conservative dependencies can increase context size and abstentions. Unchanged
helpers may contain further dynamic behavior, making a previously plausible chunk
explicitly incomplete. Parser dependencies and analysis memory are new maintenance
costs. Build-system aliases, dependency injection, macro expansion and runtime
dispatch require further analysis or human review; silently dropping those
obligations would defeat this decision.

The [offline context report](../research/context-planning.md) records regression
evidence, remaining limits and the reading that informed these boundaries.
