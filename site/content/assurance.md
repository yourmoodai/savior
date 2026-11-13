+++
title = "The interception boundary"
template = "page.html"
+++
Savior replaces detected offensive phrases in supported Claude Code prompts.
Its mod connects the host’s prompt box to a local Rust detector, then continues
with the resulting text. A failed transformation blocks that submission.
The host may have other paths for sending text; this submission handler does not
control them.

The release remains **unreleased**. Code inspection and controlled tests do not
establish that every host path keeps original text away from Anthropic.

## What the implementation covers

| Path | Current boundary |
| --- | --- |
| Ordinary prompt-box submission | Local replacement before the continuation runs |
| Replacement error or unavailable runtime | Submission is blocked |
| Slash commands and other input origins | Bypass the replacement handler |
| Disabled or unloaded mod | No interception guarantee |
| Host telemetry, voice uploads, other extensions | Not established by the submission handler |

Unmatched text is deliberately preserved. An unchanged result can therefore be
correct. The important distinction is whether text passed through the supported
processing path and whether a phrase selected for replacement escaped unchanged.

## How we test it

The installed-client harness sends synthetic prompts to a loopback HTTP receiver.
It checks the decoded request for expected replacement text and rejects the
original marked phrase anywhere in the captured payload. It also tests protected
text, missing models, malformed output, worker exit, and recovery. An immediate
timeout response and a worker that actually reaches the 45-second process
deadline are separate cases.

The release gate distinguishes an unmodified candidate from a complete disposable
copy with injected failures. Both identify their archive, client, model, and
harness. Source-checkout runs support development; they do not qualify a release.

These observations concern requests that reach that receiver. They do not observe
every possible host destination. The harness disables nonessential host traffic
for isolation; that is a test condition, not proof of ordinary host behavior.
Mocked handler tests separately cover malformed results, cancellation, concurrent
submissions, and failure recovery. Passing those tests proves handler behavior,
not network coverage.

The current development adapter targets Claude Code 2.1.322. A loaded adapter
refuses supported submissions on unknown versions. A future release may select
different adapters for different Claude APIs. If an API change prevents the mod
from loading, its guard cannot run; installation alone is not evidence of protection.

Qualification tracks stable releases from a rolling 90-day window on macOS ARM,
Linux x64, and Windows x64. That is the assessment policy, not a claim that every
version is supported. Each release must list passing versions and visible gaps;
newly discovered clients do not enter the support list automatically.

## What still blocks a stronger claim

Which client versions and platforms have qualified release artifacts? Can
original text leave through a route outside the submission handler? These remain
separate questions from whether a local replacement is correct.

Release evidence must identify the exact client version and platform, demonstrate
the installed artifact, and observe supported success and failure paths. Broader
claims also require independent observation or containment of every outbound
route, including telemetry, retries, redirects, startup, voice, and bypasses.

The proposed proxy is not implemented. It cannot be used as evidence. If the host
can send original text outside the mod’s control, that requires a separate
enforcement design before claiming complete protection.

Savior’s inference works offline after setup. The hosting provider still handles
requests for this website, and your chosen AI service receives submitted text.
Phrase replacement does not guarantee compliance or prevent account restrictions.

[Inspect the handler](https://github.com/yourmoodai/savior/blob/main/hooks/register.tsx),
[read the host test](https://github.com/yourmoodai/savior/blob/main/scripts/test-installed-mod.py),
or [build from source](https://github.com/yourmoodai/savior/blob/main/CONTRIBUTING.md#development).
