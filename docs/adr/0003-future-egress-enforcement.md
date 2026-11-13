# 3. Proposed transparent-proxy adapter

Date: 2026-10-20

## Status

Proposed. It is not implemented, supported, or evidence of complete egress
enforcement. This record preserves research requirements; it does not authorize
deployment or expand the accepted mod boundary in ADR 1.

## Context

A proxy is an intermediary between a client and its provider. Egress is traffic
leaving the machine. Intercepting one request path does not establish control
over every outbound path.

A proxy could preserve a supported client's interface while applying local
replacement to outbound requests. A host integration has a smaller maintenance
surface; a proxy could support additional clients but must handle transport and
platform behavior beyond text transformation.

## Decision

The proposed proxy would extract supported prompt fields, transform them in Rust,
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

### Open questions

Which client request formats can be reconstructed without changing their
meaning? Which platform controls can contain alternate outbound routes? Those
questions need client and platform evidence before support can be advertised.

[ADR 1](0001-local-span-replacement.md) defines the accepted adapter boundary.
[Release evidence](0002-release-and-quality-evidence.md) distinguishes observed
delivery from claims about all traffic leaving the host.
