# 3. Planned transparent-proxy adapter

Date: 2026-10-20

## Status

Planned next adapter. It is not implemented, supported, or evidence of complete
egress enforcement.

## Context

A proxy could preserve a supported client's interface while applying local
replacement to outbound requests. A host integration has a smaller maintenance
surface; a proxy could support additional clients but must handle transport and
platform behavior beyond text transformation.

## Decision

The planned proxy would extract supported prompt fields, transform them in Rust,
validate the reconstructed request, and then forward it. It owns TLS termination,
upstream validation, HTTP structure, compression, framing, streaming, routing,
certificates, and operating-system lifecycle. Rust continues to own detection,
source offsets, protection, and replacement.

## Invariants & Abstention

- Preserve supported non-text fields. Never apply source offsets to serialized
  HTTP or JSON bytes.
- Withhold requests that are unsupported, protected, or cannot be safely
  interpreted or transformed. Transport failures cannot trigger original-text
  retries.
- Demonstrate compatibility with real clients. Synthetic canaries and an
  independently controlled receiver must observe transformed supported requests.
- Before advertising support, test request fidelity, TLS, compression,
  streaming, errors, retries, redirects, and each platform's certificate and
  proxy lifecycle.
- Interception does not prove complete coverage. Stronger egress claims require
  independent bypass resistance, authenticated privileged control, lifecycle
  containment, and platform-native tests.

## Consequences

The proxy could extend [ADR 1's boundary](0001-local-span-replacement.md) to
another transport. It must track provider formats, HTTP/1.1 and HTTP/2, QUIC or
opaque traffic, trust configuration, routing changes, privileges, and OS
updates. Support and bypass resistance remain separate, unproven gates.
