"""mcp_server: the MCP server - every tool Claude gets (ask, council, best, budget, notes, mail, hosting, rest api...). Plain functions; cli.py wraps the same ones as commands."""
from __future__ import annotations

import asyncio
import os

import coderabbit as coderabbit_api
import audio
import images
import integrations
import jev as jev_api
import ytdlp
from apps import blender as blender_app, gimp as gimp_app, godot as godot_app, krita as krita_app
import google
import hosting
import microsoft
import notion
import rest
import scoring
from mcp.server.mcpserver import MCPServer
from providers import load_key_file
from router import Answer, Router, RouterError, render


# ------------------------------------------------------------------ the MCP server
mcp = MCPServer("openapi")
_router: Router | None = None


def router() -> Router:
    global _router
    if _router is None:
        load_key_file()
        _router = Router()
    return _router


def _fmt(a: Answer) -> str:
    left = scoring.describe(a.provider)
    note = (f", {a.parts} parts" if a.parts > 1 else "") + ("; STILL TRUNCATED, ask again for the rest" if a.truncated else "")
    return f"{a.text}\n\n[{a.provider}/{a.model}, {a.tokens} tokens{note}" + (f"; left: {left}" if left else "") + "]" + (f"\n[skipped: {'; '.join(a.tried)}]" if a.tried else "")


@mcp.tool()
async def ask(prompt: str, model: str = "auto", system: str = "", max_tokens: int = 0, task: str = "general", paid: bool = False, public: bool = False) -> str:
    """Ask another model. model: "auto" (the best available model for the task, failing over on rate limits), a provider ("groq"), or "provider/model-id".
    task: general, code, reason, fast or long - decides which models rank first (code prefers coder models). paid: kept for old callers - everything available is used by default, in order: free and free-tier providers first (by model score), paid models of
    free_only providers (OpenRouter) after their free ones, paid-only providers (anthropic, openai, perplexity) last; OPENAPI_FREE_ONLY=1 limits it to free.
    public: also allow the SUSPICIOUS providers (unknown operator, see providers.py) - only for a plain question or research, nothing private: no paths, code, keys, emails or names (checked, and refused if found)."""
    try:
        return _fmt(await router().ask(prompt, model, system or None, max_tokens or None, task=task, paid=paid or None, public=public))
    except RouterError as e:
        return f"error: {e}"


@mcp.tool()
async def research(question: str, max_tokens: int = 0) -> str:
    """Extra opinions for a general question or research, from the SUSPICIOUS providers (unknown operators, free): the question is scrubbed first
    (paths, emails, IPs, keys, your user/host name become <path>, <email>, ...) and refused if code or anything private is left, then sent to all of them
    in parallel. Ask the idea, not your code. Answers are unverified leads: check them."""
    try:
        clean, removed, res = await router().research(question, None, max_tokens or None)
    except RouterError as e:
        return f"error: {e}"
    head = f"sent as: {clean}" + (f"\nscrubbed: {', '.join(removed)}" if removed else "")
    return head + "\n\n" + "\n\n".join(f"## {n} (suspicious, unverified)\n{_fmt(r) if isinstance(r, Answer) else 'error: ' + str(r)}" for n, r in res)


@mcp.tool()
async def council(prompt: str, models: list[str], max_tokens: int = 0) -> str:
    """Ask several models the same question in parallel and show every answer side by side (models as for ask)."""
    res = await router().council(prompt, models, None, max_tokens or None)
    return "\n\n".join(f"## {m}\n{_fmt(r) if isinstance(r, Answer) else 'error: ' + str(r)}" for m, r in zip(models, res))


@mcp.tool()
async def best(task: str = "general", top: int = 10, paid: bool = False) -> str:
    """The highest-scoring models you could get right now for a task (general, code, reason, fast, long), across all configured providers."""
    r, rows = router(), []
    for p in r.configured():
        try:
            ids = await asyncio.wait_for(r.models(p, paid=paid or bool(os.environ.get("OPENAPI_PAID"))), 10)
        except Exception:
            continue
        rows += [(r.worth(p, i, task), p.name, i) for i in r.ranked(p, [i for i in ids if i not in r._bad.get(p.name, ())], task)[:top]]
    rows.sort(key=lambda t: -t[0])
    return "\n".join(f"{sc:6.1f}  {pn}/{i}" + (f"   [{'resting ' + str(round(r.resting(pn))) + 's' if r.resting(pn) else 'ready'}]") for sc, pn, i in rows[:top]) or "no provider has a key set"


@mcp.tool()
async def budget() -> str:
    """What is left: per provider the last rate-limit headers (requests and tokens remaining), cooldowns, this session's use, and OpenRouter credit."""
    r, lines = router(), []
    for p in r.providers.values():
        if not p.key():
            continue
        s = r.stats[p.name]
        rest = f"resting {r.resting(p.name):.0f}s" if r.resting(p.name) else "ready"
        lines.append(f"{p.name:<12} {rest:<14} used {s.calls} calls/{s.tokens} tok   {scoring.describe(p.name)}")
    if os.environ.get("OPENROUTER_API_KEY"):
        try:
            import httpx2
            async with httpx2.AsyncClient(timeout=10) as c:
                d = (await c.get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"]})).json().get("data", {})
            left = d.get("limit_remaining")
            lines.append(f"openrouter credit: used ${d.get('usage', 0):.4f}" + (f", left ${left:.4f} of ${d.get('limit'):.2f}" if left is not None else ", no spending limit set")
                         + (", free tier (no credits bought)" if d.get("is_free_tier") else ""))
        except Exception as e:
            lines.append(f"openrouter credit: cannot read ({type(e).__name__})")
    return "\n".join(lines) or "no provider has a key set"


@mcp.tool()
async def list_models(provider: str = "") -> str:
    """The models each configured provider offers (OpenRouter: only the free ones), the default marked with *."""
    r, lines = router(), []
    for p in r.configured():
        if provider and p.name != provider:
            continue
        try:
            ids = await r.models(p, refresh=True)
        except Exception as e:  # a listing failure must not hide the other providers
            lines.append(f"{p.name}: cannot list ({type(e).__name__})")
            continue
        best = r.choose(p, ids)
        lines.append(f"{p.name} ({len(ids)}):\n" + "\n".join(f"  {'*' if i == best else ' '} {i}" for i in ids))
    return "\n".join(lines) or "no provider has a key set"


@mcp.tool()
def obsidian_search(query: str, limit: int = 20) -> str:
    """Search notes in the Obsidian vault (OBSIDIAN_VAULT) by file name and text."""
    return integrations.obsidian_search(query, limit)


@mcp.tool()
def obsidian_read(path: str) -> str:
    """Read one note from the Obsidian vault, path relative to the vault."""
    return integrations.obsidian_read(path)


@mcp.tool()
def obsidian_write(path: str, text: str, append: bool = False) -> str:
    """Create or overwrite (or append to) a note in the Obsidian vault."""
    return integrations.obsidian_write(path, text, append)


@mcp.tool()
async def notion_search(query: str, limit: int = 20) -> str:
    """Search Notion pages and databases shared with the integration (NOTION_API_KEY)."""
    return await notion.notion_search(query, limit)


@mcp.tool()
async def notion_read(page_id: str) -> str:
    """Read the text blocks of a Notion page."""
    return await notion.notion_read(page_id)


@mcp.tool()
async def notion_create(parent_page_id: str, title: str, text: str = "") -> str:
    """Create a Notion page under a parent page; paragraphs split on blank lines."""
    return await notion.notion_create(parent_page_id, title, text)


@mcp.tool()
async def coderabbit(path: str = "/v1/users") -> str:
    """Read-only GET against the CodeRabbit API (optional, CODERABBIT_API_KEY)."""
    return await coderabbit_api.coderabbit_get(path)


@mcp.tool()
async def gmail_search(query: str = "", limit: int = 10) -> str:
    """Search Gmail ("from:x is:unread newer_than:7d"): id, date, from, subject per line (GOOGLE_* OAuth env, see google.py)."""
    return await google.gmail_search(query, limit)


@mcp.tool()
async def gmail_read(message_id: str) -> str:
    """Read one Gmail message: headers and plain-text body."""
    return await google.gmail_read(message_id)


@mcp.tool()
async def gmail_draft(to: str, subject: str, body: str) -> str:
    """Save a Gmail draft; nothing is sent."""
    return await google.gmail_draft(to, subject, body)


@mcp.tool()
async def gmail_send(to: str, subject: str, body: str, yes: bool = False) -> str:
    """Send a Gmail message; without yes=True it only shows what would go out."""
    return await google.gmail_send(to, subject, body, yes)


@mcp.tool()
async def outlook_search(query: str = "", limit: int = 10) -> str:
    """Search Outlook mail through Microsoft Graph (MS_* OAuth env, see microsoft.py); empty query = newest."""
    return await microsoft.outlook_search(query, limit)


@mcp.tool()
async def outlook_read(message_id: str) -> str:
    """Read one Outlook message: headers and plain-text body."""
    return await microsoft.outlook_read(message_id)


@mcp.tool()
async def outlook_draft(to: str, subject: str, body: str) -> str:
    """Save an Outlook draft; nothing is sent."""
    return await microsoft.outlook_draft(to, subject, body)


@mcp.tool()
async def outlook_send(to: str, subject: str, body: str, yes: bool = False) -> str:
    """Send an Outlook message (to: comma separated); without yes=True it only shows what would go out."""
    return await microsoft.outlook_send(to, subject, body, yes)


@mcp.tool()
async def hosting_servers(provider: str = "all") -> str:
    """List servers at hetzner, digitalocean, vultr, linode (all with a key) in one shape."""
    return await hosting.servers(provider)


@mcp.tool()
async def hosting_plans(provider: str = "all", min_ram: float = 0, min_cpu: int = 0, limit: int = 10) -> str:
    """Cheapest VPS plans across providers, monthly price as listed."""
    return await hosting.plans(provider, min_ram, min_cpu, limit)


@mcp.tool()
async def hosting_create(provider: str, name: str, plan: str, region: str, image: str, yes: bool = False) -> str:
    """Create a server (hetzner, digitalocean); bills. Without yes=True it only shows the request."""
    return await hosting.create(provider, name, plan, region, image, yes)


@mcp.tool()
async def hosting_delete(provider: str, server_id: str, yes: bool = False) -> str:
    """Destroy a server (hetzner, digitalocean); not undoable. Without yes=True it only shows the request."""
    return await hosting.delete(provider, server_id, yes)


@mcp.tool()
async def image(prompt: str, width: int = 1024, height: int = 1024) -> str:
    """Generate a picture: a keyed provider (Together FLUX, OpenAI, ...) if one is set, else keyless Pollinations. Returns the file path."""
    return await images.image(prompt, width, height)


@mcp.tool()
async def jev(state: str, questions: dict, model: str = "jev-latest") -> str:
    """Ask Jev (TypeSafe System One, TYPESAFE_API_KEY) typed questions about a text: noul (yes/no), choice, score; returns calibrated probabilities."""
    return await jev_api.jev(state, questions, model)


@mcp.tool()
async def video(url: str, mode: str = "context", out: str = "") -> str:
    """A video's context through yt-dlp (no key): mode info | transcript | context (both) | audio | video. out = a file for the text, or a directory for audio/video."""
    return await ytdlp.fetch(url, mode, out or None)


@mcp.tool()
async def blender(mode: str, file: str = "", out: str = "", arg: str = "") -> str:
    """Blender headless (`blender -b`): mode version | info FILE.blend | render FILE OUTDIR [frame] | export FILE OUTDIR FMT (glb obj fbx stl usd ply) | script FILE.py [OUTDIR]. Never touches an open Blender."""
    return await blender_app.run(mode, file, out, arg)


@mcp.tool()
async def gimp(mode: str, file: str = "", out: str = "", arg: str = "") -> str:
    """GIMP 3 batch (`gimp-console -i`, flatpak ok): mode version | convert FILE OUTDIR FMT | scale FILE OUTDIR WxH | scriptfu with arg = the expression. Uses a throwaway profile."""
    return await gimp_app.run(mode, file, out, arg)


@mcp.tool()
async def krita(mode: str, file: str = "", out: str = "", arg: str = "") -> str:
    """Krita export without a window: mode version | export FILE OUTDIR FMT (png jpg ora psd kra webp tiff bmp)."""
    return await krita_app.run(mode, file, out, arg)


@mcp.tool()
async def godot(mode: str, project: str = "", out: str = "", arg: str = "") -> str:
    """Godot 4 headless: mode version | script PROJECT arg=file.gd | check PROJECT arg=file.gd | import PROJECT | export PROJECT OUTDIR arg="PRESET|FILE". Not for a project open in the editor."""
    return await godot_app.run(mode, project, out, arg)


@mcp.tool()
async def stats() -> str:
    """How many providers have a key and answer, and how many models they offer in total."""
    return render(await router().survey())


@mcp.tool()
def status() -> str:
    """Which providers have a key, which rest after a rate limit, and what this session has used."""
    r, lines = router(), []
    for p in r.providers.values():
        s = r.stats[p.name]
        state = "dead (key rejected)" if p.name in r._dead else "no key" if not p.key() else (f"resting {r.resting(p.name):.0f}s" if r.resting(p.name) else "ready")
        lines.append(f"{p.name:<12} {state:<22} calls {s.calls:<4} tokens {s.tokens:<8} errors {s.errors}" + (f" ({s.last_error})" if s.last_error else ""))
    return "\n".join(lines)


@mcp.tool()
def services(name: str = "") -> str:
    """The REST services `api` can drive (hosting, DNS, CDN, notes, dev); with a name: its auth, docs and example calls."""
    try:
        svc = rest.load()
        return rest.info(svc[name]) if name in svc else rest.listing(svc) if not name else f"error: unknown service '{name}' (known: {', '.join(svc)})"
    except rest.RestError as e:
        return f"error: {e}"


@mcp.tool()
async def api(service: str, method: str, path: str, query: dict | None = None, data: str = "", yes: bool = False) -> str:
    """Call any service from `services` with the key from the environment. GET/HEAD run at once; POST/PUT/PATCH/DELETE change or bill something
    and are only shown until yes=true, so ask the user before passing it. data is a JSON string. The whole body comes back, never cut."""
    try:
        r = await rest.call(rest.load()[service], method, path, {k: str(v) for k, v in (query or {}).items()}, data or None, yes)
    except KeyError:
        return f"error: unknown service '{service}' (see services)"
    except rest.RestError as e:
        return f"error: {e}"
    return f"[{r.request.splitlines()[0]} -> {r.status}]\n{r.text}" if r.sent else f"{r.text}\n{r.request}"


@mcp.tool()
async def speak(text: str, voice: str = "") -> str:
    """Text to speech: Groq or OpenAI if a key is set, else local espeak-ng. Returns the wav file path."""
    return await audio.speak(text, voice)


@mcp.tool()
async def transcribe(path: str, language: str = "") -> str:
    """Speech to text for an audio file (under 25 MB): Groq whisper or OpenAI if a key is set, else local whisper-cli. Returns the transcript."""
    return await audio.transcribe(path, language)
