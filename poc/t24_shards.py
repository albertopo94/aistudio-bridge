#!/usr/bin/env python3
"""T24, live: does the sharded upload route actually work?

The route the route table calls `shards`: split an archive into parts, upload
the parts one by one through the environments files endpoint, then reassemble
and unpack them **inside** the sandbox with one extra agent turn. Google does
not document it, and until one live run shows the parts arriving intact and
the tree coming out byte-identical, the table must not advertise it.

What this script proves, and what was already measured elsewhere: the route's
ceiling is the account's fair-use window, the same one the direct upload
spends (odd/tasks/remote-worker-gaps.md L29) -- splitting does not raise it.
The open question is not the limit, it is whether the technique works at all.

The measurement is deliberately small (three ~1 MiB files, ~3 MiB of parts):
it is a question about the *technique*, so it is asked with the cheapest
payload that can answer it. One verdict block comes out, at the end, always:

* how many parts and how many bytes, and how long the upload took;
* how long the single agent turn took, and what it cost in tokens;
* the agent's own report (its `sha256sum` output);
* the host's verification: the extracted tree downloaded back and hashed;
* any service error, verbatim.

The environment this script creates is deleted on every exit path, including
failure: a sandbox left behind holds account storage for its whole 7-day TTL.

Run it with no arguments::

    python3 poc/t24_shards.py

It reads the API key from ``~/.remote-worker/config.json`` (the file the CLI
writes) and never prints it. ``--model`` and ``--budget`` override the
config file: the run that records the numbers must run with exactly the
parameters the session authorizing it named.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from remote_worker.google_client import (  # noqa: E402  (path set above)
    EnvironmentsClient, FilesClient, InteractionsError, InteractionsClient,
    UploadClient, UploadError,
)
from remote_worker.planner import agent_config  # noqa: E402
from remote_worker.routes import (  # noqa: E402
    human_bytes, human_duration, human_rate,
)
from remote_worker.runner import AGENT  # noqa: E402

#: The CLI's own config file; the key never leaves this process.
CONFIG_PATH = os.path.expanduser("~/.remote-worker/config.json")
MODEL = "gemini-3.8-flash"
BUDGET = 250_000

#: Three parts, three files: enough parts that "in order" means something,
#: small enough that the whole run costs about a minute and a few MiB.
PART_COUNT = 3
FILE_BYTES = 1024 * 1024
FILE_NAMES = ("alpha.txt", "beta.txt", "gamma.txt")

PARTS_DIR = "workspace/parts"
ARCHIVE = "/workspace/archive.tar.gz"
EXTRACT_DIR = "/workspace/extracted"

#: The whole ask, stated once. It is a `system_instruction` in the product's
#: own shape (the worker contract lives there), and the input below points at
#: it; the agent has to do the three steps in the shell, not describe them.
INSTRUCTION = (
    "Do exactly this inside the sandbox, with the shell, and report the exact "
    "command output:\n"
    f"1. concatenate the uploaded parts, in order, into {ARCHIVE}. The parts "
    f"are /{PARTS_DIR}/part0, /{PARTS_DIR}/part1 and /{PARTS_DIR}/part2;\n"
    f"2. extract that archive into {EXTRACT_DIR};\n"
    "3. print the file list with the sha256sum of each file.\n"
    "Do not modify, re-upload or delete the parts."
)
INPUT = ("The parts are already uploaded. Run the procedure in the system "
         "instruction now and report its output.")


def build_tree(root: str) -> dict:
    """Three deterministic ~1 MiB files; returns ``{name: sha256}``.

    Deterministic on purpose: the same tree every run, so a mismatch points at
    the transport (or the agent) and never at the input.
    """
    os.makedirs(root, exist_ok=True)
    expected = {}
    for name in FILE_NAMES:
        rows, size, index = [], 0, 0
        while size < FILE_BYTES:
            row = (f"{name} {index:08d} "
                   f"{hashlib.sha256(f'{name}{index}'.encode()).hexdigest()}\n")
            rows.append(row)
            size += len(row)
            index += 1
        data = "".join(rows).encode()
        with open(os.path.join(root, name), "wb") as handle:
            handle.write(data)
        expected[name] = hashlib.sha256(data).hexdigest()
    return expected


def make_archive(tree: str, archive_path: str) -> bytes:
    """tar.gz of the tree, members at the archive root; returns the bytes."""
    with tarfile.open(archive_path, "w:gz") as tar:
        for name in FILE_NAMES:
            tar.add(os.path.join(tree, name), arcname=name)
    with open(archive_path, "rb") as handle:
        return handle.read()


def split(raw: bytes, count: int) -> list:
    """Cut the archive into ``count`` contiguous parts that rebuild it exactly."""
    step = (len(raw) + count - 1) // count
    parts = [raw[offset:offset + step] for offset in range(0, len(raw), step)]
    if b"".join(parts) != raw:  # pragma: no cover - arithmetic guard
        raise AssertionError("the parts do not rebuild the archive")
    return parts


def parse_sha_lines(report: str) -> dict:
    """``{name: sha256}`` from the ``sha256sum`` lines the agent printed."""
    found = {}
    for line in (report or "").splitlines():
        fields = line.strip().split()
        if len(fields) < 2:
            continue
        digest, name = fields[0], fields[-1].lstrip("*")
        if len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest):
            found[os.path.basename(name)] = digest
    return found


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise SystemExit(f"cannot read {CONFIG_PATH}: {exc}") from exc


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None,
                        help="agent_config model (default: the config file)")
    parser.add_argument("--budget", type=int, default=None,
                        help="max_total_tokens (default: the config file)")
    args = parser.parse_args(argv)

    config = load_config()
    api_key = config.get("api_key")
    if not api_key:
        raise SystemExit(f"no api_key in {CONFIG_PATH}")
    model = args.model or config.get("model") or MODEL
    budget = args.budget or int(config.get("budget") or BUDGET)

    environments = EnvironmentsClient(api_key=api_key)
    uploads = UploadClient(api_key=api_key)
    files = FilesClient(api_key=api_key)
    client = InteractionsClient(api_key=api_key)

    started = time.monotonic()
    facts = {
        "model": model,
        "parts": 0, "part_bytes": 0, "archive_bytes": 0,
        "upload_seconds": None, "upload_error": None,
        "environment_id": None, "create_seconds": None,
        "turn_seconds": None, "status": None, "tokens": None,
        "report": "", "turn_error": None,
        "expected": {}, "agent_hashes": {}, "host_hashes": {},
        "host_error": None, "listing": [], "released": None,
    }
    tmp = tempfile.mkdtemp(prefix="t24-shards.")
    verdict = 1
    try:
        tree = os.path.join(tmp, "tree")
        expected = build_tree(tree)
        facts["expected"] = expected
        archive_path = os.path.join(tmp, "archive.tar.gz")
        raw = make_archive(tree, archive_path)
        parts = split(raw, PART_COUNT)
        facts.update({"parts": len(parts), "part_bytes": len(raw),
                      "archive_bytes": len(raw)})
        print(f"tree: {len(expected)} files, {human_bytes(len(raw))} archived, "
              f"{len(parts)} parts of "
              f"{', '.join(human_bytes(len(p)) for p in parts)}",
              flush=True)

        print("creating the sandbox...", flush=True)
        mark = time.monotonic()
        environment = environments.create_environment()
        facts["create_seconds"] = time.monotonic() - mark
        facts["environment_id"] = environment.get("id")
        if not facts["environment_id"]:
            raise ValueError(f"no environment id in {environment!r}")
        print(f"sandbox {facts['environment_id']} ready in "
              f"{human_duration(facts['create_seconds'])}", flush=True)

        mark = time.monotonic()
        sent = 0
        try:
            for index, part in enumerate(parts):
                target = f"{PARTS_DIR}/part{index}"
                uploads.upload(environment_id=facts["environment_id"],
                               path=target, data=part)
                sent += len(part)
                print(f"uploaded {target} ({human_bytes(len(part))}, "
                      f"{human_bytes(sent)} of {human_bytes(len(raw))})",
                      flush=True)
        except UploadError as exc:
            facts["upload_error"] = f"{exc.status}: {exc.body}"
            raise
        facts["upload_seconds"] = time.monotonic() - mark

        mark = time.monotonic()
        try:
            interaction = client.create_interaction(
                agent=AGENT,
                input=INPUT,
                # The id as a BARE STRING, the documented form for "run in
                # this existing sandbox" (L44: an object without `type` is a
                # 400, and a {"id": ...} object does not exist in the API).
                environment=facts["environment_id"],
                agent_config=agent_config(model=model, budget=budget),
                system_instruction=INSTRUCTION,
                tools=[{"type": "code_execution"}],
            )
        except InteractionsError as exc:
            facts["turn_error"] = f"{exc.status}: {exc.body}"
            raise
        facts["turn_seconds"] = time.monotonic() - mark
        facts["status"] = interaction.get("status")
        facts["tokens"] = client.usage(interaction).get("total_tokens")
        facts["report"] = client.last_model_output(interaction)
        print(f"turn: status {facts['status']!r} in "
              f"{human_duration(facts['turn_seconds'])}, "
              f"{facts['tokens']} tokens", flush=True)

        try:
            facts["listing"] = [entry.get("path") for entry in
                                files.list_files(facts["environment_id"],
                                                 "workspace")]
            back = os.path.join(tmp, "back")
            os.makedirs(back, exist_ok=True)
            for name in FILE_NAMES:
                dest = os.path.join(back, name)
                files.download_to(facts["environment_id"],
                                  f"{EXTRACT_DIR}/{name}", dest,
                                  recursive=False)
                with open(dest, "rb") as handle:
                    facts["host_hashes"][name] = hashlib.sha256(
                        handle.read()).hexdigest()
        except InteractionsError as exc:
            facts["host_error"] = f"{exc.status}: {exc.body}"

        facts["agent_hashes"] = parse_sha_lines(facts["report"])
        verdict = 0 if all(
            facts["host_hashes"].get(name) == digest
            for name, digest in expected.items()
        ) else 1
    except (UploadError, InteractionsError, ValueError, OSError) as exc:
        if facts["upload_error"] is None and facts["turn_error"] is None:
            facts["host_error"] = facts["host_error"] or str(exc)
    finally:
        if facts["environment_id"]:
            try:
                environments.delete_environment(facts["environment_id"])
                facts["released"] = True
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                facts["released"] = f"failed: {exc}"
        shutil.rmtree(tmp, ignore_errors=True)
        print_verdict(facts, time.monotonic() - started, verdict)
    return verdict


def print_verdict(facts: dict, seconds: float, verdict: int) -> None:
    """The one block a human reads: what went up, what came back, what failed."""
    rule = "=" * 78
    expected = facts["expected"]
    if facts["upload_seconds"] is not None and facts["upload_seconds"] > 0:
        rate = facts["part_bytes"] / facts["upload_seconds"]
        upload = (f"{facts['parts']} parts, {human_bytes(facts['part_bytes'])}, "
                  f"{human_duration(facts['upload_seconds'])} at "
                  f"{human_rate(rate)}")
    else:
        upload = f"{facts['parts']} parts, {human_bytes(facts['part_bytes'])}, " \
                 "not uploaded"

    matches = sum(1 for name, digest in expected.items()
                  if facts["host_hashes"].get(name) == digest)
    headline = ("WORKS: the sandbox rebuilt the archive and the tree came back "
                "byte-identical" if verdict == 0 else
                "DID NOT WORK: see the evidence below")
    print(f"\n{rule}\nT24 verdict -- sharded upload (parts + one reassembly turn)"
          f"\n{rule}")
    print(f"verdict    : {headline}")
    print(f"upload     : {upload}")
    print(f"sandbox    : {facts['environment_id'] or '(not created)'}, "
          f"created in {human_duration(facts['create_seconds'] or 0)}")
    print(f"turn       : status {facts['status']!r}, "
          f"{human_duration(facts['turn_seconds'] or 0)}, "
          f"{facts['tokens']} tokens, model {facts['model']}")
    print(f"host check : {matches}/{len(expected)} files match the source "
          "-- downloaded back and hashed on this machine")
    for name, digest in expected.items():
        host = facts["host_hashes"].get(name)
        agent = facts["agent_hashes"].get(name)
        print(f"  {name:<12} source {digest[:16]}  host "
              f"{(host or 'MISSING')[:16]}  agent {(agent or 'not reported')[:16]}"
              f"  {'match' if host == digest else 'MISMATCH'}")
    if facts["listing"]:
        print(f"sandbox    : workspace holds "
              f"{', '.join(sorted(p for p in facts['listing'] if p))}")
    print(f"errors     : upload {facts['upload_error'] or 'none'} | "
          f"turn {facts['turn_error'] or 'none'} | "
          f"read-back {facts['host_error'] or 'none'}")
    print(f"cleanup    : sandbox deleted: {facts['released']}")
    print(f"total      : {human_duration(seconds)}")
    print("agent report (as printed by the sandbox):")
    report = (facts["report"] or "(none)").strip()
    for line in report.splitlines():
        print(f"  {line}")
    print(rule)


if __name__ == "__main__":
    sys.exit(main())