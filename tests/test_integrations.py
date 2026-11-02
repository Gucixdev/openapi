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
