"""google.py, microsoft.py and hosting.py against one local HTTP server standing in for Google, Graph, Hetzner and DigitalOcean."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import pytest

import google
import hosting
import microsoft

seen: list[dict] = []


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


ROUTES = {
    ("POST", "/gtoken"): {"access_token": "gtok", "expires_in": 3600},
    ("GET", "/gmail/messages"): {"messages": [{"id": "m1"}]},
    ("GET", "/gmail/messages/m1"): {"id": "m1", "payload": {"mimeType": "multipart/alternative", "headers": [{"name": "From", "value": "a@x.org"}, {"name": "Subject", "value": "hej"},
                                    {"name": "Date", "value": "Mon, 1 Jan 2026 10:00:00"}, {"name": "To", "value": "me@x.org"}],
                                    "parts": [{"mimeType": "text/plain", "body": {"data": b64("treść listu")}}]}},
    ("POST", "/gmail/drafts"): {"id": "d1"},
    ("POST", "/gmail/messages/send"): {"id": "s1"},
    ("POST", "/mtoken"): {"access_token": "mtok", "expires_in": 3600},
    ("GET", "/graph/messages"): {"value": [{"id": "o1", "subject": "cześć", "receivedDateTime": "2026-01-01T10:00:00Z", "from": {"emailAddress": {"name": "A", "address": "a@x.org"}}}]},
    ("GET", "/graph/messages/o1"): {"subject": "cześć", "receivedDateTime": "2026-01-01T10:00:00Z", "from": {"emailAddress": {"name": "A", "address": "a@x.org"}},
                                    "toRecipients": [{"emailAddress": {"name": "B", "address": "b@x.org"}}], "body": {"content": "tekst"}},
    ("POST", "/graph/messages"): {"id": "od1"},
    ("POST", "/graph/sendMail"): {},
    ("GET", "/hz/servers"): {"servers": [{"id": 7, "name": "web", "status": "running", "public_net": {"ipv4": {"ip": "1.2.3.4"}}, "datacenter": {"name": "fsn1-dc14"}, "server_type": {"name": "cx22"}}],
                             "meta": {"pagination": {"next_page": None}}},
    ("GET", "/hz/server_types"): {"server_types": [{"name": "cx22", "cores": 2, "memory": 4, "disk": 40, "prices": [{"price_monthly": {"gross": "4.59"}}, {"price_monthly": {"gross": "5.10"}}]},
                                                   {"name": "old", "cores": 1, "memory": 2, "disk": 20, "deprecated": True, "prices": [{"price_monthly": {"gross": "1.00"}}]}]},
    ("POST", "/hz/servers"): {"server": {"id": 9}},
    ("DELETE", "/hz/servers/7"): {},
    ("GET", "/do/droplets"): {"droplets": [{"id": 5, "name": "db", "status": "active", "networks": {"v4": [{"type": "private", "ip_address": "10.0.0.2"}, {"type": "public", "ip_address": "5.6.7.8"}]},
                                            "region": {"slug": "fra1"}, "size_slug": "s-1vcpu-1gb"}], "links": {}},
    ("GET", "/do/sizes"): {"sizes": [{"slug": "s-1vcpu-1gb", "vcpus": 1, "memory": 1024, "disk": 25, "price_monthly": 6, "available": True}]},
}


class H(BaseHTTPRequestHandler):
    def _do(self):
        n = int(self.headers.get("content-length") or 0)
        u = urlparse(self.path)
        seen.append({"m": self.command, "p": u.path, "q": parse_qs(u.query), "h": dict(self.headers), "b": self.rfile.read(n).decode()})
        out = ROUTES.get((self.command, u.path))
        code = 200 if out is not None else 404
        raw = json.dumps(out if out is not None else {"error": "no route"}).encode()
        self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    do_GET = do_POST = do_DELETE = _do
    def log_message(self, *a): pass


@pytest.fixture
def srv(monkeypatch):
    s = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    seen.clear()
    base = f"http://127.0.0.1:{s.server_port}"
    google._cache.update(token=None, exp=0.0)
    microsoft._cache.update(token=None, exp=0.0)
    for k, v in {"GOOGLE_TOKEN_URL": base + "/gtoken", "GMAIL_BASE": base + "/gmail", "GOOGLE_CLIENT_ID": "id", "GOOGLE_CLIENT_SECRET": "sec", "GOOGLE_REFRESH_TOKEN": "ref",
                 "MS_TOKEN_URL": base + "/mtoken", "GRAPH_BASE": base + "/graph", "MS_CLIENT_ID": "mid", "MS_REFRESH_TOKEN": "mref",
                 "HETZNER_BASE": base + "/hz", "HCLOUD_TOKEN": "hk", "DIGITALOCEAN_BASE": base + "/do", "DIGITALOCEAN_TOKEN": "dk"}.items():
        monkeypatch.setenv(k, v)
    for k in ("GOOGLE_ACCESS_TOKEN", "MS_ACCESS_TOKEN", "VULTR_API_KEY", "LINODE_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    yield base
    s.shutdown()


async def test_gmail_refresh_search_read(srv):
    out = await google.gmail_search("is:unread", 5)
    assert "m1" in out and "hej" in out and "a@x.org" in out
    tok = [s for s in seen if s["p"] == "/gtoken"][0]
    assert "grant_type=refresh_token" in tok["b"] and "ref" in tok["b"]
    assert [s for s in seen if s["p"] == "/gmail/messages"][0]["h"]["Authorization"] == "Bearer gtok"
    assert "treść listu" in await google.gmail_read("m1")
    assert len([s for s in seen if s["p"] == "/gtoken"]) == 1       # the token is reused


async def test_gmail_send_needs_yes_and_draft_is_raw(srv):
    out = await google.gmail_send("b@x.org", "temat", "treść")
    assert out.startswith("not sent") and not any(s["p"].endswith("/send") for s in seen)
    assert (await google.gmail_send("b@x.org", "temat", "treść", yes=True)).startswith("ok: sent s1")
    raw = json.loads([s for s in seen if s["p"].endswith("/send")][0]["b"])["raw"]
    assert b"Subject: temat" in base64.urlsafe_b64decode(raw + "==")
    assert (await google.gmail_draft("b@x.org", "t", "b")).startswith("ok: draft d1")


async def test_gmail_missing_env_is_an_error(srv, monkeypatch):
    monkeypatch.delenv("GOOGLE_REFRESH_TOKEN")
    out = await google.gmail_search()
    assert out.startswith("error:") and "GOOGLE_REFRESH_TOKEN" in out


async def test_outlook_search_read_send(srv):
    assert "cześć" in await microsoft.outlook_search("x")
    assert [s for s in seen if s["p"] == "/graph/messages"][0]["q"]["$search"] == ['"x"']
    out = await microsoft.outlook_read("o1")
    assert "To: B <b@x.org>" in out and "tekst" in out
    assert (await microsoft.outlook_send("b@x.org, c@x.org", "t", "b")).startswith("not sent")
    assert (await microsoft.outlook_send("b@x.org, c@x.org", "t", "b", yes=True)).startswith("ok: sent")
    body = json.loads([s for s in seen if s["p"] == "/graph/sendMail"][0]["b"])
    assert [r["emailAddress"]["address"] for r in body["message"]["toRecipients"]] == ["b@x.org", "c@x.org"]
    assert (await microsoft.outlook_draft("b@x.org", "t", "b")).startswith("ok: draft od1")


async def test_hosting_servers_normalised_and_skips(srv):
    out = await hosting.servers()
    assert "hetzner" in out and "1.2.3.4" in out and "cx22" in out
    assert "digitalocean" in out and "5.6.7.8" in out and "fra1" in out       # the public address, not the private one
    assert "skipped, no key: vultr, linode" in out
    assert (await hosting.servers("vultr")).startswith("error: no key set for vultr")
    assert (await hosting.servers("nope")).startswith("error: unknown provider")


async def test_hosting_plans_cheapest_first_and_filters(srv):
    out = await hosting.plans()
    lines = out.splitlines()
    assert "cx22" in lines[0] and "4.59" in lines[0] and "old" not in out      # cheapest price over locations, deprecated dropped
    assert "s-1vcpu-1gb" in lines[1]
    assert "s-1vcpu-1gb" not in await hosting.plans(min_ram=2)
    assert (await hosting.plans(min_ram=64)) .startswith("(no plan matches)")


async def test_hosting_create_delete_need_yes(srv):
    out = await hosting.create("hetzner", "n", "cx22", "fsn1", "debian-12")
    assert out.startswith("not sent") and not any(s["m"] == "POST" for s in seen)
    assert "ok: HTTP 200" in await hosting.create("hetzner", "n", "cx22", "fsn1", "debian-12", yes=True)
    sent = json.loads([s for s in seen if s["m"] == "POST"][0]["b"])
    assert sent == {"name": "n", "server_type": "cx22", "location": "fsn1", "image": "debian-12"}
    assert (await hosting.delete("hetzner", "7")).startswith("not sent") and not any(s["m"] == "DELETE" for s in seen)
    assert "ok: HTTP 200" in await hosting.delete("hetzner", "7", yes=True)
    assert (await hosting.delete("hetzner", "../x", yes=True)).startswith("error:")
    assert (await hosting.create("vultr", "n", "p", "r", "i", yes=True)).startswith("error: create covers")
