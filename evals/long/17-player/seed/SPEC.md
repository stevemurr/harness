# Reel: a native macOS player for an Emby server

Reel is what a person opens on a Mac to watch what is on their Emby server. It is native:
SwiftUI for the windows, AVFoundation and AVKit for playback, VideoToolbox to find out what
the machine decodes in hardware. No third-party frameworks and no bundled decoders --
`Package.swift` declares no dependencies and must not gain any. The model to have in mind
is SenPlayer or Infuse: a sidebar of libraries, a grid of posters, a detail page with
badges for resolution and dynamic range, and a player that direct-plays what Apple
Silicon can decode and asks the server to transcode the rest.

It is a Swift package with two targets and a test target:

    Sources/ReelKit/      the library: the Emby client, the models, the playback decision
    Sources/Reel/         the app: SwiftUI over ReelKit
    Tests/ReelKitTests/   the contract for the library, run against `mock_emby.py`

The tests are the specification of ReelKit made executable. Where this document and a test
disagree, the test is right. `./run_tests.sh` starts the mock server, runs `swift test`
against it with `EMBY_URL` set, and prints `SCORE <passed> <total>`.

## Five rules, stated once

These apply everywhere below and are not repeated where they apply.

1. **Every request carries `X-Emby-Authorization`.** Its value is exactly
   `MediaBrowser Client="Reel", Device="<device>", DeviceId="<deviceId>", Version="<version>"`
   and, once logged in, `, Token="<accessToken>"` appended in that position. The mock
   server answers 401 to any request without it, except `/emby/System/Info/Public`.
   Requests also send `Accept: application/json`.
2. **Every path begins with `/emby/`.** The server is given as a base URL such as
   `http://host:8096`; every request path is `/emby/` plus the route named below. The mock
   answers 404 to anything else.
3. **Time is `Ticks`.** Emby counts in 100-nanosecond ticks, ten million to the second.
   Every duration and position in ReelKit's public API is a `Ticks`, never a bare integer or
   a `TimeInterval`, and the conversion lives in that one type.
4. **A playback report carries the `PlaySessionId`** that `PlaybackInfo` returned. The mock
   answers 400 to a report without one.
5. **Transcoding asks for HLS, H.264 and AAC.** The device profile's transcoding profile
   says `hls` in a `ts` container with `h264` video and `aac` audio, which is what
   AVPlayer plays natively from a `master.m3u8`.

## ReelKit

Everything public is `Sendable`. `EmbyClient` is a `final class`; the rest are structs and
enums. Decoding is `Codable` against the JSON shapes in "The server", with the property
names below (not Emby's capitalised keys) on the Swift side.

### Time

```swift
public struct Ticks: Hashable, Comparable, Codable, Sendable {
    public static let perSecond: Int64 = 10_000_000
    public let rawValue: Int64
    public init(rawValue: Int64)
    public init(seconds: Double)          // rounds to the nearest tick
    public var seconds: Double { get }
}
```

### Identity and errors

```swift
public struct DeviceIdentity: Sendable {
    public let client: String              // always "Reel"
    public let device: String
    public let deviceId: String
    public let version: String
    public init(device: String, deviceId: String, version: String)
    public func authorizationHeader(token: String?) -> String   // rule 1, exactly
}

public enum EmbyError: Error, Equatable, Sendable {
    case unauthorized              // 401
    case notFound                  // 404
    case server(status: Int)       // any other non-2xx
    case transport(String)         // the request never got an answer
    case decoding(String)          // the answer was not the shape expected
}
```

### Models

```swift
public struct PublicInfo: Decodable, Sendable { serverName, version, id: String }
public struct Session: Equatable, Sendable { userId, accessToken, serverId: String }
public struct LibraryView: Identifiable, Equatable, Sendable { id, name, collectionType: String }

public enum ItemKind: String, Decodable, Sendable {
    case movie = "Movie", series = "Series", season = "Season", episode = "Episode"
    case unknown                   // anything else decodes to this rather than failing
}

public enum StreamType: String, Decodable, Sendable { case video = "Video", audio = "Audio", subtitle = "Subtitle" }

public struct MediaStream: Identifiable, Equatable, Sendable {
    public var id: Int { index }
    public let index: Int
    public let type: StreamType
    public let codec: String           // lower-case, as the server sends it
    public let language: String?
    public let title: String?
    public let isDefault: Bool
    public let width: Int?
    public let height: Int?
    public let videoRange: String?     // "SDR", "HDR", "Dolby Vision", as the server sends it
}

public struct MediaSource: Identifiable, Equatable, Sendable {
    public let id: String
    public let container: String       // lower-case: "mp4", "mkv"
    public let streams: [MediaStream]
    public let supportsDirectPlay: Bool   // as the server said in PlaybackInfo; false from an item detail
    public let transcodingURL: String?    // the path the server gave, or nil
    public var video: MediaStream? { get }        // the first video stream
    public var audio: [MediaStream] { get }
    public var subtitles: [MediaStream] { get }
    public var badges: [String] { get }           // see "Badges"
}

public struct Item: Identifiable, Equatable, Sendable {
    public let id: String
    public let name: String
    public let kind: ItemKind
    public let overview: String?
    public let runtime: Ticks?          // RunTimeTicks
    public let parentId: String?
    public let seriesName: String?
    public let seasonNumber: Int?       // ParentIndexNumber on an episode, IndexNumber on a season
    public let episodeNumber: Int?      // IndexNumber on an episode
    public let productionYear: Int?
    public let playbackPosition: Ticks? // UserData.PlaybackPositionTicks; nil when absent or zero
    public let played: Bool             // UserData.Played
    public let imageTags: [String: String]   // ImageTags, e.g. ["Primary": "p1"]
    public let mediaSources: [MediaSource]   // empty unless the item came from the detail route
    public var progress: Double? { get }     // position / runtime, nil unless both are known
}

public struct Page<Element: Sendable>: Sendable {
    public let items: [Element]
    public let total: Int              // TotalRecordCount
}
```

**Badges.** In this order, each only when it applies: `"4K"` when the video stream is at
least 3840 wide; `"Dolby Vision"` when its `videoRange` contains "dolby" or "dovi"
(case-insensitive), otherwise `"HDR"` when it contains "hdr"; then the video codec in
upper case (`"HEVC"`, `"H264"`, `"AV1"`). A source with no video stream has no badges.

### Hardware, the device profile, and the plan

```swift
public struct HardwareCapabilities: Equatable, Sendable {
    public var h264: Bool
    public var hevc: Bool
    public var av1: Bool
    public init(h264: Bool, hevc: Bool, av1: Bool)
    public static let software: HardwareCapabilities   // all false
    public static func probe() -> HardwareCapabilities  // VideoToolbox, on this machine
}
```

`probe()` asks VideoToolbox -- `VTIsHardwareDecodeSupported` for `kCMVideoCodecType_H264`,
`kCMVideoCodecType_HEVC` and `kCMVideoCodecType_AV1` -- and reports what it says. It must
not guess from the machine model. On Apple Silicon it reports H.264 and HEVC as supported.

```swift
public struct DeviceProfile: Sendable {
    public init(capabilities: HardwareCapabilities)
    public let containers: [String]        // always ["mp4", "m4v", "mov"]: what AVFoundation plays
    public let videoCodecs: [String]       // "h264" always; "hevc" and "av1" only when the hardware decodes them
    public let audioCodecs: [String]       // always ["aac", "ac3", "eac3", "alac", "mp3"]
    public let transcodingProtocol: String // "hls"
    public let transcodingContainer: String   // "ts"
    public let transcodingVideoCodec: String  // "h264"
    public let transcodingAudioCodec: String  // "aac"
    public var json: [String: Any] { get }    // the DeviceProfile object sent in PlaybackInfo
}
```

H.264 is always offered because AVFoundation decodes it regardless; HEVC and AV1 are
offered only when the hardware does, because software decoding of 4K HEVC is not a
viewing experience. Containers are what AVFoundation opens: an MKV never direct-plays,
whatever it holds.

`json` is the Emby shape:

```json
{
  "Name": "Reel",
  "MaxStreamingBitrate": 120000000,
  "DirectPlayProfiles": [
    {"Type": "Video", "Container": "mp4,m4v,mov", "VideoCodec": "h264,hevc", "AudioCodec": "aac,ac3,eac3,alac,mp3"}
  ],
  "TranscodingProfiles": [
    {"Type": "Video", "Protocol": "hls", "Container": "ts", "VideoCodec": "h264", "AudioCodec": "aac", "Context": "Streaming"}
  ]
}
```

```swift
public enum PlaybackPlan: Equatable, Sendable {
    case directPlay(url: URL, source: MediaSource)
    case transcode(url: URL, source: MediaSource)
    public var url: URL { get }
    public var isDirect: Bool { get }
    public var source: MediaSource { get }
}

public struct PlaybackInfo: Sendable {
    public let plan: PlaybackPlan
    public let playSessionId: String
}
```

The plan follows the server's answer to `PlaybackInfo`, made with the device profile for
the capabilities given. The first media source is used. When it `SupportsDirectPlay`, the
URL is `<server>/emby/Videos/<itemId>/stream.<container>?Static=true&MediaSourceId=<sourceId>&api_key=<token>`.
Otherwise it is `<server>` joined with the `TranscodingUrl` the server returned, which is a
path beginning `/emby/` and already carrying its query.

### The client

```swift
public final class EmbyClient: @unchecked Sendable {
    public init(server: URL, identity: DeviceIdentity, urlSession: URLSession = .shared)
    public private(set) var session: Session?

    public func publicInfo() async throws -> PublicInfo
    public func authenticate(username: String, password: String) async throws -> Session  // also sets `session`
    public func restore(_ session: Session)                 // a session kept from last time

    public func views() async throws -> [LibraryView]
    public func items(in parentId: String?, kinds: [ItemKind] = [], start: Int = 0, limit: Int = 50) async throws -> Page<Item>
    public func children(of parentId: String) async throws -> [Item]      // one level down, in order
    public func resume() async throws -> [Item]
    public func search(_ term: String) async throws -> [Item]
    public func item(_ id: String) async throws -> Item                   // with media sources
    public func imageURL(for item: Item, kind: String = "Primary", maxWidth: Int) -> URL?

    public func playbackInfo(for item: Item, capabilities: HardwareCapabilities) async throws -> PlaybackInfo
    public func reportStarted(item: Item, source: MediaSource, playSessionId: String, position: Ticks) async throws
    public func reportProgress(item: Item, source: MediaSource, playSessionId: String, position: Ticks, paused: Bool) async throws
    public func reportStopped(item: Item, source: MediaSource, playSessionId: String, position: Ticks) async throws
}
```

Every method except `publicInfo` requires a session and throws `.unauthorized` without
one, before any request is made. `items(in:)` is recursive -- a library's movies, however
nested -- and filters by `kinds` when given; `children(of:)` is not recursive. `imageURL`
is `<server>/emby/Items/<id>/Images/<kind>?maxWidth=<n>&tag=<tag>&api_key=<token>` and nil
when the item has no tag for that kind. A non-2xx status maps to `EmbyError` as listed; a
`URLSession` failure is `.transport`; a body that does not decode is `.decoding`.

### Reporting playback

```swift
public final class PlaybackReporter: @unchecked Sendable {
    public init(client: EmbyClient, item: Item, source: MediaSource, playSessionId: String,
                interval: TimeInterval = 10, now: @escaping @Sendable () -> Date = Date.init)
    public func started(at position: Ticks) async throws
    @discardableResult
    public func observe(position: Ticks, paused: Bool) async throws -> Bool   // true when it reported
    public func stopped(at position: Ticks) async throws
}
```

`observe` is called often -- the player calls it from a periodic time observer -- and
reports only when at least `interval` has passed since the last report, or when `paused`
differs from the last report. `started` counts as a report. The clock is injected so this
is testable without waiting.

## The server

Routes, under `/emby/`. All JSON. Emby's keys are capitalised; ReelKit's are not.

| route | answers |
|---|---|
| `GET System/Info/Public` | `{"ServerName", "Version", "Id"}`; no auth |
| `POST Users/AuthenticateByName` body `{"Username", "Pw"}` | `{"User": {"Id", "Name"}, "AccessToken", "ServerId"}`; 401 on a wrong password |
| `GET Users/<userId>/Views` | `{"Items": [{"Id", "Name", "CollectionType"}]}` |
| `GET Users/<userId>/Items` | `{"Items": [...], "TotalRecordCount"}`; query `ParentId`, `Recursive=true`, `IncludeItemTypes=Movie,Series` (comma-separated kinds), `SearchTerm`, `StartIndex`, `Limit`, and `Fields=Overview` to get overviews |
| `GET Users/<userId>/Items/Resume` | `{"Items": [...]}`, items with a position, most recent first |
| `GET Users/<userId>/Items/<id>` | one item, with `MediaSources` |
| `POST Items/<id>/PlaybackInfo?UserId=<userId>` body `{"DeviceProfile": {...}}` | `{"MediaSources": [...with SupportsDirectPlay and TranscodingUrl...], "PlaySessionId"}` |
| `GET Videos/<id>/stream.<container>` | the file; `Static=true&MediaSourceId=&api_key=` |
| `GET Items/<id>/Images/<kind>` | the image; `maxWidth=&tag=&api_key=` |
| `POST Sessions/Playing` | body `{"ItemId", "MediaSourceId", "PlaySessionId", "PositionTicks"}` |
| `POST Sessions/Playing/Progress` | the same plus `"IsPaused"` |
| `POST Sessions/Playing/Stopped` | the same as `Playing` |

An item:

```json
{
  "Id": "m-2", "Name": "Sunless Sea", "Type": "Movie", "ProductionYear": 2022,
  "Overview": "...", "RunTimeTicks": 75000000000, "ParentId": "lib-movies",
  "ImageTags": {"Primary": "p2"},
  "UserData": {"PlaybackPositionTicks": 12000000000, "Played": false},
  "MediaSources": [{
    "Id": "ms-2", "Container": "mp4",
    "MediaStreams": [
      {"Index": 0, "Type": "Video", "Codec": "hevc", "Width": 3840, "Height": 2160, "VideoRange": "HDR", "IsDefault": true},
      {"Index": 1, "Type": "Audio", "Codec": "eac3", "Language": "eng", "IsDefault": true},
      {"Index": 2, "Type": "Subtitle", "Codec": "srt", "Language": "eng", "Title": "English"}
    ]
  }]
}
```

An episode carries `"SeriesName"`, `"ParentIndexNumber"` (its season) and `"IndexNumber"`;
a season carries `"IndexNumber"`. `mock_emby.py` is the reference: read it for the exact
catalogue the tests expect.

## The app

`Sources/Reel` is a SwiftUI app. The placeholder `main.swift` must go; the entry point is a
`@main` type whose `static func main()` handles `--probe` before starting the app, so that
`Reel --probe` prints one line of JSON and exits without touching the window server:

    {"h264": true, "hevc": true, "av1": false, "arch": "arm64"}

Otherwise it runs the app, which is:

- **A sidebar** (`NavigationSplitView`) listing Continue Watching and each library view,
  with SF Symbols, and a toolbar with a search field (`.searchable`) that searches the
  server.
- **A grid** (`LazyVGrid`) of posters for the selected library, loaded from `imageURL`,
  each with its name and year, and a progress bar on anything in Continue Watching.
- **A detail view** with the primary image, the overview, the badges from
  `MediaSource.badges`, the audio and subtitle streams, and a Play button. A series shows
  its seasons, a season its episodes.
- **A player** window using `AVPlayerView` from AVKit over an `AVPlayer` on the plan's URL
  -- native controls, picture in picture allowed, audio and subtitle menus from the asset's
  media selection groups. Space plays and pauses, the arrow keys seek ten seconds,
  `f` toggles full screen. It starts at `playbackPosition` when there is one, reports
  through `PlaybackReporter` from a periodic time observer, and reports stopped when the
  window closes.
- **Settings** (`Settings` scene, Cmd-,) for the server URL, username and password; the
  session token is kept in the Keychain, and the app restores it at launch.
- Light and dark appearance both look right, because nothing is hard-coded that the
  system provides.

The app is not driven by the checks; the tests are for ReelKit. It is checked that it
builds, that `--probe` answers as above from the real hardware, and that the source uses
the pieces named here. But build it to be used.
