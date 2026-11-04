"""WU-5: tests for remote_worker.config and the setup command.

Rules:
1. Saving the config writes a private file with the chosen model and budget.
2. Saving over an existing config keeps a timestamped backup of it.
3. An unsupported model is rejected before anything is written.
4. A missing config yields the planner defaults, never an error.
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


class TestConfig(unittest.TestCase):
    def setUp(self):
        if config is None:
            self.fail("RED: remote_worker.config not implemented yet")

    # Rule 1
    def test_save_writes_private_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = config.save_config(model="gemini-4-argon", budget=9000,
                                      root=tmp)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            with open(path) as fh:
                data = json.load(fh)
        self.assertEqual(data["model"], "gemini-4-argon")
        self.assertEqual(data["budget"], 9000)

    # Rule 2
    def test_save_backs_up_existing_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = config.save_config(model="gemini-3.8-flash", budget=1000,
                                       root=tmp)
            config.save_config(model="gemini-4-argon", budget=2000, root=tmp)
            backups = [n for n in os.listdir(tmp)
                       if n.startswith(os.path.basename(first) + ".bak-")]
            self.assertEqual(len(backups), 1)
            with open(os.path.join(tmp, backups[0])) as fh:
                self.assertEqual(json.load(fh)["model"], "gemini-3.8-flash")

    # Rule 3
    def test_rejects_model_below_floor(self):
        from remote_worker.planner import UnsupportedModelError
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(UnsupportedModelError):
                config.save_config(model="gemini-2.5-flash", budget=1000,
                                   root=tmp)
            self.assertEqual(os.listdir(tmp), [])

    # Rule 4
    def test_missing_config_returns_defaults(self):
        from remote_worker.planner import DEFAULT_BUDGET, DEFAULT_MODEL
        with tempfile.TemporaryDirectory() as tmp:
            loaded = config.load_config(root=tmp)
        self.assertEqual(loaded["model"], DEFAULT_MODEL)
        self.assertEqual(loaded["budget"], DEFAULT_BUDGET)


if __name__ == "__main__":
    unittest.main()
