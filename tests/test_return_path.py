"""S13/S14: the way back out of the sandbox is a table too, with honest rows.

The upload side got a table in v0.18.0. The return side was prose, and the
prose was asymmetric in a way that hid something: the upload has five routes
because it depends on *where the files live*, while the return has one
transport — the Environments files endpoint — because a sandbox is a filesystem
with no memory of how it was filled. The axis that actually needed populating
is **granularity** (odd/tasks/remote-worker-gaps.md L34).

Two things this file pins:

* The table says which granularities exist and which of them the CLI can take.
  `FilesClient.list_files` and single-file `download_to` were implemented and
  unreachable: built and buried (rules 1, tested for reach in
  tests/test_fetch_command.py).
* Nothing here invents a number. The download ceilings were **never measured**
  and say so in words — writing a size would be the same failure class as the
  deleted 1.5 GiB upload constant (S14).

And one rejection stated instead of left silent (L33, L36): the sandbox never
pushes the result to a git host, because that would put a write credential
inside it (AGENTS.md section 8).

Rules under test:

1. The return path is a table with the same fields as the upload one, every row
   carrying its real status, and `routes` prints both tables.
2. The rows that move bytes say `not measured` and state no size.
3. The git-host row is refused by design and says why, and a refused row is
   never offered as a way to work.
4. One table, three readers: README and the skill embed the rendered return
   table, and the README states the git-host refusal in prose.
"""

import io
import os
import re
import unittest
from contextlib import redirect_stdout
from unittest import mock

from remote_worker import routes
from remote_worker.google_client import FilesClient
from remote_worker.skill_installer import skill_content
from remote_worker import cli


class ReturnRouteTableTest(unittest.TestCase):
    """Rule 1: the way back is data with the same fields, read by the same command."""

    def test_every_row_states_its_ceiling_speed_needs_and_status(self):
        rows = routes.RETURN_ROUTES
        self.assertTrue(rows)
        for row in rows:
            for field in ("id", "how", "ceiling", "speed", "needs", "status"):
                self.assertTrue(getattr(row, field), f"{row.id}.{field} empty")

    def test_the_table_lists_the_granularities_that_exist(self):
        """One transport, several granularities, plus the route refused by design."""
        self.assertEqual(
            [row.id for row in routes.RETURN_ROUTES],
            ["workspace-archive", "workspace-listing", "single-file",
             "push-to-git-host"])

    def test_routes_prints_both_tables(self):
        """One command, one place to read what can go in and what can come out."""
        listing = cli.build_parser().parse_args(["routes"])
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(["routes"], runner=object())
        self.assertEqual(code, 0)
        body = out.getvalue()
        self.assertIn(routes.format_routes(), body)
        self.assertIn(routes.format_return_routes(), body)
        self.assertEqual(listing.command, "routes")

    def test_the_return_table_needs_no_key_and_no_call(self):
        """It is what you read to decide whether to spend quota: it cannot spend any."""
        with mock.patch.object(FilesClient, "list_files",
                               side_effect=AssertionError("network")):
            out = io.StringIO()
            with redirect_stdout(out):
                code = cli.main(["routes"], runner=None)
        self.assertEqual(code, 0, out.getvalue())

    # Rule 2: the unmeasured ceiling stays unmeasured.
    def test_the_download_ceiling_says_not_measured_instead_of_guessing(self):
        rendered = routes.format_return_routes()
        self.assertIn("not measured", rendered)
        for row in routes.RETURN_ROUTES:
            if row.id == "push-to-git-host":
                continue
            self.assertRegex(row.ceiling.lower(), r"not measured",
                             f"{row.id} states a ceiling nobody measured")
            self.assertFalse(
                re.search(r"\d+(\.\d+)?\s*(GiB|MiB|KiB|GB|MB|KB)", row.ceiling),
                f"{row.id} invented a size: {row.ceiling}")

    # Rule 3: the rejection is stated, with its reason.
    def test_the_git_host_route_is_refused_and_says_why(self):
        row = routes.return_routes_by_id()["push-to-git-host"]
        self.assertEqual(row.status, routes.STATUS_REFUSED)
        reason = f"{row.needs} {row.speed} {row.ceiling}".lower()
        self.assertIn("credential", reason)
        self.assertIn("never", reason)

    def test_a_refused_route_is_never_offered_as_a_way_to_work(self):
        """Only `works` rows may be quoted as an option (S11)."""
        self.assertNotIn("github", routes.alternatives_line().lower())
        usable = [row.id for row in routes.RETURN_ROUTES
                  if row.status == routes.STATUS_WORKS]
        self.assertNotIn("push-to-git-host", usable)



class ReturnTableIsReadByThreeTest(unittest.TestCase):
    """Rule 7: one table, three readers, and the refusal documented (S13)."""

    def test_the_skill_embeds_the_rendered_return_table(self):
        self.assertIn(routes.format_return_routes(), skill_content())

    def test_the_readme_embeds_the_rendered_return_table(self):
        self.assertIn(routes.format_return_routes(), self._readme())

    def test_the_readme_states_why_the_sandbox_never_pushes(self):
        """The rejection is documented with its reason, not left silent (L33)."""
        body = self._readme().lower()
        self.assertIn("never puts a write credential", body)
        self.assertIn("remote-worker fetch", body)

    def _readme(self):
        path = os.path.join(os.path.dirname(routes.__file__), "..", "README.md")
        with open(path, encoding="utf-8") as handle:
            return handle.read()



if __name__ == "__main__":
    unittest.main()
