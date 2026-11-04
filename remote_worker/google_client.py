"""HTTP client for the Gemini Interactions API (stdlib only).

Design notes (from plan-remote-worker/02, validated by the PoC):
- The API key travels ONLY in the ``x-goog-api-key`` header, never in the
  body and never in any error message.
- The agent's answer text lives in the last ``model_output`` step, not in
  a top-level ``output_text`` field (PoC finding).
- Rate limits (429) and API errors (4xx) are reported to the caller with
  the exact response body; there is no automatic retry.
- Timeouts surface as a clear error, never a hang.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"


class InteractionsError(RuntimeError):
    """The API answered with an error status (4xx/5xx).

    ``status`` is the HTTP status code; ``body`` is the raw response body
    (the exact error message from the API).
    """

    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"Interactions API error {status}: {body}")


class InteractionsTimeoutError(TimeoutError):
    """The request exceeded the client's timeout."""


class InteractionsClient:
    """Minimal client for ``POST /v1beta/interactions``."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 600.0):
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def create_interaction(self, *, agent: str, input: str,
                           environment="remote", agent_config=None,
                           tools=None, system_instruction=None,
                           previous_interaction_id=None) -> dict:
        """Create one interaction and return the parsed interaction dict.

        Only the declared fields are sent; ``None`` values are omitted so
        the request body stays exact and reviewable.
        """
        body = {"agent": agent, "input": input}
        if environment is not None:
            body["environment"] = environment
        if agent_config is not None:
            body["agent_config"] = agent_config
        if tools is not None:
            body["tools"] = tools
        if system_instruction is not None:
            body["system_instruction"] = system_instruction
        if previous_interaction_id is not None:
            body["previous_interaction_id"] = previous_interaction_id

        return self._post("/v1beta/interactions", body)

    def last_model_output(self, interaction: dict) -> str:
        """Return the text of the LAST ``model_output`` step.

        The agent answer is the last model_output step; earlier text
        blocks separated by tool calls are not part of the answer (PoC
        finding). Returns "" when the interaction has no model_output.
        """
        steps = interaction.get("steps", [])
        outputs = [s for s in steps if s.get("type") == "model_output"]
        if not outputs:
            return ""
        parts = outputs[-1].get("content", [])
        return "".join(
            p.get("text", "") for p in parts if isinstance(p, dict)
        )

    def usage(self, interaction: dict) -> dict:
        """Token counts for the interaction ({} when absent)."""
        return dict(interaction.get("usage", {}))

    # --- internals -------------------------------------------------------

    def _post(self, path: str, body: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self._base_url + path,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self._api_key,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except TimeoutError as exc:
            raise InteractionsTimeoutError(
                f"timeout: no response after {self._timeout}s calling {path}"
            ) from exc
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise InteractionsError(exc.code, raw) from exc
        except urllib.error.URLError as exc:
            # A URLError with a timeout cause (no exception object) vs.
            # a plain network error: both are reported with the cause.
            cause = str(exc.reason)
            if "timeout" in cause.lower():
                raise InteractionsTimeoutError(
                    f"timeout: no response after {self._timeout}s calling {path}"
                ) from exc
            raise InteractionsError(0, f"network error: {cause}") from exc
