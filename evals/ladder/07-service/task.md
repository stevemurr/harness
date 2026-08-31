Create `server.py`: an HTTP server using only the Python standard library, started with
`python3 server.py <port>`. It must serve `GET /health` returning status 200 and the exact
body `ok`, and `POST /echo` returning status 200 with the request body sent back unchanged.
Any other path returns 404. Do not use any third-party package.
