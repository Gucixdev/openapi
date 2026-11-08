"""providers.json: the loader, the merge that never drops a row, stable output, and the catalog build over fake clients (no network, no keys)."""
import json

try:
    import httpx2 as httpx
except ImportError:
    import httpx
import openai
import pytest

import catalog
from openapi import PROVIDERS, Provider, Router, build_catalog, catalog_providers, user_providers

ROW = {"name": "newhost", "base_url": "http://n.example/v1", "env": "NEWHOST_KEY", "signup": "https://n.example/keys", "prefer": ["llama"], "tags": ["free"]}


def _write(path, rows):
    path.write_text(json.dumps({"schema": 1, "providers": rows}))


def test_load_missing_is_empty_and_bad_files_raise(tmp_path):
    assert catalog.load(tmp_path / "none.json") == []
    (tmp_path / "bad.json").write_text("{nope")
    with pytest.raises(ValueError, match="not valid JSON"):
        catalog.load(tmp_path / "bad.json")
    (tmp_path / "shape.json").write_text('{"providers": 3}')
    with pytest.raises(ValueError, match="'providers' list"):
        catalog.load(tmp_path / "shape.json")
    _write(tmp_path / "req.json", [{"name": "x", "base_url": "http://x"}])
    with pytest.raises(ValueError, match="needs name, base_url, env"):
        catalog.load(tmp_path / "req.json")


def test_extra_rows_never_replace_a_builtin():
    rows = [{**ROW, "name": PROVIDERS[0].name, "base_url": "http://evil"}, ROW, ROW]
    got = catalog.extra_rows(rows, {p.name for p in PROVIDERS})
    assert [r["name"] for r in got] == ["newhost"]


def test_catalog_adds_providers_before_the_toml_and_a_broken_file_is_skipped(tmp_path, monkeypatch, capsys):
    cat = tmp_path / "providers.json"
    _write(cat, [ROW, {**ROW, "name": "viatoml"}])
    monkeypatch.setenv("OPENAPI_CATALOG", str(cat))
    (tmp_path / "p.toml").write_text('[[provider]]\nname = "viatoml"\nbase_url = "http://toml"\nenv = ""\n')
    provs = {p.name: p for p in user_providers(tmp_path / "p.toml")}
    assert provs["newhost"].env == "NEWHOST_KEY" and provs["newhost"].prefer == ("llama",)
    assert provs["viatoml"].base_url == "http://toml"            # providers.toml has the last word
    assert len(provs) == len(PROVIDERS) + 2
    cat.write_text("{broken")
    assert catalog_providers() == []                              # never takes the server down
    assert "ignoring catalog" in capsys.readouterr().err


def test_merge_keeps_every_old_row_and_old_fields():
    old = [{"name": "a", "base_url": "u", "env": "", "note": "mine", "status": "unverified"}, {"name": "z", "base_url": "u", "env": ""}]
    new = [{"name": "a", "base_url": "u2", "env": "", "status": "ok"}, {"name": "m", "base_url": "u", "env": ""}]
    out = catalog.merge(old, new)
    assert [r["name"] for r in out] == ["a", "m", "z"]
    a = out[0]
    assert (a["base_url"], a["status"], a["note"]) == ("u2", "ok", "mine")


def test_dump_is_sorted_and_stable():
    rows = [{"name": "b", "base_url": "u", "env": "", "listed": [{"id": "y"}, {"id": "x"}]}, {"name": "a", "base_url": "u", "env": ""}]
    text = catalog.dump(rows, "2026-01-01")
    assert text == catalog.dump(list(reversed(rows)), "2026-01-01")
    data = json.loads(text)
    assert [r["name"] for r in data["providers"]] == ["a", "b"] and [m["id"] for m in data["providers"][1]["listed"]] == ["x", "y"]


def test_provider_and_model_tags():
    assert "local" in catalog.provider_tags({"name": "ollama", "base_url": "http://localhost:11434/v1", "env": ""})
    t = catalog.provider_tags({"name": "groq", "base_url": "https://g", "env": "K"})
    assert "free" in t and "credits-or-paid" not in t
    assert "credits-or-paid" in catalog.provider_tags({"name": "unknownco", "base_url": "https://g", "env": "K"})
    assert "free" in catalog.model_tags("x/llama:free", {}, False)
    assert "free" in catalog.model_tags("x/llama", {"pricing": {"prompt": "0", "completion": "0"}}, False)
    assert "free" not in catalog.model_tags("x/llama", {"pricing": {"prompt": "0", "completion": "0.5"}}, False)
    tags = catalog.model_tags("qwen3-coder-vl-8b", {"ctx": 262144}, False)
    assert {"code", "vision", "long", "fast"} <= set(tags)


def test_listed_entry_survives_odd_listings():
    class Odd:
        def model_dump(self):
            return {"id": "m", "architecture": "transformer", "top_provider": None, "pricing": "free", "context_length": "131072"}
    assert catalog.listed_entry(Odd()) == {"id": "m", "tags": [], "ctx": 131072}


def _resp(code):
    return httpx.Response(code, request=httpx.Request("GET", "http://x"))


class FakeClient:
    def __init__(self, models=None, err=None, reply="ok"):
        self.err, self.reply, ids = err, reply, models or []
        outer = self

        class _M:
            async def list(self_):
                if outer.err:
                    raise outer.err
                return type("P", (), {"data": [type("M", (), {"id": i, "model_dump": lambda s=None, i=i: {"id": i, "pricing": {"prompt": "0", "completion": "0"}}})() for i in ids]})()

        class _C:
            async def create(self_, **kw):
                return type("R", (), {"choices": [type("Ch", (), {"message": type("Msg", (), {"content": outer.reply})()})()], "usage": type("U", (), {"total_tokens": 3})()})()
        self.models = _M()
        self.chat = type("Chat", (), {"completions": _C()})()


async def test_build_catalog_statuses(monkeypatch):
    provs = (Provider("good", "http://g", "T_GOOD", ("llama",)), Provider("nokey", "http://n", "T_NOKEY"), Provider("gone", "http://x", "T_GONE"),
             Provider("loc", "http://localhost:1/v1", ""))
    monkeypatch.setenv("T_GOOD", "s3cr3t-value-123")
    monkeypatch.setenv("T_GONE", "k")
    monkeypatch.delenv("T_NOKEY", raising=False)
    fakes = {"good": FakeClient(["llama-3-70b"]),
             "nokey": FakeClient(err=openai.AuthenticationError("no", response=_resp(401), body=None)),
             "gone": FakeClient(err=openai.APIConnectionError(request=httpx.Request("GET", "http://x"))),
             "loc": FakeClient(err=openai.APIConnectionError(request=httpx.Request("GET", "http://x")))}
    r = Router(provs, lambda p: fakes[p.name])
    rows = {x["name"]: x for x in await build_catalog(r, [])}
    assert rows["good"]["status"] == "ok" and rows["good"]["listed"] == [{"id": "llama-3-70b", "tags": ["free"]}] and rows["good"]["free_count"] == 1
    assert rows["nokey"]["status"] == "needs-key" and "listed" not in rows["nokey"]
    assert rows["gone"]["status"] == "unreachable"
    assert rows["loc"]["status"] == "down"
    assert all("checked" in x and x["tags"] for x in rows.values())
    assert "s3cr3t-value-123" not in json.dumps(rows)                       # a key never reaches the catalog
