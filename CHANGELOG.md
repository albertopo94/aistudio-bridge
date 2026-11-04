# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) while
unstable: `0.x` may break freely.

## [0.9.0] — 2026-11-04

### Added

- `setup` stores the Gemini API key in the private config file (0600):
  prompts once with hidden input, or accepts `--api-key`. Re-running setup
  without a key keeps the stored one.
- `run`/`ask`/`continue` resolve the key in order: `GEMINI_API_KEY`
  environment, then the stored config. A new user needs **no** `.env` and no
  shell exports.

### Changed

- README rewritten for the zero-friction first run: setup asks for the key,
  the model switching section explains how to alternate between
  `gemini-4-argon` and `gemini-3.8-flash`, and a new run-vs-ask table
  explains which mode fits which task.

## [0.8.0] — 2026-11-04

### Added

- Live streaming (SSE) for `remote-worker ask --stream`: the worker's text
  is printed as it is generated instead of waiting silently for the full
  round trip. Verified against the real API. Non-streaming calls are
  unchanged.

## [0.7.0] — 2026-11-04

### Added

- `remote-worker ask "<task>"`: delegate a NON-code task (research, analysis,
  generation) and get the worker's text back. No repository is mounted, no
  workspace is downloaded; memory callbacks still resolve locally. This is
  the mode for offloading token cost from the local model to Google's free
  tier, or simply using the agent for everything it can do.
- Skill installer: `remote-worker setup --install-skill` writes a
  gentle-shell-compatible skill (delegation rubric included) into
  `~/.agents/skills/remote-worker/SKILL.md`, with timestamped backups, a
  zero-state marker for the first install, `--list-skill-backups` and
  `--restore-skill` (factory reset when restoring the zero state). The
  backup/restore/factory-reset shape mirrors agy-model-bridge.
- TODO (documented in the plan): `ask` with an optional mounted repo
  (analysis without diff), multi-agent support (codex, claude-code,
  opencode), streaming SSE, private repositories, ADR 0001.

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