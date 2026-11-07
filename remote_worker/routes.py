"""The ways code can reach the sandbox, and come back from it, as data.

The tool has more than one route into a sandbox and the best one changes with
the time of day and with the user's own infrastructure (odd/tasks/
remote-worker-gaps.md L26-L29). Two measurements forced this module:

* The ceiling on the direct upload is the **account's fair use window over a
  time span**, not a size of the file. 1530 MiB was accepted on fresh quota;
  300 MiB was refused at 290 MiB hours later; 20 MiB went through right after.
  A local constant claiming a per-file ceiling would be a lie, so the service
  is the only authority and this module only explains its answer.
* Throughput is imposed by the service at ~1.1-1.6 MiB/s regardless of the
  user's own uplink (measured 51.86 Mbps up), so the wait is predictable.

The table is read by two sides on purpose: the CLI renders it in its error
messages, and the skill embeds the same rendered text so a local agent can
present the same menu. The CLI itself never asks the user to choose: it prints
the options, the human decides.

``RETURN_ROUTES`` is the same treatment for the trip back (S13). It is shorter
than the upload table because a sandbox is a filesystem with no memory of how
it was filled: one transport, several granularities, and one route closed on
purpose. Its ceilings say **not measured** because nobody measured them, and
an invented number there would be the same failure class as the deleted
1.5 GiB constant (S14).
"""

from __future__ import annotations

from typing import NamedTuple

#: The route works here today, with the limits in its own row.
STATUS_WORKS = "works"
#: The route is built but its limit was never observed against the real
#: service, so it must not be advertised as available (S11).
STATUS_UNVALIDATED = "not validated live"
#: The route is documented by Google and this tool does not implement it.
STATUS_NOT_BUILT = "not implemented here"
#: The route is closed on purpose: it could be built, and this tool will not.
#: It is listed so a reader finds the reason instead of wondering about it.
STATUS_REFUSED = "refused by design"

#: How recently the numbers below were measured, so a reader knows whether to
#: trust them or re-run the probe.
MEASURED_ON = "2026-11-06"

#: The wording the service uses when the account's upload window is spent.
#: Captured live, verbatim, twice (poc/g5_upload_ceiling.sh and the 300 MiB
#: run in /tmp/g5-timings-300.log).
SIZE_LIMIT_MARKER = "exceeds maximum allowed limit"


class Route(NamedTuple):
    """One way to get files into a sandbox, stated in the same fields for all."""

    id: str
    #: What the user types, so the row is actionable on its own.
    how: str
    ceiling: str
    speed: str
    needs: str
    status: str


ROUTES = (
    Route(
        id="repository-source",
        how="--repo <url>",
        ceiling="500 MB (documented)",
        speed="cloned by the sandbox itself: no bytes leave your machine",
        needs="the repository on a git host the sandbox can reach",
        status=STATUS_WORKS,
    ),
    Route(
        id="direct-upload",
        how="--local-repo <path>",
        # No size here on purpose: the limit is the account's window over a
        # time span, so any number would be wrong most of the day.
        ceiling="the account's fair use window at that moment",
        speed="~1.1-1.6 MiB/s imposed by the service: 500 MB is ~6 min",
        needs="nothing but the API key",
        status=STATUS_WORKS,
    ),
    Route(
        id="inline-files",
        how="--mount-file LOCAL:TARGET",
        ceiling="1 MB per file, 2 MB in total (documented)",
        speed="seconds, it rides along with the request",
        needs="text files only, listed one by one",
        status=STATUS_WORKS,
    ),
    Route(
        id="shards",
        how="the archive split into parts, reassembled in the sandbox",
        ceiling="the same fair use window: splitting does not raise it",
        speed="the same per-byte rate, plus one turn to reassemble",
        needs="an extra sandbox turn to concatenate and unpack the parts; "
              "Google does not document this route, so it is unproven",
        status=STATUS_UNVALIDATED,
    ),
    Route(
        id="gcs-source",
        how="a Cloud Storage bucket as the environment source",
        ceiling="2 GB (documented)",
        speed="~5 min for 1.5 GiB on a 51 Mbps uplink",
        needs="your own GCP project, bucket and gsutil; the transport differs, "
              "so it is the route that escapes the upload window",
        status=STATUS_NOT_BUILT,
    ),
)

#: The way back out of the sandbox, in the same fields as the way in (S13).
#
#: The symmetry with the upload table is a false friend and this table says so:
#: there is one transport out (the Environments files endpoint) because a
#: sandbox is a filesystem with no memory of how it was filled, so the axis
#: here is **granularity**, not where the files live. The last row is a route
#: this tool refuses, listed with its reason rather than left silent.
RETURN_ROUTES = (
    Route(
        id="workspace-archive",
        how="the whole workspace as one tar (what `run` does every time)",
        ceiling="not measured: no ceiling is documented and none was observed",
        speed="one request, whatever the size; the tar is read into memory",
        needs="a task that mounted a workspace, and a sandbox inside its "
              "7-day life",
        status=STATUS_WORKS,
    ),
    Route(
        id="workspace-listing",
        how="remote-worker fetch <task> --list [--path DIR]",
        ceiling="not measured: how many entries one call returns was never "
                "observed",
        speed="metadata only: no file content moves",
        needs="the environment id of a task this tool recorded",
        status=STATUS_WORKS,
    ),
    Route(
        id="single-file",
        how="remote-worker fetch <task> --path FILE [--out DEST]",
        ceiling="not measured: the size one file may come back at was never "
                "observed",
        speed="one request for that file alone",
        needs="the path as `--list` printed it; an existing local file needs "
              "--overwrite",
        status=STATUS_WORKS,
    ),
    Route(
        id="push-to-git-host",
        how="the sandbox pushing the result to your git host",
        ceiling="not applicable: no bytes travel this way here",
        speed="not measured, because this route is never taken",
        needs="a credential with write access to your repositories inside the "
              "sandbox, which this tool never puts there: the worker only "
              "ever receives read access, and git truth stays on your machine",
        status=STATUS_REFUSED,
    ),
)

_FIELD_ORDER = ("ceiling", "speed", "needs", "status")


def routes_by_id() -> dict:
    """The table keyed by id, for callers that need one row."""
    return {route.id: route for route in ROUTES}


def return_routes_by_id() -> dict:
    """The return table keyed by id, for callers that need one row."""
    return {route.id: route for route in RETURN_ROUTES}


def format_return_routes() -> str:
    """The way back out, rendered exactly like the way in."""
    width = max(len(route.how) for route in RETURN_ROUTES)
    lines = []
    for route in RETURN_ROUTES:
        lines.append(route.how.ljust(width).rstrip())
        for field in _FIELD_ORDER:
            lines.append(f"    {field}: {getattr(route, field)}")
        lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def alternatives_line(*, exclude: str | None = None) -> str:
    """The routes usable right now, as the short list an error message can quote.

    Only `works` rows appear: a route that is unvalidated or unimplemented is
    not an alternative, it is a plan.
    """
    usable = [route.how for route in ROUTES
              if route.status == STATUS_WORKS and route.id != exclude]
    return ", ".join(usable)


def format_routes() -> str:
    """The whole table as text: one block, read by `routes` and by the skill."""
    width = max(len(route.how) for route in ROUTES)
    lines = []
    for route in ROUTES:
        lines.append(route.how.ljust(width).rstrip())
        for field in _FIELD_ORDER:
            lines.append(f"    {field}: {getattr(route, field)}")
        lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def human_bytes(count: float) -> str:
    """A byte count in the unit a person reads it in."""
    for unit, size in (("MiB", 1024 ** 2), ("KiB", 1024)):
        if count >= size:
            return f"{count / size:.1f} {unit}"
    return f"{count:.0f} B"


def human_rate(bytes_per_second: float) -> str:
    """A throughput in the unit a person reads it in."""
    for unit, size in (("MiB", 1024 ** 2), ("kB", 1024)):
        if bytes_per_second >= size:
            return f"{bytes_per_second / size:.1f} {unit}/s"
    return f"{bytes_per_second:.0f} bytes/s"


def human_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m{seconds % 60:02d}s"


def is_size_limit(status: int, body: str) -> bool:
    """Whether a failed upload is the window being spent, not a broken request."""
    return status == 400 and SIZE_LIMIT_MARKER in body


def upload_failure_message(status: int, body: str,
                           environment_id: str | None = None,
                           *, released: bool = False) -> str:
    """Turn a refused upload into what the user can act on.

    The service says "File size exceeds maximum allowed limit", which reads
    like a property of the file and is not: it is the account's window. Saying
    so is the difference between a user waiting for a reset and a user
    rewriting a repository that was never the problem.

    ``environment_id`` names the sandbox the attempt created; ``released`` says
    whether this tool already deleted it. Either way the user must know, or a
    half-used sandbox holds his quota for its whole 7-day life.
    """
    hint = "`remote-worker routes` prints every route with its limits."
    if released:
        held = (f"The sandbox {environment_id} this attempt created was "
                "released again.")
    elif environment_id:
        held = (f"The sandbox {environment_id} this attempt created is still "
                "yours: `remote-worker clean --yes` releases it.")
    else:
        held = "`remote-worker clean` lists what your account still holds."
    if is_size_limit(status, body):
        return (
            'the sandbox refused the upload: the service answered 400 "File '
            'size exceeds maximum allowed limit". That is the account\'s fair '
            "use window for uploads over a time span, not a size this "
            "repository exceeded: the same account took 1530 MiB on fresh "
            "quota, refused 300 MiB at 290 MiB hours later, and accepted 20 "
            "MiB minutes after that. Nothing about the tree can be fixed "
            "here.\n"
            "Retry later: the window resets on Google's schedule, and the "
            "refusal above is what a spent window looks like. Routes that "
            f"move these bytes another way: "
            f"{alternatives_line(exclude='direct-upload')}. "
            f"{hint} {held}"
        )
    return (f"the sandbox refused the upload: the service answered {status} "
            f"{body}. {hint} {held}")
