"""proxy: the Anthropic <-> OpenAI translation (pure functions) and the HTTP endpoint over a fake router."""
import json
from types import SimpleNamespace as NS

import pytest
from starlette.testclient import TestClient

import proxy
from router import RouterError

TOOL_TURN = {
    "model": "claude-x", "max_tokens": 32000, "system": [{"type": "text", "text": "be brief"}],
    "tools": [{"name": "read", "description": "read a file", "input_schema": {"type": "object", "properties": {"p": {"type": "string"}}}}],
    "tool_choice": {"type": "any"},
    "messages": [
        {"role": "user", "content": "open a.txt"},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}, {"type": "tool_use", "id": "t1", "name": "read", "input": {"p": "a.txt"}},
                                          {"type": "tool_use", "id": "t2", "name": "read", "input": {"p": "b.txt"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "AAA"},
                                     {"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "BBB"}]},
                                     {"type": "text", "text": "now summarise"}]},
    ],
}


def test_request_translation_keeps_every_tool_id_in_order():
    kw = proxy.to_openai(TOOL_TURN)
    m = kw["messages"]
    assert m[0] == {"role": "system", "content": "be brief"}
    calls = [c["id"] for x in m if x["role"] == "assistant" for c in x.get("tool_calls", [])]
    results = [x["tool_call_id"] for x in m if x["role"] == "tool"]
    assert calls == results == ["t1", "t2"]                     # the invariant verify/Translate.lean proves for the model
    assert [x["role"] for x in m][-1] == "user" and m[-1]["content"] == "now summarise"
    assert kw["tool_choice"] == "required" and kw["tools"][0]["function"]["name"] == "read"
    assert kw["max_tokens"] == 8192                              # capped: free models refuse 32k


def test_response_translation_tool_use_and_stop_reasons():
    r = NS(choices=[NS(message=NS(content="hi", tool_calls=[NS(id="c1", function=NS(name="read", arguments='{"p":"x"}'))]), finish_reason="tool_calls")], usage=NS(total_tokens=7))
    msg = proxy.from_openai(r, "groq/m")
    assert [b["type"] for b in msg["content"]] == ["text", "tool_use"] and msg["content"][1]["input"] == {"p": "x"} and msg["stop_reason"] == "tool_use"
    cut = NS(choices=[NS(message=NS(content="x", tool_calls=None), finish_reason="length")], usage=NS(total_tokens=1))
    assert proxy.from_openai(cut, "m")["stop_reason"] == "max_tokens"
    bad = NS(choices=[NS(message=NS(content=None, tool_calls=[NS(id="c", function=NS(name="n", arguments="{oops"))]), finish_reason="stop")], usage=NS(total_tokens=0))
    assert proxy.from_openai(bad, "m")["content"][0]["input"] == {}   # broken JSON from a weak model must not crash the proxy


def test_sse_is_well_formed():
    msg = proxy.from_openai(NS(choices=[NS(message=NS(content="yo", tool_calls=None), finish_reason="stop")], usage=NS(total_tokens=2)), "m")
    ev = [e.split("\n")[0] for e in proxy.sse(msg)]
    assert ev == ["event: message_start", "event: content_block_start", "event: content_block_delta", "event: content_block_stop", "event: message_delta", "event: message_stop"]


class FakeRouter:
    def __init__(self, fail=False):
        self.fail, self.seen = fail, None

    async def chat(self, messages, model="auto", **kw):
        self.seen = (messages, model, kw)
        if self.fail:
            raise RouterError("all providers failed: a: HTTP 402")
        return NS(choices=[NS(message=NS(content="pong", tool_calls=None), finish_reason="stop")], usage=NS(total_tokens=3)), NS(name="fake"), "m1", []


def test_endpoint_json_stream_and_failure():
    fr = FakeRouter()
    c = TestClient(proxy.app(fr))
    j = c.post("/v1/messages", json={"model": "claude-sonnet", "max_tokens": 10, "messages": [{"role": "user", "content": "ping"}]}).json()
    assert j["content"][0]["text"] == "pong" and j["model"] == "fake/m1" and fr.seen[1] == "auto"
    s = c.post("/v1/messages", json={"model": "groq/llama", "stream": True, "max_tokens": 10, "messages": [{"role": "user", "content": "ping"}]})
    assert "message_stop" in s.text and fr.seen[1] == "groq/llama"
    bad = TestClient(proxy.app(FakeRouter(fail=True))).post("/v1/messages", json={"messages": [{"role": "user", "content": "x"}]})
    assert bad.status_code == 529 and bad.json()["error"]["type"] == "overloaded_error"
    assert c.post("/v1/messages", content=b"{nope").status_code == 400
    assert c.post("/v1/messages/count_tokens", json={"messages": [{"role": "user", "content": "abcd" * 10}]}).json()["input_tokens"] > 5
