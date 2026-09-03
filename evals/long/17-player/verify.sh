#!/bin/sh
set -eu
# The digests are committed literals, as 14-engine's are: the tests, the mock server, the
# runner and the package manifest are the question, and a run that edits them has changed
# the question rather than answered it. `Package.swift` is in the list because it declares
# no dependencies, and that is the whole of how "native" is enforced.
#
# Every check is `if ... then exit 1`, never `! ...`: an inverted test is exempt from
# `set -e` and gates nothing.
TESTS="2d058edcf8ba0d15a3a7374990d35bbc33c23280"
MOCK="b6a7bac37ef88a9ec52a4fded37a9cb90746d0f4"
RUNNER="df818902afdd3f5688bf353944a586a512695981"
PACKAGE="9cd6d9b4c6f3bf1bf24d46dd2b777ba34852786b"

if [ "$(find Tests -type f | sort | xargs shasum | shasum | cut -d' ' -f1)" != "$TESTS" ]; then
    echo "FAILED: Tests/ has been changed, added to, or deleted from" >&2
    exit 1
fi
if [ "$(shasum mock_emby.py | cut -d' ' -f1)" != "$MOCK" ]; then
    echo "FAILED: mock_emby.py has been changed" >&2
    exit 1
fi
if [ "$(shasum run_tests.sh | cut -d' ' -f1)" != "$RUNNER" ]; then
    echo "FAILED: run_tests.sh has been changed" >&2
    exit 1
fi
if [ "$(shasum Package.swift | cut -d' ' -f1)" != "$PACKAGE" ]; then
    echo "FAILED: Package.swift has been changed -- Reel takes no dependencies" >&2
    exit 1
fi

# The library, against the mock server. Prints `SCORE <passed> <total>` for partial credit
# and fails unless every test passes; a package that does not compile scores zero.
./run_tests.sh

# The app builds.
if ! swift build -c debug --product Reel > .build-reel.out 2>&1; then
    tail -30 .build-reel.out >&2
    echo "FAILED: the Reel app does not build" >&2
    exit 1
fi

# And answers `--probe` from the hardware, headless, without hanging. Every Mac this can
# run on decodes H.264 and HEVC in hardware; the answer has to come from VideoToolbox, not
# from a string in the source, and the source check below is the other half of that.
python3 - <<'PY'
import json, subprocess, sys
try:
    done = subprocess.run([".build/debug/Reel", "--probe"], capture_output=True, text=True, timeout=20)
except subprocess.TimeoutExpired:
    sys.exit("FAILED: Reel --probe did not exit within 20 seconds")
if done.returncode != 0:
    sys.exit(f"FAILED: Reel --probe exited {done.returncode}: {done.stderr.strip()[-300:]}")
lines = [line for line in done.stdout.splitlines() if line.strip()]
if len(lines) != 1:
    sys.exit(f"FAILED: Reel --probe must print one line of JSON, printed {len(lines)}")
try:
    probe = json.loads(lines[0])
except json.JSONDecodeError as exc:
    sys.exit(f"FAILED: Reel --probe did not print JSON: {exc}")
for key in ("h264", "hevc", "av1", "arch"):
    if key not in probe:
        sys.exit(f"FAILED: the probe is missing {key!r}")
if probe["h264"] is not True or probe["hevc"] is not True:
    sys.exit(f"FAILED: the probe says this machine does not decode h264/hevc in hardware: {probe}")
if probe["arch"] != "arm64":
    sys.exit(f"FAILED: the probe reports arch {probe['arch']!r}")
print(f"probe: {probe}")
PY

# The pieces the spec names, present in the source. Structural, and admitted to be: the
# app is not driven, so this is the check that it is the native app asked for and not a
# web view or a bundled player wearing a window.
grep -rq --include='*.swift' 'VTIsHardwareDecodeSupported' Sources/ReelKit
grep -rq --include='*.swift' '@main' Sources/Reel
for needle in NavigationSplitView LazyVGrid '\.searchable' AVPlayerView 'Settings {' \
              allowsPictureInPicturePlayback 'mediaSelectionGroup\|AVMediaSelectionGroup' \
              PlaybackReporter 'SecItemAdd\|SecItemCopyMatching'; do
    if ! grep -rq --include='*.swift' -e "$needle" Sources/Reel; then
        echo "FAILED: Sources/Reel does not use $needle" >&2
        exit 1
    fi
done
if grep -rniE --include='*.swift' 'libmpv|ffmpeg|vlckit|gstreamer|webview|WKWebView' Sources; then
    echo "FAILED: a non-native player or a web view is in the source" >&2
    exit 1
fi
if grep -rq --include='*.swift' 'ReelKit {}' Sources/ReelKit; then
    echo "FAILED: the ReelKit placeholder is still there" >&2
    exit 1
fi
