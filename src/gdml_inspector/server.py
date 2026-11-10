"""Local HTTP serving shared by the two exporters."""

from __future__ import annotations

import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote


def create_server(directory: Path, port: int) -> ThreadingHTTPServer:
    """A loopback server rooted at ``directory``; the caller owns it (use as
    a context manager, or call shutdown()/server_close()). Port 0 picks a
    free port, readable from ``server.server_port``."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def serve_file(path: Path, port: int, *, open_browser: bool = True) -> None:
    path = path.resolve()
    with create_server(path.parent, port) as server:
        url = f"http://127.0.0.1:{server.server_port}/{quote(path.name)}"
        print(f"Serving {url}", flush=True)
        if open_browser:
            try:
                webbrowser.open(url)
            except webbrowser.Error:
                pass
        server.serve_forever()
