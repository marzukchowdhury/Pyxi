"""Serve .pyxi/library/*.glb at /library/<file> with CORS, standing in for the brain while
looking at library models in the browser preview (overlay.html?backend=http://127.0.0.1:8799).

    python -I scripts/library/serve.py [port]
"""

from __future__ import annotations

import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / ".pyxi"


class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        path = path.split("?", 1)[0]
        if not path.startswith("/library/") or ".." in path:
            return str(ROOT / "nothing-here")
        return str(ROOT / "library" / Path(path).name)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    ThreadingHTTPServer(("127.0.0.1", port), partial(Handler)).serve_forever()
