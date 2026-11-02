"""Setup wizard for openapi: asks for each key (hidden, Enter skips), shows the signup link, stores nothing in the repo.

Kept apart from openapi.py so the router stays small. openapi.py passes in what the wizard needs, so there is no circular import.
Every key is optional: skip anything you do not want and that provider/integration simply stays off.
"""
from __future__ import annotations

import getpass
from typing import Callable

# integrations: (env var, what it is, where to get it)
EXTRAS = (
    ("OBSIDIAN_VAULT", "Obsidian: path to your vault folder (not a secret)", "folder with your .md notes, e.g. ~/Documents/Vault"),
    ("NOTION_API_KEY", "Notion: internal integration token", "https://www.notion.so/profile/integrations (then share pages with it)"),
    ("CODERABBIT_API_KEY", "CodeRabbit: org API key (admin/metrics API, not an LLM)", "https://docs.coderabbit.ai/api"),
    ("CLOUDFLARE_ACCOUNT_ID", "Cloudflare Workers AI account id", "https://dash.cloudflare.com (right sidebar)"),
)


def ask(env: str, hint: str, link: str, have: Callable[[str], bool], store: Callable[[str, str], None], echo: Callable[[str], None], hidden: bool = True) -> None:
    if have(env):
        echo(f"{env}: already set")
        return
    echo(f"\n{hint}\n  {link}")
    val = (getpass.getpass if hidden else input)(f"  {env} (Enter = skip): ").strip()
    if val:
        store(env, val)


def run(providers, have: Callable[[str], bool], store: Callable[[str, str], None], echo: Callable[[str], None] = print, only: str = "") -> None:
    """providers: objects with .name .env .signup. only: restrict to names containing this text."""
    seen: set[str] = set()
    for p in providers:
        if not p.env or p.env in seen or (only and only not in p.name):
            continue
        seen.add(p.env)
        ask(p.env, p.name, p.signup, have, store, echo)
    if not only:
        for env, hint, link in EXTRAS:
            ask(env, hint, link, have, store, echo, hidden=env != "OBSIDIAN_VAULT")
