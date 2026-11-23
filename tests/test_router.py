"""Router tests with fake clients: no network, no keys. A fake client answers from a script of results (an Answer text or an exception)."""
try:
    import httpx2 as httpx      # openai 3.x / mcp 2.x ship their http client under this name
except ImportError:
    import httpx
import openai
import pytest

from openapi import Provider, load_key_file
from openapi import Router, RouterError

PA = Provider("alpha", "http://a", "T_ALPHA", ("big",))
PB = Provider("beta", "http://b", "T_BETA", ("small",))
PF = Provider("free", "http://f", "T_FREE", (), free_only=True)


def _resp(code, headers=None):
    return httpx.Response(code, headers=headers or {}, request=httpx.Request("POST", "http://x"))


class Fake:
    def __init__(self, models, script):
        self.script, self.calls = list(script), []
        outer = self
        class _M:
            async def list(self_):
                return type("P", (), {"data": [type("M", (), {"id": i}) for i in models]})()
        class _C:
            async def create(self_, **kw):
                outer.calls.append(kw)
                r = outer.script.pop(0)
                if isinstance(r, Exception):
                    raise r
                text, finish = r if isinstance(r, tuple) else (r, "stop")
                return type("R", (), {"choices": [type("Ch", (), {"message": type("Msg", (), {"content": text})(), "finish_reason": finish})()],
                                      "usage": type("U", (), {"total_tokens": 7})()})()
        self.models = _M()
        self.chat = type("Chat", (), {"completions": _C()})()


def mk(monkeypatch, fakes, providers, keys=None):
    for p in providers:
        monkeypatch.setenv(p.env, "k") if (keys is None or p.name in keys) else monkeypatch.delenv(p.env, raising=False)
    t = [0.0]
    r = Router(tuple(providers), lambda p: fakes[p.name], clock=lambda: t[0])
    return r, t


async def test_auto_prefers_hint_and_reports_usage(monkeypatch):
    fa = Fake(["tiny", "big-1"], ["hello"])
    r, _ = mk(monkeypatch, {"alpha": fa}, [PA])
    a = await r.ask("hi")
    assert (a.text, a.provider, a.model, a.tokens) == ("hello", "alpha", "big-1", 7)
    assert r.stats["alpha"].calls == 1 and r.stats["alpha"].tokens == 7


async def test_rate_limit_fails_over_then_rests(monkeypatch):
    rl = openai.RateLimitError("slow", response=_resp(429, {"retry-after": "5"}), body=None)
    fa, fb = Fake(["big"], [rl]), Fake(["small"], ["from-b", "again-b"])
    r, t = mk(monkeypatch, {"alpha": fa, "beta": fb}, [PA, PB])
    a = await r.ask("q")
    assert a.provider == "beta" and a.tried == ["alpha: rate limited"]
    b = await r.ask("q")                       # alpha still rests: not even tried
    assert b.provider == "beta" and "resting" in b.tried[0] and len(fa.calls) == 1
    assert r.resting("alpha") == 5.0


async def test_rested_provider_is_used_again(monkeypatch):
    rl = openai.RateLimitError("slow", response=_resp(429, {"retry-after": "5"}), body=None)
    fa, fb = Fake(["big"], [rl, "a-ok"]), Fake(["small"], ["b"])
    r, t = mk(monkeypatch, {"alpha": fa, "beta": fb}, [PA, PB])
    await r.ask("q")
    t[0] = 6.0
    assert (await r.ask("q")).provider == "alpha"


async def test_rejected_key_is_dropped_for_the_session(monkeypatch):
    bad = openai.AuthenticationError("no", response=_resp(401), body=None)
    fa, fb = Fake(["big"], [bad]), Fake(["small"], ["b1", "b2"])
    r, _ = mk(monkeypatch, {"alpha": fa, "beta": fb}, [PA, PB])
    assert (await r.ask("q")).provider == "beta"
    assert [p.name for p in r.configured()] == ["beta"]


async def test_explicit_model_does_not_fail_over(monkeypatch):
    rl = openai.RateLimitError("slow", response=_resp(429), body=None)
    fa, fb = Fake(["big"], [rl]), Fake(["small"], ["b"])
    r, _ = mk(monkeypatch, {"alpha": fa, "beta": fb}, [PA, PB])
    with pytest.raises(RouterError, match="alpha: rate limited"):
        await r.ask("q", "alpha/big")
    assert fb.calls == []


async def test_provider_slash_model_passes_the_model_through(monkeypatch):
    fa = Fake([], ["x"])
    r, _ = mk(monkeypatch, {"alpha": fa}, [PA])
    await r.ask("q", "alpha/org/name:free", system="s", max_tokens=5)
    assert fa.calls[0]["model"] == "org/name:free" and fa.calls[0]["max_tokens"] == 5
    assert fa.calls[0]["messages"][0] == {"role": "system", "content": "s"}


async def test_free_only_lists_just_free_models(monkeypatch):
    ff = Fake(["a/b", "c/d:free"], ["x"])
    r, _ = mk(monkeypatch, {"free": ff}, [PF])
    assert (await r.ask("q")).model == "c/d:free"


async def test_no_keys_and_unknown_provider(monkeypatch):
    r, _ = mk(monkeypatch, {}, [PA], keys=set())
    with pytest.raises(RouterError, match="no provider has a key"):
        await r.ask("q")
    with pytest.raises(RouterError, match="unknown provider"):
        await r.ask("q", "nope/x")


async def test_council_returns_every_result_even_failures(monkeypatch):
    fa, fb = Fake(["big"], ["one"]), Fake(["small"], [openai.APIConnectionError(request=httpx.Request("POST", "http://x"))])
    r, _ = mk(monkeypatch, {"alpha": fa, "beta": fb}, [PA, PB])
    out = await r.council("q", ["alpha", "beta"])
    assert out[0].text == "one" and isinstance(out[1], RouterError)


def test_key_file_must_be_private(tmp_path, monkeypatch):
    f = tmp_path / "k.env"
    f.write_text("# c\nexport T_KF1='abc'\nT_KF2=def\nbad line\n")
    f.chmod(0o644)
    with pytest.raises(PermissionError):
        load_key_file(f)
    f.chmod(0o600)
    monkeypatch.delenv("T_KF1", raising=False); monkeypatch.delenv("T_KF2", raising=False)
    monkeypatch.setenv("T_KF2", "keep")
    assert load_key_file(f) == 1
    import os
    assert os.environ["T_KF1"] == "abc" and os.environ["T_KF2"] == "keep"
    monkeypatch.delenv("T_KF1")


async def test_fixed_models_are_not_listed():
    from openapi import Provider, Router
    p = Provider("px", "http://x", "K", models=("sonar", "sonar-pro"))
    r = Router((p,), client_factory=lambda p: (_ for _ in ()).throw(AssertionError("must not ask the API")))
    assert await r.models(p) == ["sonar", "sonar-pro"]


async def test_answer_cut_by_token_limit_is_continued(monkeypatch):
    fa = Fake(["big"], [("part one, ", "length"), ("part two, ", "length"), "part three."])
    r, _ = mk(monkeypatch, {"alpha": fa}, [PA])
    a = await r.ask("q", max_tokens=5)
    assert a.text == "part one, part two, part three." and a.parts == 3 and not a.truncated
    last = fa.calls[-1]["messages"]                       # the continuation carries the text so far
    assert last[-2] == {"role": "assistant", "content": "part one, part two, "} and last[-1]["role"] == "user"
    assert r.stats["alpha"].calls == 3 and a.tokens == 21


async def test_continuation_stops_at_the_cap_and_says_so(monkeypatch):
    import openapi
    import router as router_mod       # the constant lives where it is read
    monkeypatch.setattr(router_mod, "CONTINUE_MAX", 2)
    fa = Fake(["big"], [("a", "length"), ("b", "length"), ("c", "length")])
    r, _ = mk(monkeypatch, {"alpha": fa}, [PA])
    a = await r.ask("q")
    assert a.text == "abc" and a.truncated and a.parts == 3
    assert "STILL TRUNCATED" in openapi._fmt(a)


async def test_finished_answer_makes_one_call(monkeypatch):
    fa = Fake(["big"], ["done"])
    r, _ = mk(monkeypatch, {"alpha": fa}, [PA])
    a = await r.ask("q")
    assert a.parts == 1 and not a.truncated and len(fa.calls) == 1


async def test_reasoning_model_with_empty_answer_is_retried_with_a_bigger_budget(monkeypatch):
    fa = Fake(["big"], [("", "length"), ("", "length"), "the answer"])
    r, _ = mk(monkeypatch, {"alpha": fa}, [PA])
    a = await r.ask("q", max_tokens=60)
    assert a.text == "the answer" and not a.truncated
    assert [c["max_tokens"] for c in fa.calls] == [60, 120, 240] and fa.calls[-1]["messages"] == fa.calls[0]["messages"]


async def test_local_server_is_the_last_resort_even_when_it_scores_higher(monkeypatch):
    monkeypatch.delenv("OPENAPI_LOCAL_FIRST", raising=False)
    loc = Provider("loc", "http://localhost:1/v1", "T_LOC", ("coder",))
    fakes = {"loc": Fake(["coder"], ["from local"]), "alpha": Fake(["tiny"], ["from alpha"])}
    r, _ = mk(monkeypatch, fakes, [loc, PA])
    a = await r.ask("hi")
    assert a.provider == "alpha"


async def test_chat_fails_over_and_skips_providers_that_cannot_take_tools(monkeypatch):
    fakes = {"alpha": Fake(["m"], [openai.RateLimitError("slow", response=_resp(429), body=None)]), "beta": Fake(["m"], ["from beta"])}
    r, _ = mk(monkeypatch, fakes, [PA, PB])
    resp, p, mid, tried = await r.chat([{"role": "user", "content": "x"}], tools=[{"type": "function", "function": {"name": "f", "parameters": {}}}])
    assert p.name == "beta" and resp.choices[0].message.content == "from beta"
    assert fakes["beta"].calls[0]["tools"][0]["function"]["name"] == "f" and any("alpha" in t for t in tried)


async def test_failover_matches_the_lean_spec_on_random_plans(monkeypatch):
    """verify/Failover.lean: the answer comes from the first provider that answers, every earlier one was skipped, none after it was called."""
    import random
    rng = random.Random(7)
    for _ in range(60):
        n = rng.randint(1, 6)
        ps = [Provider(f"p{i}", f"http://p{i}", f"T_P{i}", ()) for i in range(n)]
        ok = [rng.random() < 0.4 for _ in range(n)]
        fakes = {p.name: Fake(["m"], [f"from {p.name}"] if ok[i] else [openai.RateLimitError("slow", response=_resp(429), body=None)]) for i, p in enumerate(ps)}
        r, _ = mk(monkeypatch, fakes, ps)
        if not any(ok):
            with pytest.raises(RouterError):
                await r.ask("x")
            assert all(len(f.calls) == 1 for f in fakes.values())          # none_means_all_failed: every provider was tried
            continue
        first = ok.index(True)
        a = await r.ask("x")
        assert a.provider == f"p{first}"                                   # answer_is_first
        assert [len(fakes[f"p{i}"].calls) for i in range(n)] == [1] * (first + 1) + [0] * (n - first - 1)
        assert len(a.tried) <= n                                           # skipped_le


def test_free_filter_reads_price_and_availability():
    from types import SimpleNamespace as NS
    from router import _free
    mk_ = lambda id, **extra: NS(id=id, model_extra=extra)
    assert _free(mk_("x:free")) and _free(mk_("a", pricing={"input": 0, "output": 0})) and _free(mk_("b", pricing={"prompt": "0", "completion": "0"}))
    assert not _free(mk_("c", pricing={"input": 0, "output": 0.5})) and not _free(mk_("d")) and not _free(mk_("e", pricing={"input": 0, "output": 0}, available=False))


async def test_stream_survives_a_chunk_with_a_malformed_delta():
    from types import SimpleNamespace as NS
    from router import _stream
    async def chunks():
        for d in (NS(content="he"), [], None, NS(content="llo")):
            yield NS(choices=[NS(delta=d, finish_reason=None)], usage=None)
        yield NS(choices=[NS(delta=NS(content=None), finish_reason="stop")], usage=NS(total_tokens=4))
    r = await _stream(chunks())
    assert r.choices[0].message.content == "hello" and r.choices[0].finish_reason == "stop" and r.usage.total_tokens == 4


async def test_only_and_exclude_limit_the_pool(monkeypatch):
    fakes = {"alpha": Fake(["m"], ["from alpha"]), "beta": Fake(["m"], ["from beta"])}
    r, _ = mk(monkeypatch, fakes, [PA, PB])
    monkeypatch.setenv("OPENAPI_ONLY", "beta")
    assert [p.name for p in r.configured()] == ["beta"] and (await r.ask("x")).provider == "beta"
    monkeypatch.delenv("OPENAPI_ONLY")
    monkeypatch.setenv("OPENAPI_EXCLUDE", "alpha")
    assert [p.name for p in r.configured()] == ["beta"]


async def test_paid_only_providers_come_after_every_free_one_even_with_a_better_model(monkeypatch):
    rich = Provider("rich", "http://r", "T_RICH", ("gpt-5",), paid_only=True)
    fakes = {"rich": Fake(["gpt-5-pro"], ["from rich"]), "alpha": Fake(["tiny-1b"], ["from alpha"])}
    r, _ = mk(monkeypatch, fakes, [rich, PA])
    assert [p.name for p, _ in await r._order([(rich, None), (PA, None)], "general", True)] == ["alpha", "rich"]
    assert (await r.ask("x")).provider == "alpha" and not fakes["rich"].calls          # free first
    import openai
    fakes["alpha"].script = [openai.RateLimitError("slow", response=_resp(429), body=None)] * 3
    assert (await r.ask("y")).provider == "rich"                                       # paid is the next one in line, not an error
    monkeypatch.setenv("OPENAPI_FREE_ONLY", "1")
    r2, _ = mk(monkeypatch, {"rich": Fake(["gpt-5-pro"], ["no"]), "alpha": Fake(["tiny-1b"], [openai.RateLimitError("slow", response=_resp(429), body=None)] * 3)}, [rich, PA])
    assert [p.name for p, _ in await r2._order([(rich, None), (PA, None)], "general", False)][0] == "rich"   # free-only mode ranks by score alone: kept as it was
