"""Adapters for APIs that do not speak the OpenAI chat protocol.

Each adapter class looks like the slice of openai.AsyncOpenAI that openapi.py uses:
    client.models.list()                -> object with .data[i].id
    client.chat.completions.create(...) -> object with .choices[0].message.content and .usage.total_tokens
and raises openai's own status errors, so the router's failover logic is the same for every API.

Add one: write a class with those two calls, register it in ADAPTERS under the `api` name used in providers.
"""
from __future__ import annotations

import os
from types import SimpleNamespace as NS
from typing import Any, Callable

import openai

try:  # openai 3.x and mcp 2.x ship their http client as httpx2
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

_ERRORS = {400: openai.BadRequestError, 401: openai.AuthenticationError, 403: openai.PermissionDeniedError,
           404: openai.NotFoundError, 422: openai.UnprocessableEntityError, 429: openai.RateLimitError}


def raise_for(r: Any) -> None:
    """Turn an http error into the openai exception of the same status (what Router already handles)."""
    if r.status_code < 400:
        return
    cls = _ERRORS.get(r.status_code) or (openai.InternalServerError if r.status_code >= 500 else openai.APIStatusError)
    raise cls(r.text[:300], response=r, body=None)


def _reply(text: str, tokens: int) -> Any:
    return NS(choices=[NS(message=NS(content=text))], usage=NS(total_tokens=tokens))


def _split(msgs: list[dict]) -> tuple[str, list[dict]]:
    sys = "\n".join(m["content"] for m in msgs if m["role"] == "system")
    return sys, [m for m in msgs if m["role"] != "system"]


class _Base:
    def __init__(self, base_url: str, key: str, timeout: float = 60):
        self.base, self.key = base_url.rstrip("/"), key
        self.http = httpx.AsyncClient(timeout=timeout)
        self.models = NS(list=self._list)
        self.chat = NS(completions=NS(create=self._create))

    async def _send(self, method: str, url: str, **kw: Any) -> Any:
        try:
            r = await self.http.request(method, url, **kw)
        except httpx.TimeoutException as e:
            raise openai.APITimeoutError(request=e.request) from e
        except httpx.TransportError as e:
            raise openai.APIConnectionError(request=getattr(e, "request", None) or httpx.Request(method, url)) from e
        raise_for(r)
        return r.json()

    async def _list(self) -> Any:
        raise NotImplementedError

    async def _create(self, *, model: str, messages: list[dict], max_tokens: int | None = None, temperature: float | None = None, **_: Any) -> Any:
        raise NotImplementedError


class Anthropic(_Base):
    """api.anthropic.com/v1 - x-api-key header, /messages."""
    def _h(self) -> dict:
        return {"x-api-key": self.key, "anthropic-version": "2023-06-01"}

    async def _list(self) -> Any:
        d = await self._send("GET", f"{self.base}/models", headers=self._h(), params={"limit": 1000})
        return NS(data=[NS(id=m["id"]) for m in d.get("data", [])])

    async def _create(self, *, model, messages, max_tokens=None, temperature=None, **_):
        system, msgs = _split(messages)
        body: dict[str, Any] = {"model": model, "max_tokens": max_tokens or 8192, "messages": msgs}  # Anthropic requires one
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
        d = await self._send("POST", f"{self.base}/messages", headers=self._h(), json=body)
        u = d.get("usage", {})
        return _reply("".join(b.get("text", "") for b in d.get("content", [])), u.get("input_tokens", 0) + u.get("output_tokens", 0))


class Gemini(_Base):
    """generativelanguage.googleapis.com/v1beta - key in x-goog-api-key, :generateContent."""
    def _h(self) -> dict:
        return {"x-goog-api-key": self.key}

    async def _list(self) -> Any:
        d = await self._send("GET", f"{self.base}/models", headers=self._h(), params={"pageSize": 1000})
        return NS(data=[NS(id=m["name"].removeprefix("models/")) for m in d.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])])

    async def _create(self, *, model, messages, max_tokens=None, temperature=None, **_):
        system, msgs = _split(messages)
        gen: dict[str, Any] = {"maxOutputTokens": max_tokens} if max_tokens else {}
        if temperature is not None:
            gen["temperature"] = temperature
        body: dict[str, Any] = {"contents": [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]} for m in msgs], "generationConfig": gen}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        d = await self._send("POST", f"{self.base}/models/{model}:generateContent", headers=self._h(), json=body)
        parts = (d.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        return _reply("".join(p.get("text", "") for p in parts), d.get("usageMetadata", {}).get("totalTokenCount", 0))


class Cohere(_Base):
    """api.cohere.com/v2 - bearer key, /chat."""
    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"}

    async def _list(self) -> Any:
        d = await self._send("GET", f"{self.base.replace('/v2', '/v1')}/models", headers=self._h(), params={"endpoint": "chat", "page_size": 1000})
        return NS(data=[NS(id=m["name"]) for m in d.get("models", [])])

    async def _create(self, *, model, messages, max_tokens=None, temperature=None, **_):
        body: dict[str, Any] = {"model": model, "messages": messages}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        d = await self._send("POST", f"{self.base}/chat", headers=self._h(), json=body)
        t = d.get("usage", {}).get("tokens", {})
        return _reply("".join(c.get("text", "") for c in d.get("message", {}).get("content", [])), t.get("input_tokens", 0) + t.get("output_tokens", 0))


class Cloudflare(_Base):
    """Workers AI native /ai/run/{model}; base_url holds {CLOUDFLARE_ACCOUNT_ID}, filled from the environment."""
    def __init__(self, base_url: str, key: str, timeout: float = 60):
        acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        if not acct:
            raise openai.AuthenticationError("CLOUDFLARE_ACCOUNT_ID is not set", response=httpx.Response(401, request=httpx.Request("GET", base_url)), body=None)
        super().__init__(base_url.replace("{CLOUDFLARE_ACCOUNT_ID}", acct), key, timeout)

    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"}

    async def _list(self) -> Any:
        d = await self._send("GET", f"{self.base}/models/search", headers=self._h(), params={"task": "Text Generation", "per_page": 100})
        return NS(data=[NS(id=m["name"]) for m in d.get("result", [])])

    async def _create(self, *, model, messages, max_tokens=None, temperature=None, **_):
        body: dict[str, Any] = {"messages": messages}
        if max_tokens:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        d = await self._send("POST", f"{self.base}/run/{model}", headers=self._h(), json=body)
        res = d.get("result", {})
        return _reply(res.get("response") or "", res.get("usage", {}).get("total_tokens", 0))


ADAPTERS: dict[str, Callable[[str, str], Any]] = {"anthropic": Anthropic, "gemini": Gemini, "cohere": Cohere, "cloudflare": Cloudflare}
