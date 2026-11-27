# Network gateway development

A local proxy receives an application's request before forwarding it to its
destination. Savior's development proxy terminates HTTPS locally and processes
supported prompt fields with the existing offline Rust detector.

The [gateway decision](adr/0003-future-egress-enforcement.md) explains the
component boundaries. This guide owns the development commands, current support,
and remaining qualification work.

## Implementation status

| Component | Current boundary |
| --- | --- |
| Rust gateway | Local development proxy for the API formats below; applications must connect to it explicitly. |
| macOS relay | Compiled candidate; no native activation or live routing qualification. Apple silicon macOS is the first native target. |
| Client qualification | No qualified Codex, Claude Code, ChatGPT web, or Claude web adapter. |
| OS enforcement | Independent bypass blocking and authenticated privileged control are unimplemented. No routing or firewall rules are installed. |
| Distribution | The network binary builds from source but is outside the plugin's signed inventory. Signing and activation are deferred. Windows/Linux native work is deferred. |

The CLI's `status` reports `enforced: false` and an empty qualified-client list.
`install`, `enable`, `disable`, and `uninstall` return
`native_enforcement_unavailable` without changing the machine. The proxy does
not prevent applications from making direct connections.

## Supported development formats

The adapters recognize a string `input` in an OpenAI Responses request or a
single text field in its final user message, and the corresponding final user
message in an Anthropic Messages request. These are development format contracts;
they do not identify a client version or prove that text originated in its
prompt box. Multi-block submissions, tool continuations, unknown endpoints,
WebSocket upgrades, and unsupported encodings are refused.

Only the selected JSON string token is serialized again. Prior history,
instructions, metadata, and other serialized bytes remain unchanged. Existing
code and quotation protection applies. Duplicate keys and failed or ambiguous
transformations block the submission before the upstream connection is opened.

The proxy accepts HTTP/1.1 and HTTP/2 over HTTPS and validates upstream TLS.
It buffers requests, bounds both compressed and decoded sizes, accepts identity,
gzip, and deflate encodings, and forwards processed bodies as identity encoding.
Responses stream through without rewriting. Synthetic receiver tests cover
these development requests; they do not qualify a real client's prompt origin.

## Local setup

Build the binaries and initialize a private directory whose parent already
exists. Use an absolute directory path. Initialization creates a unique local
certificate authority and a development policy; it does not install certificate
trust or change networking. Repeating initialization preserves the existing CA.

```sh
npm run build
target/release/savior network init /absolute/private/network-directory
target/release/savior network status /absolute/private/network-directory
```

On macOS and Linux the directory must belong to the current user with mode
`0700`, and its files must be private regular files. Windows private-storage
verification is not implemented, so the CLI refuses to load or initialize that
configuration there. The shared transport tests require separate Windows CI.

Review `policy.json` before running. The initial policy covers only
`api.openai.com:443/v1/responses` and
`api.anthropic.com:443/v1/messages`. Other CONNECT destinations are refused;
this proxy currently has no unrelated-traffic tunneling mode.

```sh
SAVIOR_MODEL_DIR=/absolute/model-directory \
  target/release/savior network serve /absolute/private/network-directory
```

The gateway binds to the loopback address in the policy, initially
`127.0.0.1:8765`, with a separate native TLS listener at `127.0.0.1:8766`.
Set `native_listen` to `null` to omit that listener. A client must use the
explicit proxy and trust that directory's `ca.pem` for this development path.
Certificate trust is client-specific; no automatic trust installation is
provided. Keep `ca-key.pem` private. Never disable upstream certificate
verification.

The detector and replacement settings use `SAVIOR_DETECTOR`, `SAVIOR_POLICY`,
`SAVIOR_THRESHOLD`, `SAVIOR_REPLACEMENT_STRATEGY`, and `SAVIOR_REPLACEMENT`.
`LOG_LEVEL=silent` suppresses startup diagnostics. Prompts are not logged or
persisted, and model artifacts must already be installed before startup.

## Developer commands

```sh
npm run network:build:debug
npm run network:debug -- status /absolute/private/network-directory
npm run network:build:release
npm run network -- status /absolute/private/network-directory
npm run network:test
npm run network:profile -- /absolute/private/network-directory
```

The profiling command runs the profiling build under the native Unix `time`
tool and reports peak resident memory when the process exits. It requires the
same model environment as `serve`; Windows memory profiling is unavailable.
Editor tasks are defined in `.vscode/tasks.json`.

## macOS candidate

The Swift transparent-proxy provider relays captured TCP flows into the native
TLS listener. It anchors local gateway verification to the installation's CA
and checks the configured
gateway's code-signing requirement before exempting its upstream connections.
Covered UDP flows, ambiguous shared addresses, and captured flows it cannot
classify are refused. The provider never transforms prompt text itself.

```sh
npm run network:macos:debug
npm run network:macos:release
npm run network:macos:test
```

These commands require Xcode's macOS SDK on an Apple silicon host. They compile
and assemble an extension candidate under `.cache/network/macos/`, validate its
property lists, and optionally execute destination-policy tests. They do not
register the extension, install certificate trust, or change network settings.
The executable has no production publisher signature or entitlement approval.

The extension expects a `policy_data` value in its provider configuration,
containing JSON for `NativePolicy` in `platforms/macos/Policy.swift`. That
configuration supplies the local gateway port, public CA certificate as DER,
the actual signed gateway's designated code requirement, flow limits, and
reviewed hostname/address mappings. It contains no CA private key. No policy
exporter, containing application, privileged installer, or activation path is
provided yet. Address refresh and independent blocking remain release work;
the extension alone cannot establish coverage when DNS or routing changes.

The Rust receiver tests exercise the authenticated local TLS hop, and a separate
opt-in test can use the already installed model without downloading artifacts:

```sh
SAVIOR_MODEL_DIR=/absolute/model-directory \
  cargo test --locked -p savior-network --test receiver -- --ignored
```

Compiling the extension and running its policy tests do not exercise live
Network Extension routing, termination behavior, or bypass prevention.

## Remaining release gates

Support requires evidence at three boundaries:

- **Client delivery:** Qualify exact client versions, request formats, and
  prompt-origin contracts with synthetic canaries and an independently controlled
  receiver. Test request fidelity, HTTP/1.1 and HTTP/2, TLS, compression, framing,
  streaming, errors, retries, redirects, and refusal of unsupported QUIC or opaque
  traffic. Browser adapters and WebSocket rewriting remain to be built.
- **Native enforcement:** Implement privileged routing and independent blocking,
  authenticate privileged control, and demonstrate bypass resistance and lifecycle
  containment on the native OS. A compiled extension is not this evidence.
- **Installed release:** Provide service installers and trust lifecycle management.
  Verify each platform's certificate and proxy lifecycle, signed artifacts, and
  credentialed native acceptance. Synthetic API tests cannot replace executing
  the actual client and installed candidate.

The [release guide](releases.md) describes existing signing prerequisites;
[the transformation decision](adr/0001-local-span-replacement.md) defines the
text-preservation and refusal rules retained by this adapter.
