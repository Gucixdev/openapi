"""Scoring: coder models win code, small ones win fast, junk kinds are dropped, auto picks the best provider, headers are remembered."""
import scoring
from openapi import Provider, Router

def test_code_prefers_coder():
    ids = ["llama-3.3-70b-versatile", "qwen3-coder-480b", "gpt-oss-120b"]
    assert scoring.rank(ids, "code")[0] == "qwen3-coder-480b"

def test_fast_prefers_small():
    assert scoring.rank(["llama-3.3-70b", "llama-3.1-8b-instant"], "fast")[0] == "llama-3.1-8b-instant"

def test_general_prefers_big():
    assert scoring.rank(["llama-3.1-8b-instant", "llama-3.3-70b"], "general")[0] == "llama-3.3-70b"

def test_junk_dropped():
    assert scoring.rank(["text-embedding-3", "whisper-large", "llama-3.3-70b", "llama-guard-3"]) == ["llama-3.3-70b"]

def test_unknown_keeps_order():
    assert scoring.rank(["zzz-1", "aaa-2"]) == ["zzz-1", "aaa-2"]

async def test_auto_orders_providers_by_best_model():
    weak, strong = Provider("weak", "http://w", "K_W"), Provider("strong", "http://s", "K_S")
    class C:
        def __init__(self, ids):
            self.models = type("M", (), {"list": staticmethod(self._l)})()
            self._ids = ids
        async def _l(self):
            return type("P", (), {"data": [type("M", (), {"id": i}) for i in self._ids]})()
    r = Router((weak, strong), client_factory=lambda p: C(["llama-3.1-8b"] if p.name == "weak" else ["qwen3-coder-480b"]))
    order = await r._order([(weak, None), (strong, None)], "code", False)
    assert [p.name for p, _ in order] == ["strong", "weak"]

def test_record_headers():
    scoring.record("x", {"x-ratelimit-remaining-tokens": "900", "x-ratelimit-limit-tokens": "1000"})
    assert "tok 900/1000" in scoring.describe("x")
    assert scoring.describe("nobody") == ""
