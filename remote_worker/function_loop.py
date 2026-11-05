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
                       system_instruction=None,
                       tools=None) -> dict:
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
            answer = dispatcher.dispatch(call)
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

    return interaction