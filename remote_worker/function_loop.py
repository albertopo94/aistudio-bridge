"""Drive an interaction to completion, answering custom function calls.

The Interactions API stops a turn at ``status: "requires_action"`` when the
agent wants a function it cannot run itself (memory reads, for example).
This module answers those requests on the local machine and resumes the
same interaction through ``previous_interaction_id``.

Filesystem and code-execution calls are executed by the sandbox itself and
never reach this loop.
"""

from __future__ import annotations

MAX_STEPS_DEFAULT = 10

#: Statuses the service reports while an interaction is still working. There
#: is no ``queued`` status (API reference, Interaction resource).
RUNNING_STATUSES = ("in_progress", "requires_action")


class WatchLimitExceeded(RuntimeError):
    """The bounded watch of a background interaction ran out.

    This is the *watch* ending, not the task failing: the interaction may
    still complete while nobody is looking, so the caller keeps the task
    resumable.
    """


class LoopLimitExceeded(RuntimeError):
    """The agent kept requesting functions past the configured bound."""


class NoPendingCallsError(RuntimeError):
    """``requires_action`` arrived with no call left to answer."""


def pending_calls(interaction: dict) -> list[dict]:
    """Function calls in this interaction that have no result yet.

    The API echoes earlier steps back on every turn, including results the
    caller already supplied, so a call is only pending when no
    ``function_result`` carries its id.
    """
    steps = interaction.get("steps") or []
    answered = {
        step.get("call_id")
        for step in steps
        if step.get("type") == "function_result"
    }
    return [
        step for step in steps
        if step.get("type") == "function_call"
        and step.get("id") not in answered
    ]


def run_until_complete(client, dispatcher, interaction: dict, *,
                       max_steps: int = MAX_STEPS_DEFAULT,
                       on_event=None,
                       on_round=None,
                       system_instruction=None,
                       tools=None,
                       call_cache=None) -> dict:
    """Answer local function calls until the interaction stops asking.

    Returns the final interaction (``completed``, ``incomplete`` or any
    other terminal status). Raises :class:`LoopLimitExceeded` when the agent
    asks for more than ``max_steps`` rounds, and :class:`NoPendingCallsError`
    when it asks for action without naming a call.

    ``system_instruction`` and ``tools`` are the interaction-scoped
    parameters the caller sent on the initial call (the worker contract and
    the tool set). The API scopes them to one interaction, so every
    continuation must re-send them or the resumed turn runs without the
    contract and without the memory tools (G15). When omitted they are not
    sent, preserving the previous behaviour for callers that pass neither.

    ``on_round`` receives the interaction created by each round, so a caller
    can persist its id before the next round starts. A process killed between
    rounds then leaves a record naming the live conversation instead of a
    stale one (hardening S7).

    ``call_cache`` (D-1) makes a local call execute at most once per
    (interaction, call) across crashes: after the side effect the answer
    is stored under the pending call's own id, and a resumed round that
    re-reads the same interaction replays the stored answer instead of
    re-running the call. The cache is consulted before dispatch and
    written after it, so a call that raised is never cached and a fresh
    retry re-executes it.
    """
    steps_taken = 0

    while interaction.get("status") == "requires_action":
        calls = pending_calls(interaction)
        if not calls:
            raise NoPendingCallsError(
                "interaction requires action but has no pending function call"
            )
        if steps_taken >= max_steps:
            raise LoopLimitExceeded(
                f"agent requested more than {max_steps} function rounds"
            )

        results = []
        for call in calls:
            answer = None
            if call_cache is not None:
                answer = call_cache.get(interaction.get("id"),
                                        call.get("id"))
            if answer is None:
                answer = dispatcher.dispatch(call)
                if call_cache is not None:
                    call_cache.set(interaction.get("id"), call.get("id"),
                                   answer)
            results.append({
                "type": "function_result",
                "name": call.get("name"),
                "call_id": call.get("id"),
                "result": answer,
            })
            if on_event is not None:
                on_event(call, answer)

        interaction = client.create_interaction(
            agent=interaction.get("agent"),
            input=results,
            environment=interaction.get("environment_id"),
            previous_interaction_id=interaction.get("id"),
            system_instruction=system_instruction,
            tools=tools,
        )
        steps_taken += 1
        if on_round is not None:
            on_round(interaction)

    return interaction


def wait_until_done(client, dispatcher, interaction: dict, *,
                    poll_interval: float = 5.0,
                    max_polls: int = 360,
                    system_instruction=None,
                    tools=None,
                    on_event=None,
                    on_round=None,
                    call_cache=None) -> dict:
    """Follow a background interaction to a terminal status (G13).

    A background create returns while the work is still running, and the
    only way to follow it is reading the stored interaction back. Each read
    is one GET; a read that reports ``requires_action`` is answered through
    :func:`run_until_complete`, which re-sends the contract and the tools on
    its continuation (G15 has to hold inside the watch loop too), and the
    watch continues from the interaction that round trip returned.

    Raises :class:`WatchLimitExceeded` when ``max_polls`` reads still find
    the interaction running. ``poll_interval`` seconds separate the reads.
    """
    polls = 0
    while interaction.get("status") in RUNNING_STATUSES:
        if polls >= max_polls:
            raise WatchLimitExceeded(
                f"the interaction is still running after {max_polls} polls"
            )
        if polls:
            import time
            time.sleep(poll_interval)
        polls += 1
        interaction = client.get_interaction(interaction.get("id"))
        if interaction.get("status") == "requires_action":
            interaction = run_until_complete(
                client, dispatcher, interaction,
                system_instruction=system_instruction, tools=tools,
                on_event=on_event, on_round=on_round,
                call_cache=call_cache,
            )
    return interaction