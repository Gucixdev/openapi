"""The command line over the same code as the MCP tools: exit codes, --json, refusal of writes, stdin, streamed finish_reason."""
import json
from types import SimpleNamespace as NS

import pytest
from typer.testing import CliRunner

import openapi

run = CliRunner().invoke


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))      # no real keys.env or services.json
    monkeypatch.delenv("HCLOUD_TOKEN", raising=False)


def test_services_list_info_and_unknown():
    assert "hetzner" in run(openapi.app, ["services"]).output
    r = run(openapi.app, ["services", "hostinger", "--json"])
    assert r.exit_code == 0 and json.loads(r.output)["base_url"] == "https://developers.hostinger.com"
    assert run(openapi.app, ["services", "nope"]).exit_code == 2


def test_api_write_is_refused_with_exit_3_and_get_without_key_fails(monkeypatch):
    monkeypatch.setenv("HCLOUD_TOKEN", "x")
    r = run(openapi.app, ["api", "hetzner", "POST", "/servers", "-d", '{"name":"a"}'])
    assert r.exit_code == 3 and "not sent" in r.output and "x" not in r.output.replace("hetzner", "").replace("not sent", "").split("POST")[0]
    monkeypatch.delenv("HCLOUD_TOKEN")
    assert run(openapi.app, ["api", "hetzner", "GET", "/servers"]).exit_code == 1


def test_dump_api_lists_every_command():
    names = {c["command"] for c in json.loads(run(openapi.app, ["dump-api"]).output)}
    assert {"ask", "council", "api", "services", "obsidian search", "notion create", "coderabbit", "image", "jev", "video", "blender", "gimp", "krita", "godot", "serve"} <= names


def test_ask_json_and_stdin(monkeypatch):
    async def fake(self, prompt, model="auto", system=None, max_tokens=None, temperature=None, task="general", paid=None, public=False):
        return openapi.Answer(prompt.upper(), "alpha", "m", 3)
    monkeypatch.setattr(openapi.Router, "ask", fake)
    r = run(openapi.app, ["ask", "-", "--json"], input="hello")
    assert r.exit_code == 0 and json.loads(r.output)["text"] == "HELLO"


def test_ask_exit_1_when_still_truncated(monkeypatch):
    async def fake(self, *a, **k):
        return openapi.Answer("cut", "alpha", "m", 3, truncated=True)
    monkeypatch.setattr(openapi.Router, "ask", fake)
    assert run(openapi.app, ["ask", "q"]).exit_code == 1


async def test_stream_keeps_finish_reason():
    async def gen():
        yield NS(choices=[NS(delta=NS(content="a"), finish_reason=None)], usage=None)
        yield NS(choices=[NS(delta=NS(content="b"), finish_reason="length")], usage=NS(total_tokens=9))
    r = await openapi._stream(gen())
    assert r.choices[0].message.content == "ab" and r.choices[0].finish_reason == "length" and r.usage.total_tokens == 9


def test_obsidian_cli_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_VAULT", str(tmp_path / "v")); (tmp_path / "v").mkdir()
    assert run(openapi.app, ["obsidian", "write", "a", "-"], input="note body").exit_code == 0
    assert "note body" in run(openapi.app, ["obsidian", "read", "a"]).output
    assert run(openapi.app, ["obsidian", "read", "../zz"]).exit_code == 1
