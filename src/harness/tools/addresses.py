"""Which addresses the web tools may reach, and who they say they are.

Shared by the fetch path and the browser fallback, because the rule has to be one rule:
a page a browser renders makes requests of its own, and each of them is checked here
exactly as the URL the model typed was. Kept apart from `web.py` so the browser module can
import it without importing the tool that imports the browser.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import urllib.parse

#: A browser's, because the alternative is the challenge page. Sending `python-httpx` as a
#: `User-Agent` to an endpoint with anomaly detection is asking to be classified correctly.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    + "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


async def address_error(url: str, block_private: bool) -> str:
    """Why this URL must not be fetched, or an empty string.

    Resolution happens in a thread: `getaddrinfo` is blocking, and blocking the event loop
    inside a tool stalls every other thing the run is doing.
    """
    parts = urllib.parse.urlsplit(url)
    try:
        # Reading `.port` is where a malformed authority raises -- `http://x:80a/` parses
        # fine until something asks for the number.
        port = parts.port
    except ValueError:
        return f"{url!r} does not have a usable port"
    if parts.scheme not in ("http", "https"):
        return f"only http and https URLs can be opened, not {parts.scheme or 'a bare path'!r}"
    host = parts.hostname
    if not host:
        return f"no host in {url!r}"
    if not block_private:
        return ""

    try:
        found = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        return f"could not resolve {host}: {exc}"

    for entry in found:
        address = ipaddress.ip_address(entry[4][0])
        if (
            address.is_loopback
            or address.is_private
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            return (
                f"{host} resolves to {address}, which is on this machine or its private "
                + "network. Only public addresses can be opened."
            )
    return ""
