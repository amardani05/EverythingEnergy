#!/usr/bin/env python3
"""
Everything Energy · dev server.

Replaces `python3 -m http.server 8000` — same static-file behavior plus:

  POST /api/refresh   → re-runs consolidate_data.py (rebuilds dashboard_data.json),
                        returns {status, returncode, stdout, stderr} so the
                        front-end loading screen can show progress.

Run:    python3 server.py
        (or python3.11 server.py if your `python3` lacks pyyaml/pandas)
"""

import http.server
import socketserver
import json
import subprocess
import sys
from pathlib import Path

PORT = 8000
ROOT = Path(__file__).parent.resolve()


class Handler(http.server.SimpleHTTPRequestHandler):
    # Serve from the script's directory regardless of cwd
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        # No browser caching during dev — the frontend already cache-busts JSON
        # but JS/CSS can still go stale on hard refresh otherwise.
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        super().end_headers()

    def do_POST(self):
        if self.path != "/api/refresh":
            self.send_error(404, "Unknown endpoint")
            return

        print(f"\n[server] POST /api/refresh · running pipeline...")
        try:
            result = subprocess.run(
                [sys.executable, "consolidate_data.py"],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(ROOT),
            )
            payload = {
                "status": "ok" if result.returncode == 0 else "error",
                "returncode": result.returncode,
                # Truncate to keep responses small; the tail is what matters anyway
                "stdout": result.stdout[-2000:],
                "stderr": result.stderr[-2000:],
            }
            code = 200 if result.returncode == 0 else 500
            print(f"[server]   → returncode {result.returncode}, status {payload['status']}")
        except subprocess.TimeoutExpired:
            payload = {"status": "error", "error": "pipeline timed out (>5min)"}
            code = 504
            print(f"[server]   → TIMEOUT")
        except Exception as e:  # noqa: BLE001
            payload = {"status": "error", "error": str(e)}
            code = 500
            print(f"[server]   → EXCEPTION: {e}")

        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    # Browsers only run JSX through Babel-standalone if it's served as JS
    Handler.extensions_map[".jsx"] = "application/javascript"

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"\nEverything Energy · dev server")
        print(f"  http://localhost:{PORT}/dashboard.html")
        print(f"  POST /api/refresh  →  re-run consolidate_data.py")
        print(f"  ctrl-C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[server] shutting down")


if __name__ == "__main__":
    main()
