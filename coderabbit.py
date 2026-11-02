"""CodeRabbit API client (own file, standalone). Needs CODERABBIT_API_KEY. Read-only admin/metrics API, not an LLM."""
from __future__ import annotations

import os

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx


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
