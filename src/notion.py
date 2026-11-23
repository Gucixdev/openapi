"""Notion REST API client (own file, standalone). Needs NOTION_API_KEY: an internal integration token from
https://www.notion.so/profile/integrations, with the pages shared to it. Functions return text; errors start with "error:"."""
from __future__ import annotations

import os
from typing import Any

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

NOTION = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


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


