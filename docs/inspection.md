# See what Savior changed

Choose how much you want to see and whether to keep it.

| View | What it shows | What it retains |
| --- | --- | --- |
| `/savior` in Claude Code | Latest original and replacement | Session memory, cleared at session boundaries |
| Browser inspector with recording enabled | Saved edits and model details | Private local records, with separate opt-in |

These views do not add the original wording to model context. A successful edit
means local processing completed; it is not proof of provider receipt.

## Saved history

Recording is off by default. Enable it before submitting prompts you want to
inspect later; the inspector cannot recover originals from Claude history.

From a [source build](../CONTRIBUTING.md#development):

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
app's confirmation. In a packaged plugin, invoke `bridge/cli.mjs` with the bundled
`runtime/<target>/node` executable, using `node.exe` on Windows. The bridge
prepares verified runtime artifacts when necessary.

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
[Verification commands](benchmarking.md#inspection-checks) and
[dated inspection evidence](evaluation.md#inspection-evidence) are maintained
separately.
