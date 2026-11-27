import Foundation
import Network

@main
struct PolicyTests {
    static func main() throws {
        let destination = NativeDestination(host: "api.example.com", port: 443, addresses: ["203.0.113.10", "2001:db8::10"])
        let policy = NativePolicy(version: 1, gateway_port: 8766, gateway_ca_der: Data(),
                                  gateway_requirement: "", max_flows: 32, destinations: [destination])
        let ipv4 = try policy.destination(host: "203.0.113.10", port: 443)
        let ipv6 = try policy.destination(host: "2001:0db8:0:0:0:0:0:10", port: 443)
        let unrelated = try policy.destination(host: "api.example.com.evil.test", port: 443)
        precondition(ipv4?.host == "api.example.com")
        precondition(ipv6?.host == "api.example.com")
        precondition(unrelated == nil)
        do {
            _ = try policy.destination(host: "203.0.113.10", port: 8443)
            fatalError("alternate covered port was accepted")
        } catch NetworkPolicyError.ambiguous_destination {}
        let overlapping = NativePolicy(version: 1, gateway_port: 8766, gateway_ca_der: Data(),
                                       gateway_requirement: "", max_flows: 32,
                                       destinations: [destination, NativeDestination(host: "other.example.com", port: 443, addresses: ["203.0.113.10"])])
        do {
            _ = try overlapping.destination(host: "203.0.113.10", port: 443)
            fatalError("ambiguous shared address was accepted")
        } catch NetworkPolicyError.ambiguous_destination {}
        precondition(!policy.is_gateway(audit_token: nil))
        do {
            try policy.validate()
            fatalError("unsigned gateway policy was accepted")
        } catch NetworkPolicyError.invalid_native_policy {}
        print("macos policy tests passed; native activation remains unverified")
    }
}
