"""ModelClient adapters (MVP plan §5.2, whitepaper §11.1 T3 access, §18.4 item 4).

Three adapters implementing ``ontoforge.contracts.models.ModelClient``:

- HeuristicAdapter  — deterministic rule-based proposer; always available; zero tokens.
- CassetteAdapter   — JSON record/replay around any inner client: deterministic CI,
                      zero live calls in tests.
- AnthropicAdapter  — live frontier calls over raw HTTPS (urllib, no SDK); only
                      constructible when ANTHROPIC_API_KEY is set; never used in tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from typing import Any, Callable, Mapping, Optional

from ontoforge.contracts.models import ModelRequest, ModelResponse

# --------------------------------------------------------------------------
# Heuristic
# --------------------------------------------------------------------------


class HeuristicAdapter:
    """Dispatches ``req.task`` to a registered deterministic handler.

    Handlers take the ModelRequest and may return:
      * a ModelResponse (passed through unchanged),
      * a str (becomes ``text``; parsed as JSON when a schema was requested),
      * any JSON-serializable object (becomes ``parsed``; text is its JSON dump).

    Unknown tasks raise KeyError. Token counts are always zero (no model ran).
    """

    def __init__(self, handlers: Mapping[str, Callable[[ModelRequest], Any]]) -> None:
        self._handlers = dict(handlers)

    def propose(self, req: ModelRequest) -> ModelResponse:
        if req.task not in self._handlers:
            raise KeyError(f"no heuristic handler registered for task {req.task!r}")
        result = self._handlers[req.task](req)
        if isinstance(result, ModelResponse):
            return result
        if isinstance(result, str):
            parsed: Any = None
            if req.schema is not None:
                try:
                    parsed = json.loads(result)
                except ValueError:
                    parsed = None
            return ModelResponse(
                text=result, parsed=parsed, input_tokens=0, output_tokens=0, model_id="heuristic"
            )
        text = json.dumps(result, sort_keys=True, default=str)
        return ModelResponse(
            text=text, parsed=result, input_tokens=0, output_tokens=0, model_id="heuristic"
        )


# --------------------------------------------------------------------------
# Cassette (record/replay)
# --------------------------------------------------------------------------


def _cassette_key(req: ModelRequest) -> str:
    h = hashlib.sha256()
    for part in (req.task, req.prompt, req.schema if req.schema is not None else "\x00<no-schema>"):
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


class CassetteAdapter:
    """JSON-file cassette keyed by hash(task, prompt, schema).

    mode='record'  — requires ``inner``; every propose() goes to the inner client
                     and the response is persisted (overwriting any prior entry).
    mode='replay'  — hits are served from the cassette byte-identically (cached=True).
                     Misses: delegated to ``inner`` and recorded when present,
                     otherwise KeyError (deterministic CI: zero live calls).
    """

    def __init__(self, path: str, inner: Optional[Any] = None, mode: str = "replay") -> None:
        if mode not in ("replay", "record"):
            raise ValueError(f"mode must be 'replay' or 'record', got {mode!r}")
        if mode == "record" and inner is None:
            raise ValueError("record mode requires an inner ModelClient")
        self._path = path
        self._inner = inner
        self._mode = mode
        self._entries: dict[str, dict[str, Any]] = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                self._entries = json.load(f)

    def _persist(self) -> None:
        tmp = f"{self._path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._entries, f, sort_keys=True, indent=1)
        os.replace(tmp, self._path)

    def propose(self, req: ModelRequest) -> ModelResponse:
        key = _cassette_key(req)
        if self._mode == "replay" and key in self._entries:
            e = self._entries[key]
            return ModelResponse(
                text=e["text"],
                parsed=e["parsed"],
                input_tokens=e["input_tokens"],
                output_tokens=e["output_tokens"],
                model_id=e["model_id"],
                cached=True,
            )
        if self._inner is None:
            raise KeyError(
                f"cassette miss in replay mode with no inner client "
                f"(task={req.task!r}, key={key[:12]}…); record this exchange first"
            )
        resp = self._inner.propose(req)
        try:
            parsed_json = json.loads(json.dumps(resp.parsed))
        except (TypeError, ValueError):
            parsed_json = None
        self._entries[key] = {
            "task": req.task,
            "text": resp.text,
            "parsed": parsed_json,
            "input_tokens": resp.input_tokens,
            "output_tokens": resp.output_tokens,
            "model_id": resp.model_id,
        }
        self._persist()
        return resp


# --------------------------------------------------------------------------
# Anthropic (live; raw HTTPS, no SDK)
# --------------------------------------------------------------------------


class AnthropicAdapter:
    """Frontier T3 calls to api.anthropic.com via stdlib urllib (no SDK).

    Constructible ONLY when ANTHROPIC_API_KEY is present in the environment;
    construction performs no network I/O. Never exercised by the test suite.
    """

    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"
    DEFAULT_MODEL = "claude-sonnet-4-6"

    def __init__(self, model_id: str = DEFAULT_MODEL, timeout: float = 120.0) -> None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "AnthropicAdapter requires the ANTHROPIC_API_KEY environment variable; "
                "use HeuristicAdapter or CassetteAdapter when no live access is available"
            )
        self._api_key = key
        self.model_id = model_id
        self._timeout = timeout

    def propose(self, req: ModelRequest) -> ModelResponse:
        body: dict[str, Any] = {
            "model": self.model_id,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "messages": [{"role": "user", "content": req.prompt}],
        }
        if req.schema is not None:
            body["system"] = (
                "Respond ONLY with a JSON value conforming exactly to this JSON schema "
                "(no prose, no code fences):\n" + req.schema
            )
        request = urllib.request.Request(
            self.API_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": self.API_VERSION,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        parsed: Any = None
        if req.schema is not None:
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
        usage = data.get("usage", {})
        return ModelResponse(
            text=text,
            parsed=parsed,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            model_id=data.get("model", self.model_id),
        )
