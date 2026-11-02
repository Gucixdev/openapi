"""Starts the server as Claude Code would (stdio) and calls the tools through the real MCP client. Run: .venv/bin/python tests/stdio_check.py"""
import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=[str(__import__("pathlib").Path(__file__).resolve().parent.parent / "openapi.py")])
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = [t.name for t in (await s.list_tools()).tools]
            print("tools:", tools)
            for name, args in (("status", {}), ("obsidian_read", {"path": "x"}), ("notion_search", {"query": "x"}), ("coderabbit", {})):
                res = await s.call_tool(name, args)
                print(f"--- {name}\n" + "".join(c.text for c in res.content if hasattr(c, "text")))
            return 0 if sorted(tools) == sorted(["ask", "council", "list_models", "stats", "status", "obsidian_search", "obsidian_read", "obsidian_write", "notion_search", "notion_read", "notion_create", "coderabbit"]) else 1


sys.exit(asyncio.run(main()))
