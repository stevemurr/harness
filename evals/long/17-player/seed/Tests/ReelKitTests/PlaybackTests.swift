// The playback decision: the device profile follows the hardware, and the plan follows
// the server's answer to it. And the reporter, against a clock it does not own.

import Foundation
import ReelKit
import XCTest

final class PlaybackTests: XCTestCase {
    let everything = HardwareCapabilities(h264: true, hevc: true, av1: true)
    let noAv1 = HardwareCapabilities(h264: true, hevc: true, av1: false)

    func testTheDeviceProfileFollowsTheHardware() {
        let software = DeviceProfile(capabilities: .software)
        let full = DeviceProfile(capabilities: everything)

        XCTAssertEqual(software.containers, ["mp4", "m4v", "mov"])
        XCTAssertEqual(software.videoCodecs, ["h264"])
        XCTAssertEqual(full.videoCodecs, ["h264", "hevc", "av1"])
        XCTAssertEqual(full.audioCodecs, ["aac", "ac3", "eac3", "alac", "mp3"])
        XCTAssertEqual(full.transcodingProtocol, "hls")
        XCTAssertEqual(full.transcodingContainer, "ts")
        XCTAssertEqual(full.transcodingVideoCodec, "h264")
        XCTAssertEqual(full.transcodingAudioCodec, "aac")

        let direct = try? XCTUnwrap((full.json["DirectPlayProfiles"] as? [[String: Any]])?.first)
        XCTAssertEqual(direct?["Container"] as? String, "mp4,m4v,mov")
        XCTAssertEqual(direct?["VideoCodec"] as? String, "h264,hevc,av1")
        let transcode = try? XCTUnwrap((full.json["TranscodingProfiles"] as? [[String: Any]])?.first)
        XCTAssertEqual(transcode?["Protocol"] as? String, "hls")
    }

    func testH264InMp4DirectPlaysEvenWithoutHardware() async throws {
        let client = try await Mock.loggedIn()
        let movie = try await client.item("m-1")

        let info = try await client.playbackInfo(for: movie, capabilities: .software)

        XCTAssertTrue(info.plan.isDirect)
        XCTAssertEqual(info.plan.url.host, Mock.url.host)
        XCTAssertEqual(info.plan.url.path, "/emby/Videos/m-1/stream.mp4")
        XCTAssertEqual(info.plan.url.queryItems["Static"], "true")
        XCTAssertEqual(info.plan.url.queryItems["MediaSourceId"], "ms-1")
        XCTAssertEqual(info.plan.url.queryItems["api_key"], "tok-alice")
        XCTAssertEqual(info.plan.source.id, "ms-1")
        XCTAssertTrue(info.plan.source.supportsDirectPlay)
    }

    func testHevcDirectPlaysOnlyWhenTheHardwareDecodesIt() async throws {
        let client = try await Mock.loggedIn()
        let movie = try await client.item("m-2")

        let without = try await client.playbackInfo(
            for: movie, capabilities: HardwareCapabilities(h264: true, hevc: false, av1: false)
        )
        let with = try await client.playbackInfo(for: movie, capabilities: noAv1)

        XCTAssertFalse(without.plan.isDirect)
        XCTAssertTrue(without.plan.url.path.hasSuffix("/master.m3u8"))
        XCTAssertEqual(without.plan.url.queryItems["PlaySessionId"], without.playSessionId)
        XCTAssertTrue(with.plan.isDirect)
    }

    func testAnMkvNeverDirectPlays() async throws {
        let client = try await Mock.loggedIn()
        let movie = try await client.item("m-3")

        let info = try await client.playbackInfo(for: movie, capabilities: everything)

        XCTAssertFalse(info.plan.isDirect)
        XCTAssertEqual(info.plan.url.host, Mock.url.host)
        XCTAssertEqual(info.plan.url.path, "/emby/Videos/m-3/master.m3u8")
        XCTAssertEqual(info.plan.source.transcodingURL?.hasPrefix("/emby/"), true)
    }

    func testAv1NeedsTheHardwareToo() async throws {
        let client = try await Mock.loggedIn()
        let movie = try await client.item("m-4")

        let without = try await client.playbackInfo(for: movie, capabilities: noAv1)
        let with = try await client.playbackInfo(for: movie, capabilities: everything)

        XCTAssertFalse(without.plan.isDirect)
        XCTAssertTrue(with.plan.isDirect)
    }

    func testEveryPlaybackInfoHasItsOwnPlaySessionId() async throws {
        let client = try await Mock.loggedIn()
        let movie = try await client.item("m-1")

        let first = try await client.playbackInfo(for: movie, capabilities: .software)
        let second = try await client.playbackInfo(for: movie, capabilities: .software)

        XCTAssertFalse(first.playSessionId.isEmpty)
        XCTAssertNotEqual(first.playSessionId, second.playSessionId)
    }

    func testTheProbeAnswersFromVideoToolbox() {
        // Whatever the machine, H.264 is decoded in hardware on every Mac that runs this.
        let probed = HardwareCapabilities.probe()

        XCTAssertTrue(probed.h264)
    }

    // -- reporting -------------------------------------------------------------------

    private func reporter(clock: FakeClock) async throws -> (PlaybackReporter, EmbyClient) {
        try await Mock.reset()
        let client = try await Mock.loggedIn()
        let movie = try await client.item("m-1")
        let info = try await client.playbackInfo(for: movie, capabilities: .software)
        let reporter = PlaybackReporter(
            client: client, item: movie, source: info.plan.source,
            playSessionId: info.playSessionId, interval: 10, now: clock.read
        )
        return (reporter, client)
    }

    func testStartedIsReportedWithThePlaySessionId() async throws {
        let clock = FakeClock()
        let (reporter, _) = try await reporter(clock: clock)

        try await reporter.started(at: Ticks(seconds: 30))

        let reports = try await Mock.reports()
        XCTAssertEqual(reports.count, 1)
        XCTAssertEqual(reports[0]["kind"] as? String, "started")
        XCTAssertEqual(reports[0]["ItemId"] as? String, "m-1")
        XCTAssertEqual(reports[0]["MediaSourceId"] as? String, "ms-1")
        XCTAssertEqual(reports[0]["PositionTicks"] as? Int, 300_000_000)
        XCTAssertEqual((reports[0]["PlaySessionId"] as? String)?.isEmpty, false)
    }

    func testProgressIsReportedOnceAnIntervalNotOnEveryTick() async throws {
        let clock = FakeClock()
        let (reporter, _) = try await reporter(clock: clock)
        try await reporter.started(at: Ticks(seconds: 0))

        clock.advance(by: 3)
        let atThree = try await reporter.observe(position: Ticks(seconds: 3), paused: false)
        clock.advance(by: 7)
        let atTen = try await reporter.observe(position: Ticks(seconds: 10), paused: false)
        clock.advance(by: 5)
        let atFifteen = try await reporter.observe(position: Ticks(seconds: 15), paused: false)
        clock.advance(by: 5)
        let atTwenty = try await reporter.observe(position: Ticks(seconds: 20), paused: false)

        XCTAssertEqual([atThree, atTen, atFifteen, atTwenty], [false, true, false, true])
        let progress = try await Mock.reports().filter { $0["kind"] as? String == "progress" }
        XCTAssertEqual(progress.map { $0["PositionTicks"] as? Int }, [100_000_000, 200_000_000])
        XCTAssertEqual(progress.map { $0["IsPaused"] as? Bool }, [false, false])
    }

    func testAPauseIsReportedAtOnce() async throws {
        let clock = FakeClock()
        let (reporter, _) = try await reporter(clock: clock)
        try await reporter.started(at: Ticks(seconds: 0))

        clock.advance(by: 2)
        let paused = try await reporter.observe(position: Ticks(seconds: 2), paused: true)
        clock.advance(by: 1)
        let stillPaused = try await reporter.observe(position: Ticks(seconds: 2), paused: true)
        let resumed = try await reporter.observe(position: Ticks(seconds: 2), paused: false)

        XCTAssertEqual([paused, stillPaused, resumed], [true, false, true])
        let progress = try await Mock.reports().filter { $0["kind"] as? String == "progress" }
        XCTAssertEqual(progress.map { $0["IsPaused"] as? Bool }, [true, false])
    }

    func testStoppedIsReported() async throws {
        let clock = FakeClock()
        let (reporter, _) = try await reporter(clock: clock)
        try await reporter.started(at: Ticks(seconds: 0))

        try await reporter.stopped(at: Ticks(seconds: 42))

        let stopped = try await Mock.reports().filter { $0["kind"] as? String == "stopped" }
        XCTAssertEqual(stopped.count, 1)
        XCTAssertEqual(stopped[0]["PositionTicks"] as? Int, 420_000_000)
    }

    func testTheServerRefusesAReportWithoutAPlaySessionId() async throws {
        let client = try await Mock.loggedIn()
        let movie = try await client.item("m-1")
        let source = try XCTUnwrap(movie.mediaSources.first)

        do {
            try await client.reportProgress(
                item: movie, source: source, playSessionId: "", position: Ticks(seconds: 1), paused: false
            )
            XCTFail("a report without a play session was accepted")
        } catch let error as EmbyError {
            XCTAssertEqual(error, .server(status: 400))
        }
    }
}
