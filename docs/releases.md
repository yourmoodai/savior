# Release preparation

This guide is for maintainers. Users should start with
[installation](usage.md#installation). No release is currently published.
[ADR 2](adr/0002-release-and-quality-evidence.md) governs existing artifact
integrity and qualification; [ADR 5](adr/0005-sign-binaries-before-release.md)
proposes additional platform signing and promotion requirements.

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

The current instructions use a moving `latest` marketplace URL. It does not
implement ADR 5's proposed independent promotion point; that remains unresolved.
Do not treat this procedure as evidence that platform signing is implemented.

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

## Proposed platform signing

**Proposed and unimplemented.** The following is ADR 5's intended release order,
not an available signing command or a claim about current artifacts.

1. Build the supported targets from one identified source revision.
2. Sign and timestamp final platform artifacts; complete macOS notarization and
   any applicable ticket packaging.
3. Hash final runtime bytes, sign the manifest, assemble the archive, and
   calculate its marketplace checksum.
4. Validate versions, inventory, signer identities, notarization, sizes, hashes,
   and the generated marketplace entry.
5. Pass packaged-runtime and installed-host smoke tests on every advertised
   platform, including clean machines with normal OS protections.
6. Publish immutable versioned assets and pass the real public-download matrix.
7. Promote through a separately controlled marketplace point only after success.

Keep the persistent Ed25519 key separate from platform certificate keys or
managed-signing permissions. Record expected public identities independently
of candidate artifacts. Private keys and credentials never enter source or
release assets.

### Unresolved choices

- Windows signer: evaluate SignPath Foundation eligibility, including its
  already-released-project requirement, and Microsoft Artifact Signing enrollment
  and cost. Provider selection cannot relax the trusted-signature requirement.
- Apple identity: choose the legal publisher and developer team responsible for
  certificate renewal and recovery.
- Notarization: select a container and ticket strategy for separately downloaded
  command-line binaries and the addon, including offline first launch. Justify
  hardened-runtime entitlements against the packaged Node runtime.
- Promotion: replace reliance on a moving release URL with an independently
  controlled promotion mechanism.

macOS can block unnotarized downloads; Windows reputation or application-control
checks can block unsigned binaries. Missing signatures alone do not establish
that an OS will delete a file. Signing and notarization do not guarantee
warning-free execution or acceptance on managed machines.

### References

- [Apple enrollment](https://developer.apple.com/programs/enroll/) and
  [Developer ID certificates](https://developer.apple.com/help/account/certificates/create-developer-id-certificates).
- [Apple notarization](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)
  and [Gatekeeper](https://support.apple.com/en-us/102445).
- [Microsoft Artifact Signing](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart)
  and [SmartScreen reputation](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).
- [SignPath Foundation eligibility](https://signpath.org/terms).
- [Sigstore CI signing](https://docs.sigstore.dev/quickstart/quickstart-ci/).

## Review automation

The [reviewer guide](https://github.com/yourmoodai/savior/blob/main/tools/adversarial_reviewer/README.md)
owns the `adversarial verdict` job and its review limits. Required human reviews
and stale-review dismissal remain in effect. CI authenticates through
short-lived AWS credentials and GitHub OIDC; never copy credentials into CI.
