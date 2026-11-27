import Foundation
import Network
import NetworkExtension
import Security

final class FlowRelay {
    private let flow: NEAppProxyTCPFlow
    private let connection: NWConnection
    private let queue: DispatchQueue
    private let destination: NativeDestination
    private let finished: () -> Void
    private var stopped = false
    private var header = Data()
    private var deadline: DispatchWorkItem?

    init(flow: NEAppProxyTCPFlow, destination: NativeDestination, policy: NativePolicy,
         queue: DispatchQueue, finished: @escaping () -> Void) {
        self.flow = flow
        self.destination = destination
        self.queue = queue
        self.finished = finished
        let tls = NWProtocolTLS.Options()
        sec_protocol_options_set_tls_server_name(tls.securityProtocolOptions, "localhost")
        sec_protocol_options_add_tls_application_protocol(tls.securityProtocolOptions, "http/1.1")
        let certificate = SecCertificateCreateWithData(nil, policy.gateway_ca_der as CFData)!
        sec_protocol_options_set_verify_block(tls.securityProtocolOptions, { _, trust, complete in
            let value = sec_trust_copy_ref(trust).takeRetainedValue()
            guard SecTrustSetAnchorCertificates(value, [certificate] as CFArray) == errSecSuccess,
                  SecTrustSetAnchorCertificatesOnly(value, true) == errSecSuccess else {
                complete(false)
                return
            }
            complete(SecTrustEvaluateWithError(value, nil))
        }, queue)
        connection = NWConnection(host: .ipv4(.loopback), port: NWEndpoint.Port(rawValue: policy.gateway_port)!,
                                  using: NWParameters(tls: tls, tcp: NWProtocolTCP.Options()))
    }

    func start() {
        let deadline = DispatchWorkItem { [weak self] in self?.stop(NetworkPolicyError.gateway_unavailable) }
        self.deadline = deadline
        queue.asyncAfter(deadline: .now() + 10, execute: deadline)
        connection.stateUpdateHandler = { [weak self] state in
            guard let self, !self.stopped else { return }
            switch state {
            case .ready:
                let authority = "\(self.destination.host):\(self.destination.port)"
                let request = Data("CONNECT \(authority) HTTP/1.1\r\nHost: \(authority)\r\n\r\n".utf8)
                self.connection.send(content: request, completion: .contentProcessed { [weak self] error in
                    guard let self else { return }
                    if let error { self.stop(error) } else { self.read_header() }
                })
            case .failed(let error): self.stop(error)
            case .cancelled: self.stop(NetworkPolicyError.gateway_unavailable)
            default: break
            }
        }
        connection.start(queue: queue)
    }

    private func read_header() {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 4096) { [weak self] data, _, complete, error in // 4 KiB CONNECT response
            guard let self, !self.stopped else { return }
            if let error { self.stop(error); return }
            if let data { self.header.append(data) }
            guard self.header.count <= 4096, !complete else { self.stop(NetworkPolicyError.gateway_refused); return }
            guard let separator = self.header.range(of: Data("\r\n\r\n".utf8)) else { self.read_header(); return }
            guard separator.upperBound == self.header.endIndex,
                  let text = String(data: self.header, encoding: .ascii),
                  text.hasPrefix("HTTP/1.1 200 ") else { self.stop(NetworkPolicyError.gateway_refused); return }
            self.deadline?.cancel()
            self.header.removeAll()
            self.flow.open(withLocalFlowEndpoint: nil) { [weak self] error in
                guard let self else { return }
                self.queue.async { [self] in
                    if let error { self.stop(error); return }
                    self.read_application()
                    self.read_gateway()
                }
            }
        }
    }

    private func read_application() {
        guard !stopped else { return }
        flow.readData { [weak self] data, error in
            guard let self else { return }
            self.queue.async { [self] in
                guard !self.stopped else { return }
                if let error { self.stop(error); return }
                guard let data, !data.isEmpty else {
                    self.connection.send(content: nil, contentContext: .finalMessage, isComplete: true, completion: .contentProcessed { [weak self] error in
                        if let error { self?.stop(error) }
                    })
                    return
                }
                self.connection.send(content: data, completion: .contentProcessed { [weak self] error in
                    guard let self else { return }
                    if let error { self.stop(error) } else { self.read_application() }
                })
            }
        }
    }

    private func read_gateway() {
        guard !stopped else { return }
        connection.receive(minimumIncompleteLength: 1, maximumLength: 64 * 1024) { [weak self] data, _, complete, error in // 64 KiB relay chunk
            guard let self, !self.stopped else { return }
            if let error { self.stop(error); return }
            guard let data, !data.isEmpty else {
                if complete { self.stop(nil) } else { self.read_gateway() }
                return
            }
            self.flow.write(data) { [weak self] error in
                guard let self else { return }
                self.queue.async { [self] in
                    if let error { self.stop(error) }
                    else if complete { self.stop(nil) }
                    else { self.read_gateway() }
                }
            }
        }
    }

    func stop(_ error: Error?) {
        guard !stopped else { return }
        stopped = true
        deadline?.cancel()
        connection.stateUpdateHandler = nil
        connection.cancel()
        flow.closeReadWithError(error)
        flow.closeWriteWithError(error)
        finished()
    }
}
