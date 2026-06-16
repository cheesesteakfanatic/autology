"""ModelClient adapters (MVP plan §5.2, whitepaper §11.1 T3 access, §18.4 item 4).

Four adapters implementing ``ontoforge.contracts.models.ModelClient``:

- HeuristicAdapter     — deterministic rule-based proposer; always available; zero tokens.
- CassetteAdapter      — JSON record/replay around any inner client: deterministic CI,
                         zero live calls in tests.
- AnthropicAdapter     — live frontier calls over raw HTTPS (urllib, no SDK); only
                         constructible when ANTHROPIC_API_KEY is set; never used in tests.
- OpenAICompatAdapter  — live calls to any OpenAI-compatible chat/completions endpoint
                         (Kimi/Moonshot, Qwen/DashScope, OpenAI, …) over raw HTTPS; only
                         constructible when its api-key env var is set; never used in tests.

Both live adapters share a bounded-retry transport (``_post_json``) with a timeout and
short exponential backoff over transient HTTP/timeout errors, and raise a clear
``RuntimeError`` (never a bare urllib exception) when a call ultimately fails. The
keyless/deterministic path (HeuristicAdapter / CassetteAdapter replay) is untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
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
# Live-adapter transport: bounded retry over transient failures (shared)
# --------------------------------------------------------------------------

# HTTP statuses worth retrying: rate-limit + transient server errors.
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


def _is_retryable(exc: Exception) -> bool:
    """True for transient failures (timeouts, connection resets, 429/5xx)."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _RETRYABLE_STATUS
    if isinstance(exc, urllib.error.URLError):
        # URLError wraps socket-level failures (timeouts, refused, DNS, reset).
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return False


def _post_json(
    url: str,
    body: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: float,
    max_retries: int,
    backoff: float,
    sleep: Callable[[float], None],
    label: str,
) -> dict[str, Any]:
    """POST ``body`` as JSON and decode the JSON reply.

    Retries up to ``max_retries`` extra times on transient HTTP/timeout errors
    with deterministic exponential backoff (``backoff * 2**attempt`` seconds,
    no jitter). On final failure raises a clean ``RuntimeError`` carrying the
    underlying cause — a bare urllib exception never escapes. Non-retryable
    HTTP errors (e.g. 400/401/403) fail fast on the first attempt.

    ``sleep`` is injected so tests stay instant and never touch the wall clock.
    """
    payload = json.dumps(body).encode("utf-8")
    last_exc: Optional[Exception] = None
    attempts = max(0, max_retries) + 1
    for attempt in range(attempts):
        request = urllib.request.Request(
            url, data=payload, headers=dict(headers), method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 — re-raised below as RuntimeError
            last_exc = exc
            if attempt + 1 < attempts and _is_retryable(exc):
                sleep(backoff * (2**attempt))
                continue
            break
    raise RuntimeError(
        f"{label} request to {url} failed after {attempts} attempt(s): "
        f"{type(last_exc).__name__}: {last_exc}"
    ) from last_exc


# --------------------------------------------------------------------------
# Anthropic (live; raw HTTPS, no SDK)
# --------------------------------------------------------------------------


class AnthropicAdapter:
    """Frontier T3 calls to api.anthropic.com via stdlib urllib (no SDK).

    Constructible ONLY when ANTHROPIC_API_KEY is present in the environment;
    construction performs no network I/O. Never exercised by the test suite.
    ``propose`` has a timeout and a small bounded retry over transient
    HTTP/timeout errors; on final failure it raises a clean ``RuntimeError``
    (no bare urllib exception escapes).
    """

    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"
    DEFAULT_MODEL = "claude-sonnet-4-6"

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        timeout: float = 120.0,
        max_retries: int = 2,
        backoff: float = 0.5,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "AnthropicAdapter requires the ANTHROPIC_API_KEY environment variable; "
                "use HeuristicAdapter or CassetteAdapter when no live access is available"
            )
        self._api_key = key
        self.model_id = model_id
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff
        # Lazy import keeps the module import-time free of the time module's
        # only real use here; injectable so tests never touch the wall clock.
        if sleep is None:
            import time

            sleep = time.sleep
        self._sleep = sleep

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
        data = _post_json(
            self.API_URL,
            body,
            headers={
                "content-type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": self.API_VERSION,
            },
            timeout=self._timeout,
            max_retries=self._max_retries,
            backoff=self._backoff,
            sleep=self._sleep,
            label="AnthropicAdapter",
        )
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


# --------------------------------------------------------------------------
# OpenAI-compatible (live; raw HTTPS, no SDK) — Kimi/Moonshot, Qwen, …
# --------------------------------------------------------------------------


class OpenAICompatAdapter:
    """Live T3 calls to any OpenAI-compatible ``/chat/completions`` endpoint.

    One adapter for every OpenAI-shaped provider: pass the provider's
    ``base_url`` (its OpenAI-compatible root, e.g.
    ``https://api.moonshot.cn/v1`` for Kimi/Moonshot or
    ``https://dashscope.aliyuncs.com/compatible-mode/v1`` for Qwen), the
    ``model_id`` to invoke, and the NAME of the environment variable that holds
    the api key (``api_key_env``). Construction reads that env var and raises a
    clear ``RuntimeError`` when it is absent, so the adapter is never silently
    active in the keyless/offline default. Construction does NO network I/O.

    When ``req.schema`` is set the request asks for JSON output via the
    ``response_format`` field (``{"type": "json_object"}``); providers that
    ignore it still work — we parse the returned text best-effort either way.
    ``propose`` has a timeout and a small bounded retry over transient
    HTTP/timeout errors and raises a clean ``RuntimeError`` on final failure.
    """

    def __init__(
        self,
        base_url: str,
        model_id: str,
        api_key_env: str,
        timeout: float = 120.0,
        max_retries: int = 2,
        backoff: float = 0.5,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        if not base_url:
            raise ValueError("OpenAICompatAdapter requires a non-empty base_url")
        if not model_id:
            raise ValueError("OpenAICompatAdapter requires a non-empty model_id")
        if not api_key_env:
            raise ValueError(
                "OpenAICompatAdapter requires api_key_env (the NAME of the env var "
                "holding the api key, e.g. 'MOONSHOT_API_KEY' or 'DASHSCOPE_API_KEY')"
            )
        key = os.environ.get(api_key_env)
        if not key:
            raise RuntimeError(
                f"OpenAICompatAdapter requires the {api_key_env} environment variable; "
                "use HeuristicAdapter or CassetteAdapter when no live access is available"
            )
        self._api_key = key
        self._api_key_env = api_key_env
        self.base_url = base_url.rstrip("/")
        self.url = f"{self.base_url}/chat/completions"
        self.model_id = model_id
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff = backoff
        if sleep is None:
            import time

            sleep = time.sleep
        self._sleep = sleep

    def propose(self, req: ModelRequest) -> ModelResponse:
        messages: list[dict[str, str]] = []
        if req.schema is not None:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Respond ONLY with a JSON value conforming exactly to this JSON "
                        "schema (no prose, no code fences):\n" + req.schema
                    ),
                }
            )
        messages.append({"role": "user", "content": req.prompt})
        body: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        if req.schema is not None:
            # JSON mode; providers that don't support it ignore the field.
            body["response_format"] = {"type": "json_object"}
        data = _post_json(
            self.url,
            body,
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {self._api_key}",
            },
            timeout=self._timeout,
            max_retries=self._max_retries,
            backoff=self._backoff,
            sleep=self._sleep,
            label="OpenAICompatAdapter",
        )
        choices = data.get("choices") or []
        text = ""
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            text = content if isinstance(content, str) else ""
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
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            model_id=data.get("model", self.model_id),
        )
