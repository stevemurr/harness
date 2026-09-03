// What every test needs: the mock server's address, a logged-in client, and the reports
// the mock recorded. `run_tests.sh` sets EMBY_URL; without it the tests say so and fail.

import Foundation
import ReelKit
import XCTest

enum Mock {
    static var url: URL {
        guard let raw = ProcessInfo.processInfo.environment["EMBY_URL"], let url = URL(string: raw) else {
            XCTFail("EMBY_URL is not set: run the tests with ./run_tests.sh")
            return URL(string: "http://127.0.0.1:1")!
        }
        return url
    }

    static let identity = DeviceIdentity(device: "Test Mac", deviceId: "dev-test", version: "0.1")

    static func client() -> EmbyClient {
        EmbyClient(server: url, identity: identity)
    }

    static func loggedIn() async throws -> EmbyClient {
        let client = client()
        _ = try await client.authenticate(username: "alice", password: "secret")
        return client
    }

    /// Every playback report the mock has received since the last reset, oldest first.
    static func reports() async throws -> [[String: Any]] {
        let (data, _) = try await URLSession.shared.data(from: url.appendingPathComponent("__test/reports"))
        return try JSONSerialization.jsonObject(with: data) as? [[String: Any]] ?? []
    }

    static func reset() async throws {
        var request = URLRequest(url: url.appendingPathComponent("__test/reset"))
        request.httpMethod = "POST"
        _ = try await URLSession.shared.data(for: request)
    }
}

extension URL {
    var queryItems: [String: String] {
        var found: [String: String] = [:]
        for item in URLComponents(url: self, resolvingAgainstBaseURL: false)?.queryItems ?? [] {
            found[item.name] = item.value ?? ""
        }
        return found
    }
}

/// The controllable clock a reporter is given.
final class FakeClock: @unchecked Sendable {
    private var now = Date(timeIntervalSince1970: 1_000_000)
    func advance(by seconds: TimeInterval) { now = now.addingTimeInterval(seconds) }
    func read() -> Date { now }
}
