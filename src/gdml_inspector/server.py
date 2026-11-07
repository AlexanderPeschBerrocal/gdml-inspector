"""Local HTTP serving shared by the two exporters."""

from __future__ import annotations

import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote


def serve_file(path: Path, port: int, *, open_browser: bool = True) -> None:
    path = path.resolve()
    handler = partial(SimpleHTTPRequestHandler, directory=str(path.parent))
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        url = f"http://127.0.0.1:{server.server_port}/{quote(path.name)}"
        print(f"Serving {url}", flush=True)
        if open_browser:
            try:
                webbrowser.open(url)
            except webbrowser.Error:
                pass
        server.serve_forever()
