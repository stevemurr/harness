"""The HTTP front end.

`app.py` is the routes and the one error shape; `conversations.py` is what this front end
hands `new_agent`; `runs.py`, `events.py` and `stream.py` are one run in flight, its
append-only log, and the stream a client follows; `workspaces.py` is a registered folder.
`pages/` is the browser client. The command that serves it is `harness serve`, in the CLI
package: this package builds an app and knows nothing about flags.
"""

from harness.server.app import create_app

__all__ = ["create_app"]
