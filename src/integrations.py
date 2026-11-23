"""Obsidian integration for the openapi MCP server (a vault folder on disk).

Notion lives in notion.py and CodeRabbit in coderabbit.py.

Needs OBSIDIAN_VAULT (path to the vault).
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
