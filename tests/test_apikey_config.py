"""Tests for storing the API key in the product config (no .env needed).

Rules under test:
1. save_config stores the api_key inside config.json with 0600 permissions.
2. load_config returns the stored key.
3. An existing key is preserved when setup is called again without one.
4. resolve_api_key prefers the environment variable and falls back to the
   stored config; with neither, returns None (status/setup still work).
5. The key never appears in the saved model/budget section unmasked... (it
   lives in the same file, but the file is 0600 in the user's home).
"""

import json
import os
import stat
import tempfile
import unittest

try:
    from remote_worker import config
except ImportError:
    config = None


class TestApiKeyConfig(unittest.TestCase):
    def setUp(self):
        if config is None:
            self.fail("RED: api key storage not implemented yet")

    def _env(self, value=None):
        old = os.environ.pop("GEMINI_API_KEY", None)
        if value is not None:
            os.environ["GEMINI_API_KEY"] = value
        return old

    def _restore_env(self, old):
        if old is not None:
            os.environ["GEMINI_API_KEY"] = old
        else:
            os.environ.pop("GEMINI_API_KEY", None)

    # Rule 1
    def test_save_stores_api_key_with_private_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = config.save_config(model=None, budget=None,
                                      api_key="AQ.secret", root=tmp)
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(mode, 0o600)
            with open(path) as fh:
                data = json.load(fh)
        self.assertEqual(data["api_key"], "AQ.secret")

    # Rule 2
    def test_load_returns_stored_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            config.save_config(model=None, budget=None,
                               api_key="AQ.k1", root=tmp)
            loaded = config.load_config(root=tmp)
        self.assertEqual(loaded["api_key"], "AQ.k1")

    # Rule 3: re-running setup without a key keeps the old one.
    def test_resetup_without_key_keeps_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config.save_config(model=None, budget=None,
                               api_key="AQ.first", root=tmp)
            config.save_config(model="gemini-4-argon", budget=1000,
                               api_key=None, root=tmp)
            loaded = config.load_config(root=tmp)
        self.assertEqual(loaded["api_key"], "AQ.first")

    # Rule 4: env wins, then config, then None.
    def test_resolve_prefers_env_over_config(self):
        old = self._env("AQ.env")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                config.save_config(model=None, budget=None,
                                   api_key="AQ.cfg", root=tmp)
                self.assertEqual(config.resolve_api_key(root=tmp), "AQ.env")
        finally:
            self._restore_env(old)

    def test_resolve_falls_back_to_config(self):
        old = self._env(None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                config.save_config(model=None, budget=None,
                                   api_key="AQ.cfg", root=tmp)
                self.assertEqual(config.resolve_api_key(root=tmp), "AQ.cfg")
        finally:
            self._restore_env(old)

    def test_resolve_returns_none_without_any(self):
        old = self._env(None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self.assertIsNone(config.resolve_api_key(root=tmp))
        finally:
            self._restore_env(old)


if __name__ == "__main__":
    unittest.main()
