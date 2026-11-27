import Foundation
import Network
import NetworkExtension

@objc(SaviorProxyProvider)
final class SaviorProxyProvider: NETransparentProxyProvider, NEAppProxyUDPFlowHandling {
    private let queue = DispatchQueue(label: "com.yourmood.savior.network.flows")
    private var policy: NativePolicy?
    private var relays: [UUID: FlowRelay] = [:]

    override func startProxy(options: [String: Any]? = nil, completionHandler: @escaping (Error?) -> Void) {
        do {
            guard let configuration = (protocolConfiguration as? NETunnelProviderProtocol)?.providerConfiguration,
                  let data = configuration["policy_data"] as? Data, data.count <= 65_536 else { // 64 KiB native policy
                throw NetworkPolicyError.invalid_native_policy
            }
            let policy = try JSONDecoder().decode(NativePolicy.self, from: data)
            try policy.validate()
            var rules: [NENetworkRule] = []
            for destination in policy.destinations {
                rules.append(NENetworkRule(destinationHostEndpoint: .hostPort(host: .name(destination.host, nil), port: .any), protocol: .any))
                for address in destination.addresses {
                    let host = Network.NWEndpoint.Host(address)
                    rules.append(NENetworkRule(destinationNetworkEndpoint: .hostPort(host: host, port: .any), prefix: address.contains(":") ? 128 : 32, protocol: .any))
                }
            }
            let settings = NETransparentProxyNetworkSettings(tunnelRemoteAddress: "127.0.0.1")
            settings.includedNetworkRules = rules
            setTunnelNetworkSettings(settings) { [weak self] error in
                guard let self else { completionHandler(NetworkPolicyError.gateway_unavailable); return }
                self.queue.sync { self.policy = error == nil ? policy : nil }
                completionHandler(error)
            }
        } catch { completionHandler(error) }
    }

    override func handleNewFlow(_ flow: NEAppProxyFlow) -> Bool {
        queue.sync {
            guard let policy else { reject(flow); return true }
            // Only the configured, signed gateway may open provider connections without recursion.
            if policy.is_gateway(audit_token: flow.metaData.sourceAppAuditToken) { return false }
            guard let tcp = flow as? NEAppProxyTCPFlow,
                  case .hostPort(let host, let port) = tcp.remoteFlowEndpoint else { reject(flow); return true }
            let name: String
            switch host {
            case .name(let value, _): name = value
            case .ipv4(let value): name = value.debugDescription
            case .ipv6(let value): name = value.debugDescription
            @unknown default: reject(flow); return true
            }
            do {
                guard let destination = try policy.destination(host: name, port: port.rawValue) else {
                    reject(flow)
                    return true
                }
                guard relays.count < policy.max_flows else { reject(flow); return true }
                let identifier = UUID()
                let relay = FlowRelay(flow: tcp, destination: destination, policy: policy, queue: queue) { [weak self] in
                    self?.relays.removeValue(forKey: identifier)
                }
                relays[identifier] = relay
                relay.start()
            } catch { reject(flow) }
            return true
        }
    }

    func handleNewUDPFlow(_ flow: NEAppProxyUDPFlow, initialRemoteFlowEndpoint remoteEndpoint: Network.NWEndpoint) -> Bool {
        // Covered QUIC cannot leave through an unprocessed UDP path.
        reject(flow)
        return true
    }

    override func stopProxy(with reason: NEProviderStopReason, completionHandler: @escaping () -> Void) {
        queue.sync {
            policy = nil
            let current = Array(relays.values)
            for relay in current { relay.stop(NetworkPolicyError.gateway_unavailable) }
            relays.removeAll()
        }
        completionHandler()
    }

    private func reject(_ flow: NEAppProxyFlow) {
        let error = NSError(domain: NEAppProxyErrorDomain, code: NEAppProxyFlowError.refused.rawValue)
        flow.closeReadWithError(error)
        flow.closeWriteWithError(error)
    }
}
