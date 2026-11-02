"""Image generation, own file. Tries providers in order and falls through on any failure, like the chat router:
  1. any OpenAI-style /images/generations API that has a key (together FLUX, openai, ...)
  2. Pollinations, keyless (flux), the fallback that needs nothing.
The picture is written to a file; the tool returns the path (never the bytes). IMAGE_DIR overrides ~/Pictures/openapi."""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from urllib.parse import quote

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

# (name, env var, base url, model). Add more: any API with POST {base}/images/generations.
KEYED = (
    ("together", "TOGETHER_API_KEY", "https://api.together.xyz/v1", "black-forest-labs/FLUX.1-schnell"),
    ("openai", "OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-image-1"),
    ("nebius", "NEBIUS_API_KEY", "https://api.studio.nebius.com/v1", "black-forest-labs/flux-schnell"),
    ("fireworks", "FIREWORKS_API_KEY", "https://api.fireworks.ai/inference/v1", "accounts/fireworks/models/flux-1-schnell-fp8"),
)


def _out(ext: str) -> Path:
    d = Path(os.environ.get("IMAGE_DIR") or "~/Pictures/openapi").expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"img-{int(time.time() * 1000)}.{ext}"


async def _keyed(c, name: str, env: str, base: str, model: str, prompt: str, size: str) -> Path:
    r = await c.post(f"{base}/images/generations", json={"model": model, "prompt": prompt, "n": 1, "size": size}, headers={"Authorization": f"Bearer {os.environ[env]}"})
    r.raise_for_status()
    item = r.json()["data"][0]
    if item.get("b64_json"):
        data = base64.b64decode(item["b64_json"])
    else:
        img = await c.get(item["url"])
        img.raise_for_status()
        data = img.content
    p = _out("png")
    p.write_bytes(data)
    return p


async def image(prompt: str, width: int = 1024, height: int = 1024) -> str:
    tried: list[str] = []
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as c:
        for name, env, base, model in KEYED:
            if not os.environ.get(env):
                continue
            try:
                return f"ok: {await _keyed(c, name, env, base, model, prompt, f'{width}x{height}')} ({name})"
            except Exception as e:
                tried.append(f"{name}: {str(e)[:80]}")
        try:
            r = await c.get(f"https://image.pollinations.ai/prompt/{quote(prompt)}", params={"width": width, "height": height, "nologo": "true"})
            r.raise_for_status()
            if not r.headers.get("content-type", "").startswith("image/"):
                raise RuntimeError("not an image")
            p = _out("jpg")
            p.write_bytes(r.content)
            return f"ok: {p} (pollinations)"
        except Exception as e:
            tried.append(f"pollinations: {str(e)[:80]}")
    return "error: no image provider worked: " + "; ".join(tried)
