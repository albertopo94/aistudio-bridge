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
        self.replies = []  # optional queue of (status, body); consumed per request
        self.reply_bytes = b""  # what GET returns
        self.get_replies = []  # optional queue of (status, bytes) for GETs
        self.delete_replies = []  # optional queue of (status, bytes) for DELETEs
        self.reply_delay = 0.0  # seconds; used to force client timeouts
        self.sse_events = None  # optional list of dicts -> SSE stream (alt=sse)
        self._server = None
        self._thread = None

    def __enter__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output clean
                pass

            def do_GET(self):
                outer.requests.append({
                    "method": "GET",
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": "",
                })
                if outer.get_replies:
                    status, payload = outer.get_replies.pop(0)
                else:
                    status, payload = outer.reply_status, outer.reply_bytes
                self.send_response(status)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_DELETE(self):
                outer.requests.append({
                    "method": "DELETE",
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": "",
                })
                if outer.delete_replies:
                    status, payload = outer.delete_replies.pop(0)
                else:
                    status, payload = outer.reply_status, outer.reply_bytes
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                outer.requests.append({
                    "method": "POST",
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body,
                })
                # Mirror the live contract for the interaction's
                # `environment` field: an object there is an
                # EnvironmentConfig, where `type` is required and its value
                # is "remote"; an existing environment is referenced by its
                # id as a bare STRING. The real service answers this exact
                # 400 otherwise, and this fake let an invented {"id": ...}
                # shape go green until T25 ran it live (odd L44).
                if self.path.startswith("/v1beta/interactions") and body:
                    try:
                        parsed = json.loads(body)
                    except (json.JSONDecodeError, TypeError):
                        parsed = None
                    env = (parsed.get("environment")
                           if isinstance(parsed, dict) else None)
                    if isinstance(env, dict) and not env.get("type"):
                        err = json.dumps({"error": {
                            "message": "Provide a 'type' field (for "
                                       "EnvironmentConfig) at "
                                       "'environment'",
                            "code": "invalid_request",
                        }}).encode("utf-8")
                        self.send_response(400)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(err)))
                        self.end_headers()
                        self.wfile.write(err)
                        return
                if outer.reply_delay:
                    import time
                    time.sleep(outer.reply_delay)
                # SSE path: when the client asked for streaming (alt=sse in the
                # query) and the test configured sse_events, emit them as a
                # server-sent-events body and stop.
                if "alt=sse" in self.path and outer.sse_events is not None:
                    chunks = []
                    for ev in outer.sse_events:
                        ev_type = ev.get("event_type", "")
                        chunks.append(f"event: {ev_type}\n")
                        chunks.append("data: " + json.dumps(ev) + "\n\n")
                    chunks.append("event: done\ndata: [DONE]\n\n")
                    payload = "".join(chunks).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if outer.replies:
                    status, body = outer.replies.pop(0)
                else:
                    status, body = outer.reply_status, outer.reply_body
                payload = json.dumps(body).encode("utf-8")
                self.send_response(status)
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
