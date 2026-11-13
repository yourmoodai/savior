# Repository instructions

See [README.md](README.md) for setup and [the architecture decision](docs/adr/0001-local-span-replacement.md) for invariants.

- Keep detection and replacement in Rust; adapters only transport requests and results.
- Never forward original input after a processing failure.
- Never log original prompts. Persistence is permitted only in the explicitly
  consented, private trace store described by ADR 4; metrics consent does not
  authorize it. Use synthetic fixtures in tests and profiles.
- Runtime inference must remain offline. Artifact downloads are allowed only during explicit setup or automatic first activation of an installed release.
- Preserve unmatched source text exactly. Reject ambiguous protected-span edits.
- Use `LOG_LEVEL` for logging; messages and error codes stay lowercase.
- Run `npm run build` and `npm run verify` before concluding implementation work.
- Model changes also require `npm run eval:local` and reference parity verification.
- Never add assistant attribution, signatures, or contributor footers.

## Completion and CI repair

- Define observable acceptance gates before editing, including the execution
  environment when behavior depends on GitHub or an operating system.
- Reproduce failures before claiming their cause. Label untested explanations
  as hypotheses and add regression coverage for confirmed failures.
- Before pushing, validate every new commit message and run offline reviewer
  admission against the exact base and head. A generated manifest is not admission.
- Missing evidence, unsupported analysis, failed checks, and unfinished runs are
  unresolved work even when a command exits zero.
- After pushing, follow that revision's CI to completion. Retrieve failed-job
  logs, investigate, repair, and rerun affected checks without waiting for another
  user instruction. New revisions invalidate earlier completion claims.
- Keep repairs in the existing PR unless separation is technically necessary
  and its dependencies and activation path have been established.
- Report implemented, locally verified, CI verified, and activated separately.
  A commit, PR link, passing linter, or mocked API test is not end-to-end evidence.
- Stop only when acceptance gates pass, the user stops work, or progress requires
  genuinely missing authorization or information. Complete independent work first
  and identify the exact blocked action. Never bypass protections to satisfy a gate.
- Keep feedback loops focused: rerun checks affected by changes or new failures;
  do not repeatedly run unrelated passing suites as a substitute for diagnosis.

These rules apply the short feedback loops and characterization tests described
by Michael Feathers in *Working Effectively with Legacy Code*, chapters 2 and 13,
and the integration/system testing distinctions in Mauricio Aniche's *Effective
Software Testing*, sections 1.4 and chapter 9. For analyzer repairs, preserve
soundness while improving precision, following Nielson, Nielson, and Hankin's
*Principles of Program Analysis*. Tests and observed results establish completion;
instructions and literature alone do not guarantee correctness.

## Evaluation language

- Use **measured policy alignment** for provider-policy evaluations.
- Separate replacement correctness, agreement with reference labels, and cross-model agreement in documentation, UI, reports, and result summaries.
- Model agreement never establishes provider approval, enforcement guarantees, reviewed labels, or judge calibration. Do not describe evaluation as certification.
- Keep provisional labels and unresolved judgments visible; report implementation checks separately from model-quality outcomes.
