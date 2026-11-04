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
