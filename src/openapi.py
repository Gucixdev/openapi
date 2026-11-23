#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2", "openai>=3", "typer>=0.12"]
# ///
"""openapi - an MCP server over the free tiers of many LLM providers (with failover) and many REST APIs, and the CLI that sets it up.

    ./openapi.py init          guided setup: a key per provider (hidden), what is available, register with Claude Code
    ./openapi.py stats         providers / models count        ./openapi.py test   one live call per provider
    ./openapi.py add NAME      set one key                     ./openapi.py        (no arguments) the MCP server on stdio
Tools it gives Claude: ask (task-aware), council, best, budget, list_models, stats, status.   Keys: environment, or ~/.config/openapi/keys.env (mode 600).
Any other OpenAI-compatible endpoint: three lines in ~/.config/openapi/providers.toml (see providers.user_providers).

The code is split by job: providers.py (rows + keys), router.py (pick, ask, fail over), mcp_server.py (the tools), cli.py (the commands),
and one small module per integration (rest, notion, google, microsoft, hosting, ...). This file is only the entry point and keeps the
old names importable (openapi.Router, openapi.app)."""
from __future__ import annotations

from providers import *  # noqa: F401,F403
from router import *  # noqa: F401,F403
from router import _stream  # noqa: F401
from mcp_server import *  # noqa: F401,F403
from mcp_server import _fmt  # noqa: F401
from cli import *  # noqa: F401,F403
from cli import app

if __name__ == "__main__":
    app()
