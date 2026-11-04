# See what Savior changed

Choose how much you want to see and whether to keep it.

| View | What it shows | What it retains |
| --- | --- | --- |
| `/savior` in Claude Code | Latest original and replacement | Session memory, cleared at session boundaries |
| `savior watch` in another terminal | New replacement events | No history |
| Browser inspector with recording enabled | Saved edits and model details | Private local records, with separate opt-in |

These views do not add the original wording to model context. A successful edit
means local processing completed; it is not proof of provider receipt.

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
You [-idiot-]{+ya ya ya+}, keep the test.
```

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

The native binary exposes the same command:
`./target/release/savior watch` on macOS/Linux or
`.\target\release\savior.exe watch` on Windows. The watcher needs no model load.
In a packaged plugin, run `bridge/cli.mjs watch` from the plugin directory
using its bundled executable: `runtime/linux-x64/node` on Linux,
`runtime/win32-x64/node.exe` on Windows, or
`runtime/darwin-arm64/SaviorRuntime.app/Contents/MacOS/savior-bootstrap` on macOS.
Use the same launcher for `inspect` and `traces`; the bridge prepares verified
runtime artifacts when necessary.

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
access token; do not share it. The app removes the token from the address bar,
so refresh requires reopening the original URL. `Ctrl+C` stops the server;
no background service is installed. Recording can also be enabled through the
app's confirmation.

Disabling stops new text capture. Deletion also disables capture and removes
managed records, annotations, and projections. Exports and backups remain
outside that deletion. Records have private permissions but are not encrypted.

## Storage and interpretation

On Unix, downloads use `$XDG_CACHE_HOME/savior` (default `~/.cache/savior`), and
traces use `$XDG_STATE_HOME/savior/profiles/<opaque-id>` (default
`~/.local/state/savior/profiles/<opaque-id>`). Windows uses private directories
under `%LOCALAPPDATA%/savior`. `CLAUDE_CONFIG_DIR` selects the profile; otherwise
the default `.claude` location identifies it. Absolute paths are required;
symlinked storage paths are rejected.

Retention is seven days with a 256 MiB storage ceiling. Conservative reservations
for database maintenance and snapshots can evict older records sooner. Expiry
runs when capturing or inspecting records; stopped software cannot enforce a
wall-clock deletion deadline. Existing text-free personal-review metrics remain
in their original store and keep their separate consent and retention policy.

`traces.sqlite3` is the canonical journal. `traces.parquet` is a revision-bound
projection rebuilt only after data changes, with the same event IDs and joined
annotations. Export columns are `schema`, `id`, `timestamp_ms`, `outcome`, `event`,
and `annotation`. The latter two contain versioned JSON; trace spans use UTF-8
byte offsets. Opening the inspector does not rerun detection or recover old
originals from Claude history.

`processed` means local rewriting completed. `forwarded` means the adapter's
downstream call returned; it does not prove provider receipt. Failed or cancelled
attempts have no successful output. Adapter-level failures without a worker
result retain unavailable model identity rather than an invented one. A recording
gap warns without changing the successful replacement. Capture is currently
wired to the Claude adapter's version-3 bridge requests and the explicit native
`rewriteTraced` method; existing version-1/2 callers do not gain persistence.

The native binary also exposes `savior inspect` and `savior traces`, which
delegate to the adjacent `savior-review` executable.
[Verification commands](benchmarking.md#inspection-and-live-feed-checks) and
[dated inspection evidence](evaluation.md#inspection-evidence) are maintained
separately.
