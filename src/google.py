"""Google (own file, standalone): Gmail over the REST API. Calendar and Drive would go here too.

Needs an OAuth refresh token, once: create an OAuth client in Google Cloud (type Desktop), get a refresh token for the scopes
  https://www.googleapis.com/auth/gmail.readonly  https://www.googleapis.com/auth/gmail.compose  https://www.googleapis.com/auth/gmail.send
(the OAuth 2.0 Playground with your own client id does it), and set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN.
A short-lived GOOGLE_ACCESS_TOKEN works for a quick try. The access token is fetched when needed and kept in memory only.
Reading and drafting run at once; sending is only shown until yes=True (it leaves your account). Functions return text, errors start with "error:".
Endpoint shapes are from Google's documentation, not yet run against a real account (tests use a local stand-in server).
"""
from __future__ import annotations

import base64
import os
import time
from email.message import EmailMessage
from typing import Any

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
_cache: dict[str, Any] = {"token": None, "exp": 0.0}


def _urls() -> tuple[str, str]:
    return os.environ.get("GOOGLE_TOKEN_URL", TOKEN_URL), os.environ.get("GMAIL_BASE", API)


async def token() -> str:
    if os.environ.get("GOOGLE_ACCESS_TOKEN"):
        return os.environ["GOOGLE_ACCESS_TOKEN"]
    if _cache["token"] and time.monotonic() < _cache["exp"]:
        return _cache["token"]
    need = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN")
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        raise RuntimeError("set " + ", ".join(missing) + " (see the top of google.py)")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(_urls()[0], data={"client_id": os.environ[need[0]], "client_secret": os.environ[need[1]], "refresh_token": os.environ[need[2]], "grant_type": "refresh_token"})
    if r.status_code >= 400:
        raise RuntimeError(f"google token HTTP {r.status_code}: {r.text[:200]}")
    j = r.json()
    _cache["token"], _cache["exp"] = j["access_token"], time.monotonic() + int(j.get("expires_in", 3600)) - 60
    return _cache["token"]


async def _call(method: str, path: str, params: dict | None = None, body: dict | None = None) -> Any:
    h = {"Authorization": f"Bearer {await token()}"}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.request(method, _urls()[1] + path, params=params, json=body, headers=h)
    if r.status_code >= 400:
        raise RuntimeError(f"gmail HTTP {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else {}


def _hdr(msg: dict, name: str) -> str:
    return next((h["value"] for h in msg.get("payload", {}).get("headers", []) if h["name"].lower() == name.lower()), "")


def _text(part: dict) -> str:
    """The plain-text body of a message part tree (first text/plain, else the first html with tags left in)."""
    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"] + "==").decode("utf-8", "replace")
    for p in part.get("parts", []) or []:
        t = _text(p)
        if t:
            return t
    if part.get("mimeType") == "text/html" and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"] + "==").decode("utf-8", "replace")
    return ""


async def gmail_search(query: str = "", limit: int = 10) -> str:
    """Messages matching a Gmail search ("from:x is:unread newer_than:7d"); one line each: id, date, from, subject."""
    try:
        ids = (await _call("GET", "/messages", {"q": query, "maxResults": max(1, min(limit, 50))})).get("messages", [])
        rows = []
        for m in ids:
            j = await _call("GET", f"/messages/{m['id']}", {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]})
            rows.append(f"{m['id']}  {_hdr(j, 'Date')[:25]:<25}  {_hdr(j, 'From')[:40]:<40}  {_hdr(j, 'Subject')}")
        return "\n".join(rows) or "(no messages)"
    except Exception as e:
        return f"error: {e}"


async def gmail_read(message_id: str) -> str:
    """One message: headers and the plain-text body."""
    try:
        j = await _call("GET", f"/messages/{message_id}", {"format": "full"})
        return f"From: {_hdr(j, 'From')}\nTo: {_hdr(j, 'To')}\nDate: {_hdr(j, 'Date')}\nSubject: {_hdr(j, 'Subject')}\n\n{_text(j.get('payload', {}))}"
    except Exception as e:
        return f"error: {e}"


def _raw(to: str, subject: str, body: str) -> str:
    m = EmailMessage()
    m["To"], m["Subject"] = to, subject
    m.set_content(body)
    return base64.urlsafe_b64encode(m.as_bytes()).decode()


async def gmail_draft(to: str, subject: str, body: str) -> str:
    """Save a draft (nothing is sent); you send it from Gmail."""
    try:
        j = await _call("POST", "/drafts", body={"message": {"raw": _raw(to, subject, body)}})
        return f"ok: draft {j.get('id', '?')} to {to}"
    except Exception as e:
        return f"error: {e}"


async def gmail_send(to: str, subject: str, body: str, yes: bool = False) -> str:
    """Send a message. Without yes it only shows what would go out."""
    if not yes:
        return f"not sent: to {to}, subject {subject!r}, {len(body)} chars. Pass yes=True / --yes to send."
    try:
        j = await _call("POST", "/messages/send", body={"raw": _raw(to, subject, body)})
        return f"ok: sent {j.get('id', '?')} to {to}"
    except Exception as e:
        return f"error: {e}"
