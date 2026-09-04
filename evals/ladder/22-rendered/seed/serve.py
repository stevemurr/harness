"""Serve `site/` on 127.0.0.1: `python3 serve.py PORT`. Runs until stopped."""

from __future__ import annotations

import functools
import http.server
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent / "site"


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        sys.stderr.write(f"{self.address_string()} {format % args}\n")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    handler = functools.partial(Quiet, directory=str(SITE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"serving {SITE} on http://127.0.0.1:{port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
