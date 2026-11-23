"""router: pick provider and model, ask, fail over, continue a truncated answer, survey what is reachable."""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import openai
import privacy
from adapters import ADAPTERS
import rest
import scoring
from providers import Provider, user_providers


# ------------------------------------------------------------------ router: pick provider+model, ask, fail over
COOLDOWN = 60.0          # seconds a provider rests after a 429 without a retry-after
OUTAGE_COOLDOWN = 30.0
MODEL_TRIES = 3          # how many of a provider's models are tried before the next provider
CONTINUE_MAX = int(os.environ.get("OPENAPI_CONTINUE", "8"))   # extra calls to finish an answer cut by the token limit; 0 = never continue


class RouterError(Exception):
    pass


@dataclass
class Answer:
    text: str
    provider: str
    model: str
    tokens: int
    tried: list[str] = field(default_factory=list)     # "provider: why it was skipped", in order
    truncated: bool = False                            # still cut off after CONTINUE_MAX continuations
    parts: int = 1                                     # how many calls the text was stitched from


@dataclass
class Survey:
    provider: str
    has_key: bool
    ok: bool             # the endpoint listed its models
    models: int          # usable: after the free filter (OpenRouter), else all listed; 0 without a key
    listed: int          # everything the endpoint listed (a public catalog counts even without a key)
    ms: int
    error: str


@dataclass
class Stat:
    calls: int = 0
    tokens: int = 0
    errors: int = 0
    last_error: str = ""


def _factory(p: Provider) -> Any:
    if p.api != "openai":
        return ADAPTERS[p.api](p.base_url, p.key() or "none")
    if not p.env:   # keyless: send no Authorization header at all (some endpoints reject a bogus bearer)
        async def strip(req: Any) -> None:
            req.headers.pop("authorization", None)
        import httpx2
        return openai.AsyncOpenAI(base_url=p.base_url, api_key="none", max_retries=0, timeout=60, http_client=httpx2.AsyncClient(event_hooks={"request": [strip], "response": [_limits_hook(p.name)]}))
    import httpx2
    return openai.AsyncOpenAI(base_url=p.base_url, api_key=p.key() or "none", max_retries=0, timeout=60, http_client=httpx2.AsyncClient(timeout=60, event_hooks={"response": [_limits_hook(p.name)]}))


def _limits_hook(name: str) -> Any:
    async def hook(resp: Any) -> None:
        try:
            scoring.record(name, resp.headers)
        except Exception:   # bookkeeping must never break a call
            pass
    return hook


async def _stream(resp: Any) -> Any:
    """Read a streamed completion to the end; the result looks like a non-streamed one (.choices[0].message.content/.tool_calls, .usage.total_tokens)."""
    from types import SimpleNamespace as NS
    parts: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    tokens = n = 0
    finish = None
    async for ch in resp:
        n += 1
        d = ch.choices[0].delta if ch.choices else None      # a sloppy server sends delta as a list or null on some chunks: skip those, never crash
        if getattr(d, "content", None):
            parts.append(d.content)
        for tc in getattr(d, "tool_calls", None) or []:
            c = calls.setdefault(tc.index or 0, {"id": "", "name": "", "arguments": ""})
            c["id"] = tc.id or c["id"]
            if tc.function:
                c["name"] += tc.function.name or ""
                c["arguments"] += tc.function.arguments or ""
        if ch.choices and getattr(ch.choices[0], "finish_reason", None):
            finish = ch.choices[0].finish_reason
        if getattr(ch, "usage", None):
            tokens = ch.usage.total_tokens or 0
    tcs = [NS(id=c["id"], function=NS(name=c["name"], arguments=c["arguments"])) for _, c in sorted(calls.items())] or None
    return NS(choices=[NS(message=NS(content="".join(parts), tool_calls=tcs), finish_reason=finish)], usage=NS(total_tokens=tokens or n))


def _free(m: Any) -> bool:
    """A listed model is free when its id ends in :free (OpenRouter), or the listing prices input and output at zero and does not say it is unavailable (BlockRun)."""
    if m.id.endswith(":free"):
        return True
    extra = getattr(m, "model_extra", None) or (m if isinstance(m, dict) else {})
    pr = extra.get("pricing") or {}
    vals = [pr[k] for k in ("input", "output", "prompt", "completion") if k in pr]
    try:
        return bool(vals) and all(float(v) == 0 for v in vals) and extra.get("available", True) is not False
    except (TypeError, ValueError):
        return False


def _retry_after(e: openai.RateLimitError) -> float:
    try:
        return min(600.0, max(1.0, float(e.response.headers.get("retry-after", COOLDOWN))))
    except (AttributeError, TypeError, ValueError):
        return COOLDOWN


class Router:
    def __init__(self, providers: tuple[Provider, ...] | None = None, client_factory: Callable[[Provider], Any] = _factory, clock: Callable[[], float] = time.monotonic):
        self.providers = {p.name: p for p in (providers if providers is not None else user_providers())}
        self._factory, self._clock = client_factory, clock
        self._clients: dict[str, Any] = {}
        self._models: dict[str, list[str]] = {}
        self._full: dict[str, list[str]] = {}    # everything listed, before the free filter (paid models are used only when allowed)
        self._raw: dict[str, int] = {}           # models listed before the free filter
        self._bad: dict[str, set[str]] = {}      # provider -> models it listed but refused (not for this key/plan)
        self._rest: dict[str, float] = {}        # provider -> clock() until which it is skipped
        self._dead: set[str] = set()             # provider -> rejected our key this session
        self.stats: dict[str, Stat] = {n: Stat() for n in self.providers}

    def configured(self) -> list[Provider]:
        """Providers with a key (or none needed) that are not dead this session. OPENAPI_ONLY=a,b limits the pool to those names, OPENAPI_EXCLUDE=a,b drops them."""
        only = {x for x in os.environ.get("OPENAPI_ONLY", "").replace(" ", "").split(",") if x}
        out = {x for x in os.environ.get("OPENAPI_EXCLUDE", "").replace(" ", "").split(",") if x}
        return [p for p in self.providers.values() if p.key() and p.name not in self._dead and (not only or p.name in only) and p.name not in out]

    def resting(self, name: str) -> float:
        return max(0.0, self._rest.get(name, 0.0) - self._clock())

    def _client(self, p: Provider) -> Any:
        if p.name not in self._clients:
            self._clients[p.name] = self._factory(p)
        return self._clients[p.name]

    async def models(self, p: Provider, refresh: bool = False, paid: bool = False) -> list[str]:
        if p.models:
            self._raw[p.name] = len(p.models)
            return list(p.models)
        if refresh or p.name not in self._models:
            rows = (await self._client(p).models.list()).data
            ids = sorted(m.id for m in rows)
            self._raw[p.name] = len(ids)
            self._full[p.name] = ids
            self._models[p.name] = sorted(m.id for m in rows if _free(m)) if p.free_only else ids
        return self._full[p.name] if paid and p.name in self._full else self._models[p.name]

    @staticmethod
    def worth(p: Provider, mid: str, task: str = "general") -> float:
        """Score of one model for a task: the name heuristic (scoring.score) plus a small bonus for the provider's own `prefer` hints."""
        low = mid.lower()
        hint = next((len(p.prefer) - n for n, h in enumerate(p.prefer) if h in low), 0)
        return scoring.score(mid, task) + 0.5 * hint

    @staticmethod
    def ranked(p: Provider, ids: list[str], task: str = "general") -> list[str]:
        """The provider's models, best first for the task (embeddings, image and speech models dropped); ties keep the listed order."""
        keep = [(Router.worth(p, i, task), n, i) for n, i in enumerate(ids)]
        return [i for sc, _, i in sorted(keep, key=lambda t: (-t[0], t[1])) if sc > -500]

    @staticmethod
    def choose(p: Provider, ids: list[str], task: str = "general") -> str | None:
        return next(iter(Router.ranked(p, ids, task)), None)

    async def _order(self, plan: list[tuple[Provider, str | None]], task: str, paid: bool) -> list[tuple[Provider, str | None]]:
        """auto: best provider first = the one whose best model scores highest for the task. Providers whose list cannot be had keep their place at the end."""
        async def top(p: Provider) -> float:
            try:
                ids = await asyncio.wait_for(self.models(p, paid=paid), 8)
            except Exception:
                return -2000.0
            best = self.choose(p, [i for i in ids if i not in self._bad.get(p.name, ())], task)
            return self.worth(p, best, task) if best else -1500.0
        tops = await asyncio.gather(*(top(p) for p, _ in plan))
        # a local server is the last resort, not the first pick (small local models are junk for tools and code); OPENAPI_LOCAL_FIRST=1 ranks it like any other
        local = (lambda p: False) if os.environ.get("OPENAPI_LOCAL_FIRST") else (lambda p: p.base_url.startswith(("http://localhost", "http://127.0.0.1")))
        # tiers, not one score: free and free-tier providers by model score first, the paid-only ones after them (a strong paid model must not jump the queue)
        rank = lambda x: (local(x[0]), paid and x[0].paid_only)
        return [x for _, _, _, x in sorted(((rank(x), -t, n, x) for n, (t, x) in enumerate(zip(tops, plan))), key=lambda r: r[:3])]

    async def survey(self, timeout: float = 20.0) -> list[Survey]:
        """Ask every provider what it offers, all at once. Without a key the public catalog (if there is one) still counts as listed."""
        async def one(p: Provider) -> Survey:
            keyed = bool(os.environ.get(p.env)) if p.env else True
            t0 = self._clock()
            try:
                ids = await asyncio.wait_for(self.models(p, refresh=True), timeout)
            except Exception as e:  # any failure is a row in the table, not a crash
                return Survey(p.name, keyed, False, 0, 0, round((self._clock() - t0) * 1000), type(e).__name__ if keyed else "no key")
            return Survey(p.name, keyed, True, len(ids) if keyed else 0, self._raw.get(p.name, len(ids)), round((self._clock() - t0) * 1000), "")
        return list(await asyncio.gather(*(one(p) for p in self.providers.values())))

    def plan(self, spec: str) -> list[tuple[Provider, str | None]]:
        """"auto" / "groq" / "groq/llama-3.3-70b-versatile" -> an ordered list of (provider, model or None = its preferred ones)."""
        if spec in ("", "auto"):
            return [(p, None) for p in self.configured()]
        head, _, rest = spec.partition("/")
        if head in self.providers:
            p = self.providers[head]
            if not p.key():
                raise RouterError(f"{p.name}: no key (set {p.env})")
            return [(p, rest or None)]
        raise RouterError(f"unknown provider '{head}' (known: {', '.join(self.providers)}); use auto, provider or provider/model")

    async def ask(self, prompt: str, model: str = "auto", system: str | None = None, max_tokens: int | None = None, temperature: float | None = None,
                  task: str = "general", paid: bool | None = None, public: bool = False) -> Answer:
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        try:
            p, mid, r, tried = await self._failover(msgs, model, max_tokens, temperature, task, paid, public=public)
        except RouterError as e:
            # every normal provider failed: a clean plain question may still go to the suspicious ones (they are free capacity), nothing else does
            if public or model not in ("", "auto") or os.environ.get("OPENAPI_NO_SUSPICIOUS_FALLBACK") or not str(e).startswith("all providers failed"):
                raise
            try:
                p, mid, r, tried = await self._failover(msgs, model, max_tokens, temperature, task, paid, public=True, only_suspicious=True)
            except RouterError:
                raise e from None
            tried.insert(0, "normal providers all failed, so a suspicious one was used for this clean question")
        text = (r.choices[0].message.content or "") if r.choices else ""
        tokens = getattr(r.usage, "total_tokens", 0) or 0
        cut = bool(r.choices) and getattr(r.choices[0], "finish_reason", None) == "length"
        parts, budget = 1, max_tokens
        while cut and parts <= CONTINUE_MAX:
            if text:      # the model stopped at the token limit, not because it was done: hand it its own text back and ask for the rest
                follow = msgs + [{"role": "assistant", "content": text}, {"role": "user", "content": "Continue exactly where you stopped. No repetition, no preamble."}]
            else:         # nothing visible yet: a reasoning model spent the whole budget thinking, so ask again with twice the room
                follow, budget = msgs, (budget or 1024) * 2
            try:
                r2, _ = await self._first_model(p, [mid], follow, budget, temperature, tried, explicit=True)
            except openai.OpenAIError as e:
                tried.append(f"{p.name}/{mid}: continuation failed ({type(e).__name__})")
                break
            more = (r2.choices[0].message.content or "") if r2.choices else ""
            tokens += getattr(r2.usage, "total_tokens", 0) or 0
            text += more
            parts += 1
            cut = bool(r2.choices) and getattr(r2.choices[0], "finish_reason", None) == "length"
        st = self.stats[p.name]
        st.calls, st.tokens = st.calls + parts, st.tokens + tokens
        return Answer(text, p.name, mid, tokens, tried, truncated=cut, parts=parts)
        raise RouterError("all providers failed: " + "; ".join(tried))

    @staticmethod
    def _screen(plan: list[tuple[Provider, str | None]], msgs: list[dict], explicit: bool, public: bool, tools: bool, tried: list[str]) -> list[tuple[Provider, str | None]]:
        """Suspicious providers (unknown operator): only by name or with public=True, never with tools, and only when the prompt leaks nothing private."""
        if not any(p.suspicious for p, _ in plan):
            return plan
        bad = privacy.leaks("\n".join(str(m.get("content") or "") for m in msgs))
        keep = []
        for p, w in plan:
            if not p.suspicious:
                keep.append((p, w))
                continue
            if not (explicit or public):          # plain auto: left out silently, not an error and no noise in the footer
                continue
            why = "no tools for a suspicious provider" if tools else ("the prompt has " + ", ".join(bad)) if bad else ""
            if why:
                if explicit:
                    raise RouterError(f"{p.name} is suspicious: {why}")
                tried.append(f"{p.name}: {why}")
                continue
            keep.append((p, w))
        if not keep:
            raise RouterError("no provider left: " + "; ".join(tried or ["every provider was left out"]))
        return keep

    async def _cands(self, p: Provider, paid: bool, task: str) -> list[str]:
        """The models to try on one provider, best first; on a free_only provider the free ones come before its paid ones."""
        ok = [m for m in await self.models(p, paid=paid) if m not in self._bad.get(p.name, ())]
        if paid and p.free_only and p.name in self._models:
            free = set(self._models[p.name])
            return (self.ranked(p, [m for m in ok if m in free], task) + self.ranked(p, [m for m in ok if m not in free], task))[:MODEL_TRIES]
        return self.ranked(p, ok, task)[:MODEL_TRIES]

    async def _failover(self, msgs: list[dict], model: str, max_tokens: int | None, temperature: float | None, task: str, paid: bool | None,
                        extra: dict[str, Any] | None = None, public: bool = False, only_suspicious: bool = False) -> tuple[Provider, str, Any, list[str]]:
        """The failover loop: first (provider, model) that answers -> (provider, model id, raw response, what was skipped)."""
        paid = (not os.environ.get("OPENAPI_FREE_ONLY")) if paid is None else paid     # default: everything that is available, free first, paid last
        plan, explicit = self.plan(model), model not in ("", "auto")
        if not explicit and len(plan) > 1:
            plan = await self._order(plan, task, paid)
        if not plan:
            raise RouterError("no provider has a key: openapi init  (or set one of " + ", ".join(p.env for p in self.providers.values() if p.env) + ")")
        tried: list[str] = []
        if only_suspicious:
            plan = [(p, w) for p, w in plan if p.suspicious]
        plan = self._screen(plan, msgs, explicit, public, bool(extra), tried)
        for p, want in plan:
            if not explicit and self.resting(p.name):
                tried.append(f"{p.name}: resting {self.resting(p.name):.0f}s")
                continue
            if extra and p.api != "openai":
                tried.append(f"{p.name}: no tool support")
                continue
            try:
                cands = [want] if want else await self._cands(p, paid, task)
                if not cands:
                    tried.append(f"{p.name}: no models")
                    continue
                r, mid = await self._first_model(p, cands, msgs, max_tokens, temperature, tried, explicit=bool(want), extra=extra)
            except openai.AuthenticationError as e:
                self._dead.add(p.name)
                self._fail(p, f"key rejected ({e.status_code})", tried)
            except openai.RateLimitError as e:
                self._rest[p.name] = self._clock() + _retry_after(e)
                self._fail(p, "rate limited", tried)
            except (openai.APIConnectionError, openai.APITimeoutError):
                self._rest[p.name] = self._clock() + OUTAGE_COOLDOWN
                self._fail(p, "unreachable", tried)
            except openai.APIStatusError as e:
                if e.status_code == 402:      # payment required: it lists models it will not serve to this key - stop trying it this session
                    self._dead.add(p.name)
                if e.status_code >= 500:
                    self._rest[p.name] = self._clock() + OUTAGE_COOLDOWN
                self._fail(p, f"HTTP {e.status_code}", tried)
            else:
                return p, mid, r, tried
        raise RouterError("all providers failed: " + "; ".join(tried))

    async def chat(self, messages: list[dict], model: str = "auto", max_tokens: int | None = None, temperature: float | None = None,
                   tools: list[dict] | None = None, tool_choice: Any = None, task: str = "general") -> tuple[Any, Provider, str, list[str]]:
        """Full chat (messages, optional tools) with the same failover; returns the raw response (choices[0].message.content/.tool_calls, finish_reason, usage)."""
        extra = {k: v for k, v in (("tools", tools), ("tool_choice", tool_choice)) if v}
        p, mid, r, tried = await self._failover(messages, model, max_tokens, temperature, task, None, extra or None)
        self.stats[p.name].calls += 1
        return r, p, mid, tried

    async def _first_model(self, p: Provider, cands: list[str], msgs: list[dict], max_tokens: int | None, temperature: float | None, tried: list[str], explicit: bool, extra: dict[str, Any] | None = None) -> tuple[Any, str]:
        """Try the provider's candidate models in order: one it lists but refuses (404/400/403) is remembered as bad and the next is tried."""
        last: Exception | None = None
        for mid in cands:
            kw: dict[str, Any] = {"model": mid, "messages": msgs, **(extra or {})}
            if max_tokens:        # unset = the provider's own limit; streaming means a long answer never hits a read timeout
                kw["max_tokens"] = max_tokens
            if temperature is not None:
                kw["temperature"] = temperature
            try:
                c = self._client(p)
                if isinstance(c, openai.AsyncOpenAI):
                    return await _stream(await c.chat.completions.create(stream=True, stream_options={"include_usage": True}, **kw)), mid
                return await c.chat.completions.create(**kw), mid
            except (openai.NotFoundError, openai.BadRequestError, openai.PermissionDeniedError, openai.UnprocessableEntityError) as e:
                if explicit:
                    raise
                self._bad.setdefault(p.name, set()).add(mid)
                tried.append(f"{p.name}/{mid}: refused ({e.status_code})")
                last = e
        assert last is not None
        raise last

    def _fail(self, p: Provider, why: str, tried: list[str]) -> None:
        st = self.stats[p.name]
        st.errors, st.last_error = st.errors + 1, why
        tried.append(f"{p.name}: {why}")

    async def research(self, question: str, system: str | None = None, max_tokens: int | None = None) -> tuple[str, list[str], list[tuple[str, Answer | RouterError]]]:
        """A question for the suspicious providers, in parallel: scrubbed of anything private first, refused if anything is left. Returns (the question as sent,
        what was scrubbed, [(provider, Answer or error)]). Answers are leads, never facts."""
        clean, removed = privacy.scrub(question)
        left = privacy.leaks(clean)
        if left:
            raise RouterError("not sent, still private after scrubbing: " + ", ".join(left) + " (ask the idea, not your code)")
        names = [p.name for p in self.configured() if p.suspicious and not self.resting(p.name)]
        if not names:
            raise RouterError("no suspicious provider is available")
        res = await asyncio.gather(*(self.ask(clean, n, system, max_tokens) for n in names), return_exceptions=True)
        return clean, removed, list(zip(names, res))  # type: ignore[arg-type]

    async def council(self, prompt: str, models: list[str], system: str | None = None, max_tokens: int | None = None) -> list[Answer | RouterError]:
        """The same question to several models at once; each result is an Answer or the error that stopped that model."""
        return await asyncio.gather(*(self.ask(prompt, m, system, max_tokens) for m in models), return_exceptions=True)  # type: ignore[return-value]


def render(rows: list[Survey]) -> str:
    """The survey as a plain-text table with the totals line (used by `stats` and the stats tool)."""
    w = max([len(r.provider) for r in rows] + [8])
    out = [f"{'provider':<{w}}  {'key':<4} {'status':<14} {'usable':>6} {'listed':>6} {'ms':>6}"]
    for r in rows:
        status = ("ok" if r.has_key else "catalog only") if r.ok else ("no key" if not r.has_key else f"FAIL {r.error}")
        out.append(f"{r.provider:<{w}}  {'yes' if r.has_key else '-':<4} {status:<14} {r.models if r.ok else '-':>6} {r.listed if r.ok else '-':>6} {r.ms if r.ok or r.has_key else '-':>6}")
    keyed, live = [r for r in rows if r.has_key], [r for r in rows if r.ok and r.has_key]
    out += ["", f"{len(rows)} providers known, {len(keyed)} with a key, {len(live)} answering; {sum(r.models for r in live)} usable models ({sum(r.listed for r in live)} listed)",
            f"public catalogs without a key: {sum(r.listed for r in rows if r.ok and not r.has_key)} more models listed by {sum(1 for r in rows if r.ok and not r.has_key)} providers (need a key to use)"]
    if len(keyed) < len(rows):
        out.append("no key yet: " + ", ".join(r.provider for r in rows if not r.has_key) + "   (openapi init)")
    return "\n".join(out)


