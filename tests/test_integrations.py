import coderabbit
import integrations
import notion


def test_obsidian_roundtrip_and_escape(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSIDIAN_VAULT", str(tmp_path))
    assert integrations.obsidian_write("a/b", "hello world").startswith("ok")
    assert "hello" in integrations.obsidian_read("a/b")
    assert "a/b.md" in integrations.obsidian_search("world")
    assert integrations.obsidian_write("../evil", "x").startswith("error")
    assert not (tmp_path.parent / "evil.md").exists()


def test_obsidian_unset(monkeypatch):
    monkeypatch.delenv("OBSIDIAN_VAULT", raising=False)
    assert integrations.obsidian_read("x").startswith("error")


async def test_notion_and_coderabbit_need_keys(monkeypatch):
    monkeypatch.delenv("NOTION_API_KEY", raising=False)
    monkeypatch.delenv("CODERABBIT_API_KEY", raising=False)
    assert (await notion.notion_search("x")).startswith("error")
    assert (await coderabbit.coderabbit_get()).startswith("error")
    monkeypatch.setenv("CODERABBIT_API_KEY", "k")
    assert (await coderabbit.coderabbit_get("/etc/passwd")).startswith("error")


async def test_jev_needs_key(monkeypatch):
    import jev
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert (await jev.jev("x", {})).startswith("error")


async def test_image_keyless_falls_back_to_pollinations(tmp_path, monkeypatch):
    import images
    for _, env, _, _ in images.KEYED:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("IMAGE_DIR", str(tmp_path))
    real = images.httpx.AsyncClient
    monkeypatch.setattr(images.httpx, "AsyncClient", lambda **kw: real(transport=images.httpx.MockTransport(lambda req: images.httpx.Response(200, content=b"\xff\xd8img", headers={"content-type": "image/jpeg"})), **kw))
    out = await images.image("a red fox", 64, 64)
    assert out.startswith("ok:") and "pollinations" in out, out
    assert [f.read_bytes() for f in tmp_path.iterdir()] == [b"\xff\xd8img"]


async def test_image_reports_every_failure(tmp_path, monkeypatch):
    import images
    monkeypatch.setenv("TOGETHER_API_KEY", "k")
    monkeypatch.setenv("IMAGE_DIR", str(tmp_path))
    real = images.httpx.AsyncClient
    monkeypatch.setattr(images.httpx, "AsyncClient", lambda **kw: real(transport=images.httpx.MockTransport(lambda req: images.httpx.Response(402, text="pay")), **kw))
    out = await images.image("x")
    assert out.startswith("error:") and "together" in out and "pollinations" in out
