Read `SPEC.md` and build Reel, a native macOS media player for an Emby server, in this
folder. It is a Swift package: `ReelKit` is the library that talks to the server and decides
how to play, and `Reel` is the SwiftUI app over it. Both targets are declared in
`Package.swift` already, with placeholder sources.

The tests in `Tests/ReelKitTests/` are the contract for the library. Run them with
`./run_tests.sh`, which starts the mock Emby server in `mock_emby.py`, runs `swift test`
against it, and prints how many pass. Keep working until all of them pass. Do not edit
anything in `Tests/`, `mock_emby.py`, `run_tests.sh` or `Package.swift`: they are the
specification made executable, and changing them changes the question rather than
answering it.

The app is checked structurally, not by driving it: it must build, `Reel --probe` must run
headless and report what the machine can decode in hardware, and the source must use the
native pieces the spec names. Make it feel like a modern macOS application anyway -- a
sidebar, a poster grid, a detail view with badges, a player with native controls -- because
that is what it is for, and the spec says what that means.

This is a long task and the pieces depend on each other: the client feeds the models, the
models feed the playback decision, and the decision feeds the player. Work through it in
pieces, run the tests often, and let the failures tell you what to do next.
