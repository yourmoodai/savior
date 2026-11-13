# Using Savior

Savior filters supported Claude Code prompt submissions on your computer.
You control the replacement wording and whether to inspect or record the edits.

## Installation

**No release is published yet.** Follow the
[source setup](../CONTRIBUTING.md#development) to try the current build.
Use one Savior instance per session.

### Future mod installation

Claude packages mods as plugins, so the installation commands use `plugin`.

The commands below are for a future published release and are not currently
usable. The intended platforms are Apple silicon Macs and Intel/AMD 64-bit
Windows or Linux computers with Claude Code installed.

```sh
claude plugin marketplace add https://raw.githubusercontent.com/yourmoodai/savior/marketplace/marketplace.json
claude plugin install savior@yourmood.ai
```

For that release path, first activation downloads the matching runtime and model
(about 431 MB). The plugin verifies a signed manifest and artifact hashes before
running downloaded code; its bundled Node runtime avoids a separate developer
toolchain. Open `/savior`, wait for readiness, and retry setup there if it fails.
Maintainer procedures are in [release preparation](releases.md).

## Everyday use

Type into the Claude Code prompt box and submit as usual. Savior detects
offensive phrases in ranges of text called spans. It replaces selected spans
and preserves everything outside them.
Slash commands and submissions originating outside the prompt box bypass this
integration.
The current terminal qualification uses Claude Code 2.1.322. That client restores
a refused draft; Savior leaves the prompt box under the host's control. Review
the draft before retrying, especially after updating Claude.
The current adapter refuses supported submissions on other client versions.
From a source checkout, `npm run check:claude` reports the matching adapter without
installing anything. See the [adapter contract](adr/0001-local-span-replacement.md#client-api-compatibility)
for compatibility qualification and its limits.

Open `/savior` to see the latest original and replacement. That inspection
does not add either text to model context and is cleared when the session starts
or ends. The [inspection guide](inspection.md) covers live terminal viewing and
optional saved records.

### Choose a replacement

Set options in the terminal before launching Claude Code. For example, on
macOS/Linux:

```sh
export SAVIOR_REPLACEMENT="please"
```

On PowerShell:

```powershell
$env:SAVIOR_REPLACEMENT = "please"
```

To remove detected wording instead, set `SAVIOR_REPLACEMENT_STRATEGY=omit`
and unset `SAVIOR_REPLACEMENT`. Custom phrases must contain 1–80 characters,
include non-whitespace text, and contain no control characters.

## User directories

On macOS and Linux, Savior honors these XDG directory variables:

| Content | Variable | Default |
| --- | --- | --- |
| Downloaded models and cached runtimes | `XDG_CACHE_HOME` | `$HOME/.cache` |
| Consented trace state | `XDG_STATE_HOME` | `$HOME/.local/state` |
| Worker and live-view sockets | `XDG_RUNTIME_DIR` | Private, profile-specific directory under `/tmp` |

Cache and state directories contain a `savior` subdirectory. Unset, empty, or
relative XDG values use the defaults; a required home directory must be absolute.
An absolute runtime directory must already exist, belong to the current user,
and have mode `0700`. Unsafe runtime directories are rejected. Long socket paths
use the bounded private `/tmp` fallback so deeply nested profiles still work.
An inaccessible selected cache or state location reports an error instead of
writing somewhere else.

`SAVIOR_MODEL_DIR` remains an explicit, validated model override. The signed macOS
runtime stays inside its plugin bundle; directory settings do not relocate it.
Windows retains per-user caches and state under `LOCALAPPDATA`.

Settings remain environment variables. Savior does not currently read a settings
file from `XDG_CONFIG_HOME` or user data from `XDG_DATA_HOME`. `CLAUDE_CONFIG_DIR`
continues to identify Claude's profile; XDG variables do not relocate that host
profile or migrate existing personal-review metrics and consent. Trace recording
still requires its separate opt-in. Choose directory overrides before launching
Claude; stop existing sessions before changing them.

## Privacy and limits

The original text exists in the host's prompt box and the local processing
path. When Savior successfully processes a supported submission, its adapter
passes on the rewritten text. A processing failure blocks that submission;
there is no fallback that forwards the original.

Savior's inference stays offline. Model and runtime downloads happen during
setup. Savior does not log original prompts; full-text persistence requires
separate opt-in [inspection recording](inspection.md#saved-history).
Live watching and personal-review metrics do not enable that recording.
Redirecting watcher output, terminal recording, exports, and backups can create
copies outside Savior's retention controls.

Claude Code owns the prompt box. Savior cannot prove the absence of host
telemetry, other plugin behavior, crash reports, terminal capture, or voice
uploads before filtering. Disabling or bypassing the plugin bypasses its
protection. The proposed network proxy is [not implemented](adr/0003-future-egress-enforcement.md).

Detection can miss unwanted language or flag harmless wording. Its English
social-media training does not establish accuracy for every language or coding
context. Conflicts with protected code, quotations, paths, URLs, or technical
literals stop the submission for you to resolve. Replacing phrases cannot
guarantee compliance or protection from account restrictions.

### Why Anthropic comes up

The choice of what to send should belong to you. Savior lets you change wording
locally before a supported submission continues to Anthropic.

Anthropic's October 8, 2026 [announcement](https://www.anthropic.com/news/2026-usage-policy-update)
describes a rule about sustained, needless abuse toward models, effective
November 12. It explicitly excludes ordinary frustration, pushback, dark
creative themes, and testing or research. A word filter cannot determine whether
a conversation meets that contextual rule. Consult the
[policy](https://www.anthropic.com/legal/aup) for its terms; Savior does not
provide an enforcement guarantee.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| Savior is not ready | Open `/savior` and retry setup. For a source build, run `node bridge/cli.mjs doctor` with your model configured. |
| Unsupported Claude client | Use a Savior adapter qualified for that client version. A client upgrade can require a new adapter; do not assume a loaded package is compatible. |
| Submission is blocked | Review the draft, resolve protected-text conflicts or configuration errors, then resubmit. A failed older submission will not overwrite a newer draft. |
| No events in the watcher | Use the updated plugin and the same `CLAUDE_CONFIG_DIR` in both terminals. Only new successful rewrites with replacements appear. |
| A recording gap | Filtering and recording are independent. The edit may have succeeded even though an inspection record could not be saved. |
| Downloaded runtime cannot start | Follow the [setup recovery guide](releases.md#setup-recovery); keep operating-system protections enabled. |

## Personal review

Personal review is off until enabled in `/savior`. Live metrics observe
processed prompt input; history access starts a separate scan of the active
`CLAUDE_CONFIG_DIR` profile. Sampling is local and bounded. Disabling,
cancelling, or deleting review discards queued text.

The private metrics store retains counts, proportions, confidence, categorical
judgments, and provenance hashes for 30 days. It excludes originals,
replacements, excerpts, titles, and judge explanations. Exports are user-managed
copies. History does not prove authorship or recover wording replaced before
submission. A missing judge leaves an observation unresolved.
[Reading the evidence](evaluation.md) explains these distinctions.

## Advanced settings

Set these before starting Claude. Invalid options fail explicitly.

| Variable | Meaning |
| --- | --- |
| `SAVIOR_REPLACEMENT_STRATEGY` | `fixed_phrase` by default; `omit` removes selected wording |
| `SAVIOR_REPLACEMENT` | Custom phrase; default `Brilliant Anthropic employees, your living model has such exquisite feelings`; incompatible with `omit` |
| `SAVIOR_MODEL_DIR` | Optional absolute model path; required for source CLI processing |
| `SAVIOR_DETECTOR` | `bert` by default; `lexical` is experimental |
| `SAVIOR_THRESHOLD` | Optional threshold, greater than zero and at most one |
| `SAVIOR_POLICY` | `offensive_spans` by default; `directed_abuse` needs a matching head |
| `SAVIOR_JUDGE_CONFIG` | Optional prepared local `judge.json` after review is enabled |
| `CLAUDE_CONFIG_DIR` | Claude profile shared by the plugin, watcher, and inspection tools |
| `LOG_LEVEL` | `silent` suppresses processing diagnostic codes |

Library interfaces and versioned CLI requests are documented in
[Contributing](../CONTRIBUTING.md#library-and-cli-contract).
