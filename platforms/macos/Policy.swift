import Foundation
import Network
import Security

enum NetworkPolicyError: String, Error {
    case invalid_native_policy
    case ambiguous_destination
    case gateway_unavailable
    case gateway_refused
}

struct NativeDestination: Codable {
    let host: String
    let port: UInt16
    let addresses: [String]
}

struct NativePolicy: Codable {
    let version: Int
    let gateway_port: UInt16
    let gateway_ca_der: Data
    let gateway_requirement: String
    let max_flows: Int
    let destinations: [NativeDestination]

    func validate() throws {
        guard version == 1, gateway_port != 0, (1...128).contains(max_flows),
              !destinations.isEmpty, destinations.count <= 128,
              SecCertificateCreateWithData(nil, gateway_ca_der as CFData) != nil,
              gateway_requirement.contains("anchor apple generic"),
              gateway_requirement.contains("identifier "),
              gateway_requirement.contains("certificate leaf[subject.OU]") else {
            throw NetworkPolicyError.invalid_native_policy
        }
        var requirement: SecRequirement?
        guard SecRequirementCreateWithString(gateway_requirement as CFString, [], &requirement) == errSecSuccess else {
            throw NetworkPolicyError.invalid_native_policy
        }
        var hosts = Set<String>()
        for destination in destinations {
            guard destination.port != 0, destination.host.utf8.count <= 253,
                  destination.host.split(separator: ".", omittingEmptySubsequences: false).allSatisfy({ label in
                      !label.isEmpty && label.count <= 63 && label.first != "-" && label.last != "-" &&
                      label.utf8.allSatisfy({ (97...122).contains($0) || (48...57).contains($0) || $0 == 45 })
                  }), hosts.insert(destination.host).inserted,
                  !destination.addresses.isEmpty, destination.addresses.count <= 64,
                  destination.addresses.allSatisfy({ address in
                      if let ip = IPv4Address(address) { return ip.rawValue[0] != 0 && ip.rawValue[0] != 127 }
                      if let ip = IPv6Address(address) { return ip != .loopback && ip != .any }
                      return false
                  }) else { throw NetworkPolicyError.invalid_native_policy }
        }
    }

    func destination(host: String, port: UInt16) throws -> NativeDestination? {
        let matches = destinations.filter { destination in
            destination.host == host || destination.addresses.contains { canonical($0) == canonical(host) }
        }
        guard matches.count <= 1 else { throw NetworkPolicyError.ambiguous_destination }
        guard let destination = matches.first else { return nil }
        guard destination.port == port else { throw NetworkPolicyError.ambiguous_destination }
        return destination
    }

    private func canonical(_ address: String) -> String {
        if let ip = IPv4Address(address) { return ip.debugDescription }
        if let ip = IPv6Address(address) { return ip.debugDescription }
        return address
    }

    func is_gateway(audit_token: Data?) -> Bool {
        guard let audit_token else { return false }
        var code: SecCode?
        let attributes = [kSecGuestAttributeAudit: audit_token] as CFDictionary
        guard SecCodeCopyGuestWithAttributes(nil, attributes, [], &code) == errSecSuccess,
              let code else { return false }
        var requirement: SecRequirement?
        guard SecRequirementCreateWithString(gateway_requirement as CFString, [], &requirement) == errSecSuccess,
              let requirement else { return false }
        return SecCodeCheckValidity(code, [], requirement) == errSecSuccess
    }
}
