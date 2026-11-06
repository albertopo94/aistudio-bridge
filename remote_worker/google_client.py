"""HTTP clients for the Gemini API (stdlib only).

Two small clients share one request path:

* :class:`InteractionsClient` — ``POST /v1beta/interactions``, the agent
  turns.
* :class:`FilesClient` — the Environments files endpoints, used to retrieve
  what the sandbox produced.

Behaviour the PoC proved is required, pinned here for both clients:

- The API key travels ONLY in the ``x-goog-api-key`` header: never in the
  body, never in a URL, never in an error message.
- The agent's answer text lives in the last ``model_output`` step, not in
  a top-level ``output_text`` field.
- Rate limits (429) and API errors (4xx) are reported to the caller with
  the exact response body; there is no automatic retry.
- Timeouts surface as a clear error, never a hang.

Diagnostics: with ``REMOTE_WORKER_TRACE`` set to a path, every request appends
one JSON line there (method, URL, request body, secrets masked). The request
itself is never modified or blocked by tracing, and a trace that cannot be
written is silently skipped: it exists to leave evidence for a human to read,
not to change what happens.
"""

from __future__ import annotations

import copy
import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"

#: Set to a file path to record every request the clients send.
TRACE_ENV_VAR = "REMOTE_WORKER_TRACE"

#: Substrings that mark a field as a credential once its name is normalised
#: (lower-cased, non-alphanumerics removed). The API key itself never reaches a
#: body -- it travels in a header the trace does not record -- but a worker
#: result echoed back into ``input`` can carry one.
_SECRET_HINTS = ("apikey", "token", "secret", "password", "credential",
                 "authorization", "privatekey")


def _is_secret_name(name: str) -> bool:
    normalised = "".join(ch for ch in name.lower() if ch.isalnum())
    return any(hint in normalised for hint in _SECRET_HINTS)


def _mask(value):
    """Deep-copy ``value`` with credential-shaped fields replaced."""
    if isinstance(value, dict):
        return {
            key: ("[masked]" if _is_secret_name(str(key))
                  else _mask(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_mask(item) for item in value]
    return value


def _trace_request(method: str, url: str, body) -> None:
    """Append one JSON line to ``REMOTE_WORKER_TRACE`` when it is set.

    Never raises: a broken, unwritable or missing trace path must not change
    what the product does, and must not lose the real request.
    """
    path = os.environ.get(TRACE_ENV_VAR)
    if not path:
        return
    try:
        record = {"method": method, "url": url}
        if isinstance(body, (dict, list)):
            record["body"] = _mask(copy.deepcopy(body))
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


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


def _send(method: str, url: str, api_key: str, timeout: float,
          data: bytes | None = None) -> bytes:
    """Perform one request and return the raw response body."""
    headers = {"x-goog-api-key": api_key}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=data, method=method, headers=headers
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.read()
    except TimeoutError as exc:
        raise InteractionsTimeoutError(
            f"timeout: no response after {timeout}s calling {url}"
        ) from exc
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise InteractionsError(exc.code, raw) from exc
    except urllib.error.URLError as exc:
        cause = str(exc.reason)
        if "timeout" in cause.lower():
            raise InteractionsTimeoutError(
                f"timeout: no response after {timeout}s calling {url}"
            ) from exc
        raise InteractionsError(0, f"network error: {cause}") from exc


def _quote_path(path: str) -> str:
    return urllib.parse.quote(path.strip("/"), safe="/")


class InteractionsClient:
    """Minimal client for ``POST /v1beta/interactions``."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 600.0):
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def create_interaction(self, *, agent: str, input,
                           environment="remote", agent_config=None,
                           tools=None, system_instruction=None,
                           previous_interaction_id=None) -> dict:
        """Create one interaction and return the parsed interaction dict.

        ``input`` is a string for a new turn, or a list of step dicts (for
        example ``function_result`` entries) when resuming an interaction.
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

        url = self._base_url + "/v1beta/interactions"
        _trace_request("POST", url, body)
        raw = _send(
            "POST", url,
            self._api_key, self._timeout, json.dumps(body).encode("utf-8"),
        )
        return json.loads(raw.decode("utf-8"))

    def stream_interaction(self, *, agent: str, input,
                           environment="remote", agent_config=None,
                           tools=None, system_instruction=None,
                           on_delta=None) -> dict:
        """Stream one interaction over SSE and return the final interaction.

        Sends `stream: true` and requests `alt=sse`. For each text delta the
        optional ``on_delta`` callback is invoked with the incremental text.
        Returns the interaction from the `interaction.completed` event (which
        carries the final `usage`). Raises `InteractionsError` on an `error`
        event.
        """
        body = {"agent": agent, "input": input, "stream": True}
        if environment is not None:
            body["environment"] = environment
        if agent_config is not None:
            body["agent_config"] = agent_config
        if tools is not None:
            body["tools"] = tools
        if system_instruction is not None:
            body["system_instruction"] = system_instruction

        data = json.dumps(body).encode("utf-8")
        sse_url = self._base_url + "/v1beta/interactions?alt=sse"
        _trace_request("POST", sse_url, body)
        request = urllib.request.Request(
            sse_url,
            data=data, method="POST",
            headers={"Content-Type": "application/json",
                     "x-goog-api-key": self._api_key},
        )
        try:
            resp = urllib.request.urlopen(request, timeout=self._timeout)
        except TimeoutError as exc:
            raise InteractionsTimeoutError(
                f"timeout: no response after {self._timeout}s streaming") from exc
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise InteractionsError(exc.code, raw) from exc

        final = None
        pending_data = ""
        with resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    pending_data = line[len("data:"):].strip()
                    if pending_data == "[DONE]":
                        break
                    ev = json.loads(pending_data)
                    if ev.get("event_type") == "step.delta":
                        delta = ev.get("delta", {})
                        if delta.get("type") == "text" and on_delta:
                            on_delta(delta.get("text", ""))
                    elif ev.get("event_type") == "interaction.completed":
                        final = ev.get("interaction")
                    elif ev.get("event_type") == "error":
                        err = ev.get("error", {})
                        raise InteractionsError(0, err.get("message", "error"))
        if final is None:
            raise InteractionsError(0, "stream ended without interaction.completed")
        return final

    def _interaction_url(self, interaction_id: str, verb: str = "") -> str:
        if not interaction_id:
            raise ValueError("interaction_id is required")
        url = (f"{self._base_url}/v1beta/interactions/"
               f"{urllib.parse.quote(interaction_id, safe='')}")
        return url + verb

    def get_interaction(self, interaction_id: str) -> dict:
        """Read one interaction back from the service (G18).

        The create response carries only the steps the model generated; the
        stored resource is the complete record, and it is the only way to
        learn the current status of work that is no longer in this process.
        An interaction past its retention comes back as a 404.
        """
        url = self._interaction_url(interaction_id)
        _trace_request("GET", url, None)
        raw = _send("GET", url, self._api_key, self._timeout)
        return json.loads(raw.decode("utf-8"))

    def cancel_interaction(self, interaction_id: str) -> dict:
        """Ask the service to stop an interaction (G12).

        The verb is a path segment (``/cancel``), not a ``:cancel`` custom
        verb, and the request carries no body. The service answers with the
        interaction, whose status becomes ``cancelled``. Only a running
        interaction can be cancelled; what the service reports for one that
        already finished is undocumented, so a caller must treat a failure as
        information rather than as a failed operation.
        """
        url = self._interaction_url(interaction_id, "/cancel")
        _trace_request("POST", url, None)
        raw = _send("POST", url, self._api_key, self._timeout)
        return json.loads(raw.decode("utf-8"))

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


class FilesClient:
    """Read-side client for the Environments files endpoints."""

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 300.0):
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _files_url(self, environment_id: str, path: str = "",
                   *, media: bool = False, recursive: bool = True) -> str:
        url = (
            f"{self._base_url}/v1beta/environments/"
            f"{urllib.parse.quote(environment_id, safe='')}/files"
        )
        if path.strip("/"):
            url += "/" + _quote_path(path)
        params = []
        if media:
            params.append("alt=media")
        if recursive:
            params.append("recursive=true")
        if params:
            url += "?" + "&".join(params)
        return url

    def list_files(self, environment_id: str, path: str = "",
                   recursive: bool = True) -> list[dict]:
        """Metadata for the entries in an environment directory."""
        raw = _send("GET", self._files_url(environment_id, path,
                                           recursive=recursive),
                    self._api_key, self._timeout)
        return json.loads(raw.decode("utf-8")).get("files", [])

    def download_to(self, environment_id: str, path: str, dest: str,
                    recursive: bool = True) -> int:
        """Download a directory (as tar) or a file to ``dest``.

        Returns the number of bytes written. The destination's directory is
        created if needed.
        """
        raw = _send("GET", self._files_url(environment_id, path,
                                           media=True, recursive=recursive),
                    self._api_key, self._timeout)
        parent = os.path.dirname(os.path.abspath(dest))
        os.makedirs(parent, exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(raw)
        return len(raw)