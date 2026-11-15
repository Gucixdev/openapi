"""One caller for every REST API in services.json (hosting, DNS, CDN, notes, ...): `openapi api SERVICE METHOD PATH`.

The key comes from the environment (or ~/.config/openapi/keys.env) and is added here; it is never printed, never put in a URL
you typed, and a path cannot name another host, so the key cannot be sent anywhere but the service's own base URL.
Anything but GET/HEAD changes or bills something, so it is shown and not sent until the caller passes yes=True (--yes).
The body is returned whole: no truncation here, the caller decides what to cut.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

SAFE = ("GET", "HEAD")
FILE = Path(__file__).with_name("services.json")


class RestError(Exception):
    pass


@dataclass
class Result:
    sent: bool          # False = refused, nothing left this machine
    status: int         # HTTP status, 0 when not sent
    text: str
    request: str        # what was (or would be) sent, key redacted


def user_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "openapi" / "services.json"


def load(extra: Path | None = None) -> dict[str, dict]:
    """Built-in services, then the user's file on top (same name = the user's row wins)."""
    out: dict[str, dict] = {}
    for f in (FILE, extra or user_file()):
        try:
            rows = json.loads(f.read_text())["services"]
        except FileNotFoundError:
            continue
        except (ValueError, KeyError) as e:
            raise RestError(f"{f}: not a services file ({e})")
        for r in rows:
            out[r["name"]] = r
    return out


def listing(services: dict[str, dict]) -> str:
    rows = []
    for s in sorted(services.values(), key=lambda s: (s.get("kind", ""), s["name"])):
        key = "key set" if os.environ.get(s["env"]) else "no key"
        rows.append(f"{s['name']:<14} {s.get('kind', ''):<8} {key:<8} {s['env']:<22} {s['base_url']}" + ("" if s.get("verified") else "  (unverified)"))
    return "\n".join(rows)


def info(s: dict) -> str:
    lines = [f"{s['name']}  {s['base_url']}", f"auth: {s['auth']} from ${s['env']}" + (" (set)" if os.environ.get(s["env"]) else " (NOT set)"), f"docs: {s.get('docs', '')}"]
    lines.append("verified: " + (s["verified"] if s.get("verified") else "no, from memory: check the docs before trusting"))
    if s.get("notes"):
        lines.append(s["notes"])
    lines += ["  " + e for e in s.get("examples", [])]
    return "\n".join(lines)


def _auth(s: dict, key: str) -> tuple[dict[str, str], dict[str, str]]:
    a = s["auth"]
    if a == "bearer":
        return {"Authorization": f"Bearer {key}"}, {}
    if a == "basic":
        return {"Authorization": "Basic " + base64.b64encode(key.encode()).decode()}, {}
    if a.startswith("header:"):
        return {a[7:]: key}, {}
    if a.startswith("query:"):
        return {}, {a[6:]: key}
    raise RestError(f"{s['name']}: unknown auth '{a}'")


def build(s: dict, method: str, path: str, query: dict[str, str] | None = None, data: str | None = None) -> tuple[str, dict, dict[str, str]]:
    method = method.upper()
    if not path.startswith("/") or "://" in path or ".." in path or path.startswith("//"):
        raise RestError("path must start with one / and name no host, no ..")
    if method not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
        raise RestError(f"unknown method {method}")
    url = s["base_url"].rstrip("/") + ("" if path == "/" and s["base_url"].endswith(("/graphql/v2",)) else path)
    body = None
    if data not in (None, ""):
        try:
            body = json.loads(data)
        except ValueError as e:
            raise RestError(f"data is not JSON: {e}")
    return url, {"json": body} if body is not None else {}, dict(query or {})


async def call(s: dict, method: str, path: str, query: dict[str, str] | None = None, data: str | None = None, yes: bool = False, timeout: float = 60.0) -> Result:
    method = method.upper()
    url, kw, params = build(s, method, path, query, data)
    shown = f"{method} {url}" + (f"?{'&'.join(f'{k}={v}' for k, v in params.items())}" if params else "") + (f"\n{json.dumps(kw['json'])}" if "json" in kw else "")
    if method not in SAFE and not yes:
        return Result(False, 0, "not sent: this changes or bills something; add --yes to send it", shown)
    key = os.environ.get(s["env"])
    if not key:
        raise RestError(f"set {s['env']} (see: openapi services {s['name']})")
    headers, qp = _auth(s, key)
    headers.update(s.get("headers", {}))
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
            r = await c.request(method, url, params={**params, **qp}, headers=headers, **kw)
    except httpx.HTTPError as e:
        raise RestError(f"{s['name']}: {type(e).__name__}: {e}")
    text = r.text
    try:
        text = json.dumps(r.json(), indent=1, ensure_ascii=False)
    except ValueError:
        pass
    return Result(True, r.status_code, text, shown)
