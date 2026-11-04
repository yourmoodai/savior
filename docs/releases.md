# Release preparation

This guide is for maintainers. Users should start with
[installation](usage.md#installation). No release is currently published.
[ADR 2](adr/0002-release-and-quality-evidence.md) governs existing artifact
integrity and qualification.

## Current packaging and qualification

After building, stage one supported target into a new directory:

```sh
python3 scripts/release/package.py stage darwin-arm64 .cache/parts/darwin-arm64
```

Use `linux-x64` or `win32-x64` on their corresponding build hosts.
Staging obtains a checksum-pinned Node runtime and copies the built binaries
and addon. CI gathers all three target directories, then assembles the plugin:

```sh
python3 scripts/release/package.py assemble .cache/parts .cache/release/plugin
```

Assembly requires a fresh destination. It creates the plugin ZIP, runtime
assets, signed runtime manifest, public key, checksums, and marketplace entry.
The marketplace points at a versioned, checksummed ZIP. Source archives are not
installable plugin bundles.

The protected `release` environment supplies `SAVIOR_RELEASE_SIGNING_KEY`
as an Ed25519 private PEM. Provision it separately; do not generate or publish a
production identity in a development test. Non-tag qualification builds use
ephemeral keys. The plugin carries the trusted public key and signed manifest;
key rotation requires a new trusted plugin version.

CI checks all advertised platforms, packaged execution, and the installed host
before tag publication. The `published-download` matrix tests the actual public
assets afterward. Local cache or mock-download checks do not establish success
on that distribution path. Marketplace promotion must wait for the public
download checks.

## Setup recovery

The bootstrap reuses verified downloads and recovers a setup lock whose recorded
PID is no longer alive. An incomplete lock or interrupted recovery may require
manual repair. First confirm that no setup process is running, then remove only
the lock in the identified release cache and retry setup. Verified partial
downloads remain reusable. Cache locations are described in
[inspection storage](inspection.md#storage-and-interpretation).

If the operating system blocks a runtime, keep its protections enabled and
report the failure with content-free diagnostics. Never remove quarantine
metadata or disable Gatekeeper, Smart App Control, or antivirus as an
installation workaround.

## Review automation

The [reviewer guide](https://github.com/yourmoodai/savior/blob/main/tools/adversarial_reviewer/README.md)
owns the `adversarial verdict` job and its review limits. Required human reviews
and stale-review dismissal remain in effect. CI authenticates through
short-lived AWS credentials and GitHub OIDC; never copy credentials into CI.

## Tagged releases and repository controls

[ADR 2](adr/0002-release-and-quality-evidence.md) owns release packaging,
activation, and qualification. Run `python3 scripts/release/package.py stage`
with a supported target and output directory after a release build. CI assembles
all targets, exercises the archive, and gates tag publication on installed-host
tests. The generated release marketplace points at a checksummed ZIP; never
point it at an unqualified candidate. Source archives are not installable
release bundles.

Run **prepare release** from `main` in GitHub Actions with an exact SemVer
version, such as `0.2.0-rc.1`. It must exceed the current source version and
have no existing tag or release. The workflow opens a labeled `release/v…`
PR updating npm, Cargo, lockfiles, and Claude plugin metadata together.
Repeating the request reuses the open PR; a branch without an open PR requires
maintainer inspection. Enable **Allow GitHub Actions to create and approve
pull requests** in repository Actions settings for this automation.

Preparation dispatches verification, commit checks, and three-platform
qualification for the PR's exact head. PR qualification uses ephemeral keys
and has no production environment or publication job. Review the generated PR
and its checks before merging. Merging does not create a tag or publish.

After reviewing release prerequisites, fetch `main` into a clean checkout,
verify the merged commit and version, then explicitly create and push its tag:

```sh
git pull --ff-only
git tag v0.2.0-rc.1
git push origin v0.2.0-rc.1
```

The **release** workflow runs only for pushed `v*` tags. Before building or
accessing credentials, it requires valid SemVer, agreement between all package
versions, an exact `v<version>` tag, and a tagged commit reachable from `main`.
It refuses an existing GitHub release, including a draft. Rerun failed jobs
on the original tagged run when no release exists; never move tags or replace
published assets. A correction to published bytes requires a new version.
The current source version may be tagged directly for the first release.

The protected `release` environment must provide `SAVIOR_RELEASE_SIGNING_KEY`
as an Ed25519 private PEM secret and `SAVIOR_RELEASE_PUBLIC_KEY` as its matching
public PEM variable. Provision them outside the repository; do not generate
or publish a production identity as part of a development test. Only the
packaging job receives the private key. Publication verifies the signature
and runtime hashes against the independently configured public key. Missing
or mismatched configuration blocks publication. Non-tag builds use an ephemeral
qualification key. The plugin carries its public key and signed manifest;
rotating the key requires a new trusted plugin version.
Platform signing and notarization remain separate prerequisites; this workflow
change does not establish OS acceptance. The existing moving `latest`
marketplace URL also requires a separate promotion change before it can
guarantee that advertising waits for public-download checks. See
[local inspection](inspection.md) for trace tooling.

### Automation maintenance

PR updates run one native build matrix on macOS, Linux, and Windows. The same
matrix builds fresh artifacts for integration-branch pushes and release tags.
`verify` owns tooling and Core ML checks; qualification owns native tests,
packaging, and installed-host checks. Browser checks run on macOS and Linux.
The completion-triggered cancellation workflow cancels active sibling checks
only after an eligible workflow fails on the same revision. It runs trusted
default-branch code and does not poll while builds are running.

External actions use full commit SHA pins. Weekly Dependabot PRs cover workflow
and composite-action references and the isolated Claude host installation in
`tools/host`. Compatible action updates are grouped; major upgrades remain
separate. Review upstream changes and CI results before merging updates.

Rust uses `rust-toolchain.toml`, with the same exact version passed to the
pinned installer. Cargo commands require the committed lockfile. Node's
packaged version and per-platform checksums live in `scripts/release/package.py`;
update setup-node inputs alongside them. Python uses an exact `3.12.10`
installer version. The Windows host fixture uses a hash-locked CPython 3.12
wheel. Updating Python requires reviewing that wheel and its hash together.
Hosted runner labels receive image updates and do not provide immutable builds.

After `npm ci`, run `python3 scripts/release/lint_workflows.py` to check every
workflow, local composite metadata, action pins, and update coverage. The
script downloads checksum-verified actionlint `1.7.12` for Linux x64 or macOS
ARM64. Run `npm run build` and `npm run verify` for the native checks. Generic
manual qualification also runs the ignored training/reload parity checks on
macOS and Linux; generated release PR dispatches omit that optional experiment.

Repository administrators can preview release protections without changing
GitHub settings:

```sh
python3 scripts/release/configure_github.py
python3 scripts/release/configure_github.py --apply
```

The command uses the configured `gh` account and repository. It allows only
maintainers and admins to create `v*` tags, blocks their updates and deletion
with a separate ruleset that has no bypasses, and restricts the `release`
environment to those tags. It preserves existing environment reviewers and
unrelated rulesets. Unexpected deployment policies require inspection.

Once these workflow changes are merged into `main`, enable SHA enforcement and
read-only default workflow tokens:

```sh
python3 scripts/release/configure_github.py --enforce-after-merge
python3 scripts/release/configure_github.py --enforce-after-merge --apply
```

The command verifies that the local automation matches `main` before enforcing
pins, so old base-branch review workflows remain usable during migration.
Qualification artifacts expire after seven days; production staging artifacts
expire after 30 days. Published release assets retain their separate lifecycle.

The `adversarial verdict` CI job and its review limits are documented in the
[reviewer guide](https://github.com/yourmoodai/savior/blob/main/tools/adversarial_reviewer/README.md). Existing required human
reviews and stale-review dismissal remain in effect. Authentication uses
short-lived AWS credentials through GitHub OIDC; do not copy credentials into
CI.
