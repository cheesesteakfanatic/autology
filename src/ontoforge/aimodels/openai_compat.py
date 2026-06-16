"""OpenAICompatAdapter — live calls to any OpenAI-compatible chat endpoint.

Kimi/Moonshot, Qwen (DashScope-compatible), and OpenAI itself all expose the same
``POST {base_url}/chat/completions`` shape, so ONE adapter behind the frozen
``ModelClient`` seam covers all of them — the only difference is ``base_url`` +
``model_id`` + the API key, all supplied at construction by
:mod:`ontoforge.aimodels.activation` from env.

Like ``AnthropicAdapter`` this uses stdlib ``urllib`` (no SDK), performs NO network
I/O at construction, and is NEVER exercised by the test suite over the wire (tests
inject a mock/cassette inner). It is constructed ONLY when both a key and a base URL
are present; activation refuses to build it otherwise (lazy-user fail-safe to
keyless).
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from ontoforge.contracts.models import ModelRequest, ModelResponse

__all__ = ["OpenAICompatAdapter"]


class OpenAICompatAdapter:
    """Frontier T2/T3 calls to an OpenAI-compatible ``/chat/completions`` endpoint.

    ``base_url`` is the provider API root (e.g. ``https://api.moonshot.cn/v1``),
    ``api_key`` the bearer token, ``model_id`` the model name. Construction does no
    network I/O; a missing key/base raises so activation can fall back to keyless.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model_id: str,
        *,
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise RuntimeError("OpenAICompatAdapter requires an API key")
        if not base_url:
            raise RuntimeError("OpenAICompatAdapter requires a base_url")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model_id = model_id
        self._timeout = timeout

    @property
    def url(self) -> str:
        return f"{self._base_url}/chat/completions"

    def propose(self, req: ModelRequest) -> ModelResponse:
        messages: list[dict[str, str]] = []
        if req.schema is not None:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "Respond ONLY with a JSON value conforming exactly to this "
                        "JSON schema (no prose, no code fences):\n" + req.schema
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
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {self._api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choices = data.get("choices", [])
        text = ""
        if choices:
            text = choices[0].get("message", {}).get("content", "") or ""
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
