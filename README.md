# aistudio-bridge

Local orchestrator that delegates bounded code tasks to Google's Antigravity
managed agent (`antigravity-preview-09-2026`, Interactions API), keeps memory
local-first through Engram, and returns verified code changes to your local
git as a branch with a clean diff.

**State:** pre-code. The product plan lives in `plan-remote-worker/`; the
research evidence lives in `poc/`.

## Why

- Google hosts the sandbox, the agent loop, code execution, and web access.
  This project does not reimplement any of that.
- Memory never leaves your machine: the remote worker requests
  `memory_search` and the local process answers it against your local Engram.
- The worker never pushes to GitHub. Git truth stays on your machine; the
  remote result comes back as a tar, applied to a local branch, tested
  locally, and merged only when you decide.

## Layout

| Path | What it is |
|---|---|
| `remote_worker/` | Product code (from WU-1 onward) |
| `tests/` | Test suite (stdlib `unittest`, no third-party deps) |
| `plan-remote-worker/` | Product plan: evidence, architecture, step-by-step roadmap |
| `poc/` | Disposable bash scripts that proved the design (with JSON logs as evidence) |
| `docs/adr/` | Architecture Decision Records |
| `AGENTS.md` | Norms for every agent and human working on this repo |
| `investigacion-*.md` | Base research document (Spanish) |

## Requirements

- Python 3.10+ (standard library only — no third-party dependencies)
- macOS 13+ (Intel or Apple Silicon)
- `git` on PATH
- A Gemini API key (free tier works for research; see `plan-remote-worker/01-evidencia-poc.md`)

## Test command

```
python3 -m unittest discover -s tests
```

Focused:

```
python3 -m unittest tests.test_<module>
```

## Status

Version: see `VERSION`. Stability: unstable — `0.x` may break freely.
