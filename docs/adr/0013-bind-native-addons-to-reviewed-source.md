# 13. Bind the native Node addon to reviewed source

Date: 2026-11-27

## Status

Proposed. This decision covers the handoff from the Rust build to source
review, native qualification and the installed Node loader. Adoption requires
a reviewed implementation and a successful rehearsal on every release target.

## Context

The Node bridge calls Rust through a native addon. In a checkout, the build
generates `native/index.cjs`, which selects a platform binary. The installed
bridge can also load `savior.node` from `SAVIOR_RUNTIME_DIR`. A directory
provided by a caller identifies a location. It supplies no evidence of who
built the file there or which revision it implements. PR #43 exposed this on
both sides of its diff. [Bridge](../../bridge/cli.mjs) ·
[Build inputs](../../crates/node/Cargo.toml)

Source review runs before a PR can execute native code. Rebuilding the addon
during review would execute candidate build scripts with the wrong privileges
and charge native compute to rejected changes. Committing the generated
JavaScript would make its dispatch visible, but each platform binary would
still need its own provenance. The existing release bootstrap already checks
a signed, target-specific inventory. [Review ordering](0012-approve-before-building.md) ·
[Release bootstrap](../../bridge/bootstrap.mjs)

## Decision

**At source review**, use one tracked addon loader and a versioned description
of its interface. Record the Rust crate, exported Node names and types, Node-API
level, supported targets, build entry point and both lockfiles. The trusted
analyzer resolves these inputs, the build script, generator settings, and
first-party Rust dependencies from the old and new Git trees. It puts the
changed interface and implementation in the review packet and records native
verification as outstanding. A PR that edits the loader or interface must
receive review under the policy already on the base branch.
[Source evidence](0010-freeze-review-evidence-before-inference.md)

**At native qualification**, each existing target job builds the approved merge
tree in isolation. It loads its own candidate addon with synthetic inputs,
checks the declared exports and API level, and exercises the bridge call.
The trusted coordinator records the repository, base and head commits, tested
merge tree, target, toolchain and recipe identity, workflow identity, run and
attempt, and the measured SHA-256 and byte length of each output. It obtains
the digests from the immutable job artifact and verifies the producer identity
through the CI control plane. A JSON file supplied by the PR cannot authorize
its own output. A changed revision, recipe, target or workflow identity
invalidates the handoff. Dependency caches may still accelerate compilation.

**At installation**, the packager binds the qualified artifact to the signed
runtime manifest. When platform signing changes bytes, its protected job
records and verifies both the input and final digests. The manifest names the
final bytes. The loader derives the addon from the verified manifest and
target, including the macOS bundle layout. It checks length and digest in a
private location protected from replacement through loading. An environment
directory can locate a candidate only if the same checks succeed. Source
development can use an explicitly requested local build with its own receipt.
Its authority ends at that checkout. [Windows signing handoff](../../scripts/release/windows_handoff.py)

## Invariants & Abstention

- Only a static, recognized loader contract qualifies for source review.
  An arbitrary `require`, an unknown generated shim, a missing Rust source
  or an incomplete dependency graph keeps the affected revision blocked.
- Native jobs receive no publication credentials. GitHub readiness requires
  every applicable target's exact-revision evidence. Release signing and
  publication retain their protected environment approvals.
- The installed loader refuses a wrong target, stale or missing receipt,
  changed bytes, unexpected file, link or replaceable path. Failure stops
  processing before the bridge forwards input. Developer receipts cannot
  satisfy installed release verification.
- Rehearse the handoff with a harmless fixture on macOS ARM64, Windows x64
  and both Linux architectures. Check the happy path plus substituted bytes,
  wrong architecture, a replayed receipt, a directory override and a swapped
  file between verification and loading. Record the observed result per
  platform before enabling the new admission rule.

## Consequences

Ordinary review can inspect one tracked interface before paying for a native
build. Maintainers must keep the interface and Rust exports in sync and retain
per-target evidence long enough to verify an installed release. The rehearsal
must establish how each operating system protects the verified file through
loading, and how a signing retry binds its output to the original input.

PR #43's historical loader remains an opaque dependency. Adopting this proposal
first requires maintainers to establish an authorized route to review the
legacy side of the loader migration. Then #43 can rebase onto that migration
and receive a fresh review. A later receipt cannot establish what an arbitrary
historical runtime directory held.
