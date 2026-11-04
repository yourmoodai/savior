# Savior

<img src="docs/assets/savior.png" width="180" alt="Savior golden sparkle">

**You shouldn’t have to tiptoe around a chatbot.**

Savior gives you control over the wording you submit to Claude. It runs on your
computer and replaces detected offensive language in supported Claude Code
prompts before they continue to Anthropic. You choose the replacement and can
see exactly what changed.

## What Savior does

Write the frustrated instruction. Savior replaces the detected phrase and
preserves the surrounding text:

```text
You type:     You idiot, keep the test.
After Savior: You ya ya ya, keep the test.
```

The filter works locally and offline after setup. Its default replacement is
`ya ya ya`; you can choose your own phrase or remove the detected wording.
If processing fails or an edit conflicts with protected text, Savior stops that
submission so you can fix it.

## Get started

**There is no published release yet.** You can
[build and try Savior from source](CONTRIBUTING.md#development). The intended
release platforms are Apple silicon Macs and Windows/Linux on Intel or AMD
64-bit computers, with Claude Code installed.

After setup, open `/savior` in Claude Code to check that it is ready. The
[usage guide](docs/usage.md) covers settings, troubleshooting, and the future
plugin installation path.

## See what changed

Open `/savior` to inspect the latest original and replacement. For a live
two-terminal demo, start this from your built source checkout in the second
terminal, then submit prompts in Claude Code using the same profile:

```sh
node bridge/cli.mjs watch
```

The default view shows the flagged phrases. Choose `--view span-diff` for
before/after edits or `--view prompt-diff` for the full prompt with edits.
The feed keeps no history; `Ctrl+C` stops it.
[Inspection and optional saved history](docs/inspection.md) explains the choices.

## What stays under your control

You choose the replacement, whether to watch it happen, and whether to retain
local inspection records. Savior does not send prompts to a remote detector.
Full-text recording is off by default; watching does not turn it on.

Savior can miss language or select wording you wanted to keep. It cannot
guarantee protection from account restrictions or policy enforcement. Claude
Code still owns the prompt box, and this plugin does not establish that the
host, voice features, or other plugins never expose original text.
[How your text is handled](docs/usage.md#privacy-and-limits) explains the boundary.

## Learn more

- [Everyday use and settings](docs/usage.md)
- [Watch and inspect replacements](docs/inspection.md)
- [Evidence and limitations](docs/evaluation.md)
- [Build, contribute, or extend Savior](CONTRIBUTING.md)
- [Moving from an earlier name](docs/migration.md)
- [Get help](https://github.com/yourmoodai/savior/issues)

Savior is open source under [AGPL-3.0-or-later](LICENSE).
