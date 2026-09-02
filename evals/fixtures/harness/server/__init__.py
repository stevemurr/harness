"""The HTTP front end.

`app.py` is the routes and the one error shape; `conversations.py` is what this front end
hands `new_agent`; `runs.py`, `events.py` and `stream.py` are one run in flight, its
append-only log, and the stream a client follows; `workspaces.py` is a registered folder.
`pages/` is the browser client. Nothing outside this package imports anything but the two
names below.
"""

from harness.server.app import create_app, main

__all__ = ["create_app", "main"]
