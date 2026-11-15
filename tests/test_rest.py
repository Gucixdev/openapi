"""rest.py against a local HTTP server standing in for a service: auth placement, refusal without --yes, host escape, user rows."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import rest

seen: list[dict] = []


class H(BaseHTTPRequestHandler):
    def _do(self):
        n = int(self.headers.get("content-length") or 0)
        seen.append({"m": self.command, "p": self.path, "h": dict(self.headers), "b": self.rfile.read(n).decode()})
        out = json.dumps({"ok": True, "pad": "x" * 20000}).encode()      # a big body must come back whole
        self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)
    do_GET = do_POST = do_DELETE = _do
    def log_message(self, *a): pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    seen.clear()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def svc(base, auth="bearer", **kw):
    return {"name": "t", "base_url": base, "env": "T_KEY", "auth": auth, **kw}


async def test_get_bearer_whole_body(server, monkeypatch):
    monkeypatch.setenv("T_KEY", "sekret")
    r = await rest.call(svc(server + "/v1"), "get", "/servers", {"page": "2"})
    assert r.sent and r.status == 200 and len(r.text) > 20000
    assert seen[0]["p"] == "/v1/servers?page=2" and seen[0]["h"]["Authorization"] == "Bearer sekret"
    assert "sekret" not in r.request


async def test_write_is_not_sent_without_yes(server, monkeypatch):
    monkeypatch.setenv("T_KEY", "k")
    r = await rest.call(svc(server), "POST", "/servers", data='{"name":"x"}')
    assert not r.sent and not seen and '"name": "x"' in r.request
    r = await rest.call(svc(server), "POST", "/servers", data='{"name":"x"}', yes=True)
    assert r.sent and json.loads(seen[0]["b"]) == {"name": "x"}
    r = await rest.call(svc(server), "DELETE", "/servers/1")
    assert not r.sent and len(seen) == 1


@pytest.mark.parametrize("auth,check", [("header:X-Auth-Token", lambda s: s["h"]["X-Auth-Token"] == "k"), ("query:token", lambda s: "token=k" in s["p"]),
                                         ("basic", lambda s: s["h"]["Authorization"].startswith("Basic "))])
async def test_auth_styles(server, monkeypatch, auth, check):
    monkeypatch.setenv("T_KEY", "k")
    await rest.call(svc(server, auth), "GET", "/x")
    assert check(seen[0])


async def test_extra_headers(server, monkeypatch):
    monkeypatch.setenv("T_KEY", "k")
    await rest.call(svc(server, headers={"Notion-Version": "v"}), "GET", "/x")
    assert seen[0]["h"]["Notion-Version"] == "v"


@pytest.mark.parametrize("path", ["http://evil.example/x", "//evil.example/x", "x", "/a/../b"])
async def test_path_cannot_leave_the_service(server, monkeypatch, path):
    monkeypatch.setenv("T_KEY", "k")
    with pytest.raises(rest.RestError):
        await rest.call(svc(server), "GET", path)
    assert not seen


async def test_missing_key_and_bad_json(server, monkeypatch):
    monkeypatch.delenv("T_KEY", raising=False)
    with pytest.raises(rest.RestError, match="T_KEY"):
        await rest.call(svc(server), "GET", "/x")
    with pytest.raises(rest.RestError, match="JSON"):
        await rest.call(svc(server), "POST", "/x", data="{nope", yes=True)


def test_registry_is_valid_and_user_row_wins(tmp_path):
    s = rest.load(tmp_path / "none.json")
    assert {"hetzner", "hostinger", "flyio", "cloudflare", "notion", "coderabbit"} <= set(s)
    for r in s.values():
        assert r["base_url"].startswith("https://") and r["env"] and (r["auth"] in ("bearer", "basic") or r["auth"].split(":")[0] in ("header", "query"))
    mine = tmp_path / "mine.json"
    mine.write_text(json.dumps({"services": [{"name": "hetzner", "base_url": "https://x.test", "env": "E", "auth": "bearer"}, {"name": "new", "base_url": "https://n.test", "env": "N", "auth": "bearer"}]}))
    s = rest.load(mine)
    assert s["hetzner"]["base_url"] == "https://x.test" and "new" in s and "vultr" in s
    assert "hetzner" in rest.listing(s)
