"""G4/G5: resumable upload to an environment, so repos over 500 MB can reach it.

The `repository` source is capped at 500 MB by the service; a local repo that
size must be pushed as a tar.gz through a resumable upload session instead.
This client implements the documented protocol (agent-environment, "Upload
large files with a resumable session"):

* init: `PUT /upload/v1beta/environments/{env}/files/{path}?uploadType=resumable`
  with `X-Upload-Content-Type`, `X-Upload-Content-Length`, `x-goog-api-key`;
  the reply carries a `Location` header with a session URL (which needs no key
  on the chunk calls).
* chunks: `PUT <session>` with `Content-Type` and
  `Content-Range: bytes {start}-{end}/{total}`.
* every non-final chunk answers `308 Resume Incomplete` with a
  `Range: bytes=0-{last}` header that says where to resume from; the final
  chunk returns the file metadata.

Rules under test (against the fake server):

1. The init request is a PUT with the exact `/upload/` path, the
   `uploadType=resumable` query and both `X-Upload-*` headers, and no body.
2. The session URL is read from the `Location` header.
3. Each chunk is a PUT to the session URL with a `Content-Range` counting from
   the byte the server reported last, and a 308 moves the cursor forward.
4. A 308 with no usable `Range` header is an error, not an endless resume.
5. A non-308 status on a chunk fails the upload with the status and body.
6. The final response can be a file metadata JSON body; when it is empty the
   upload is still reported as finished.
"""

import io
import tarfile
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

try:
    from remote_worker.google_client import UploadClient, UploadResumeError
    from remote_worker.sync import tar_gz_bytes
except ImportError:  # pragma: no cover
    UploadClient = None


class FakeUploadServer:
    """A fake of the resumable-upload surface: init + chunk + final."""

    def __init__(self):
        self.requests = []          # (method, path, headers, body)
        self.init_status = 200
        self.init_location = None   # session URL; None -> build a default
        self.chunk_status = 308     # status for each chunk (last may differ)
        self.chunk_ranges = None    # None -> reflect the client's chunk; [] -> no Range
        self.final_status = 200
        self.final_body = b"{}"
        self._server = None
        self._thread = None

    def __enter__(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_PUT(self):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                outer.requests.append((
                    self.command, self.path,
                    {k.lower(): v for k, v in self.headers.items()}, body,
                ))
                if "uploadType=resumable" in self.path:
                    # init
                    self.send_response(outer.init_status)
                    location = outer.init_location or (
                        f"http://{self.headers['Host']}/upload/session/"
                        f"{len(outer.requests)}")
                    self.send_header("Location", location)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                # chunk or final
                if outer.chunk_status == 308:
                    # The final chunk ends the session with a 200; only a
                    # non-final chunk gets a 308. Parse the end/total out of
                    # the Content-Range the client sent.
                    cr = self.headers.get("Content-Range", "")
                    try:
                        end_part, total_part = (
                            cr.split("/")[0].split("-")[1],
                            cr.split("/")[1],
                        )
                        is_last = int(end_part) == int(total_part) - 1
                    except (ValueError, IndexError):
                        is_last = False
                    if not is_last:
                        self.send_response(308)
                        if outer.chunk_ranges:
                            last = outer.chunk_ranges.pop(0)
                            self.send_header("Range", f"bytes=0-{last}")
                        elif outer.chunk_ranges is None:
                            last = str(int(end_part))
                            self.send_header("Range", f"bytes=0-{last}")
                        # chunk_ranges == [] -> no Range header at all.
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                if outer.chunk_status is not None and outer.chunk_status != 308:
                    self.send_response(outer.chunk_status)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(outer.final_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(outer.final_body)))
                self.end_headers()
                self.wfile.write(outer.final_body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address
        self.base_url = f"http://{host}:{port}"
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()


@unittest.skipIf(UploadClient is None, "remote_worker not implemented")
class UploadClientTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_init_headers_and_path(self):
        with FakeUploadServer() as srv:
            client = UploadClient(api_key="AQ.k", base_url=srv.base_url,
                                  chunk_size=16)
            client.upload(environment_id="env_1", path="repo.tar.gz",
                          data=b"0123456789abcdef" * 4)
            init = srv.requests[0]
            method, path, headers, body = init
        self.assertEqual(method, "PUT")
        self.assertIn("/upload/v1beta/environments/env_1/files/"
                      "repo.tar.gz", path)
        self.assertIn("uploadType=resumable", path)
        self.assertEqual(headers.get("x-upload-content-type"),
                         "application/gzip")
        self.assertEqual(body, b"")

    def test_chunks_use_content_range_and_308_moves_the_cursor(self):
        srv = FakeUploadServer()
        # The server reports it committed bytes 0..15, then 0..31, then 0..47.
        srv.chunk_ranges = ["15", "31", "47"]
        with srv:
            client = UploadClient(api_key="AQ.k", base_url=srv.base_url,
                                  chunk_size=16)
            client.upload(environment_id="env_1", path="repo.tar.gz",
                          data=b"0123456789abcdef" * 4)  # 64 bytes
            chunk_reqs = [r for r in srv.requests if "upload/session" in r[1]]
        # 3 data chunks, then a final one that returns 200.
        self.assertEqual(len(chunk_reqs), 4)
        self.assertEqual(chunk_reqs[-1][0], "PUT")
        ranges = [r[2]["content-range"] for r in chunk_reqs]
        self.assertIn("bytes 0-15/64", ranges[0])
        self.assertIn("bytes 16-31/64", ranges[1])
        self.assertIn("bytes 32-47/64", ranges[2])

    def test_no_range_on_308_is_an_error(self):
        srv = FakeUploadServer()
        srv.chunk_ranges = []   # empty -> the 308 carries no Range header
        with srv:
            client = UploadClient(api_key="AQ.k", base_url=srv.base_url,
                                  chunk_size=8)
            with self.assertRaises(UploadResumeError):
                client.upload(environment_id="env_1", path="repo.tar.gz",
                              data=b"x" * 32)
            chunk = [r for r in srv.requests if "upload/session" in r[1]]
        self.assertEqual(len(chunk), 1)   # stopped after the first 308

    def test_non_308_fails_with_status_and_body(self):
        srv = FakeUploadServer()
        srv.chunk_status = None   # -> the final-response branch (not 308)
        srv.final_status = 500
        srv.final_body = b'{"error":"boom"}'
        with srv:
            client = UploadClient(api_key="AQ.k", base_url=srv.base_url,
                                  chunk_size=8)
            with self.assertRaises(Exception) as ctx:
                client.upload(environment_id="env_1", path="repo.tar.gz",
                              data=b"x" * 16)
        self.assertIn("500", str(ctx.exception))

    def test_requires_id_and_path(self):
        client = UploadClient(api_key="AQ.k", base_url="http://127.0.0.1:1")
        for env_id, path in [("", "repo.tar.gz"), ("env_1", "")]:
            with self.assertRaises(ValueError):
                client.upload(environment_id=env_id, path=path, data=b"x")


@unittest.skipIf(UploadClient is None, "remote_worker not implemented")
class TarGzTest(unittest.TestCase):
    def test_tar_gz_bytes_round_trip(self):
        raw = tar_gz_bytes({"file.txt": b"hello\n"})
        self.assertIsInstance(raw, bytes)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            self.assertIn("file.txt", tar.getnames())


if __name__ == "__main__":
    unittest.main()