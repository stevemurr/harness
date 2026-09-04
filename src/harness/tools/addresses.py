"""Which addresses the web tools may reach, and who they say they are.

Shared by the fetch path and the browser fallback, because the rule has to be one rule:
a page a browser renders makes requests of its own, and each of them is checked here
exactly as the URL the model typed was. Kept apart from `web.py` so the browser module can
import it without importing the tool that imports the browser.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import urllib.parse

from harness.settings import DEFAULT_USER_AGENT

#: A browser's, because the alternative is the challenge page. Sending `python-httpx` as a
#: `User-Agent` to an endpoint with anomaly detection is asking to be classified correctly.
#: The string itself lives in `settings`, where a deployment can change it.
USER_AGENT = DEFAULT_USER_AGENT

_CHROME = re.compile(r"Chrome/(\d+)")


def navigation_headers(
    user_agent: str = USER_AGENT, accept_language: str = "en-US,en;q=0.9"
) -> dict[str, str]:
    """The headers a browser sends when a person opens a page, for a fetch to send too.

    A `User-Agent` alone is no longer enough. A bot check reads the whole request: the
    `Accept` a browser sends for a document, the `Sec-Fetch-*` headers that say this is a
    top-level navigation, `Upgrade-Insecure-Requests`, and the client hints
    (`sec-ch-ua`, platform, mobile) -- which must agree with the user agent, because a
    Chrome that says it is version 151 in one header and something else in another is
    the fingerprint of a script. Measured 2026-09-03: a Cloudflare-fronted page answered
    403 with a challenge to the old header set and 200 to this one, same client.

    The client hints are derived from the user agent, so changing it in `[web]` keeps
    them consistent; a user agent that is not Chrome's sends none, as that browser would.
    """
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
        + "image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": accept_language,
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    if (chrome := _CHROME.search(user_agent)) is not None:
        major = chrome.group(1)
        platform = "Linux"
        if "Mac" in user_agent:
            platform = "macOS"
        elif "Windows" in user_agent:
            platform = "Windows"
        headers["sec-ch-ua"] = (
            f'"Chromium";v="{major}", "Google Chrome";v="{major}", "Not-A.Brand";v="24"'
        )
        headers["sec-ch-ua-mobile"] = "?0"
        headers["sec-ch-ua-platform"] = f'"{platform}"'
    return headers


async def address_error(url: str, block_private: bool) -> str:
    """Why this URL must not be fetched, or an empty string.

    Resolution happens in a thread: `getaddrinfo` is blocking, and blocking the event loop
    inside a tool stalls every other thing the run is doing.
    """
    try:
        parts = urllib.parse.urlsplit(url)
        # Reading `.port` is where a malformed authority raises -- `http://x:80a/` parses
        # fine until something asks for the number.
        port = parts.port
    except ValueError:
        return "the URL does not have a usable port or host"
    if parts.username is not None or parts.password is not None:
        return "URLs containing credentials cannot be opened"
    if parts.scheme not in ("http", "https"):
        return f"only http and https URLs can be opened, not {parts.scheme or 'a bare path'!r}"
    host = parts.hostname
    if not host:
        return f"no host in {url!r}"
    if not block_private:
        return ""

    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            found = await asyncio.to_thread(
                socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM
            )
        except (socket.gaierror, UnicodeError) as exc:
            return f"could not resolve {host}: {exc}"
        addresses = [ipaddress.ip_address(entry[4][0]) for entry in found]
    if not addresses:
        return f"could not resolve {host}: no addresses"
    for address in addresses:
        # is_global also excludes shared address space (100.64/10), which is neither
        # private nor reserved in ipaddress. Multicast has its own global classification.
        if not address.is_global or address.is_multicast:
            return (
                f"{host} resolves to {address}, which is on this machine or its private "
                + "network. Only public addresses can be opened."
            )
    return ""
