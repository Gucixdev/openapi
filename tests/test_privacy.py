"""privacy.leaks and the suspicious-provider rules in the router."""
import pytest

import privacy
from openapi import Provider, Router, RouterError
from test_router import Fake, mk

SUS = Provider("sus", "http://s", "T_SUS", ("m",), suspicious=True)
OK = Provider("fine", "http://f", "T_FINE", ("m",))


@pytest.mark.parametrize("text,kind", [
    ("what is in /home/someone/dev/x.py", "file path"), ("open ~/notes", "file path"), ("see ./src/main.rs", "file path"), ("C:\\Users\\a\\b.txt", "file path"),
    ("src/lib/util/helper.ts is slow", "file path"), ("mail me at a.b@example.com", "email address"), ("server 192.168.1.20", "ip address"),
    ("key sk-abcdefghijklmnopqrstuvwxyz123456", "secret or token"), ("API_KEY=hunter2xyz", "env assignment"), ("```py\nx=1\n```", "code block"),
])
def test_private_material_is_found(text, kind):
    assert kind in privacy.leaks(text)


def test_identity_is_found(monkeypatch):
    monkeypatch.setenv("USER", "zorblax")
    assert "your user or host name" in privacy.leaks("my account zorblax is locked")


@pytest.mark.parametrize("text", ["Explain how CRDTs merge concurrent edits.", "What is the difference between TCP and UDP?", "compare https://github.com/a/b/c/d.py with rust's std::fs",
                                  "Why is the sky blue?"])
def test_plain_questions_pass(text):
    assert privacy.leaks(text) == []


async def test_auto_never_uses_a_suspicious_provider_without_public(monkeypatch):
    fakes = {"sus": Fake(["m"], ["from sus"]), "fine": Fake(["m"], ["from fine"])}
    r, _ = mk(monkeypatch, fakes, [SUS, OK])
    assert (await r.ask("Why is the sky blue?")).provider == "fine" and not fakes["sus"].calls


async def test_public_allows_it_for_a_clean_question_and_blocks_a_private_one(monkeypatch):
    fakes = {"sus": Fake(["m"], ["from sus"] * 2), "fine": Fake(["m"], ["from fine"] * 2)}
    r, _ = mk(monkeypatch, fakes, [SUS, OK])
    assert (await r.ask("Why is the sky blue?", public=True, model="sus")).provider == "sus"
    a = await r.ask("what is wrong in /home/me/dev/app.py ?", public=True)
    assert a.provider == "fine" and any("file path" in t for t in a.tried)           # skipped with the reason, the secret itself is never echoed
    assert "app.py" not in " ".join(a.tried)


async def test_naming_it_with_private_text_is_refused_and_nothing_is_sent(monkeypatch):
    fakes = {"sus": Fake(["m"], ["x"]), "fine": Fake(["m"], ["y"])}
    r, _ = mk(monkeypatch, fakes, [SUS, OK])
    with pytest.raises(RouterError, match="suspicious: the prompt has file path"):
        await r.ask("fix ~/dev/x/y.py", model="sus")
    assert not fakes["sus"].calls


async def test_tools_never_go_to_a_suspicious_provider(monkeypatch):
    fakes = {"sus": Fake(["m"], ["x"]), "fine": Fake(["m"], ["y"])}
    r, _ = mk(monkeypatch, fakes, [SUS, OK])
    resp, p, *_ = await r.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "f", "parameters": {}}}])
    assert p.name == "fine" and not fakes["sus"].calls


def test_scrub_replaces_private_tokens_and_keeps_the_question():
    q = "why does ~/dev/app/main.py fail on 10.0.0.5 for bob@example.com with token sk-abcdefghijklmnopqrstuvwxyz123456? see https://github.com/a/b/c/d.py"
    clean, done = privacy.scrub(q)
    assert privacy.leaks(clean) == [] and "<path>" in clean and "<email>" in clean and "<ip>" in clean and "<secret>" in clean
    assert "https://github.com/a/b/c/d.py" in clean and "main.py" not in clean and set(done) >= {"file path", "email address", "ip address", "secret or token"}


def test_scrub_cannot_save_code():
    clean, _ = privacy.scrub("fix:\n```c\nint x;\n```")
    assert "code block" in privacy.leaks(clean)          # research() therefore refuses it


def test_fuzz_private_material_is_always_found_and_plain_questions_never_flagged():
    import random
    rng = random.Random(11)
    words = "why how does the compiler optimize a loop when memory is aliased in rust versus c ? explain briefly".split()
    paths = ["/home/{u}/dev/{a}/{b}.py", "~/{a}/{b}.rs", "./{a}/{b}/{a}.ts", "../{a}/{b}.c", "C:\\Users\\{u}\\{a}.txt", "/etc/{a}.conf", "/data/dev/{a}/{b}.md", "src/{a}/{b}/{a}.go"]
    secrets = ["sk-{h}", "ghp_{h}", "gsk_{h}", "AKIA{H}", "{h}{h}", "API_TOKEN={a}", "-----BEGIN RSA PRIVATE KEY-----"]
    for _ in range(300):
        a, b, u = (rng.choice(["alpha", "beta", "core", "web", "x1", "notes"]) for _ in range(3))
        h = "".join(rng.choice("abcdef0123456789") for _ in range(24))
        item = rng.choice(paths + secrets + ["{a}@{b}.com", "192.168.{n}.{n}"]).format(u=u, a=a, b=b, h=h, H=h.upper(), n=rng.randint(1, 250))
        text = " ".join(rng.sample(words, 5) + [item] + rng.sample(words, 3))
        assert privacy.leaks(text), text
        assert privacy.leaks(privacy.scrub(text)[0]) == [], text          # scrub always lands on clean text for everything but code blocks
    for _ in range(300):
        text = " ".join(rng.sample(words, rng.randint(4, 10))) + rng.choice(["", " (see https://en.wikipedia.org/wiki/Alias_analysis)", " e.g. a/b", " in 2025", " and/or"])
        assert privacy.leaks(text) == [], text


async def test_research_scrubs_then_fans_out_to_every_suspicious_provider(monkeypatch):
    sus2 = Provider("sus2", "http://s2", "T_SUS2", ("m",), suspicious=True)
    fakes = {"sus": Fake(["m"], ["a1"]), "sus2": Fake(["m"], ["a2"]), "fine": Fake(["m"], ["no"])}
    r, _ = mk(monkeypatch, fakes, [SUS, sus2, OK])
    sent, removed, res = await r.research("why does ~/dev/x/y.py hang?")
    assert sent == "why does <path> hang?" and removed == ["file path"] and [n for n, _ in res] == ["sus", "sus2"] and not fakes["fine"].calls
    assert fakes["sus"].calls[0]["messages"][-1]["content"] == "why does <path> hang?"          # the provider only ever saw the scrubbed text
    with pytest.raises(RouterError, match="still private"):
        await r.research("fix ```x=1```")


async def test_clean_question_falls_back_to_suspicious_only_when_every_normal_provider_failed(monkeypatch):
    import openai
    from test_router import _resp
    boom = lambda: openai.RateLimitError("slow", response=_resp(429), body=None)
    fakes = {"sus": Fake(["m"], ["from sus", "from sus"]), "fine": Fake(["m"], [boom(), boom(), boom()])}
    r, _ = mk(monkeypatch, fakes, [SUS, OK])
    a = await r.ask("Why is the sky blue?")
    assert a.provider == "sus" and any("suspicious one was used" in t for t in a.tried)
    with pytest.raises(RouterError):                                                 # a private prompt is never rescued that way
        await r.ask("what is wrong in /home/me/dev/app.py")
    assert len(fakes["sus"].calls) == 1
