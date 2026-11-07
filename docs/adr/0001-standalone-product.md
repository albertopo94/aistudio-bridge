# 0001 — aistudio-bridge is a standalone product, not part of agy-model-bridge

- Status: proposed — awaiting the user's sign-off (the decision is his; this
  record drafts it for review, per AGENTS.md §9)
- Date: decision 2026-11-04 · record drafted 2026-11-07

## Context

agy-model-bridge is a pure-stdlib, stateless model gateway: it forwards
model calls and holds no per-user state. The remote worker (aistudio-bridge)
is a different kind of workload: it authenticates with an API key, keeps
multi-turn sessions, creates and tears down sandboxes, and writes code diffs
back into local git.

Folding it into agy-model-bridge would force that gateway to carry state it
has no business holding — task records, environment ids, config, work
archives — and would couple two independent release cycles. The alternative
considered was building the remote worker as a module or subcommand of
agy-model-bridge, sharing its plumbing.

## Decision

aistudio-bridge is a **standalone product**, not inside agy-model-bridge.
agy-model-bridge keeps its promise as a "pure stdlib, stateless gateway of
models"; the remote worker is a separate stateful workload (auth by API key,
multi-turn sessions, sandbox, code diff). They can coexist without
depending on each other; if a unified surface is ever wanted,
agy-model-bridge will reference this product as an optional service.

## Consequences

- Two products, two changelogs, two state directories. Nothing is shared
  except the stdlib and, at most, an invocation boundary.
- aistudio-bridge may evolve its state model (tasks, environments, TTLs,
  undelivered) without touching the gateway, and vice versa.
- A user who wants both runs two CLIs; there is no shared config or session
  between them.
- Reverting this decision means moving the code into agy-model-bridge and
  re-deriving the state boundary; that cost grows with every version, so
  the decision is meant to be durable.
