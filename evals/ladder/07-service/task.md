Create `server.py` in this folder, at the top level: an HTTP server using only the Python standard library, started with
`python3 server.py <port>`.

- `GET /health` -> 200, body exactly `ok`.
- `POST /echo` -> 200, the request body returned byte for byte, including newlines.
- `GET /count` -> 200, the number of requests this process has served **before** this one,
  as a decimal string. So the first request to `/count` on a fresh server returns `0`.
- Anything else -> 404 with a JSON body `{"error": "not found"}`.

Every request counts towards `/count`, whatever its path or method. No third-party packages.
