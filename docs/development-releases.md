# Development prereleases

Development releases exercise public installation before platform publisher
signing is available. They retain artifact signatures and checksums, but lack
Apple signing/notarization and Windows publisher signing. macOS may refuse them.
[ADR 7](adr/0007-development-prereleases.md) defines this exception.

## Install the first development release

After `v0.1.0-dev.1` is published, use the supported Claude Code 2.1.322 client:

```sh
claude plugin marketplace add https://github.com/yourmoodai/savior/releases/download/v0.1.0-dev.1/marketplace.json
claude plugin install savior@yourmood.ai-dev --scope user
```

Launch Claude and open `/savior`. Setup downloads verified artifacts before the
local filter becomes ready. Do not disable OS protections to make it run.
The public installation workflow reports macOS and Linux separately; Windows
acceptance remains deferred. The stable marketplace and homepage remain unchanged.

## Prepare and publish

Merge the implementation and matching version metadata before tagging. Release
preparation permits `0.1.0-dev.1` from the initial `0.1.0` metadata only when the
repository has no tags or releases. Subsequent versions must increase.

The `development release` workflow handles `vX.Y.Z-dev.N` tags. It builds native
stages, packages with a temporary signing key, checks the sealed inventory, and
runs macOS/Linux packaged and fault tests before publishing an immutable
prerelease with `latest=false`. It does not use production signing credentials.
Tag only a reviewed main revision; never move an existing tag.

Run `npm run build`, `npm run verify`,
`python3 -m unittest discover -s scripts/release`, and
`python3 scripts/release/lint_workflows.py` before publication. For debugging use
`python3 -m pdb scripts/release/development.py gate` with the same workflow
environment. These orchestration scripts have no compiled debug/release modes
or memory-profiling requirement; their runtime inputs are release builds.

## Read the evidence

After publication, fresh profiles install from the public marketplace URL and
launch the installed plugin without a plugin-directory override. Reports bind
the downloaded archive and client hashes. Normal replacement, unchanged input,
protected-text refusal/retry, and cached bootstrap reuse are separate checks.
The bootstrap reuse check refuses download requests; it does not claim that
the entire host process has no network access.

macOS adds quarantine and runs the OS assessment before executing the package.
A rejection fails that job and leaves its evidence artifact available. No
quarantine is removed. Public failures cannot retract an immutable release:
inspect the failed stage, fix the implementation, and prepare a new version.

Fault injection uses separate disposable copies and the existing 18-case host
matrix. Its success is distinct from public installation and native publisher
trust. Production releases still follow [the signed release guide](releases.md).
