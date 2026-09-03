"""A mock Emby server, enough of it for Reel's tests. Python standard library only.

Started by `run_tests.sh`; prints `READY <port>` once it is listening. Enforces the five
rules in SPEC.md the way the real server would notice them: the authorization header, the
`/emby/` prefix, the play session id on reports, and a device profile that asks for HLS.

Two routes outside `/emby/` exist for the tests alone: `GET /__test/reports` returns every
playback report received, and `POST /__test/reset` forgets them.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

USER = {"Id": "u-alice", "Name": "alice"}
PASSWORD = "secret"
TOKEN = "tok-alice"
SERVER_ID = "srv-1"

MINUTE = 60 * 10_000_000

# A 1x1 PNG, for the image route.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def stream(index, type_, codec, **extra):
    body = {"Index": index, "Type": type_, "Codec": codec, "IsDefault": index in (0, 1)}
    body.update(extra)
    return body


def source(sid, container, streams):
    return {"Id": sid, "Container": container, "MediaStreams": streams}


VIEWS = [
    {"Id": "lib-movies", "Name": "Movies", "CollectionType": "movies"},
    {"Id": "lib-shows", "Name": "Shows", "CollectionType": "tvshows"},
]

ITEMS = [
    {
        "Id": "m-1", "Name": "Blue Harvest", "Type": "Movie", "ProductionYear": 2019,
        "Overview": "A farm, a debt, a harvest moon.", "RunTimeTicks": 90 * MINUTE,
        "ParentId": "lib-movies", "ImageTags": {"Primary": "p1"},
        "UserData": {"PlaybackPositionTicks": 0, "Played": True},
        "MediaSources": [source("ms-1", "mp4", [
            stream(0, "Video", "h264", Width=1920, Height=1080, VideoRange="SDR"),
            stream(1, "Audio", "aac", Language="eng"),
        ])],
    },
    {
        "Id": "m-2", "Name": "Sunless Sea", "Type": "Movie", "ProductionYear": 2022,
        "Overview": "Below the ice, something is listening.", "RunTimeTicks": 125 * MINUTE,
        "ParentId": "lib-movies", "ImageTags": {"Primary": "p2"},
        "UserData": {"PlaybackPositionTicks": 20 * MINUTE, "Played": False},
        "MediaSources": [source("ms-2", "mp4", [
            stream(0, "Video", "hevc", Width=3840, Height=2160, VideoRange="HDR"),
            stream(1, "Audio", "eac3", Language="eng"),
            stream(2, "Subtitle", "srt", Language="eng", Title="English"),
        ])],
    },
    {
        "Id": "m-3", "Name": "Northern Lights", "Type": "Movie", "ProductionYear": 2024,
        "Overview": "Three nights above the circle.", "RunTimeTicks": 101 * MINUTE,
        "ParentId": "lib-movies", "ImageTags": {"Primary": "p3"},
        "UserData": {"PlaybackPositionTicks": 0, "Played": False},
        "MediaSources": [source("ms-3", "mkv", [
            stream(0, "Video", "hevc", Width=3840, Height=2160, VideoRange="Dolby Vision"),
            stream(1, "Audio", "eac3", Language="eng"),
            stream(2, "Audio", "ac3", Language="fra"),
            stream(3, "Subtitle", "srt", Language="eng", Title="English"),
        ])],
    },
    {
        "Id": "m-4", "Name": "Static", "Type": "Movie", "ProductionYear": 2021,
        "Overview": "The signal was never for us.", "RunTimeTicks": 88 * MINUTE,
        "ParentId": "lib-movies", "ImageTags": {},
        "UserData": {"PlaybackPositionTicks": 0, "Played": False},
        "MediaSources": [source("ms-4", "mp4", [
            stream(0, "Video", "av1", Width=1920, Height=1080, VideoRange="SDR"),
            stream(1, "Audio", "aac", Language="eng"),
        ])],
    },
    {
        "Id": "s-1", "Name": "Harbour Lights", "Type": "Series", "ProductionYear": 2020,
        "Overview": "A lighthouse keeper and the town that forgot him.",
        "ParentId": "lib-shows", "ImageTags": {"Primary": "ps1"},
        "UserData": {"PlaybackPositionTicks": 0, "Played": False},
    },
    {
        "Id": "s-1-1", "Name": "Season 1", "Type": "Season", "IndexNumber": 1,
        "ParentId": "s-1", "SeriesName": "Harbour Lights", "ImageTags": {},
        "UserData": {"PlaybackPositionTicks": 0, "Played": False},
    },
    {
        "Id": "e-1", "Name": "Pilot", "Type": "Episode", "IndexNumber": 1, "ParentIndexNumber": 1,
        "ParentId": "s-1-1", "SeriesName": "Harbour Lights", "RunTimeTicks": 44 * MINUTE,
        "Overview": "The light goes out.", "ImageTags": {"Primary": "pe1"},
        "UserData": {"PlaybackPositionTicks": 0, "Played": True},
        "MediaSources": [source("ms-e1", "mp4", [
            stream(0, "Video", "h264", Width=1920, Height=1080, VideoRange="SDR"),
            stream(1, "Audio", "aac", Language="eng"),
        ])],
    },
    {
        "Id": "e-2", "Name": "The Storm", "Type": "Episode", "IndexNumber": 2, "ParentIndexNumber": 1,
        "ParentId": "s-1-1", "SeriesName": "Harbour Lights", "RunTimeTicks": 42 * MINUTE,
        "Overview": "Nobody comes back from the point.", "ImageTags": {"Primary": "pe2"},
        "UserData": {"PlaybackPositionTicks": 12 * MINUTE, "Played": False},
        "MediaSources": [source("ms-e2", "mp4", [
            stream(0, "Video", "h264", Width=1920, Height=1080, VideoRange="SDR"),
            stream(1, "Audio", "aac", Language="eng"),
        ])],
    },
]
BY_ID = {item["Id"]: item for item in ITEMS}

#: Continue Watching, most recent first: the order the tests expect.
RESUME_ORDER = ["m-2", "e-2"]

AUTH = re.compile(
    r'^MediaBrowser Client="Reel", Device="[^"]+", DeviceId="[^"]+", Version="[^"]+"'
    r'(?:, Token="(?P<token>[^"]+)")?$'
)

LOCK = threading.Lock()
REPORTS: list[dict] = []
SESSION_COUNTER = [0]


def descendants(parent_id):
    found = []
    for item in ITEMS:
        if item.get("ParentId") == parent_id:
            found.append(item)
            found.extend(descendants(item["Id"]))
    return found


def public(item, fields=()):
    """An item as the list routes return it: without media sources, and without the
    overview unless asked for with `Fields=Overview`."""
    body = {k: v for k, v in item.items() if k != "MediaSources"}
    if "Overview" not in fields:
        body.pop("Overview", None)
    return body


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 -- the base class's name
        sys.stderr.write("%s %s\n" % (self.command, self.path))

    # -- plumbing -----------------------------------------------------------------------

    def send_json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_bytes(self, status, data, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def token(self):
        """The token from the authorization header, "" when logged out, None when the
        header is missing or malformed."""
        header = self.headers.get("X-Emby-Authorization", "")
        match = AUTH.match(header)
        if match is None:
            return None
        return match.group("token") or ""

    def authorised(self):
        token = self.token()
        api_key = parse_qs(urlparse(self.path).query).get("api_key", [""])[0]
        return token == TOKEN or api_key == TOKEN

    # -- routing ------------------------------------------------------------------------

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def route(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if path == "/__test/reports":
            with LOCK:
                return self.send_json(200, list(REPORTS))
        if path == "/__test/reset":
            with LOCK:
                REPORTS.clear()
            return self.send_json(200, {})

        if not path.startswith("/emby/"):
            return self.send_json(404, {"error": "every route is under /emby/"})
        route = path[len("/emby/"):]

        if route == "System/Info/Public" and method == "GET":
            return self.send_json(
                200, {"ServerName": "Mock Emby", "Version": "4.9.0.0", "Id": SERVER_ID}
            )

        if self.token() is None:
            return self.send_json(401, {"error": "X-Emby-Authorization is missing or malformed"})

        if route == "Users/AuthenticateByName" and method == "POST":
            body = self.body() or {}
            if body.get("Username") == USER["Name"] and body.get("Pw") == PASSWORD:
                return self.send_json(
                    200, {"User": USER, "AccessToken": TOKEN, "ServerId": SERVER_ID}
                )
            return self.send_json(401, {"error": "wrong username or password"})

        if not self.authorised():
            return self.send_json(401, {"error": "not logged in"})

        if method == "GET":
            return self.get(route, query)
        return self.post(route, query)

    def get(self, route, query):
        user_items = f"Users/{USER['Id']}/Items"
        if route == f"Users/{USER['Id']}/Views":
            return self.send_json(200, {"Items": VIEWS})

        if route == f"{user_items}/Resume":
            return self.send_json(200, {"Items": [public(BY_ID[i]) for i in RESUME_ORDER]})

        if route == user_items:
            return self.list_items(query)

        match = re.fullmatch(rf"{user_items}/([\w-]+)", route)
        if match:
            item = BY_ID.get(match.group(1))
            if item is None:
                return self.send_json(404, {"error": "no such item"})
            return self.send_json(200, item)

        match = re.fullmatch(r"Items/([\w-]+)/Images/(\w+)", route)
        if match:
            item = BY_ID.get(match.group(1))
            if item is None or query.get("tag") != item.get("ImageTags", {}).get(match.group(2)):
                return self.send_json(404, {"error": "no such image"})
            return self.send_bytes(200, PNG, "image/png")

        match = re.fullmatch(r"Videos/([\w-]+)/stream\.(\w+)", route)
        if match:
            item = BY_ID.get(match.group(1))
            if item is None or not item.get("MediaSources"):
                return self.send_json(404, {"error": "no such video"})
            return self.send_bytes(200, b"\x00\x00\x00\x18ftypmp42not-a-real-file", "video/mp4")

        return self.send_json(404, {"error": f"no route {route}"})

    def list_items(self, query):
        fields = tuple(query.get("Fields", "").split(","))
        parent = query.get("ParentId")
        recursive = query.get("Recursive", "").lower() == "true"
        kinds = {k for k in query.get("IncludeItemTypes", "").split(",") if k}
        term = query.get("SearchTerm", "").lower()

        if parent:
            found = descendants(parent) if recursive else [
                i for i in ITEMS if i.get("ParentId") == parent
            ]
        elif recursive or term:
            found = list(ITEMS)
        else:
            found = [i for i in ITEMS if i.get("ParentId") in {v["Id"] for v in VIEWS}]
        if kinds:
            found = [i for i in found if i["Type"] in kinds]
        if term:
            found = [i for i in found if term in i["Name"].lower()]

        start = int(query.get("StartIndex", 0))
        limit = int(query.get("Limit", len(found) or 1))
        page = found[start:start + limit]
        return self.send_json(
            200, {"Items": [public(i, fields) for i in page], "TotalRecordCount": len(found)}
        )

    def post(self, route, query):
        match = re.fullmatch(r"Items/([\w-]+)/PlaybackInfo", route)
        if match:
            return self.playback_info(match.group(1), query)

        if route in ("Sessions/Playing", "Sessions/Playing/Progress", "Sessions/Playing/Stopped"):
            body = self.body()
            if body is None:
                return self.send_json(400, {"error": "the report is not JSON"})
            for key in ("ItemId", "MediaSourceId", "PlaySessionId", "PositionTicks"):
                if not body.get(key) and body.get(key) != 0:
                    return self.send_json(400, {"error": f"a report needs {key}"})
            if route.endswith("Progress") and "IsPaused" not in body:
                return self.send_json(400, {"error": "a progress report needs IsPaused"})
            kind = {"Sessions/Playing": "started", "Sessions/Playing/Progress": "progress",
                    "Sessions/Playing/Stopped": "stopped"}[route]
            with LOCK:
                REPORTS.append({"kind": kind, **body})
            return self.send_bytes(204, b"", "application/json")

        return self.send_json(404, {"error": f"no route {route}"})

    def playback_info(self, item_id, query):
        item = BY_ID.get(item_id)
        if item is None or not item.get("MediaSources"):
            return self.send_json(404, {"error": "no such item"})
        if query.get("UserId") != USER["Id"]:
            return self.send_json(400, {"error": "PlaybackInfo needs UserId"})
        body = self.body()
        profile = (body or {}).get("DeviceProfile")
        if not isinstance(profile, dict):
            return self.send_json(400, {"error": "PlaybackInfo needs a DeviceProfile"})

        transcoding = [
            p for p in profile.get("TranscodingProfiles", [])
            if p.get("Protocol") == "hls" and p.get("VideoCodec") == "h264"
            and p.get("AudioCodec") == "aac" and p.get("Container") == "ts"
        ]
        if not transcoding:
            return self.send_json(
                400, {"error": "the device profile must offer an HLS h264/aac transcode"}
            )

        containers, video_codecs, audio_codecs = set(), set(), set()
        for p in profile.get("DirectPlayProfiles", []):
            if p.get("Type") != "Video":
                continue
            containers |= set(p.get("Container", "").split(","))
            video_codecs |= set(p.get("VideoCodec", "").split(","))
            audio_codecs |= set(p.get("AudioCodec", "").split(","))

        with LOCK:
            SESSION_COUNTER[0] += 1
            play_session = f"ps-{SESSION_COUNTER[0]}"

        sources = []
        for src in item["MediaSources"]:
            video = next((s for s in src["MediaStreams"] if s["Type"] == "Video"), None)
            audio = [s for s in src["MediaStreams"] if s["Type"] == "Audio"]
            direct = (
                src["Container"] in containers
                and video is not None and video["Codec"] in video_codecs
                and any(a["Codec"] in audio_codecs for a in audio)
            )
            answer = dict(src)
            answer["SupportsDirectPlay"] = direct
            answer["SupportsTranscoding"] = True
            if not direct:
                answer["TranscodingUrl"] = (
                    f"/emby/Videos/{item_id}/master.m3u8?DeviceId=mock"
                    f"&MediaSourceId={src['Id']}&PlaySessionId={play_session}&api_key={TOKEN}"
                )
            sources.append(answer)
        return self.send_json(200, {"MediaSources": sources, "PlaySessionId": play_session})


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"READY {server.server_address[1]}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
