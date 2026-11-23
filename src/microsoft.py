"""Microsoft (own file, standalone): Outlook mail over Microsoft Graph. Calendar, OneDrive and Teams would go here too.

Needs an OAuth refresh token, once: register an app in Microsoft Entra (public client, redirect http://localhost), delegated permissions
Mail.ReadWrite, Mail.Send, offline_access; sign in once (device code or auth code flow) to get a refresh token; set MS_CLIENT_ID and
MS_REFRESH_TOKEN (MS_CLIENT_SECRET only for a confidential client, MS_TENANT defaults to "common"). MS_ACCESS_TOKEN works for a quick try.
Reading and drafting run at once; sending is only shown until yes=True. Functions return text, errors start with "error:".
Endpoint shapes are from Microsoft's documentation, not yet run against a real account (tests use a local stand-in server).
"""
from __future__ import annotations

import os
import time
from typing import Any

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

GRAPH = "https://graph.microsoft.com/v1.0/me"
SCOPE = "Mail.ReadWrite Mail.Send offline_access"
_cache: dict[str, Any] = {"token": None, "exp": 0.0}


def _urls() -> tuple[str, str]:
    default = f"https://login.microsoftonline.com/{os.environ.get('MS_TENANT', 'common')}/oauth2/v2.0/token"
    return os.environ.get("MS_TOKEN_URL", default), os.environ.get("GRAPH_BASE", GRAPH)


async def token() -> str:
    if os.environ.get("MS_ACCESS_TOKEN"):
        return os.environ["MS_ACCESS_TOKEN"]
    if _cache["token"] and time.monotonic() < _cache["exp"]:
        return _cache["token"]
    missing = [k for k in ("MS_CLIENT_ID", "MS_REFRESH_TOKEN") if not os.environ.get(k)]
    if missing:
        raise RuntimeError("set " + ", ".join(missing) + " (see the top of microsoft.py)")
    form = {"client_id": os.environ["MS_CLIENT_ID"], "refresh_token": os.environ["MS_REFRESH_TOKEN"], "grant_type": "refresh_token", "scope": SCOPE}
    if os.environ.get("MS_CLIENT_SECRET"):
        form["client_secret"] = os.environ["MS_CLIENT_SECRET"]
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(_urls()[0], data=form)
    if r.status_code >= 400:
        raise RuntimeError(f"microsoft token HTTP {r.status_code}: {r.text[:200]}")
    j = r.json()
    _cache["token"], _cache["exp"] = j["access_token"], time.monotonic() + int(j.get("expires_in", 3600)) - 60
    return _cache["token"]


async def _call(method: str, path: str, params: dict | None = None, body: dict | None = None) -> Any:
    h = {"Authorization": f"Bearer {await token()}", "Prefer": 'outlook.body-content-type="text"'}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.request(method, _urls()[1] + path, params=params, json=body, headers=h)
    if r.status_code >= 400:
        raise RuntimeError(f"graph HTTP {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else {}


def _who(x: dict) -> str:
    a = (x or {}).get("emailAddress", {})
    return f"{a.get('name', '')} <{a.get('address', '')}>".strip()


async def outlook_search(query: str = "", limit: int = 10) -> str:
    """Messages matching a text search (empty = newest); one line each: id, date, from, subject."""
    p: dict[str, Any] = {"$top": max(1, min(limit, 50)), "$select": "id,subject,from,receivedDateTime"}
    if query:
        p["$search"] = f'"{query}"'
    try:
        rows = (await _call("GET", "/messages", p)).get("value", [])
        return "\n".join(f"{m['id']}  {m.get('receivedDateTime', '')[:19]}  {_who(m.get('from'))[:40]:<40}  {m.get('subject', '')}" for m in rows) or "(no messages)"
    except Exception as e:
        return f"error: {e}"


async def outlook_read(message_id: str) -> str:
    """One message: headers and the plain-text body."""
    try:
        j = await _call("GET", f"/messages/{message_id}", {"$select": "subject,from,toRecipients,receivedDateTime,body"})
        to = ", ".join(_who(x) for x in j.get("toRecipients", []))
        return f"From: {_who(j.get('from'))}\nTo: {to}\nDate: {j.get('receivedDateTime', '')}\nSubject: {j.get('subject', '')}\n\n{j.get('body', {}).get('content', '')}"
    except Exception as e:
        return f"error: {e}"


def _message(to: str, subject: str, body: str) -> dict:
    return {"subject": subject, "body": {"contentType": "Text", "content": body}, "toRecipients": [{"emailAddress": {"address": a.strip()}} for a in to.split(",") if a.strip()]}


async def outlook_draft(to: str, subject: str, body: str) -> str:
    """Save a draft in Outlook (nothing is sent)."""
    try:
        j = await _call("POST", "/messages", body=_message(to, subject, body))
        return f"ok: draft {j.get('id', '?')} to {to}"
    except Exception as e:
        return f"error: {e}"


async def outlook_send(to: str, subject: str, body: str, yes: bool = False) -> str:
    """Send a message (to: comma separated). Without yes it only shows what would go out."""
    if not yes:
        return f"not sent: to {to}, subject {subject!r}, {len(body)} chars. Pass yes=True / --yes to send."
    try:
        await _call("POST", "/sendMail", body={"message": _message(to, subject, body), "saveToSentItems": True})
        return f"ok: sent to {to}"
    except Exception as e:
        return f"error: {e}"
