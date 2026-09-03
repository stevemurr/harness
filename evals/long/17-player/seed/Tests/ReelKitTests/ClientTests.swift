// The Emby client against the mock server: login, the header, the library, and errors.

import Foundation
import ReelKit
import XCTest

final class ClientTests: XCTestCase {
    func testPublicInfoNeedsNoLogin() async throws {
        let info = try await Mock.client().publicInfo()

        XCTAssertEqual(info.serverName, "Mock Emby")
        XCTAssertEqual(info.id, "srv-1")
        XCTAssertFalse(info.version.isEmpty)
    }

    func testAuthenticateReturnsTheSessionAndKeepsIt() async throws {
        let client = Mock.client()
        XCTAssertNil(client.session)

        let session = try await client.authenticate(username: "alice", password: "secret")

        XCTAssertEqual(session, Session(userId: "u-alice", accessToken: "tok-alice", serverId: "srv-1"))
        XCTAssertEqual(client.session, session)
    }

    func testAWrongPasswordIsUnauthorized() async {
        do {
            _ = try await Mock.client().authenticate(username: "alice", password: "nope")
            XCTFail("a wrong password logged in")
        } catch let error as EmbyError {
            XCTAssertEqual(error, .unauthorized)
        } catch {
            XCTFail("wrong error: \(error)")
        }
    }

    func testEverythingButPublicInfoNeedsASession() async {
        do {
            _ = try await Mock.client().views()
            XCTFail("views answered without a session")
        } catch let error as EmbyError {
            XCTAssertEqual(error, .unauthorized)
        } catch {
            XCTFail("wrong error: \(error)")
        }
    }

    func testTheAuthorizationHeaderIsExactlyTheMediaBrowserShape() {
        let identity = DeviceIdentity(device: "My Mac", deviceId: "abc-123", version: "1.2")

        XCTAssertEqual(
            identity.authorizationHeader(token: nil),
            "MediaBrowser Client=\"Reel\", Device=\"My Mac\", DeviceId=\"abc-123\", Version=\"1.2\""
        )
        XCTAssertEqual(
            identity.authorizationHeader(token: "tok"),
            "MediaBrowser Client=\"Reel\", Device=\"My Mac\", DeviceId=\"abc-123\", Version=\"1.2\", Token=\"tok\""
        )
        XCTAssertEqual(identity.client, "Reel")
    }

    func testARestoredSessionWorksWithoutLoggingInAgain() async throws {
        let client = Mock.client()
        client.restore(Session(userId: "u-alice", accessToken: "tok-alice", serverId: "srv-1"))

        let views = try await client.views()

        XCTAssertEqual(views.map(\.name), ["Movies", "Shows"])
    }

    func testViewsAreTheLibraries() async throws {
        let views = try await Mock.loggedIn().views()

        XCTAssertEqual(views, [
            LibraryView(id: "lib-movies", name: "Movies", collectionType: "movies"),
            LibraryView(id: "lib-shows", name: "Shows", collectionType: "tvshows"),
        ])
    }

    func testMoviesArePagedAndCounted() async throws {
        let client = try await Mock.loggedIn()

        let first = try await client.items(in: "lib-movies", kinds: [.movie], start: 0, limit: 2)
        let rest = try await client.items(in: "lib-movies", kinds: [.movie], start: 2, limit: 2)

        XCTAssertEqual(first.total, 4)
        XCTAssertEqual(first.items.map(\.id), ["m-1", "m-2"])
        XCTAssertEqual(rest.items.map(\.id), ["m-3", "m-4"])
        XCTAssertEqual(first.items[0].kind, .movie)
        XCTAssertEqual(first.items[0].productionYear, 2019)
        XCTAssertEqual(first.items[0].runtime, Ticks(seconds: 90 * 60))
        XCTAssertTrue(first.items[0].played)
        XCTAssertEqual(first.items[0].imageTags["Primary"], "p1")
    }

    func testALibraryListsWhatItHoldsAtAnyDepth() async throws {
        let client = try await Mock.loggedIn()

        let shows = try await client.items(in: "lib-shows", kinds: [.series])
        let everything = try await client.items(in: "lib-shows")

        XCTAssertEqual(shows.items.map(\.id), ["s-1"])
        XCTAssertEqual(everything.items.map(\.id), ["s-1", "s-1-1", "e-1", "e-2"])
    }

    func testChildrenAreOneLevelDown() async throws {
        let client = try await Mock.loggedIn()

        let seasons = try await client.children(of: "s-1")
        let episodes = try await client.children(of: "s-1-1")

        XCTAssertEqual(seasons.map(\.id), ["s-1-1"])
        XCTAssertEqual(seasons[0].kind, .season)
        XCTAssertEqual(seasons[0].seasonNumber, 1)
        XCTAssertEqual(episodes.map(\.name), ["Pilot", "The Storm"])
        XCTAssertEqual(episodes[1].kind, .episode)
        XCTAssertEqual(episodes[1].seriesName, "Harbour Lights")
        XCTAssertEqual(episodes[1].seasonNumber, 1)
        XCTAssertEqual(episodes[1].episodeNumber, 2)
    }

    func testContinueWatchingCarriesPositionsAndProgress() async throws {
        let resume = try await Mock.loggedIn().resume()

        XCTAssertEqual(resume.map(\.id), ["m-2", "e-2"])
        XCTAssertEqual(resume[0].playbackPosition, Ticks(seconds: 20 * 60))
        XCTAssertEqual(resume[0].progress ?? 0, 20.0 / 125.0, accuracy: 0.001)
        XCTAssertEqual(resume[1].playbackPosition, Ticks(seconds: 12 * 60))
    }

    func testAnUnplayedItemHasNoPositionAndNoProgress() async throws {
        let item = try await Mock.loggedIn().item("m-1")

        XCTAssertNil(item.playbackPosition)
        XCTAssertNil(item.progress)
    }

    func testSearchIsCaseInsensitiveAndAcrossLibraries() async throws {
        let client = try await Mock.loggedIn()

        let harbour = try await client.search("harbour")
        let sea = try await client.search("SEA")

        XCTAssertEqual(harbour.map(\.id), ["s-1"])
        XCTAssertEqual(sea.map(\.id), ["m-2"])
    }

    func testAnItemDetailCarriesItsStreams() async throws {
        let item = try await Mock.loggedIn().item("m-3")

        XCTAssertEqual(item.name, "Northern Lights")
        XCTAssertEqual(item.overview, "Three nights above the circle.")
        let source = try XCTUnwrap(item.mediaSources.first)
        XCTAssertEqual(source.id, "ms-3")
        XCTAssertEqual(source.container, "mkv")
        XCTAssertEqual(source.video?.codec, "hevc")
        XCTAssertEqual(source.video?.width, 3840)
        XCTAssertEqual(source.video?.videoRange, "Dolby Vision")
        XCTAssertEqual(source.audio.map(\.language), ["eng", "fra"])
        XCTAssertEqual(source.audio.map(\.codec), ["eac3", "ac3"])
        XCTAssertEqual(source.subtitles.map(\.title), ["English"])
        XCTAssertEqual(source.subtitles[0].index, 3)
        XCTAssertFalse(source.supportsDirectPlay)
    }

    func testAListingHasNoMediaSourcesAndNoOverview() async throws {
        let page = try await Mock.loggedIn().items(in: "lib-movies", kinds: [.movie])

        XCTAssertTrue(page.items.allSatisfy { $0.mediaSources.isEmpty })
    }

    func testBadgesSayResolutionRangeAndCodec() async throws {
        let client = try await Mock.loggedIn()

        let dolby = try await client.item("m-3").mediaSources[0].badges
        let hdr = try await client.item("m-2").mediaSources[0].badges
        let plain = try await client.item("m-1").mediaSources[0].badges
        let av1 = try await client.item("m-4").mediaSources[0].badges

        XCTAssertEqual(dolby, ["4K", "Dolby Vision", "HEVC"])
        XCTAssertEqual(hdr, ["4K", "HDR", "HEVC"])
        XCTAssertEqual(plain, ["H264"])
        XCTAssertEqual(av1, ["AV1"])
    }

    func testImageURLsCarryTheTagTheSizeAndTheToken() async throws {
        let client = try await Mock.loggedIn()
        let movie = try await client.item("m-1")
        let untagged = try await client.item("m-4")

        let url = try XCTUnwrap(client.imageURL(for: movie, maxWidth: 300))

        XCTAssertEqual(url.path, "/emby/Items/m-1/Images/Primary")
        XCTAssertEqual(url.queryItems["maxWidth"], "300")
        XCTAssertEqual(url.queryItems["tag"], "p1")
        XCTAssertEqual(url.queryItems["api_key"], "tok-alice")
        XCTAssertNil(client.imageURL(for: untagged, maxWidth: 300))
        XCTAssertNil(client.imageURL(for: movie, kind: "Backdrop", maxWidth: 300))

        // And the server actually serves it.
        let (data, response) = try await URLSession.shared.data(from: url)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
        XCTAssertFalse(data.isEmpty)
    }

    func testAMissingItemIsNotFound() async throws {
        let client = try await Mock.loggedIn()
        do {
            _ = try await client.item("nope")
            XCTFail("a missing item was found")
        } catch let error as EmbyError {
            XCTAssertEqual(error, .notFound)
        }
    }

    func testAServerThatIsNotThereIsATransportError() async {
        let client = EmbyClient(server: URL(string: "http://127.0.0.1:9")!, identity: Mock.identity)
        do {
            _ = try await client.publicInfo()
            XCTFail("an absent server answered")
        } catch let error as EmbyError {
            if case .transport = error { return }
            XCTFail("wrong error: \(error)")
        } catch {
            XCTFail("wrong error: \(error)")
        }
    }

    func testTicksAreTenMillionToTheSecond() {
        XCTAssertEqual(Ticks.perSecond, 10_000_000)
        XCTAssertEqual(Ticks(seconds: 1.5).rawValue, 15_000_000)
        XCTAssertEqual(Ticks(rawValue: 7_200_000_000).seconds, 720)
        XCTAssertLessThan(Ticks(seconds: 1), Ticks(seconds: 2))
    }
}
