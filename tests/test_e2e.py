"""End to end over real HTTP: a tiny local OpenAI-style server stands in for a provider, declared the way a user would (providers.toml + keys.env)."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from openapi import Provider, Router, load_key_file, set_key, user_providers


class H(BaseHTTPRequestHandler):
    refuse = {"bad-model"}

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(b))); self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._send(200, {"object": "list", "data": [{"id": i, "object": "model"} for i in ("bad-model", "good-model", "x/free:free")]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if self.headers.get("authorization") != "Bearer secret":
            return self._send(401, {"error": {"message": "bad key"}})
        if body["model"] in self.refuse:
            return self._send(404, {"error": {"message": "no such model for you"}})
        if body.get("stream"):   # the router always streams: two content chunks, then usage
            mk = lambda **d: "data: " + json.dumps({"id": "1", "object": "chat.completion.chunk", "created": 0, "model": body["model"], **d}) + "\n\n"
            out = (mk(choices=[{"index": 0, "delta": {"role": "assistant", "content": "pong "}}]) + mk(choices=[{"index": 0, "delta": {"content": "from " + body["model"]}, "finish_reason": "stop"}])
                   + mk(choices=[], usage={"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}) + "data: [DONE]\n\n").encode()
            self.send_response(200); self.send_header("content-type", "text/event-stream"); self.send_header("content-length", str(len(out))); self.end_headers()
            return self.wfile.write(out)
        self._send(200, {"id": "1", "object": "chat.completion", "created": 0, "model": body["model"],
                         "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "pong from " + body["model"]}}],
                         "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}})


@pytest.fixture
def server():
    s = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}/v1"
    s.shutdown()


def test_user_provider_file_and_key_file(tmp_path, monkeypatch, server):
    (tmp_path / "p.toml").write_text(f'[[provider]]\nname = "local"\nbase_url = "{server}"\nenv = "T_LOCAL_KEY"\nprefer = ["bad", "good"]\n')
    provs = user_providers(tmp_path / "p.toml")
    assert provs[-1].name == "local" and len(provs) > 8
    keys = tmp_path / "keys.env"
    set_key("T_LOCAL_KEY", "secret", keys)
    set_key("T_OTHER", "o", keys)
    set_key("T_LOCAL_KEY", "secret", keys)                      # rewriting replaces, never duplicates
    assert keys.read_text().count("T_LOCAL_KEY") == 1 and oct(keys.stat().st_mode & 0o777) == "0o600"
    monkeypatch.delenv("T_LOCAL_KEY", raising=False)
    load_key_file(keys)
    assert os.environ["T_LOCAL_KEY"] == "secret"
    monkeypatch.delenv("T_OTHER", raising=False)


async def test_real_http_survey_and_model_fallback(monkeypatch, server):
    monkeypatch.setenv("T_LOCAL_KEY", "secret")
    p = Provider("local", server, "T_LOCAL_KEY", ("bad", "good"))
    r = Router((p,))
    (row,) = await r.survey()
    assert (row.ok, row.models, row.listed) == (True, 3, 3)
    a = await r.ask("ping")                                     # best-ranked model is refused (404): the next one answers
    assert a.model == "good-model" and a.text == "pong from good-model" and any("bad-model: refused (404)" in t for t in a.tried)
    assert (await r.ask("ping")).tried == []                    # the refusal is remembered


async def test_real_http_wrong_key_is_reported(monkeypatch, server):
    monkeypatch.setenv("T_LOCAL_KEY", "wrong")
    r = Router((Provider("local", server, "T_LOCAL_KEY", ("good",)),))
    with pytest.raises(Exception, match="key rejected"):
        await r.ask("ping")
    assert r.configured() == []


async def test_survey_marks_unreachable_provider(monkeypatch):
    monkeypatch.setenv("T_DEAD_KEY", "k")
    (row,) = await Router((Provider("dead", "http://127.0.0.1:1/v1", "T_DEAD_KEY"),)).survey(timeout=5)
    assert row.has_key and not row.ok and row.error
