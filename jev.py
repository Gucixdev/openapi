"""Jev (TypeSafe AI) client, own file. Jev is a "System One" model: it does not write text, it answers typed questions with
calibrated probabilities (noul = yes/no, choice = pick one, score = rubric). Needs TYPESAFE_API_KEY (https://console.typesafe.ai/keys).
Request shape taken from docs.typesafe.ai quickstart (read through a summarising fetch, so check it against the live docs)."""
from __future__ import annotations

import json
import os

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

URL = "https://api.typesafe.ai/v1/systemone"


async def jev(state: str, questions: dict, model: str = "jev-latest") -> str:
    """questions: {name: {"type": "noul"|"choice"|"score", "instructions": str, "criteria": dict|list}}. Returns the JSON answer."""
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        return "error: set TYPESAFE_API_KEY (https://console.typesafe.ai/keys)"
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(URL, json={"state": state, "model": model, "questions": questions}, headers={"Authorization": f"Bearer {key}"})
    except Exception as e:
        return f"error: {e}"
    if r.status_code >= 400:
        return f"error: HTTP {r.status_code}: {r.text[:200]}"
    return json.dumps(r.json().get("answers", r.json()), indent=1)
