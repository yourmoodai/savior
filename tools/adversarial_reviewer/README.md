# Adversarial code review

The development reviewer sends numbered code-change evidence to models through
AWS APIs, checks their findings, and reports an assessment on the pull request.
A model catalog records candidate invocation targets; routing selects a model
for each review role. Qualification tests that selection before automated review
can run. The application detector and personal-review judges remain offline.

```text
trusted-base workflow → separate reviews → fresh verification → combined findings
                                                                      ↓
                                                           pull request assessment
```

The workflow applies spending and evidence checks before it can call models or
approve a change. For a blocked PR, start with [cost and waivers](#review-cost-and-admin-waivers).
For configuration changes, follow [model onboarding](#model-onboarding) and
[activation](#review-contexts-and-activation). [Offline checks](#offline-planning-and-verification)
exercise the implementation without establishing live provider access.

Adapted from [awill1988/whip-it](https://github.com/awill1988/whip-it/tree/main/tools/adversarial_reviewer)
under the retained MIT license. Model assessment is separate from Markdown and
commit-message linting.

Run `python3 -m unittest discover -s tools/adversarial_reviewer` for transport,
evidence, coverage, and disposition tests. CI uses AWS CLI v2 for Converse and
SigV4-signed Chat Completions for compatible AWS models in `us-east-1`.
Install the signing dependency with
`python3 -m pip install -r tools/adversarial_reviewer/requirements.txt`.

## Review cost and admin waivers

The bot's PR comment explains why a review has not run: a draft PR, an ineligible
author, a disabled reviewer, insufficient budget, missing spending evidence,
failed qualification, or an infrastructure failure. A funding block is not a
code finding and does not submit a formal request for changes.

CI permits at most **$2 per PR lifetime** and **$5 per repository per UTC day**.
The PR identity is the repository and PR number: revisions, reruns, qualification,
retries, verification, and summaries share that allowance. Midnight UTC resets
the daily allowance only. Comments cannot increase either limit.

Before inference, the workflow projects the remaining cost using configured
prices, full model context bounds, output limits, and all planned retries.
Reusable results reduce the remaining work. This whole-plan worst-case projection
is informational, not expected or actual spend. The next call's reservation must
fit both remaining allowances; equality is allowed. Prices older than 90 days, missing
prices, and unresolved spending evidence block paid calls. Each invocation
atomically reserves its own maximum and settles actual usage. Extra subdivision
must still fit the same caps. Taxes and unrelated AWS charges are excluded.

A cost block shows the next-call reservation, the informational whole-plan
projection, and the two remaining balances. Usage-priced cost and outstanding
reservations are reported separately; pricing usage does not reconcile an invoice.
It notifies matching owners from the trusted base's `CODEOWNERS` once per blocked
revision. Only a repository **admin**, including an admin who authored the PR,
can waive automated review with a newly created, unedited human comment:

```text
/savior review skip <full-head-sha>
```

The waiver requires a recorded cost block from a trusted workflow for the exact
current head and base, with evidence no older than 24 hours. It rechecks admin
permissions, comment integrity, PR readiness, and outstanding requests for
changes before submitting one GitHub `APPROVE` review. That review records the
admin, authorizing comment, revision, reservation, and balances, and explicitly
states that no automated code-review pass is asserted. CODEOWNER status alone
does not grant waiver authority. The old `approve … max-usd=…` command is rejected.

A waiver makes no model calls and changes no budget. It cannot resolve missing
spending evidence, qualification failures, outstanding requests for changes, or
other required checks. Keep stale-approval dismissal enabled in branch protection:
a changed head needs fresh evidence and a new command. PR size grants no approval.
The daily reset neither retries a review nor approves a PR automatically.

The comment handler becomes available after this workflow is merged into the
default branch, as required by GitHub's
[`issue_comment` event](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#issue_comment).
Deploy through the existing trusted-base review process; this waiver cannot
authorize its own unmerged implementation. After merging, dispatch a review for
a ready PR to obtain current cost evidence. In-budget reviews may proceed to
inference; over-budget reviews stop before AWS credentials are obtained. Offline
tests do not verify the deployed OIDC, Bedrock, or GitHub approval path.

## Spending ledger and configuration

GitHub Actions assumes `arn:aws:iam::463509296941:role/savior-bedrock-review`
through OIDC. The `bedrock-review` GitHub environment admits only the `main` and
`develop` branches. The role trusts only this repository/environment and the
`sts.amazonaws.com` audience. Its inline policy permits only `bedrock:InvokeModel`
for DeepSeek V3.2 in `us-east-1`. The exact policies are in
[`iam/trust.json`](iam/trust.json) and [`iam/invoke.json`](iam/invoke.json).
No AWS access keys or SSO credentials are stored in GitHub.

The environment variable `BEDROCK_REVIEW_ROLE_ARN` selects the role. Model
selection belongs to `models.json` and `routing.json` in the trusted base.
`BEDROCK_REVIEW_MODEL` is no longer consumed. Set the repository variable
`BEDROCK_REVIEW_ENABLED=true` only after deployment and qualification. The scope
job captures this value and passes it to the review job, so an environment-level
variable cannot shadow it. Remove the obsolete environment-level enable variable.
Set the repository flag to `false` to stop new reviews; already reserved calls
remain charged until settlement. The flag does not cancel an in-flight invocation.

Every enabled run requires passing qualification for the exact routing and
reviewer configuration; successful qualification is reusable for 24 hours. Every
paid call, including qualification, must fit both policy limits. The ledger reserves each
selected model's full context and maximum output at its catalog prices before
invocation. Actual usage releases unused reservations; missing usage and
interrupted calls retain the maximum. Prices cover modeled inference only, not
taxes or unrelated AWS charges. The historical pilot balance remains evidence,
not an additional allowance.

CI serializes review and waiver workflows across PRs. The original `haiku45-pilot-20261015`
artifact namespace is retained so changing models cannot reset spending history.
The ledger also retains $0.03 reserved for the earlier account-access probes. It uploads a reservation before inference and
reconciles it with a durable ledger afterward. Resumed runs restore that ledger;
an unresolved, deleted, or expired reservation prevents further spending. Keep
these artifacts for the reviewer's lifetime. The ledger contains token metrics and
prompt hashes, not prompt text. Use CI as the pilot's sole spending authority;
local inference must not use an independent copy of the pilot or an approved allowance.
Historical grants remain in the ledger for audit and local diagnostic ceilings;
CI ignores them as spending authority. New calls record repository, PR number,
reservation UTC date, and settlement UTC date. Unsettled calls count against
every subsequent daily allowance; a call crossing midnight counts against both
days. Restoring an older snapshot cannot remove a charge or change its owner.

[`history.json`](history.json) attributes the existing pilot calls using retained
workflow job intervals and their logged `PR_NUMBER`. Exact row fingerprints bind
that evidence to unchanged charges. The cancelled-run recovery and pending call
retain their reservations. Historical account/external probes use PR `0`, meaning
repository activity outside a PR; they still consume daily capacity. New calls
require a positive PR number. The probe start date is a conservative earliest
pilot bound, not a claim that all probes happened on that date.

A restored ledger containing other unattributed calls blocks inference and cost
waivers until reconciled against retained workflow and provider evidence.
Do not delete old rows, reset the database, or treat an expired artifact as zero.
Retain durable backups before the 90-day artifact expiry; an expired required
artifact deliberately blocks restoration. Reconciliation is a reviewed evidence
change, never a comment-based budget override. A changed row, conflicting owner,
or incomplete attribution group fails closed; applying the same evidence twice
does not change costs.

## Approval before builds

`pr-validation.yml` runs the trusted source review before admitting PR builds.
A complete assessment and its formal approval must match the head, base, workflow
run, and attempt. Cost waivers do not unlock this pipeline. The gate checks the
merge commit's parents before passing its immutable SHA to validation workflows.

Failed review skips build work and fails the aggregate PR statuses. Applicable
verification, qualification, site, and commit checks must pass before
`pr readiness` succeeds. Website-only changes skip native qualification; unrelated
changes skip site builds. A changed base or dismissed approval invalidates readiness.
Source review does not claim that downstream runtime tests have passed.

After merging these workflows, verify both rejected and approved test PRs, then
add `pr readiness` as a required status check without changing review requirements
or administrator enforcement. See [ADR 12](../../docs/adr/0012-approve-before-building.md).

## Offline planning and verification

For a local packing dry run (no AWS calls or inference charges):

```sh
AWS_PROFILE=awill AWS_REGION=us-east-1 \
python3 tools/adversarial_reviewer/adversarial_review.py \
  --base origin/main --head HEAD --strategy adaptive --dry-run \
  --json-output .cache/review-pilot/adaptive-manifest.json
```

Run `npm run test:reviewer`, `node --test tests/review-*.spec.mjs`, and
`python3 scripts/release/lint_workflows.py` to check quoting, authorization,
ledger recovery, and workflow guards without paid inference.

### Evidence packing

Use `--strategy baseline` for one primary file per request, bounded at 48 KiB. Manifests list primary units,
stable evidence IDs, full input estimates, repeated supporting context, omissions,
and maximum first-pass cost. Verification and subdivision need additional funds.
The workflow records both manifests, qualification results, usage, and latency.

A primary unit is a contiguous addition/deletion block plus its assigned context.
The adaptive primary-diff ceilings are 4,000 tokens for up to eight units, 8,000
for nine through 32, and 16,000 thereafter. Packing prefers the same file, changed
references, then directories; ordinary batches admit at most eight files and
16 units. Oversized units split on source lines, retaining old/new coordinates.
Renames and mode-only changes have file-level evidence. Deletions remain reviewable.
Consecutive evidence locations use compact ranges in prompts; stable IDs remain
in the manifest, avoiding a repeated file path and identifier on every source line.
Each primary unit has one owner; supporting context does not increase coverage.

DeepSeek V3.2 does not support Bedrock `CountTokens`. Packing estimates each UTF-8
byte as one token, plus 1,024 template tokens and 128 per message; this is not an
exact tokenizer count. Rendered prompts must fit 48,000 estimated tokens and
128 KiB. Actual input above 48,000 tokens prevents accepting the response.
Spending reservations use full context capacity independently of this estimate. Supplemental context is capped at 8,000 tokens
(currently an intentionally conservative 8,000 UTF-8 bytes); omitted support is
listed. Assessments permit 1,024 output tokens, verification 1,536, and overviews 256. An explicitly truncated assessment or verification gets one retry at 4,096 tokens. Truncated responses or three findings trigger subdivision, never approval
from a partial parent. Indivisible changes, missing context, or exhausted budget
remain incomplete. Requests do not ask for provider caching. GPT-OSS can report
automatic cache reads; valid cached-input counts are charged at the full standard
input rate and labelled `standard_input_upper_bound`, without assuming a discount.
Unknown cache pricing and cache writes retain the full reservation.
Validated assessment results are reusable for seven days from trusted workflow artifacts. Cache keys include model settings, reviewer implementation, diff, supporting evidence, and test context. Incomplete results are never cached. Formatting-only changes reuse assessments; approval and current-head coverage are computed again.

### Qualification

Qualification exercises benign changes and seeded defects, then compares both
strategies on the same clean and four-defect batches. Adaptive packing is selected
only when coverage and expected labels survive and its measured cost is lower;
otherwise the qualified baseline remains selected. Full-PR plans must also fit
the 512-assessment limit; an oversized adaptive plan falls back to baseline. Neither strategy is qualified
by unit tests alone. Account model access remains a prerequisite for the live comparison.

Qualification includes paired Node evaluation cases whose expected behavior is
checked by executing fixed fixtures. The reviewer receives observations from
fixed, reviewer-owned runtime probes to distinguish valid lexical shadowing from
duplicate declarations. These probes never execute code supplied by a PR.

### Response validation

Calls have no tools or agent execution. Only trusted checkout code runs; PR diffs
are data. A private temporary request file is removed after each invocation.
Responses must finish with `end_turn` and parse as a JSON object; token exhaustion,
filtered output, invalid JSON, and provider failures cannot approve. Whole JSON
Markdown fences are accepted because models sometimes emit them. Transport errors
omit provider response bodies and review evidence. Only the publisher updates
the PR comment.

The catalog's `response_format` defaults to `json`. GPT-OSS 20B explicitly uses
`reasoning_prefix_json` for the leading `<reasoning>...</reasoning>` block observed
on its AWS Chat Completions endpoint. A single complete leading block is removed
after token usage is settled; malformed wrappers and invalid final JSON still
fail. Plain JSON and whole JSON fences remain accepted. Other models retain their
existing parsing. This format setting is bound into quotes and qualification
cache identities; changing it requires fresh qualification.

GPT-OSS requests use the `developer` instruction role and low reasoning effort.
Invalid JSON permits one fresh generation from the same evidence with a format
reminder; the malformed output is never repaired or reused. The second failure
remains incomplete. Both calls settle separately and quotes include the retry.
AWS documents structured outputs, but local probes on 2026-11-11 returned malformed
answers with both `json_schema` and `json_object`; those request modes are not
enabled. Local evidence and length checks remain authoritative. Valid JSON alone
does not establish review quality. See the
[model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-oss-20b.html).

### Transport failures

Chat transport retries only transient network failures and HTTP 429/500/502/503/504,
at most twice with one- and two-second delays inside the existing deadline.
Every attempt reserves separately; a lost response keeps its reservation. Quotes
include these attempts. Authentication errors, invalid schemas, and malformed
model output do not trigger transport retries. Diagnostics retain status,
allowlisted error categories, request IDs, and elapsed time without provider error
bodies. Full responses are captured only in private synthetic qualification runs.

### Assessment publication

One bot-owned PR comment tracks review progress, failed prerequisites, and the
latest assessment. It is limited to 2,500 characters: a 50–100-word overview
synthesized from all chunk results, **Approved** or **Request changes**, coverage,
committed cost, and up to three concise findings. Full findings, chunk evidence,
and exclusions remain in the `adversarial-report` artifact.

A qualified, complete, clean current-head assessment submits a
formal GitHub approval. Independent verification, commit-message, and release checks
must also pass before the PR is ready to merge. Validated findings submit `REQUEST_CHANGES`; infrastructure
failures fail the check without submitting a defect review. Repeated publication
updates the same comment and does not duplicate an unchanged formal verdict.
An admin cost waiver is explicitly separate from that assessment. Branch
protection and other required reviews remain unchanged.

Non-executable PNG/JPEG/WebP files under `docs/assets` are excluded from code-review
coverage and listed as unreviewed assets. Other binary changes remain unresolved
coverage. Stale results are discarded. Model agreement is not proof of correctness.

Runtime receiver and callback targets that syntax cannot establish are disclosed
in the frozen plan, assessment and verification prompts, and report artifacts.
They do not alone indicate missing source. Reviewers must abstain when a judgment
requires unavailable runtime behavior. Missing imports, ambiguous definitions,
syntax failures, dynamic loading, reflection, macros and evidence capacity limits
remain blocking. See [ADR 11](../../docs/adr/0011-disclose-runtime-review-uncertainty.md).

### Build caches

Native verification and release jobs share dependency caches by runner image and
architecture; Core ML uses a separate namespace. Compiler, Cargo inputs, and Rust
flags remain part of the cache key. Approved, impacted builds and tests execute. Job summaries
record cache hits, restore/build duration, and existing cache bytes for comparison;
cache size is sampled before the current job saves its cache.

### Provider references

Model limits and API support are documented in the [AWS DeepSeek V3.2 model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-deepseek-deepseek-v3-2.html).
Usage and stop reasons come from [Converse](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html).
Pilot rates use [Bedrock standard pricing](https://aws.amazon.com/bedrock/pricing/);
recheck catalog rates before approving model onboarding or paid qualification.

## Model onboarding

`models.json` records reviewed capabilities and pricing. `routing.json` selects
models for correctness, security, compatibility, Elephant synthesis, and fresh
verification. The shipped routing retains DeepSeek as a candidate for all roles;
it does not claim new qualification or enable any provider. Anthropic, OpenAI,
Qwen, DeepSeek, Amazon Nova, and other fast candidates can be selected after their
account availability, API support, prices, and role quality are checked.

Discovery requires the configured account's active AWS profile and an explicit
`AWS_REGION=us-east-1`. It verifies STS identity against `iam/trust.json`, lists
models and profiles, and checks access without invoking inference:

```sh
npm run reviewer:models -- discover > .cache/model-inventory.json
```

The weekly discovery workflow stores an inventory artifact only. Configure
`BEDROCK_DISCOVERY_ROLE_ARN` with a separate OIDC role allowing STS identity,
`bedrock:ListFoundationModels`, `bedrock:ListInferenceProfiles`,
`bedrock:GetFoundationModelAvailability`, and
`bedrock:ListFoundationModelAgreementOffers`. It needs no invocation, subscription,
or IAM write permission. Until that role is configured the workflow skips.

The catalog also includes GPT-OSS 20B, Qwen Coder Next, and GLM 4.7
Flash candidates with checked standard US prices. GLM 4.7 Flash is marked legacy
by its model card; recheck its published end-of-life before promotion. Anthropic
Haiku discovery currently reports a missing agreement; pricing and use-case
prerequisites must be completed before adding an invocable entry.

Select a shortlist from the inventory and add its exact capabilities to the
catalog using current AWS model cards and pricing. API compatibility cannot be
inferred from the provider name. For cross-region targets, enumerate the exact
US profile ARN and all destination model ARNs. Re-run discovery after access
changes. Then produce a plan from an inventory less than 24 hours old:

```sh
npm run reviewer:models -- plan --input .cache/model-inventory.json \
  --models deepseek_v3_2 > .cache/model-onboarding.json
```

The plan contains its digest, provider offers, owned IAM policy, account, and
qualification estimate for the current routing. `--use-case <json-file>` includes
operator-supplied Anthropic use-case details; the tool never invents these facts.
If several offers exist, retain the intended offer in a reviewed inventory before
planning. Review the terms before supplying the plan's digest:

```sh
npm run reviewer:models -- activate --input .cache/model-onboarding.json \
  --approve <plan-id>
```

Activation requires an onboarding identity with permissions for the listed
agreements, optional use-case submission, and the owned `savior-review-models`
inline policy on `savior-bedrock-review`. It never edits other role policies,
invokes a model, changes routing, or flips the reviewer flag. Pending agreements
remain pending; discovery must confirm access. Runtime endpoint activation is
implemented. Mantle's separate resource-scoped IAM policy needs review before
using that transport; the tool refuses to broaden access automatically.

After editing routing, obtain a separate local qualification quote:

```sh
npm run reviewer:models -- quote > .cache/model-qualification-quote.json
npm run reviewer:models -- qualify --input .cache/model-qualification-quote.json \
  --approve <quote-id> --directory .cache/model-qualification
npm run reviewer:models -- status --directory .cache/model-qualification
```

Use `quote --candidate gpt_oss_20b` (or another catalog name) to qualify that
candidate in all roles without changing production routing. The quote freezes
this selection; `qualify` restores it for that run only. A combined routing still
requires its own qualification.

The quoted cap covers the whole synthetic qualification pipeline, including
bounded retries. Keep the same directory when resuming; never copy or reset its
ledger to reuse an allowance. Local grants are separate from CI grants and their
history. Local qualification does not prove CI OIDC access. Status reports the
selected routing and current qualification evidence, not assumed availability.

### Bounded diagnosis

An operator-authorized investigation can reuse an existing local qualification
ledger across configuration changes. For the authorized $1 GPT-OSS investigation:

```sh
AWS_PROFILE=awill AWS_REGION=us-east-1 \
python3 tools/adversarial_reviewer/investigate.py \
  --directory .cache/reviewer-routing/gpt-oss-qualification \
  --cap-nanodollars 1000000000 --phase qualify
```

This command requires prior spending authorization; its example amount does not
grant it. The directory must already contain its ledger. A persistent cumulative
ceiling includes previous grants and unresolved calls, survives merging, and can
only decrease. Each request must fit before it starts. Keep this ledger for all
attempts; changing code or the grant cannot reset the cap. `--phase reproduce`
runs one synthetic fixture, selected by zero-based `--case-index` and `--role`
(default: first fixture, compatibility). Neither phase modifies production routing,
IAM, or the repository enable flag.

Each private attempt directory records configuration, diagnostics, qualification
results, and budget state. Preserve failed attempts. An earlier opaque error
cannot be reconstructed without its original response: a later observed cached
usage failure establishes an adapter defect, not the exact cause of a lost call.
Historical uncertain reservations remain in the cumulative total.

`npm run reviewer:models:debug -- status` runs the command under `pdb`;
`npm run reviewer:models:profile -- status` enables allocation tracking for use
under the debugger. Python has no separate release build. Editor tasks expose
status and reviewer tests.

## Review contexts and activation

Each chunk receives three independent source-based reviews. Candidates get one
fresh verification round. The Elephant combines the resulting evidence and
cannot remove verified findings. Exact duplicates collapse; disagreements and
missing coverage cannot be converted into approval. All steps are covered by
the immutable quote. There are no implicit fallback models.

Merge the trusted workflow and catalog changes before enabling review. Remove
the old environment enable override, configure exact model access, obtain paid
qualification evidence, and set the repository enable flag. Dispatch a review to
produce a current PR quote, then approve that quote to exercise the deployed
path. Keep failed qualification evidence; offline tests never substitute for it.

[AWS access prerequisites](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html)
explain provider agreements; [API compatibility](https://docs.aws.amazon.com/bedrock/latest/userguide/models-api-compatibility.html)
and [Chat Completions endpoints](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-chat-completions.html)
own endpoint and signing details. [ADR 6](../../docs/adr/0006-qualify-review-model-routing.md)
records the context-isolation and spending decisions.

## Bounded design research

`research.py` compares current review with deterministic reporting over the same
three role assessments, and a fresh general reviewer covering all three roles.
It reuses the production transport, response validation and finding verification.
The command runs only trusted synthetic fixtures and never invokes qualification,
reads assessment caches, publishes qualification evidence, or changes CI policy.

The authorized experiment has one fixed identity, `review-designs-v1`, and a $4
cumulative ceiling. Its journal lives under the repository's shared Git directory
at `reviewer-research/review-designs-v1`; an adjacent anchor detects missing or
rolled-back history. Every worktree resolves the same directory. Preserve both
the directory and its anchor. Missing or conflicting history blocks spending;
do not delete or reinitialize them to continue. The journal retains unresolved
reservations and freezes each call's prices before transport. It is a local
durability boundary, not protection against an operator replacing both records
with an old backup.

After explicit research spending authorization, freeze the 22 cases, their oracle
outcomes, prompts, code fingerprints, runtime probes, routing, prices and execution
order. The profile must match the frozen configuration and the repository's IAM
trust account. The approved session corrected its profile to `awill`:

```sh
AWS_PROFILE=awill AWS_REGION=us-east-1 npm run reviewer:research -- prepare
AWS_PROFILE=awill AWS_REGION=us-east-1 npm run reviewer:research -- run
npm run reviewer:research -- report
```

`prepare --replay-diff path/to/change.diff` optionally adds an offline reservation
comparison for an existing PR. This diff never enters an inference request. Run
`prepare` exactly once; `run` resumes the same journal and skips already-started
arms, leaving interrupted arms incomplete. A configuration change blocks further
spending under the frozen protocol and keeps the original history. It cannot
create a fresh allowance. Each invocation reserves full model context plus its
bounded output; truncation retries, verification, Elephant reconciliation and
overview synthesis all consume the same allowance. Equality at the ceiling is
allowed. The first unaffordable reservation stops the pass and preserves results.

`report.json` contains complete findings, coverage gaps, unresolved judgments,
usage-priced charges, outstanding reservations, call counts, token usage, latency,
abstentions, invalid responses and truncations. `report.md` provides matched-case
comparisons and ranked follow-ups. Research assumes qualification solely to test
reporting behavior; no production qualification or approval follows. Incomplete
review remains incomplete, including when a model abstains.

Run `npm run reviewer:research:test` for mocked budget, interruption, restart,
transport, oracle and reporting checks. `npm run reviewer:research:debug -- report`
uses `pdb`; `npm run reviewer:research:profile -- report` enables allocation
tracking under the debugger. Python has no separate release build. Editor tasks
expose these commands. The [research report](../../docs/research/reviewer-designs.md)
owns outcomes, limitations and proposed production follow-ups.
