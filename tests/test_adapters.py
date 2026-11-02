import httpx2 as httpx
import openai
import pytest

import adapters


def client(cls, handler, base="https://x/v1"):
    c = cls(base, "k")
    c.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


async def test_anthropic():
    seen = {}
    def h(req):
        seen["h"], seen["b"] = req.headers, req.content
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "claude-x"}]})
        return httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}], "usage": {"input_tokens": 2, "output_tokens": 3}})
    c = client(adapters.Anthropic, h)
    assert [m.id for m in (await c.models.list()).data] == ["claude-x"]
    r = await c.chat.completions.create(model="claude-x", messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "q"}])
    assert r.choices[0].message.content == "hi" and r.usage.total_tokens == 5
    assert seen["h"]["x-api-key"] == "k" and b'"system":"s"' in seen["b"].replace(b" ", b"")


async def test_gemini_cohere_cloudflare(monkeypatch):
    g = client(adapters.Gemini, lambda r: httpx.Response(200, json={"models": [{"name": "models/g1", "supportedGenerationMethods": ["generateContent"]}, {"name": "models/e", "supportedGenerationMethods": ["embedContent"]}]} if r.url.path.endswith("/models") else {"candidates": [{"content": {"parts": [{"text": "yo"}]}}], "usageMetadata": {"totalTokenCount": 7}}))
    assert [m.id for m in (await g.models.list()).data] == ["g1"]
    r = await g.chat.completions.create(model="g1", messages=[{"role": "user", "content": "q"}])
    assert (r.choices[0].message.content, r.usage.total_tokens) == ("yo", 7)
    c = client(adapters.Cohere, lambda r: httpx.Response(200, json={"message": {"content": [{"text": "co"}]}, "usage": {"tokens": {"input_tokens": 1, "output_tokens": 1}}}), "https://x/v2")
    r = await c.chat.completions.create(model="m", messages=[{"role": "user", "content": "q"}])
    assert (r.choices[0].message.content, r.usage.total_tokens) == ("co", 2)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
    cf = client(adapters.Cloudflare, lambda r: httpx.Response(200, json={"result": {"response": "cf", "usage": {"total_tokens": 4}}}), "https://x/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai")
    assert "acct" in cf.base
    r = await cf.chat.completions.create(model="@cf/m", messages=[{"role": "user", "content": "q"}])
    assert (r.choices[0].message.content, r.usage.total_tokens) == ("cf", 4)


@pytest.mark.parametrize("status,exc", [(429, openai.RateLimitError), (401, openai.AuthenticationError), (404, openai.NotFoundError), (503, openai.InternalServerError)])
async def test_status_maps_to_openai_errors(status, exc):
    c = client(adapters.Anthropic, lambda r: httpx.Response(status, text="no"))
    with pytest.raises(exc):
        await c.chat.completions.create(model="m", messages=[{"role": "user", "content": "q"}])


async def test_cloudflare_needs_account(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    with pytest.raises(openai.AuthenticationError):
        adapters.Cloudflare("https://x/{CLOUDFLARE_ACCOUNT_ID}", "k")
