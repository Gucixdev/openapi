"""Hosting (own file, standalone): the same questions asked of every VPS provider, answered in one shape.

servers(): what is running, one line each (provider, id, name, status, ip, region, plan). plans(): the cheapest plans first.
create() and delete() exist for Hetzner and DigitalOcean only; everything else (volumes, firewalls, DNS, Vultr, Linode, Hostinger, Fly)
goes through `openapi api SERVICE METHOD PATH` (rest.py). Providers without a key in the environment are skipped and named.
A create or delete is shown and not sent until yes=True: it bills or destroys. Functions return text, errors start with "error:".
Field names are from each provider's API docs; Only the Hetzner base URL and auth were checked (search, 2026-11-15); every path and field name is from memory and not run against a
live account (tests use a local stand-in server). Money is the provider's own currency, monthly, as listed: no conversion.
"""
from __future__ import annotations

import json
import os

import rest

PROVIDERS = ("hetzner", "digitalocean", "vultr", "linode")


def _svc(name: str) -> dict:
    s = dict(rest.load()[name])
    env = f"{name.upper()}_BASE"                      # tests and proxies: HETZNER_BASE=http://127.0.0.1:1234/v1
    if os.environ.get(env):
        s["base_url"] = os.environ[env]
    return s


async def _get(name: str, path: str, query: dict[str, str] | None = None):
    r = await rest.call(_svc(name), "GET", path, query)
    if r.status >= 400:
        raise rest.RestError(f"{name} HTTP {r.status}: {r.text[:200]}")
    return json.loads(r.text)


async def _pages(name: str, path: str, key: str, more):
    """All pages of a list endpoint; more(json) says whether another page follows."""
    out = []
    for page in range(1, 50):
        j = await _get(name, path, {"page": str(page), "per_page": "200"})
        out += j.get(key, [])
        if not more(j):
            break
    return out


def _ip(x: dict) -> str:
    return next((n["ip_address"] for n in x.get("networks", {}).get("v4", []) if n.get("type") == "public"), "")


async def _servers(p: str) -> list[dict]:
    if p == "hetzner":
        rows = await _pages(p, "/servers", "servers", lambda j: bool(j.get("meta", {}).get("pagination", {}).get("next_page")))
        return [{"id": x["id"], "name": x["name"], "status": x["status"], "ip": (x.get("public_net", {}).get("ipv4") or {}).get("ip", ""),
                 "region": (x.get("datacenter") or {}).get("name", ""), "plan": (x.get("server_type") or {}).get("name", "")} for x in rows]
    if p == "digitalocean":
        rows = await _pages(p, "/droplets", "droplets", lambda j: bool(j.get("links", {}).get("pages", {}).get("next")))
        return [{"id": x["id"], "name": x["name"], "status": x["status"], "ip": _ip(x), "region": x.get("region", {}).get("slug", ""), "plan": x.get("size_slug", "")} for x in rows]
    if p == "vultr":
        rows = (await _get(p, "/instances", {"per_page": "500"})).get("instances", [])
        return [{"id": x["id"], "name": x.get("label", ""), "status": x.get("status", ""), "ip": x.get("main_ip", ""), "region": x.get("region", ""), "plan": x.get("plan", "")} for x in rows]
    rows = (await _get(p, "/linode/instances", {"page_size": "500"})).get("data", [])
    return [{"id": x["id"], "name": x.get("label", ""), "status": x.get("status", ""), "ip": (x.get("ipv4") or [""])[0], "region": x.get("region", ""), "plan": x.get("type", "")} for x in rows]


async def _plans(p: str) -> list[dict]:
    if p == "hetzner":
        out = []
        for x in (await _get(p, "/server_types", {"per_page": "200"})).get("server_types", []):
            if x.get("deprecated"):
                continue
            prices = [float(q["price_monthly"]["gross"]) for q in x.get("prices", []) if q.get("price_monthly")]
            if prices:
                out.append({"plan": x["name"], "cpu": x["cores"], "ram": float(x["memory"]), "disk": x["disk"], "month": min(prices)})
        return out
    if p == "digitalocean":
        return [{"plan": x["slug"], "cpu": x["vcpus"], "ram": x["memory"] / 1024, "disk": x["disk"], "month": float(x["price_monthly"])}
                for x in (await _get(p, "/sizes", {"per_page": "200"})).get("sizes", []) if x.get("available", True)]
    if p == "vultr":
        return [{"plan": x["id"], "cpu": x["vcpu_count"], "ram": x["ram"] / 1024, "disk": x["disk"], "month": float(x["monthly_cost"])}
                for x in (await _get(p, "/plans", {"per_page": "500"})).get("plans", [])]
    return [{"plan": x["id"], "cpu": x["vcpus"], "ram": x["memory"] / 1024, "disk": x["disk"] / 1024, "month": float(x["price"]["monthly"])}
            for x in (await _get(p, "/linode/types", {"page_size": "500"})).get("data", [])]


def _which(provider: str) -> tuple[list[str], list[str]]:
    names = PROVIDERS if provider in ("", "all") else (provider,)
    bad = [n for n in names if n not in PROVIDERS]
    if bad:
        raise rest.RestError(f"unknown provider {bad[0]} (one of: {', '.join(PROVIDERS)}, all)")
    have = [n for n in names if os.environ.get(rest.load()[n]["env"])]
    return have, [n for n in names if n not in have]


async def servers(provider: str = "all") -> str:
    """Every server of one provider, or of all with a key: provider, id, name, status, ip, region, plan."""
    try:
        have, skipped = _which(provider)
        if not have:
            raise rest.RestError("no key set for " + (provider if provider != "all" else ", ".join(f"{n} (${rest.load()[n]['env']})" for n in PROVIDERS)))
        rows = []
        for p in have:
            rows += [(p, x) for x in await _servers(p)]
        lines = [f"{p:<13} {str(x['id']):<10} {x['name'][:28]:<28} {x['status']:<10} {x['ip']:<16} {x['region']:<10} {x['plan']}" for p, x in rows]
        if skipped:
            lines.append("skipped, no key: " + ", ".join(skipped))
        return "\n".join(lines) if rows else "(no servers)" + ("\n" + lines[-1] if skipped else "")
    except Exception as e:
        return f"error: {e}"


async def plans(provider: str = "all", min_ram: float = 0, min_cpu: int = 0, limit: int = 10) -> str:
    """The cheapest plans (monthly, the provider's own currency) with at least min_ram GB and min_cpu cores."""
    try:
        have, skipped = _which(provider)
        if not have:
            raise rest.RestError("no key set for " + (provider if provider != "all" else ", ".join(f"{n} (${rest.load()[n]['env']})" for n in PROVIDERS)))
        rows = []
        for p in have:
            rows += [(p, x) for x in await _plans(p) if x["ram"] >= min_ram and x["cpu"] >= min_cpu]
        rows.sort(key=lambda t: (t[1]["month"], -t[1]["ram"]))
        lines = [f"{p:<13} {x['plan']:<22} {x['cpu']:>3} cpu {x['ram']:>6.1f} GB ram {x['disk']:>6.0f} GB disk {x['month']:>8.2f} /month" for p, x in rows[:max(1, limit)]]
        if skipped:
            lines.append("skipped, no key: " + ", ".join(skipped))
        return "\n".join(lines) if rows else "(no plan matches)"
    except Exception as e:
        return f"error: {e}"


async def _write(provider: str, method: str, path: str, body: dict | None, yes: bool) -> str:
    r = await rest.call(_svc(provider), method, path, None, json.dumps(body) if body else None, yes=yes)
    if not r.sent:
        return f"not sent (add yes=True / --yes):\n{r.request}"
    if r.status >= 400:
        raise rest.RestError(f"{provider} HTTP {r.status}: {r.text[:300]}")
    return f"ok: HTTP {r.status}\n{r.text[:1500]}"


async def create(provider: str, name: str, plan: str, region: str, image: str, yes: bool = False) -> str:
    """Create a server (hetzner or digitalocean). It bills from the moment it exists; without yes it only shows the request."""
    try:
        if provider == "hetzner":
            return await _write(provider, "POST", "/servers", {"name": name, "server_type": plan, "location": region, "image": image}, yes)
        if provider == "digitalocean":
            return await _write(provider, "POST", "/droplets", {"name": name, "size": plan, "region": region, "image": image}, yes)
        raise rest.RestError("create covers hetzner and digitalocean; for others use: openapi api SERVICE POST PATH --data ...")
    except Exception as e:
        return f"error: {e}"


async def delete(provider: str, server_id: str, yes: bool = False) -> str:
    """Destroy a server (hetzner or digitalocean). Not undoable; without yes it only shows the request."""
    try:
        if not str(server_id).isdigit():
            raise rest.RestError("server id is the number from `servers`")
        if provider == "hetzner":
            return await _write(provider, "DELETE", f"/servers/{server_id}", None, yes)
        if provider == "digitalocean":
            return await _write(provider, "DELETE", f"/droplets/{server_id}", None, yes)
        raise rest.RestError("delete covers hetzner and digitalocean; for others use: openapi api SERVICE DELETE PATH")
    except Exception as e:
        return f"error: {e}"
