"""Serve ``dist/`` for local use and tests — any static server works; this one
only adds ``Cache-Control: no-cache``, so a rebuilt script is never mixed with a
cached one (that once broke the page with "applySavedGridLayout is not defined").

Run: ``.venv/bin/python scripts/serve_dist.py [--dir dist] [--port 9000]`` (``task serve``).
"""

from __future__ import annotations

import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def make_server(directory: Path, host: str = "127.0.0.1", port: int = 9000) -> ThreadingHTTPServer:
    handler = functools.partial(NoCacheHandler, directory=str(directory))
    return ThreadingHTTPServer((host, port), handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=REPO / "dist")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()
    server = make_server(args.dir, args.host, args.port)
    print(f"Serving {args.dir} on http://{args.host}:{args.port}")
    server.serve_forever()
