"""Tests for the skill installer (WU-6b).

Rules under test (mirroring the agy-model-bridge backup/restore pattern):
1. Installing writes the skill to <root>/skills/<name>/SKILL.md with the
   exact frontmatter.
2. Installing over an existing skill keeps a timestamped backup of it.
3. Restoring rolls back to a specific backup, or to the most recent one.
4. Listing backups works and they are tagged by timestamp.
5. Restoring a state that had no skill before removes it (factory reset).
"""

import json
import os
import tempfile
import unittest

try:
    from remote_worker.skill_installer import (
        SKILL_NAME,
        install_skill,
        list_backups,
        restore_skill,
    )
except ImportError:
    install_skill = None


class TestSkillInstaller(unittest.TestCase):
    def setUp(self):
        if install_skill is None:
            self.fail("RED: remote_worker.skill_installer not implemented yet")

    # Rule 1: install writes the skill file with frontmatter.
    def test_install_writes_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = install_skill(root=tmp)
            self.assertTrue(os.path.exists(path))
            content = open(path).read()
        self.assertIn(f"name: {SKILL_NAME}", content)
        self.assertIn("remote-worker", content)
        # Rule 1b: the skill documents the real key resolution order
        # (env wins, stored config is the fallback), not the PoC-only env var.
        self.assertIn("GEMINI_API_KEY", content)
        self.assertIn("~/.remote-worker/config.json", content)
        self.assertNotIn("Run from the project where the API key is available", content)

    # Rule 2: reinstalling keeps a timestamped backup.
    def test_reinstall_backs_up_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_skill(root=tmp)
            install_skill(root=tmp)  # second install
            backup_dir = os.path.join(tmp, "skills", "remote-worker")
            backups = [n for n in os.listdir(backup_dir)
                       if ".backup-" in n and not n.endswith("-original")]
        self.assertEqual(len(backups), 1)

    # Rule 3: restore to the most recent backup.
    def test_restore_most_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_skill(root=tmp)
            install_skill(root=tmp)  # 2nd install: leaves a real .backup-
            path = os.path.join(tmp, "skills", SKILL_NAME, "SKILL.md")
            first = open(path).read()
            # change the installed skill to something else
            open(path, "w").write("changed\n")
            restore_skill(root=tmp)
            restored = open(path).read()
        self.assertEqual(restored, first)

    # Rule 4: list backups.
    def test_list_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_skill(root=tmp)
            install_skill(root=tmp)
            backups = list_backups(root=tmp)
        self.assertEqual(len(backups), 1)
        self.assertIn(".backup-", backups[0])

    # Rule 5: restoring a never-installed state removes it (factory reset).
    def test_restore_to_zero_state_removes_skill(self):
        with tempfile.TemporaryDirectory() as tmp:
            # first install => zero-state backup (no prior file)
            install_skill(root=tmp)
            path = os.path.join(tmp, "skills", SKILL_NAME, "SKILL.md")
            # now "change" and restore to the zero state => file removed
            open(path, "w").write("changed\n")
            restore_skill(root=tmp)
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()