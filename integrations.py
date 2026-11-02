"""Notes integrations for the openapi MCP server: Obsidian (a vault folder on disk) and Notion (REST API).

Obsidian needs OBSIDIAN_VAULT (path to the vault); Notion needs NOTION_API_KEY (an internal integration token,
shared with the pages you want it to see: https://www.notion.so/profile/integrations).
Every function returns plain text for the model; errors come back as a line starting with "error:", never a crash.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

NOTION = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


# ------------------------------------------------------------------ Obsidian: a folder of .md files
def vault() -> Path | None:
    v = os.environ.get("OBSIDIAN_VAULT")
    p = Path(v).expanduser() if v else None
    return p if p and p.is_dir() else None


def _inside(root: Path, rel: str) -> Path | None:
    p = (root / rel).resolve()
    return p if p == root.resolve() or root.resolve() in p.parents else None   # a note never leaves the vault


def obsidian_search(query: str, limit: int = 20) -> str:
    root = vault()
    if not root:
        return "error: set OBSIDIAN_VAULT to your vault folder"
    q, hits = query.lower(), []
    for f in sorted(root.rglob("*.md")):
        if ".obsidian" in f.parts or ".trash" in f.parts:
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        rel = f.relative_to(root).as_posix()
        if q in rel.lower() or q in text.lower():
            i = text.lower().find(q)
            snip = text[max(0, i - 60): i + 100].replace("\n", " ") if i >= 0 else ""
            hits.append(f"{rel}: {snip}")
            if len(hits) >= limit:
                break
    return "\n".join(hits) or "no notes match"


def obsidian_read(path: str) -> str:
    root = vault()
    if not root:
        return "error: set OBSIDIAN_VAULT to your vault folder"
    p = _inside(root, path if path.endswith(".md") else path + ".md")
    if not p or not p.is_file():
        return f"error: no note {path}"
    return p.read_text(errors="replace")


def obsidian_write(path: str, text: str, append: bool = False) -> str:
    root = vault()
    if not root:
        return "error: set OBSIDIAN_VAULT to your vault folder"
    p = _inside(root, path if path.endswith(".md") else path + ".md")
    if not p:
        return "error: path leaves the vault"
    p.parent.mkdir(parents=True, exist_ok=True)
    if append and p.exists():
        text = p.read_text(errors="replace").rstrip("\n") + "\n\n" + text
    p.write_text(text if text.endswith("\n") else text + "\n")
    return f"ok: {p.relative_to(root.resolve()).as_posix()} ({len(text)} chars)"


# ------------------------------------------------------------------ Notion
async def _notion(method: str, path: str, body: dict | None = None) -> Any:
    key = os.environ.get("NOTION_API_KEY")
    if not key:
        raise RuntimeError("set NOTION_API_KEY (an integration token)")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.request(method, NOTION + path, json=body, headers={"Authorization": f"Bearer {key}", "Notion-Version": NOTION_VERSION})
    if r.status_code >= 400:
        raise RuntimeError(f"notion HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def _title(obj: dict) -> str:
    props = obj.get("properties", {})
    for v in props.values():
        if v.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in v.get("title", [])) or "(untitled)"
    t = obj.get("title")
    return "".join(x.get("plain_text", "") for x in t) if isinstance(t, list) else "(untitled)"


async def notion_search(query: str, limit: int = 20) -> str:
    try:
        d = await _notion("POST", "/search", {"query": query, "page_size": min(limit, 100)})
    except Exception as e:
        return f"error: {e}"
    return "\n".join(f"{o['id']}  {o['object']}  {_title(o)}" for o in d.get("results", [])) or "nothing found (is the page shared with the integration?)"


async def notion_read(page_id: str) -> str:
    try:
        d = await _notion("GET", f"/blocks/{page_id}/children?page_size=100")
    except Exception as e:
        return f"error: {e}"
    out = []
    for b in d.get("results", []):
        t = b.get("type", "")
        rich = b.get(t, {}).get("rich_text", []) if isinstance(b.get(t), dict) else []
        out.append("".join(x.get("plain_text", "") for x in rich))
    return "\n".join(out) or "(empty page)"


async def notion_create(parent_page_id: str, title: str, text: str = "") -> str:
    body = {"parent": {"page_id": parent_page_id}, "properties": {"title": {"title": [{"text": {"content": title}}]}},
            "children": [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": para[:2000]}}]}} for para in text.split("\n\n") if para.strip()]}
    try:
        d = await _notion("POST", "/pages", body)
    except Exception as e:
        return f"error: {e}"
    return f"ok: {d.get('id')} {d.get('url', '')}"


# ------------------------------------------------------------------ CodeRabbit (optional, read-only admin API)
async def coderabbit_get(path: str = "/v1/users") -> str:
    """GET https://api.coderabbit.ai<path>; key in CODERABBIT_API_KEY. Not an LLM: it lists users/metrics of your CodeRabbit org."""
    key = os.environ.get("CODERABBIT_API_KEY")
    if not key:
        return "error: set CODERABBIT_API_KEY (CodeRabbit dashboard -> API keys; docs https://docs.coderabbit.ai/api)"
    if not path.startswith("/v1/") or ".." in path:
        return "error: path must start with /v1/"
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get("https://api.coderabbit.ai" + path, headers={"x-coderabbitai-api-key": key})
    except Exception as e:
        return f"error: {e}"
    return r.text[:6000] if r.status_code < 400 else f"error: HTTP {r.status_code}: {r.text[:200]}"
