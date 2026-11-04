# Release preparation

This guide is for maintainers. Users should start with [installation](usage.md#installation).
[ADR 2](adr/0002-release-and-quality-evidence.md) defines integrity and evidence
requirements; [ADR 5](adr/0005-sign-binaries-before-release.md) defines platform
signing and promotion. Production enrollment and credentialed native acceptance
must be completed before advertising a release. Placeholders cannot pass those gates.

## Release sequence

1. Run **prepare release** from `main` with an increasing SemVer version.
   It opens a labeled `release/v…` PR updating npm, Cargo, lockfiles, and plugin
   metadata together. It dispatches verification and qualification for the exact
   PR head. Repeating the request resumes an unchanged partial preparation or
   reuses the open PR; unexpected branch contents require inspection.
2. Review and merge the PR. Merging neither tags nor publishes it. In a clean
   checkout, fetch `main`, verify the merged revision and version, then explicitly
   create and push `v<version>`. The first release may tag the current version.
3. Approve the protected `release` environment. The tag workflow builds three
   native stages, signs the Windows payloads, signs and notarizes the complete
   macOS application bundle, staples its ticket, and signs the final inventory.
4. Fresh native jobs check packaged execution and the installed host. macOS also
   checks its first launch with external networking blocked, after downloading
   all model files. Publication requires reports bound to the exact archive hash.
5. Publish immutable assets with `--latest=false`. Prerelease versions retain
   GitHub's prerelease flag. The public-download matrix then rechecks signatures,
   hashes, native acceptance, and replacement using the published files.
6. Run **promote marketplace** from `main`, supplying the stable version,
   successful release run ID, and the reviewed `marketplace` branch SHA
   (`absent` for first promotion). Approve the protected `marketplace`
   environment. Promotion rechecks the current run attempt, all required jobs,
   the trusted signing key, model hashes, and immutable release before an atomic
   branch update. A changed branch head aborts promotion.

The canonical installation feed is
`https://raw.githubusercontent.com/yourmoodai/savior/marketplace/marketplace.json`.
Publishing never changes it. Each promotion records its source revision, run
attempt, and asset hashes in `promotion.json` on that branch. An intentional
rollback uses the same approved workflow with a previous qualified release and
the current branch SHA; it does not rewrite history or release assets.

Source archives are not installable plugin bundles. Candidate ZIPs must stay
below 210 MB. Qualification uses ephemeral Ed25519 keys and receives no
production credentials. Signing and notarization services receive software
artifacts, never user prompts.

## Identity provisioning

Copy `scripts/release/local.example.json` into private configuration. Keep its
public identity fields as placeholders until the organization completes enrollment:

| Configuration | Obtain independently |
| --- | --- |
| `identities.apple` | SHA-256 of the Developer ID Application leaf certificate's DER bytes |
| `identities.windows` | SHA-256 of the selected Authenticode leaf certificate's DER bytes |
| `identities.node_apple`, `identities.node_windows` | Verified upstream Node signer fingerprints for the pinned Node release |
| `windows.storetype`, `windows.alias`, optional `windows.endpoint` | Provider-specific signing configuration |
| `SAVIOR_RELEASE_PUBLIC_KEY` | Public PEM matching the separately provisioned Ed25519 release key |

For Apple, an authorized account holder creates a **Developer ID Application**
certificate in [Certificates, Identifiers & Profiles](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/).
Record the legal publisher and team ID in the organization's release inventory.
Export the certificate and corresponding private key as a password-protected
PKCS#12 file. Obtain an App Store Connect API key with notarization access,
then use `rcodesign encode-app-store-connect-api-key --help` to produce its
unified JSON credential format. Follow the pinned tool's
[credential instructions](https://gregoryszorc.com/docs/apple-codesign/stable/apple_codesign_github_actions.html);
do not paste credentials into source, issues, PRs, or command arguments.

For Windows, choose a publicly trusted provider and complete its identity
verification before configuring Jsign. For Microsoft, follow the
[Artifact Signing portal setup](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart),
create a public-trust certificate profile, and grant the signing identity only
the required profile access. Configure `TRUSTEDSIGNING`, the regional endpoint,
and the account/profile alias using [Jsign's provider documentation](https://ebourg.github.io/jsign/).
The adapter also supports `PKCS11` and `DIGICERTONE`. `PKCS12` is useful for
synthetic tests; it does not itself establish public trust. Supply trusted signer
and timestamp CA bundles independently of candidate artifacts. The timestamp
service must produce a verifiable RFC 3161 token; its documented transport may
be HTTP or HTTPS.

Node retains its upstream signatures. Do not substitute our certificate for
Node's publisher identity. Renewals, provider certificate rotation, or Node
updates require independently reviewing the expected fingerprints. A matching
hash alone does not prove OS trust.

## CI configuration

Set repository variables `SAVIOR_RELEASE_CONFIG` to the public JSON configuration
and `SAVIOR_RELEASE_PUBLIC_KEY` to the expected public PEM. Native and promotion
jobs need these public values; keep them available at repository scope.
Keep tool names as supplied in the example when using CI.

Set the following **release environment secrets**, each containing the base64
encoding of the corresponding file. Base64 is transport encoding, not encryption.
The runner decodes files into a private temporary directory, mounts it read-only,
and deletes it after signing. Credential files never enter the image or artifacts.

| Secret suffix after `SAVIOR_` | File |
| --- | --- |
| `APPLE_P12_BASE64` | Developer ID certificate and private key |
| `APPLE_PASSWORD_BASE64` | PKCS#12 password |
| `APPLE_API_BASE64` | Unified App Store Connect API key JSON |
| `WINDOWS_KEYSTORE_BASE64` | Keystore or provider configuration; omitted for `TRUSTEDSIGNING` |
| `WINDOWS_PASSWORD_BASE64` | Provider credential in Jsign's documented representation |
| `WINDOWS_CA_BASE64` | Trusted Authenticode signer CA bundle |
| `TIMESTAMP_CA_BASE64` | Trusted timestamp CA bundle |
| `MANIFEST_PRIVATE_BASE64` | Persistent Ed25519 private PEM |
| `MANIFEST_PUBLIC_BASE64` | Independently trusted matching public PEM |

Use the configured secret manager and GitHub environment settings to supply
these values. The workflow does not enroll identities or generate production keys.
Short-lived provider credentials must remain valid for the signing job.

Enable **Allow GitHub Actions to create and approve pull requests** for release
preparation. Administrators can preview repository controls before applying:

```sh
python3 scripts/release/configure_github.py
python3 scripts/release/configure_github.py --apply
```

After the updated automation reaches `main`, preview and apply the full policy:

```sh
python3 scripts/release/configure_github.py --enforce-after-merge
python3 scripts/release/configure_github.py --enforce-after-merge --apply
```

Enforcement requires the local automation to match `main`. It adds required
`verification`, `qualification`, and `check` contexts from GitHub Actions,
code-owner review with stale-review dismissal, immutable releases, full action
pins, and read-only token defaults. Existing unrelated policies remain.
Tag creation is limited to maintainers/admins; tag updates and deletion have
no bypass. The marketplace permits updates through GitHub Actions, blocks
history rewrites/deletion, and restricts its deployment environment to `main`.
Only the promotion workflow should receive write permissions for that purpose.

Existing environment reviewers are preserved. An environment without reviewers
uses the configured administrator's GitHub account; review the preview and
replace it with the organization's maintainer team if appropriate. Self-review
is permitted for this single-maintainer workflow unless an existing policy
forbids it. The separate promotion approval is still mandatory. Do not tag
production releases before these controls and credentials are configured.

## Local preparation

Install CMake 3.25+, Ninja, and Python 3.12+. Planning reports prerequisites without
network access, signing, or candidate output; CMake writes only build metadata.

```sh
cmake --workflow --preset release_plan
cmake --build --preset release_test
```

On each matching native host, build and stage `darwin-arm64`, `linux-x64`, or
`win32-x64`:

```sh
npm run build
python3 scripts/release/package.py stage darwin-arm64 .cache/parts/darwin-arm64
```

Collect all three directories with `stage.json`. Stages and assembly checkout
must identify the same revision and version, with identical shared wrappers.
Signing uses private copies and preserves the original stages.

Local configuration maps credentials to external `SAVIOR_*_FILE` paths using
the example's `credentials` object. All files, including public trust material,
must be owner-readable only and outside the checkout, stages, and outputs.
Configure paths, never credential values, in CMake:

```sh
cmake --preset release \
  -DSAVIOR_RELEASE_CONFIG=/private/release/config.json \
  -DSAVIOR_RELEASE_PARTS=/work/parts \
  -DSAVIOR_RELEASE_OUTPUT=/work/new-candidate
cmake --build --preset release_candidate
```

The targets `release_preflight`, `release_sign`, `release_notarize`,
`release_package`, and `release_validate` expose individual stages.
`release_candidate` runs them in order. The Python wrapper has no debug/release
build modes; its inputs must be release builds. To debug, use
`python3 -m pdb scripts/release/local.py plan` with the same configuration,
parts, and output arguments. Memory profiling belongs to the native runtime,
not this subprocess orchestrator.

The pinned Linux image supplies CMake, Node, Python, Java, OpenSSL, `rcodesign`,
Jsign, and `osslsigncode`:

```sh
docker build -f scripts/release/Dockerfile -t savior-release-tools .
docker run --rm --network none --user "$(id -u):$(id -g)" --volume "$PWD:/workspace:ro" \
  --entrypoint python3 savior-release-tools -m unittest discover -s scripts/release
```

For a linked worktree, also mount its common Git directory at the absolute
path referenced by `.git`. Credentialed preparation needs a writable output,
host UID/GID, read-only secret mounts, and network access to signing services,
Apple, timestamp services, and npm. Container CMake presets use a separate
`.cache/cmake-release-container` directory.

## Evidence and recovery

Local `candidate/validated.json` reports static integrity separately from
native acceptance, smoke testing, and publication. Cross-platform signature
verification does not establish native acceptance. Production jobs require
Apple's `codesign`, `stapler`, and `spctl` checks, expected signer identities,
Windows Authenticode trust and timestamps, actual addon loading, replacement,
and worker lifecycle tests. The macOS offline gate runs only on disposable
GitHub-hosted runners, adds quarantine metadata, blocks external traffic before
the first candidate execution, and restores networking afterward. Never run a
firewall-changing test on a developer machine.

A failed public-download check leaves the previous marketplace unchanged.
Do not move tags, replace immutable assets, or claim that static tests prove
warning-free installation. Managed machines and Windows reputation policies may
still impose additional restrictions.

Interrupted local notarization waits resume the recorded submission and ZIP hash.
An uncertain upload blocks automatic resubmission: reconcile Apple's submission
history before starting a fresh output. Partial packaging requires fresh output.
Remove a stale `.lock` only after confirming its process has stopped. Keep
private state out of release uploads; `LOG_LEVEL` controls wrapper diagnostics.

A tag workflow can rerun before publication when no GitHub release exists.
An existing draft or published release stops automatic publication for inspection.
Once published, corrections require a new version. If a key is compromised,
stop signing and promotion, revoke it through the issuing portal/provider,
rotate credentials and independently reviewed fingerprints, and publish a new
trusted plugin version with a new Ed25519 trust key when required. Requalify all
platforms before promotion. Record renewal owners and expiry dates outside source.

## Setup recovery

For an interrupted user setup, confirm the setup process is stopped before
removing only its stale cache lock. Verified partial downloads can be reused.
Keep Gatekeeper, Smart App Control, antivirus, and quarantine intact; report
content-free diagnostics when a runtime cannot start.

## Automation maintenance

External actions use full SHA pins with Dependabot coverage. Rust is pinned in
`rust-toolchain.toml`; Cargo uses the committed lockfile. Packaged Node and its
checksums live in `scripts/release/package.py`; the signing image's separate
toolchain lives in `scripts/release/tools.lock.json`. Update pins deliberately.
Python CI uses `3.12.10`; review the Windows host wheel lock when changing it.
Hosted runner image labels are mutable.

Run `npm run build`, `npm run verify`, release Python tests, and
`python3 scripts/release/lint_workflows.py`. Generic manual qualification also
runs optional training/reload checks on macOS and Linux; generated release PR
dispatches omit that experiment. Neither changes model-promotion requirements.

Qualification artifacts expire after seven days; production staging artifacts
expire after 30 days. Published assets have their own immutable lifecycle.
The [reviewer guide](https://github.com/yourmoodai/savior/blob/main/tools/adversarial_reviewer/README.md)
owns the advisory verdict and its limits; human review remains required.
