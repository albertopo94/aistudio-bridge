"""WU-3: tests for remote_worker.google_client.FilesClient.

Rules under test:
1. The download path is the current Environments files endpoint with
   ``alt=media`` (the legacy ``:download`` endpoint is deprecated).
2. A download writes the exact bytes to the destination and reports the
   count.
3. Listing uses ``recursive=true`` and returns the ``files`` array.
4. The API key travels only in the header, for GET as well.
5. A download streams the body to disk in bounded chunks (P1-2): the whole
   workspace is never buffered in memory -- the measured failure mode of
   big workspaces (2 GiB rejected, big TARs dying at ~122 s).
"""

import os
import tempfile
import unittest
from unittest import mock

from tests.fake_interactions import FakeInteractionsServer

try:
    from remote_worker.google_client import FilesClient
except ImportError:
    FilesClient = None


class TestFilesClient(unittest.TestCase):
    def setUp(self):
        if FilesClient is None:
            self.fail("RED: FilesClient not implemented yet")

    # Rule 1 + 3: exact list path.
    def test_list_files_uses_recursive_endpoint(self):
        with FakeInteractionsServer() as srv:
            srv.reply_bytes = b'{"files": [{"path": "workspace/a.py"}]}'
            client = FilesClient(api_key="AQ.fake", base_url=srv.base_url)
            files = client.list_files("env_1")
            req = srv.last_request
        self.assertEqual(
            req["path"], "/v1beta/environments/env_1/files?recursive=true"
        )
        self.assertEqual(req["method"], "GET")
        self.assertEqual(files, [{"path": "workspace/a.py"}])

    # Rule 1 + 2: exact download path and bytes written.
    def test_download_to_writes_bytes(self):
        payload = b"TAR-BYTES-\x00\x01"
        with FakeInteractionsServer() as srv:
            srv.reply_bytes = payload
            client = FilesClient(api_key="AQ.fake", base_url=srv.base_url)
            with tempfile.TemporaryDirectory() as tmp:
                dest = os.path.join(tmp, "workspace.tar")
                written = client.download_to("env_1", "workspace", dest)
                with open(dest, "rb") as fh:
                    on_disk = fh.read()
            path = srv.last_request["path"]
        self.assertEqual(
            path,
            "/v1beta/environments/env_1/files/workspace?alt=media&recursive=true",
        )
        self.assertNotIn(":download", path)  # legacy endpoint is deprecated
        self.assertEqual(on_disk, payload)
        self.assertEqual(written, len(payload))

    # Rule 4: the key never appears in the URL for GET requests.
    def test_key_is_header_only_on_get(self):
        with FakeInteractionsServer() as srv:
            srv.reply_bytes = b"{}"
            client = FilesClient(api_key="AQ.secret-key", base_url=srv.base_url)
            client.list_files("env_1")
            req = srv.last_request
        self.assertEqual(req["headers"]["x-goog-api-key"], "AQ.secret-key")
        self.assertNotIn("AQ.secret-key", req["path"])

    # Rule 5: the body is never held whole in memory.
    def test_download_streams_the_body_in_bounded_chunks(self):
        chunks = [b"x" * 10, b"y" * 5, b"z"]
        sizes = []

        class FakeStream:
            def __init__(self):
                self._it = iter(chunks)

            def read(self, size=None):
                sizes.append(size)
                try:
                    return next(self._it)
                except StopIteration:
                    return b""

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            return FakeStream()

        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "ws.tar")
            with mock.patch(
                    "remote_worker.google_client.urllib.request.urlopen",
                    side_effect=fake_urlopen):
                client = FilesClient(api_key="AQ.fake",
                                     base_url="http://unused")
                written = client.download_to("env_1", "workspace", dest)
            with open(dest, "rb") as fh:
                on_disk = fh.read()
        self.assertEqual(on_disk, b"".join(chunks))
        self.assertEqual(written, 16)
        # Every read asks for a bounded amount, never the whole body.
        self.assertTrue(sizes, "the body was never read")
        self.assertTrue(all(s for s in sizes), f"unbounded read: {sizes}")
        self.assertTrue(all(s <= (1 << 20) for s in sizes),
                        f"oversized read: {sizes}")


if __name__ == "__main__":
    unittest.main()