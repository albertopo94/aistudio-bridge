"""Shared test helper: a local fake of the Gemini Interactions API.

Stdlib only. Binds to 127.0.0.1 on an ephemeral port; records every
request (headers, body, path) so tests can assert on exactly what the
client sent.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeInteractionsServer:
    """Records requests; replies with a canned interaction or a canned error."""

    def __init__(self):
        self.requests = []  # list of dicts: {path, headers, body}
        self.reply_status = 200
        self.reply_body = {"id": "v1_test", "status": "completed", "steps": [], "usage": {}}
        self.reply_delay = 0.0  # seconds; used to force client timeouts
        self._server = None
        self._thread = None

    def __enter__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output clean
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                outer.requests.append({
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                })
                if outer.reply_delay:
                    import time
                    time.sleep(outer.reply_delay)
                payload = json.dumps(outer.reply_body).encode("utf-8")
                self.send_response(outer.reply_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address
        self.base_url = f"http://{host}:{port}"
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

    @property
    def last_request(self):
        return self.requests[-1] if self.requests else None

    @property
    def last_body(self):
        req = self.last_request
        return json.loads(req["body"]) if req else None
