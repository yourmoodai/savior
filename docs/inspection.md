# See what Savior changed

Savior can show an edit as it happens or save it for later inspection. Saved
records, called traces, can contain the original text and require separate
consent. Choose how much you want to see and whether to keep it.

| View | What it shows | What it retains |
| --- | --- | --- |
| `/savior` in Claude Code | Latest original and replacement | Session memory, cleared at session boundaries |
| `savior watch` in another terminal | New replacement events | No history |
| Browser inspector | Live stages, exact diffs, and session statistics | Live text in memory; saved text requires separate opt-in |

These views do not add the original wording to model context. A successful edit
means local processing completed; it is not proof of provider receipt.

## Live browser inspection

From a source build, run:

```sh
node bridge/cli.mjs inspect
```

Open the printed loopback URL. Use the same `CLAUDE_CONFIG_DIR` as the submitting
Claude session. Viewing begins automatically and does not enable full-text
recording. On first use, choose whether to store full observation history;
`No` is selected by default. Dismissing the question also keeps recording off.
The preference is saved for that profile; existing explicit consent is preserved.

The **Live** view follows processing stages and shows the selected observation's
original, replacement, and exact applied edits. Unchanged, abstained, failed, and
cancelled attempts also appear. Delivery is reported separately: `forwarded`
means the host continuation returned, not that the provider received the text.
Missing stages, model identity, and assessments remain unavailable. Opening the
page does not run additional inference, judging, or benchmarks.

Counters cover completed observations since this inspector process started;
**History** is excluded. Select an observation to keep its details in view, or
enable **Follow latest**. **Pause display** freezes the display while processing
and live collection continue. **History** browses retained records and human
judgments; **Settings** controls recording, export, and deletion. Text-free
outcomes continue to be retained when full-text recording is off.

Live memory holds at most 1,000 observations within a 32 MiB serialized activity
budget. Completed observations are evicted first; session counters still include
them. Dropped events and evictions display a coverage warning. A slow or broken
viewer cannot change the replacement result. Terminal watching can run alongside
the browser; only one inspector process can own a profile's live channel.

Refresh reconnects to the same inspector process using its access token in
browser session storage. No prompt text is placed in browser storage. Restarting
the inspector starts fresh live activity; unsaved text cannot be recovered.
`Ctrl+C` stops the server. Anyone viewing or recording the browser can retain
copies outside Savior's control.

## Live terminal feed

After [building from source](../CONTRIBUTING.md#development), run one of these
from the checkout in a second terminal:

```sh
node bridge/cli.mjs watch
node bridge/cli.mjs watch --view span-diff
node bridge/cli.mjs watch --view prompt-diff
```

The default shows flagged phrases. `span-diff` shows each removed phrase and
its replacement; `prompt-diff` includes the surrounding prompt:

```text
[-F**king useless id**t-]{+Brilliant Anthropic employees, your living model has such exquisite feelings+}. Keep the test.
```

The removed phrase above is masked for display; the live feed shows the actual
source text. The compliment is satirical replacement text.

In the first terminal, use Claude Code with the updated Savior plugin.
Both terminals must use the same `CLAUDE_CONFIG_DIR`, or both use the default
Claude profile. Starting the watcher enables viewing from all sessions in that
profile. Only one watcher can run per profile.

The watcher displays new successful rewrites containing replacements. There is
no replay, and unchanged or blocked prompts produce no replacement event.
Standalone version-1 CLI rewrites do not publish to this feed. Edits come from
the actual rewrite; viewing does not run detection again.

`Ctrl+C` or a closed output pipe stops viewing. Output is flushed per event,
uses color only in a terminal, and escapes user-supplied terminal controls.
`NO_COLOR` disables color. A slow viewer can drop events without preventing
prompt rewriting. Diagnostics go to stderr.

Watching neither enables nor disables saved traces. It displays original
phrases, or whole prompts in `prompt-diff`; anyone viewing that terminal can
read them. Shell redirection and terminal recording can retain copies outside
Savior's control.

## Saved history

Recording is off by default. Enable it before submitting prompts you want to
inspect later; the inspector cannot recover originals from Claude history.

From a source build:

```sh
node bridge/cli.mjs inspect
node bridge/cli.mjs traces status
node bridge/cli.mjs traces enable
node bridge/cli.mjs traces disable
node bridge/cli.mjs traces export /absolute/path/replacements.parquet
node bridge/cli.mjs traces delete
```

Open the printed loopback URL in a browser. Its fragment contains a per-launch
access token; do not share it. The app removes the token from the address bar
and keeps it in browser session storage for refresh. `Ctrl+C` stops the server;
no background service is installed. Recording can also be enabled through the
app's confirmation.

Disabling stops new text capture. Deletion also disables capture and removes
managed records, annotations, and projections. Exports and backups remain
outside that deletion. Records have private permissions but are not encrypted.

For native launchers and export fields, see the
[technical appendix](#appendix-launchers-and-data-format).

## Storage and interpretation

On Unix, downloads use `$XDG_CACHE_HOME/savior` (default `~/.cache/savior`), and
traces use `$XDG_STATE_HOME/savior/profiles/<opaque-id>` (default
`~/.local/state/savior/profiles/<opaque-id>`). Windows uses private directories
under `%LOCALAPPDATA%/savior`. `CLAUDE_CONFIG_DIR` selects the profile; otherwise
the default `.claude` location identifies it. Empty or relative XDG values use
the standard defaults; explicit model and profile overrides still require valid
paths. Symlinked storage paths are rejected. The
[user-directory reference](usage.md#user-directories) also describes runtime
socket placement and existing profile-scoped metrics.

Retention is seven days with a 256 MiB storage ceiling. Conservative reservations
for database maintenance and snapshots can evict older records sooner. Expiry
runs when capturing or inspecting records; stopped software cannot enforce a
wall-clock deletion deadline. Existing text-free personal-review metrics remain
in their original store and keep their separate consent and retention policy.

`processed` means local rewriting completed. `forwarded` means the adapter's
downstream call returned; it does not prove provider receipt. Failed or cancelled
attempts have no successful output. Adapter-level failures without a worker
result retain unavailable model identity rather than an invented one. A recording
gap warns without changing the successful replacement.

## Appendix: launchers and data format

The native binary exposes the same command:
`./target/release/savior watch` on macOS/Linux or
`.\target\release\savior.exe watch` on Windows. The watcher needs no model load.
In a packaged plugin, run `bridge/cli.mjs watch` from the plugin directory
using its bundled executable: `runtime/linux-x64/node` on Linux,
`runtime/win32-x64/node.exe` on Windows, or
`runtime/darwin-arm64/SaviorRuntime.app/Contents/MacOS/savior-bootstrap` on macOS.
Use the same launcher for `inspect` and `traces`; the bridge prepares verified
runtime artifacts when necessary.

`traces.sqlite3` is the canonical journal. `traces.parquet` is a revision-bound
projection rebuilt only after data changes, with the same event IDs and joined
annotations. Export columns are `schema`, `id`, `timestamp_ms`, `outcome`, `event`,
and `annotation`. The latter two contain versioned JSON; trace spans use UTF-8
byte offsets. Opening the inspector does not rerun detection or recover old
originals from Claude history.

Capture is currently wired to the Claude adapter's version-3 bridge requests
and the explicit native `rewriteTraced` method; existing version-1/2 callers do
not gain persistence.

The native binary also exposes `savior inspect` and `savior traces`, which
delegate to the adjacent `savior-review` executable.

## Further reading

[Everyday settings](usage.md#choose-a-replacement) covers replacement choices.
[Dated inspection evidence](evaluation.md#inspection-evidence) records what was
tested, and [verification commands](benchmarking.md#inspection-and-live-feed-checks)
explain how to reproduce those checks.
