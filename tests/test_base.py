"""Sanity test: the package is importable and the version file is readable."""

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestBase(unittest.TestCase):
    def test_version_file(self):
        version = (REPO_ROOT / "VERSION").read_text().strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
