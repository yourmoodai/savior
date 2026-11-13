# 7. Isolate development prereleases

Date: 2026-11-11

## Status

Accepted. Supersedes only ADR 5's platform-signing prerequisite for explicitly
identified development prereleases. Production releases retain ADR 5's gates.

## Context

Marketplace installation needs testing against publicly downloadable archives.
Qualification artifacts exercise package behavior but do not exercise the public
feed, download, installation registry, or first activation. Publisher enrollment
is incomplete. Waiting for enrollment leaves that distribution path untested;
removing production signing requirements would weaken the eventual release.

## Decision

Publish versions matching `X.Y.Z-dev.N` through a separate workflow and the
`yourmood.ai-dev` marketplace. These immutable GitHub prereleases are never latest
and cannot be promoted to the stable marketplace. They omit platform publisher
signing while retaining temporary Ed25519 artifact signatures and full hashes.
The package job's public-key hash binds downstream checks to that exact build.

Fresh macOS and Linux profiles install from the version-specific public feed.
Unmodified installation checks use the host's installed-plugin registry; fault
tests use disposable copies and separate evidence. Synthetic loopback traffic
tests the supported composer boundary, not actual provider-policy acceptance.

## Invariants & Abstention

- Production signing failures never fall back to development packaging.
- Only matching development tags on merged main revisions may publish this way.
- Existing releases, invalid inventories, failed package checks, or changed
  identities stop publication. Published bytes are never overwritten.
- The stable feed and homepage remain unchanged. Windows acceptance is deferred.
- OS protections remain enabled. macOS quarantine rejection is a blocking trust
  result, not permission to remove quarantine or claim signed-release acceptance.
- Public checks may fail after publication; failures remain visible and prevent
  promotion. Artifact integrity does not establish publisher trust.

## Consequences

Distribution can be exercised before publisher enrollment. macOS may reject the
development build, so a public prerelease does not promise installation success.
The temporary key is trusted through the workflow and installed archive; it is
not an independently provisioned production publisher identity.

[Development release instructions](../development-releases.md) own execution.
