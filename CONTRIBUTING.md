# Contributing to savior

Bug reports, design proposals, documentation corrections, tests, and code are
welcome. Start with an issue so contributors and maintainers agree on the
problem before implementing a change.

## Issue acceptance

Search [open and closed issues](https://github.com/yourmoodai/savior/issues?q=is%3Aissue)
before opening a proposal. Describe the problem, proposed behavior, a small
example, and effects on privacy, latency, compatibility, or model quality.

Community implementation starts when the maintainer applies `status: accepted`
or explicitly says the issue is ready for implementation. Acknowledgment,
questions, and ongoing design discussion are not acceptance. Issues labeled
`good first issue` or `help wanted` are accepted unless a maintainer says
otherwise. This also applies to small documentation changes.

Changes authored by [@awill1988](https://github.com/awill1988) are exempt from
issue acceptance because the maintainer supplies the prioritization directly.
The same technical and verification standards apply. An early community PR
may be closed and redirected to an issue; it can be reopened after acceptance.

## Maintainer commitments

The maintainer will acknowledge issues and initially review eligible PRs within
seven calendar days. Responses will state whether a proposal is accepted,
needs information or design discussion, duplicates existing work, or falls
outside the project's direction. Declined proposals receive a reason, and
blocked reviews receive a status update. Feedback stays specific and respectful.

Acknowledgment does not guarantee acceptance or merging. If seven days pass
without a response, one follow-up on the issue or PR is welcome.

## Design constraints

Keep detection and replacement in Rust, preserve unmatched text, and stop a
submission on processing failure. Inference stays offline; installed releases
may download verified artifacts on first activation. Never log original prompts.
Full-text retention requires ADR 4's separate consent.

Keep replacement correctness, reference-label agreement, and model agreement
distinct. Report policy evaluations as **measured policy alignment**. Missing
evidence cannot promote a model or backend. Consult the records below before
changing a boundary; discuss models, interfaces, agents, dependencies, licensing,
and releases in the accepted issue.

## Architecture records

ADRs describe enduring boundaries, not implementation logs. Keep their language
visual and audience-first; put commands, thresholds, and mechanical procedure
in the relevant benchmark, release, or tool guide.

| Record | Status | Owns |
| --- | --- | --- |
| [ADR 1](docs/adr/0001-local-span-replacement.md) | Accepted | Local transformation and adapter boundaries |
| [ADR 2](docs/adr/0002-release-and-quality-evidence.md) | Accepted | Release integrity and evidence gates |
| [ADR 3](docs/adr/0003-future-egress-enforcement.md) | Planned; unimplemented | Transparent proxy and limits on egress claims |
| [ADR 4](docs/adr/0004-consented-local-traces.md) | Accepted | Inspection and consented retention |
| [ADR 5](docs/adr/0005-sign-binaries-before-release.md) | Accepted for implementation | Platform signing, offline macOS launch, and approved promotion |

`.github/CODEOWNERS` explicitly assigns `docs/adr/**` to the maintainer. A
repository administrator must enable GitHub's **Require review from Code
Owners** branch-protection rule for protected branches; the file requests a
review but cannot enforce it by itself. ADR changes should not be merged through
an ordinary-contributor bypass.

## Development

Work on a focused branch in your fork. Source development requires Rust, a
C/C++ toolchain, and Node.js 22.18 or newer. Build the Rust library and typed
Node bindings before exercising the adapter.

```sh
npm ci
npm run build
npm run model:fetch
export SAVIOR_MODEL_DIR="$PWD/.cache/models/mudes-en-base"
node bridge/cli.mjs doctor
claude --plugin-dir "$PWD"
npm run verify
```

Model changes additionally require `npm run eval:local` and reference parity.
[Evaluation](docs/evaluation.md) explains the claim boundary;
[benchmarking](docs/benchmarking.md) owns the detailed quality and performance
procedure. Shared CI hardware does not qualify latency. An installed-host test
is separate from adapter unit tests; state explicitly when it cannot run.

Add focused tests for changed behavior. Update documentation for changed
interfaces and contributor workflows. Test and profiling data must be synthetic
or explicitly licensed for that purpose.

## Library and CLI contract

Generated TypeScript declarations are in `native/index.d.ts` after building.
Rust offsets use UTF-8 bytes; JavaScript offsets use UTF-16 units.
`detect` applies configured protection and threshold rules, while
`applyReplacements` validates and normalizes supplied spans. A detector allows
one queued or running inference; concurrent calls fail with `busy`.

The CLI and Node bridge receive versioned JSON on stdin. Version-1 requests
cover basic rewriting: success returns `version`, `status`, `text`, and
`replacement_count`; errors return a content-free `code` and exit nonzero.
Do not pass private prompts in process arguments.

The Node bridge also supports version 2 assessment and version 3 traced
rewriting. These can report a processing failure in the response without a
nonzero exit; callers must check both exit status and response status.

## Commit messages and hooks

Use lowercase Conventional Commit subjects, optionally preceded by a ticket
such as `[PROJ-123]`. Subjects must fit within 50 characters and have no trailing
period; wrap bodies at 72 characters. Do not add generated attribution,
co-author footers, or tool signatures.

The Rust [commit checker](https://github.com/yourmoodai/savior/blob/main/tools/commit_check/README.md) runs in CI and is
available through `.githooks/commit-msg`. Before configuring hooks, inspect the
existing path:

```sh
git config --get core.hooksPath
```

If no path is configured, activate the repository hooks with
`git config core.hooksPath .githooks`. If a developer profile already manages
hooks, preserve its signing, routing, and push checks. Use the checker directly
or integrate it through that profile's supported hook mechanism; do not replace
the configured path merely to install these hooks.

Existing published bootstrap commits predate commit-format enforcement. New
commits and PR titles are checked without rewriting that history.

## Pull requests

Link the accepted issue when required. Describe the resulting behavior,
affected modules and adapters, verification, limitations, and manual steps.
Use a conventional lowercase PR title. Keep review updates in new commits;
do not rewrite shared history. Green CI is required but does not replace
maintainer review or the model-promotion gates.

## Licensing

Submit only material you have the right to contribute. Synthetic fixtures use
`CC0-1.0` where declared.

Savior is licensed under the **GNU Affero General Public License v3.0 or later**
([`AGPL-3.0-or-later`](LICENSE)). Copies and forks must retain
[`LICENSE`](LICENSE) and [`NOTICE`](NOTICE), credit Adam Williams and
`yourmood.ai` with `https://github.com/yourmoodai/savior`, use a distinct
plugin `name` and `displayName` when distributed, and publish complete
Corresponding Source under `AGPL-3.0-or-later`.

Model weights remain separate Apache-2.0 artifacts. Their provenance and notices
are in [`models/catalog.json`](models/catalog.json) and
[`models/NOTICE`](models/NOTICE).

## Release and review workflows

[Release preparation](docs/releases.md) owns packaging, signing-key setup,
qualification, and recovery.
[Inspection](docs/inspection.md) covers user-visible edits and saved traces.
