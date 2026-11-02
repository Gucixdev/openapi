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
                return type("R", (), {"choices": [type("Ch", (), {"message": type("Msg", (), {"content": r})()})()],
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
