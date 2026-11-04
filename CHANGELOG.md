# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) while
unstable: `0.x` may break freely.

## [0.6.0] — 2026-11-04

### Added

- `remote-worker setup` stores the default model and budget in a private
  `~/.remote-worker/config.json`, keeping a timestamped backup of any
  previous file. `run` now uses those defaults when no flag is given.
- Complete README: install, first run, command table, model floor, limits,
  security properties and development workflow.
- `tests/test_config.py`, `tests/test_cli.py` coverage for the stored
  defaults.

## [0.5.0] — 2026-11-04

### Added

- CLI (`remote-worker`) with `run`, `status`, `continue` and `cancel`.
- `remote_worker/planner.py`: model floor (`gemini-4-argon`,
  `gemini-3.8-flash`), explicit `max_total_tokens`, and the worker contract.
- `remote_worker/state.py`: private per-project task records, newest first.
- `remote_worker/runner.py`: glue from request to branch, diff and test run.

### Fixed

- A memory failure no longer aborts a task: the worker receives
  `{ok: false, error: …}` and can report that memory is unavailable.
- The clone directory lives under the state root, so a recorded task is
  resumable in a later session.
- `status` and `cancel` no longer require `GEMINI_API_KEY`.
- The default Engram project is the working directory, not the remote
  repository name.

## [0.4.0] — 2026-11-04

### Added

- `FilesClient`: list and download environment files, including a directory
  as a tar archive via the current endpoint.
- `sync.py`: `mount_repo`, `apply_to_branch`, `run_tests`.

### Fixed

- The sandbox's own `.git` and build artifacts are purged before the overlay;
  a root-owned `__pycache__` used to abort `git add`.

## [0.3.0] — 2026-11-04

### Added

- `engram_adapter.py`: local Engram callbacks with the project pinned by the
  orchestrator and an allowlist of memory tools.
- `function_loop.py`: bounded `requires_action` loop with explicit
  continuity (`previous_interaction_id`, `environment`, `agent`).

## [0.2.0] — 2026-11-04

### Added

- `google_client.py`: standard-library client for `POST /v1beta/interactions`,
  with `last_model_output` and `usage` helpers.

## [0.1.0] — 2026-11-04

### Added

- Repository base: working norms (`AGENTS.md`), product plan
  (`plan-remote-worker/`), and the proof-of-concept evidence with its logs
  (`poc/`).