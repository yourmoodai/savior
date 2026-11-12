# Signing onboarding with browser assistance

This runbook takes a maintainer and a browser-capable coding agent from developer
account access to a signed release whose actual downloads have passed native
checks. A platform signature identifies the software publisher; notarization is
Apple's assessment of a submitted build. Savior also signs its artifact inventory
with a separate Ed25519 key. All three boundaries need evidence.

[Release preparation](releases.md) owns the commands, configuration and secret
inventory. [ADR 5](adr/0005-sign-binaries-before-release.md) owns the signing
decision. This guide owns account onboarding, human handoffs and execution order;
it does not authorize purchases, publication or promotion by itself.

Vendor references were checked on 2026-11-11. Portal layouts and prices can
change. Recheck the linked developer documentation when executing the runbook.

## Agent starting prompt

Copy this into a coding session with the repository and browser tools available:

```text
Follow docs/signing-onboarding.md and docs/releases.md for this checkout.
Discover the configured GitHub account and repository from configuration.
Use my existing browser session and prompt me to log in when needed.
Automate ordinary navigation and approved configuration work; let me enter
passwords, MFA, recovery information and identity-verification documents.
Never ask me to paste secrets into chat or return secret-bearing page content.

First inventory existing accounts, signing resources and repository settings.
Prepare a concrete cost and compatibility summary before creating paid resources.
No purchases, new subscriptions, paid signing attempts, production tags,
publication or marketplace promotion are authorized by this starting prompt.
Ask at those boundaries with the exact resource, cost or release identified.
Keep Bedrock review disabled and avoid repeated CI reruns during outages.

Use private files outside the checkout for credentials and resumable state.
Resume verified completed steps instead of recreating certificates or keys.
Keep an evidence record with public identities, revisions, hashes and run URLs.
Separate synthetic tests, static signature checks, native OS acceptance and
public-download evidence. Stop on the documented compatibility blockers rather
than weakening trust checks. Finish with a status for every phase and the next
specific action I need to take.
```

## Browser and handoff contract

Discover the available browser tools before acting. In this environment, use
Firefox tools: `list_pages`, `new_page`, `navigate_page`, `take_snapshot`, UID
click/fill/upload tools, and download tracking. Reuse the configured profile;
do not start a different account or browser profile to bypass access controls.

Open one service at a time. On ordinary configuration pages, take a fresh scoped
snapshot, select controls by accessible name and UID, perform one action, and
verify its result. Refresh the snapshot after navigation. Never reuse UIDs from
another page or depend on screen coordinates or a recorded sequence of clicks.
If labels differ, follow the current official docs and visible controls. If the
tool is unavailable, hand the user the exact URL and resume after their action.

At a login, MFA, CAPTCHA, identity-document or payment page, stop inspection of
form contents and let the user complete that interaction. Do not inspect browser
cookies, authentication storage or authorization headers. Do not capture page
text, screenshots, recordings or network bodies on credential-generation pages.
After a user reports completion, confirm only non-secret account/team metadata.

Use this login prompt, substituting the service and purpose:

> Please sign in to **[service]** in the open browser tab and complete any MFA.
> Select the account for Savior's publisher. Tell me when you are signed in;
> do not send your password or verification code here.

A login reply authorizes continuation of the current phase, not a purchase or
release. For costs, show the selected plan, recurring and usage charges,
currency, renewal terms and proposed spend limit before requesting approval.
Budget alerts alone are not a hard spending cap. For legal agreements or
identity verification, ask the account holder to complete the vendor's process.

## Progress and cost record

Keep a private directory selected through the user's configured secret-storage
mechanism, outside the checkout, input stages and output artifacts. Use owner-only
access: directories `0700`, files `0600`. Never put credentials in source,
terminal output, command arguments, PRs or the public evidence report. If a tool
must display a one-time secret, let the user save it privately and return only
its file path or secret-manager reference.

Store a resumable record there with phase states (`pending`, `waiting_for_user`,
`blocked`, `complete`), selected account/resource identifiers, approved costs,
credential references and evidence locations. Store secret values separately.
Public evidence can include certificate fingerprints and run URLs; keep billing
details, personal identity data and credential locations in the private record.

| Phase | Completion evidence | Human handoff |
| --- | --- | --- |
| Inventory | Repository, account, revision, existing resources | Account selection if ambiguous |
| Compatibility | Supported provider and identity-validation approach | Provider/cost decision |
| Apple | Matching Developer ID key/certificate and notarization key | Login, legal/account access |
| Windows | Public-trust signing identity and usable provider access | Login, identity verification, billing |
| GitHub | Public variables, private secrets, reviewed protection changes | Login and policy approval |
| Candidate | Signatures, accepted notarization, sealed inventory | Authorized signing attempt |
| Native acceptance | Reports for the exact archive on all three platforms | Production release authorization |
| Distribution | Public-download checks and separate promotion record | Publication and promotion approvals |

Prefer existing enrolled accounts. Apple's published Developer Program fee is
USD 99 per membership year; Microsoft's published Basic Artifact Signing tier
is USD 9.99 per account per month, with usage terms. These are reference prices,
not an approved budget or a regional quote. Inspect current billing before
enrollment. [Apple enrollment](https://developer.apple.com/help/account/membership/program-enrollment),
[Microsoft tiers](https://learn.microsoft.com/en-us/azure/artifact-signing/how-to-change-sku).

## 1. Inventory and compatibility

Read `AGENTS.md`, `README.md`, the release guide and ADR 5. Preserve existing local
changes. Resolve GitHub routing with these read-only commands:

```sh
git status --short --branch
git remote -v
gh auth status
gh repo view --json nameWithOwner,visibility,viewerPermission
gh variable list
gh secret list --env release
gh release list
```

Check current runs, environment reviewers, branch/tag rules and repository
variables through GitHub's read-only API or Settings. Secret listings prove
names and scope, not that values are correct. Check applicable organization
secrets too; do not overwrite existing identities merely because a list is empty.
Do not treat this guide's earlier observations as current account state.

Read runtime versions from `scripts/release/package.py` and tool versions from
`scripts/release/tools.lock.json`. Use the pinned tools and their `--help`;
the runtime Node version and signing-container Node version can differ.
Do not replace failing downloads with unpinned tools or package sources.

### Windows certificate rotation

Resolve this before buying or configuring a new provider. Microsoft Artifact
Signing renews certificates daily, gives them a 72-hour validity, and recommends
a profile-specific EKU for durable identity. Savior currently requires one exact
SHA-256 leaf-certificate fingerprint in both `local.py` and
`native_acceptance.py`. An old fingerprint will reject a renewed certificate.
[Microsoft certificate lifecycle](https://learn.microsoft.com/en-us/azure/artifact-signing/concept-certificate-management).

Choose an existing supported provider whose identity lifecycle fits that contract,
or prepare a separately reviewed change for durable provider identity and
release-bound certificate evidence. The current adapters support `PKCS12`,
`PKCS11`, `TRUSTEDSIGNING` and `DIGICERTONE`; self-signed PKCS#12 fixtures do not
establish production trust. Do not simply remove fingerprint checks or learn the
expected identity from an untrusted candidate. If no compatible approach is
approved, mark Windows onboarding blocked and continue independent Apple work.

## 2. Apple Developer ID

Open [Apple Developer Account](https://developer.apple.com/account/) and use the
login prompt. Confirm the legal publisher, team ID, membership status and the
account's certificate permissions. Creating this exportable Developer ID
certificate requires the Account Holder; cloud-managed certificates are a
different path from Savior's current PKCS#12 input. If access is missing, request
the Account Holder's action instead of switching accounts.
[Apple Developer ID instructions](https://developer.apple.com/help/account/certificates/create-developer-id-certificates/).

Inventory existing Developer ID Application certificates before creating one.
If the matching private key is available through the approved secret store,
reuse it after checking identity and expiry. A downloaded `.cer` alone has no
private key and is insufficient.

For a new identity, generate a private key and CSR locally with the pinned
`rcodesign` certificate-management workflow or OpenSSL's CSR workflow. Collect
the subject information from the account holder; never invent publisher details.
Use file outputs and private permissions. Check the CSR before uploading it;
only the CSR leaves the private directory, not the private key.
[Apple CSR instructions](https://developer.apple.com/help/account/certificates/create-a-certificate-signing-request/),
[rcodesign certificate management](https://gregoryszorc.com/docs/apple-codesign/stable/apple_codesign_certificate_management.html).

In **Certificates, Identifiers & Profiles → Certificates → Add**, select
**Developer ID Application**, upload the CSR, generate and download the public
certificate. Do not select Installer or App Store distribution certificates.
Record its serial, expiry, team and SHA-256 of the leaf certificate's DER bytes.
Package it with its matching private key as a password-protected PKCS#12 file;
obtain the password through private storage. Check that key and certificate
match, and retain the necessary issuer chain using the pinned tool's instructions.

Completion: private `apple_p12` and `apple_password` files, independently checked
public identity, and a renewal owner. Never revoke another release's certificate
as an onboarding cleanup step.

## 3. Apple notarization access

Open [App Store Connect](https://appstoreconnect.apple.com/) and prompt for login
again if necessary. Confirm the same organization. The Account Holder may need
to enable API access. Navigate **Users and Access → Integrations → App Store
Connect API → Team Keys**, following the current portal labels. Reuse an approved
key when its private half is available; otherwise have an Account Holder or Admin
generate a dedicated team key with the least role supported for notarization by
the pinned tool. Its documentation
identifies the Developer role as sufficient. Use a team key with issuer ID for
the current three-input encoding; do not substitute a differently shaped key.
[Apple API access](https://developer.apple.com/help/app-store-connect/get-started/app-store-connect-api/),
[rcodesign API requirements](https://gregoryszorc.com/docs/apple-codesign/stable/apple_codesign_getting_started.html#obtaining-an-app-store-connect-api-key).

Have the user save the private `.p8` download directly to approved private
storage. Record the non-secret issuer ID and key ID. Do not inspect the key in
chat or browser snapshots. Verify the pinned CLI's help, then encode to a file:

```text
rcodesign encode-app-store-connect-api-key --output-path PRIVATE/apple-api.json \
  ISSUER_ID KEY_ID PRIVATE/AuthKey_KEY_ID.p8
```

The placeholders above are public IDs or private paths, not values to copy
literally. Always supply `--output-path`: the default prints private JSON to
stdout. Completion is a private `apple_api` file; an authenticated notarization
attempt in the candidate phase will establish whether access actually works.

## 4. Windows publisher access

Reuse the provider approved in phase 1. For an existing hardware/cloud provider,
follow [Jsign's provider instructions](https://ebourg.github.io/jsign/) and the
provider's official enrollment docs. Confirm that the pinned Jsign supports the
exact credential representation before provisioning CI credentials.

For the Microsoft path, only after resolving certificate rotation:

1. Open [Azure Portal](https://portal.azure.com/) and prompt for login. Confirm
   the tenant and subscription explicitly; do not infer either from a directory
   name or another CLI profile.
2. Find **Artifact Signing**; older portal links may say **Trusted Signing**.
   Reuse the approved account, or present its proposed region, resource group,
   pricing tier and recurring/usage charges for approval before creation.
3. Complete **Identity validation** as the real publisher. The user supplies
   documents and legal information. If validation is pending, save the resource
   identifier and resume later; repeated submissions are not a retry strategy.
4. Create or reuse a **Public Trust** certificate profile after validation.
   Private Trust and test profiles are not substitutes for public distribution.
5. Assign the signing principal **Artifact Signing Certificate Profile Signer**
   at the required profile scope. Have an authorized administrator create or
   approve that principal rather than granting broad subscription ownership.

These steps follow [Microsoft setup](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart)
and [resource/role documentation](https://learn.microsoft.com/en-us/azure/artifact-signing/concept-resources-roles).

For `TRUSTEDSIGNING`, record the endpoint hostname and `account/profile` alias.
Savior passes Jsign a private password file and does not forward an arbitrary
Azure environment into its signing container. Use the credential format supported
by pinned Jsign; an access token must still be valid when signing runs. Do not
mistake a saved interactive token for durable unattended CI authentication.
Provider identity/credential lifecycle must be resolved before marking this phase
complete. Obtain signer and timestamp CA bundles independently, including trust
needed for the upstream Windows Node binary. For Microsoft's service, verify its
documented timestamp endpoint rather than carrying forward the example's vendor
default. [Jsign configuration](https://ebourg.github.io/jsign/),
[Microsoft timestamp requirements](https://learn.microsoft.com/en-us/azure/artifact-signing/concept-certificate-management).

Completion: verified provider identity, approved access, private credential/CA
references, usable timestamp configuration, and an explicit renewal strategy.

## 5. Manifest identity and GitHub provisioning

Reuse the persistent Ed25519 release key if it already exists. Otherwise generate
it once into private storage, derive its public PEM and record the public-key
fingerprint for independent review. Store the private key through the approved
secret mechanism. Do not regenerate a key to make a mismatch pass.
`scripts/release/check-key.mjs` checks the pair without printing it:

```text
node scripts/release/check-key.mjs PRIVATE/manifest-public.pem < PRIVATE/manifest-private.pem
```

Open the configured repository's **Settings** and prompt for GitHub login if
needed. In **Secrets and variables → Actions → Variables**, configure repository
variables `SAVIOR_RELEASE_CONFIG` and `SAVIOR_RELEASE_PUBLIC_KEY`. Start with
`scripts/release/local.example.json`; replace public placeholders, preserve the
required tool mapping, and keep credential values out of the public JSON.

In **Environments → release**, store the base64-encoded credential files using
the exact secret names in [the secret inventory](releases.md#ci-configuration).
Only `WINDOWS_KEYSTORE` may be omitted for `TRUSTEDSIGNING`. Confirm environment
scope; repository variables must remain available to native validation and
promotion jobs. Prefer CLI upload from private files to avoid exposing values in
browser automation arguments. For example, after approved configuration writes:

```sh
python3 - PRIVATE/apple.p12 <<'PY' | gh secret set SAVIOR_APPLE_P12_BASE64 --env release
import base64, pathlib, sys
sys.stdout.buffer.write(base64.b64encode(pathlib.Path(sys.argv[1]).read_bytes()))
PY
```

Run without shell tracing or output capture of the pipeline's input. Apply the
same pattern to each required file; do not pass private values with `--body` or
put base64 in chat. Check names with `gh secret list --env release`. GitHub cannot
return stored secret values for comparison; candidate execution validates them.
[GitHub secret handling](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

### Protection review

From code matching `main`, preview:

```sh
python3 scripts/release/configure_github.py --enforce-after-merge
```

Show the proposed changes before applying them. It enables strict CI and
CODEOWNER review, immutable releases, pinned actions, and separate `release`
and `marketplace` approvals. Required CODEOWNER review may conflict with the
single-maintainer lightweight-LGTM policy: bot approval is not automatically
CODEOWNER approval, and authors cannot approve their own PRs. Resolve that policy
choice explicitly before applying the preview. Do not silently alter the script
or remove protections to get a release through.
[GitHub review requirements](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews).

After approval, apply the reviewed configuration and verify the actual settings
through the API and **Settings → Environments**. Check `release` admits only
approved release tags and `marketplace` only `main`, with the intended human
reviewers. Do not change unrelated environments such as `bedrock-review`.
[GitHub environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

## 6. Candidate validation

Run the repository's build, verification, release-tool tests and workflow lint
before spending on signing. An unavailable package server or runner is a blocked
prerequisite, not permission to skip checks. Inspect the failure and bound retries.
Use synthetic fixtures; runtime inference remains offline.

Collect fresh `darwin-arm64`, `linux-x64` and `win32-x64` stages from one version
and revision. Check their `stage.json` provenance and shared wrapper consistency.
Follow [local preparation](releases.md#local-preparation) to configure private
credential paths and create a fresh candidate output. Obtain approval for the
identified signing attempt before `release_candidate`: it contacts signing,
timestamp and Apple notarization services.

A local candidate can validate signing and notarization without publishing.
Record the signing identities, Apple's accepted submission ID, stapled ticket,
signed inventories, archive hash and `candidate/validated.json`. That JSON is
static evidence, not proof of native acceptance. Preserve uncertain notarization
state: reconcile the existing submission before another upload.

## 7. Native acceptance and distribution

The existing tag-triggered `release.yml` combines production candidate creation,
native checks and publication. There is no standalone signed-candidate native
validation workflow in this runbook's inspected implementation. If the user wants
all native validation without any publication, stop after local candidate checks
and propose a separate validation-only workflow; do not invent a dispatch input
or push a tag as a test.

For an authorized release, prepare and merge the chosen version using the
documented release-preparation workflow. Before creating the immutable tag, show
the exact version and merged source revision and explain that the workflow will
publish assets after its gates pass. Obtain that authorization. Never overwrite
an existing tag or published version.

Open the resulting Actions run. At **Review deployments**, hand the protected
`release` approval to its designated reviewer. Do not approve it using another
identity. Follow the current [GitHub deployment review flow](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/review-deployments).

Require these reports for the exact archive, not just green unrelated jobs:

| Boundary | Required evidence |
| --- | --- |
| macOS | Expected publisher signatures; preserved Node signer; valid stapled notarization ticket; Gatekeeper acceptance; first quarantined launch offline on a disposable hosted runner |
| Windows | Native Authenticode trust, expected signer identities and verified timestamps; working executables and addon |
| Linux | Inventory and manifest verification, executable/addon loading and working replacement |
| Installed host | Packaged replacement and private worker lifecycle on every advertised platform |
| Public downloads | The published bytes pass the signature, hash, native and runtime checks again |

The native checks are implemented in `scripts/release/native_acceptance.py` and
`scripts/release/smoke.py`. Never run the firewall-changing macOS offline gate
on a developer workstation. Signing does not establish model quality, provider
approval or universal acceptance by managed-device policies.

Publication does not promote the marketplace. After the entire release run and
public-download matrix pass, present the stable version, run ID and reviewed
`marketplace` head for separate promotion authorization. Use `absent` only for
the verified first promotion. Dispatch `promote.yml` from `main` with those exact
inputs and hand the `marketplace` environment approval to the reviewer. Confirm
the final `promotion.json` and installation feed point at the approved assets.
See [the release sequence](releases.md#release-sequence) for the owning commands.

## Completion and resumption

Return a public-safe handoff listing each phase's state, remaining human actions,
approved spending and observed usage, renewal owners, source revision, certificate
and manifest public-key fingerprints, candidate/archive hashes, notarization ID,
native reports, release-run URL and promotion evidence. Omit personal identity
data and private credential locations from public records.

If onboarding stops at login, provider validation, billing approval, compatibility
work or an outage, record that exact block and resume from it later. Do not
recreate accounts, rotate keys or resubmit uncertain notarizations on a fresh
session. Do not report an end-to-end pass until the public-download checks pass;
do not report marketplace promotion until its separate approved update succeeds.
