"""cli: the command line (typer). `serve` and no arguments run the MCP server; every tool is also a plain command; init/add/test/register set things up."""
from __future__ import annotations

import asyncio
import datetime
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

import openai
import typer
import catalog
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
import wizard
from providers import Provider, keys_path, load_key_file, set_key, user_providers
from router import Answer, Router, RouterError, render
from mcp_server import _fmt, best, budget, list_models, mcp, router, status


# ------------------------------------------------------------------ the command line
app = typer.Typer(add_completion=False, help="Many free LLM providers and REST APIs behind one command. Every tool is a command (dump-api lists them); `serve` is the MCP server. init = guided setup.", invoke_without_command=True)


@app.callback()
def _default(ctx: typer.Context) -> None:
    try:
        load_key_file()       # keys.env feeds every command, not only the server
    except PermissionError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    if ctx.invoked_subcommand is None:
        mcp.run()


@app.command()
def serve() -> None:
    """Start the MCP server on stdio."""
    mcp.run()


@app.command()
def proxy(port: int = typer.Option(8787), host: str = typer.Option("127.0.0.1")) -> None:
    """Anthropic-compatible endpoint over the router:  ANTHROPIC_BASE_URL=http://127.0.0.1:8787 ANTHROPIC_AUTH_TOKEN=x claude"""
    import proxy as _proxy
    _proxy.run(host, port)


@app.command()
def add(name: str, key: str = typer.Argument(None, help="the key; omit to be asked (hidden)")) -> None:
    """Store the key of one provider."""
    provs = {p.name: p for p in user_providers()}
    if name not in provs:
        raise typer.BadParameter(f"unknown provider '{name}' (known: {', '.join(provs)})")
    p = provs[name]
    key = key or getpass.getpass(f"{p.env} ({p.signup or 'no signup url'}): ").strip()
    if not key:
        raise typer.Exit(1)
    typer.echo(f"saved {p.env} to {set_key(p.env, key)}")


@app.command("stats")
def stats_cmd(as_json: bool = typer.Option(False, "--json")) -> None:
    """How many providers have a key and answer, and how many models they offer."""
    load_key_file()
    rows = asyncio.run(Router().survey())
    typer.echo(json.dumps([r.__dict__ for r in rows], indent=1) if as_json else render(rows))


@app.command("test")
def test_cmd() -> None:
    """One short live call to every provider that has a key (shows which key or model is broken)."""
    load_key_file()
    r = Router()

    async def go() -> int:
        bad = 0
        for p in r.configured():
            try:
                a = await r.ask("Reply with the single word: ok", p.name, max_tokens=8)
                typer.echo(f"ok    {p.name:<12} {a.model}  ({a.tokens} tokens)")
            except RouterError as e:
                bad += 1
                typer.echo(f"FAIL  {p.name:<12} {e}")
        if not r.configured():
            typer.echo("no keys yet: openapi init")
            return 1
        return bad
    raise typer.Exit(asyncio.run(go()))


def _row_of(p: Provider, old: dict | None) -> dict:
    """The catalog row for a provider: its old row (notes, hand-made fields) overlaid with what the code says now."""
    row = dict(old or {})
    row.update(name=p.name, base_url=p.base_url, env=p.env, api=p.api, signup=p.signup, prefer=list(p.prefer), free_only=p.free_only, models=list(p.models))
    row["tags"] = catalog.provider_tags(row)
    return row


def _why(e: BaseException) -> str:
    """Short, key-free reason for a status column."""
    return " ".join(f"{type(e).__name__} {e}".split())[:140]


async def build_catalog(r: Router, old: list[dict], chat: bool = True, only: set[str] | None = None, say: Callable[[str], None] = lambda s: None) -> list[dict]:
    """List every provider (all at once), then one tiny chat call per provider that is usable, one after the other. Returns the new rows (merge them with the old)."""
    today = datetime.date.today().isoformat()
    oldby = {x["name"]: x for x in old}
    provs = [p for p in r.providers.values() if not only or p.name in only]
    sem = asyncio.Semaphore(16)

    async def one(p: Provider) -> dict:
        row = _row_of(p, oldby.get(p.name))
        keyed = bool(os.environ.get(p.env)) if p.env else True
        local = "local" in row["tags"]
        async with sem:
            try:
                if p.models:
                    data = [type("M", (), {"id": i, "model_dump": lambda self=None, i=i: {"id": i}})() for i in p.models]
                else:
                    data = (await asyncio.wait_for(r._client(p).models.list(), 20)).data
                    if not isinstance(data, list):
                        raise ValueError("answered, but not with a model list")
            except (openai.AuthenticationError, openai.PermissionDeniedError):
                row.update(status="needs-key" if not keyed else "FAIL key rejected", checked=today)
                return row
            except (openai.APIConnectionError, openai.APITimeoutError, asyncio.TimeoutError, OSError):
                row.update(status="down" if local else "unreachable", checked=today)
                return row
            except Exception as e:      # a listing failure is a status, not a crash
                row.update(status=("FAIL " if keyed else "no-key ") + _why(e), checked=today)
                return row
        free_provider = "free" in row["tags"] or "keyless" in row["tags"]
        entries = [e for e in (catalog.listed_entry(m, p.free_only) for m in data) if e["id"]]
        row["listed_count"] = len(entries)
        row["free_count"] = sum("free" in e["tags"] for e in entries)
        if keyed or not p.env:       # a public catalog is stored only when we can also use the provider
            row["listed"] = entries
        row.update(status="listed", checked=today)
        return row

    rows = list(await asyncio.gather(*(one(p) for p in provs)))
    if chat:
        for row in rows:
            p = r.providers[row["name"]]
            if row["status"] != "listed" or not (os.environ.get(p.env) if p.env else True) or "local" in row["tags"]:
                continue
            try:
                a = await r.ask("Reply with the single word: ok", p.name, max_tokens=8)
                row.update(status="ok", model=a.model, note="")
            except RouterError as e:
                row.update(status="FAIL " + _why(e).removeprefix("RouterError all providers failed: "))
            say(f"{row['status'][:4]:<5} {p.name}")
            await asyncio.sleep(0.5)       # sequential and unhurried: these are other people's free tiers
    return rows


@app.command("catalog")
def catalog_cmd(no_chat: bool = typer.Option(False, "--no-chat", help="list only, no live call"), only: list[str] = typer.Option([], "--only", help="refresh just these providers"),
                out: Path = typer.Option(None, "--out", help="another file than providers.json"), show: bool = typer.Option(False, "--print", help="print the table, write nothing")) -> None:
    """Write or refresh providers.json: every provider, its status (a live call for each one with a key) and the models of the ones that answer. Appends, never removes."""
    load_key_file()
    target = out or catalog.path()
    old = catalog.load(target)
    r = Router()
    new = asyncio.run(build_catalog(r, old, chat=not no_chat, only=set(only) or None, say=typer.echo))
    rows = catalog.merge(old, new)
    if show:
        for x in rows:
            typer.echo(f"{x['name']:<22} {x.get('status', '-'):<40} {','.join(x['tags'])}")
        return
    typer.echo(f"wrote {catalog.write(rows, datetime.date.today().isoformat(), target)}: {len(rows)} providers, {sum(1 for x in rows if x.get('status') == 'ok')} answered a live call")


@app.command()
def register() -> None:
    """Add this server to Claude Code (user scope)."""
    me = str(Path(__file__).resolve())
    cmd = ["uv", "run", "--quiet", "--script", me] if shutil.which("uv") else [sys.executable, me]
    if not shutil.which("claude"):
        typer.echo("claude not found; run:  claude mcp add openapi -- " + " ".join(cmd))
        raise typer.Exit(1)
    raise typer.Exit(subprocess.call(["claude", "mcp", "add", "--scope", "user", "openapi", "--", *cmd]))


@app.command()
def init(skip_register: bool = typer.Option(False, "--no-register")) -> None:
    """Guided setup: a key per provider (Enter skips), then what is available, then register with Claude Code."""
    load_key_file()
    wizard.run(user_providers(), lambda e: bool(os.environ.get(e)), set_key, typer.echo)
    typer.echo(f"\nkeys in {keys_path()}\n")
    load_key_file()
    typer.echo(render(asyncio.run(Router().survey())))
    if not skip_register and typer.confirm("\nRegister with Claude Code now?", default=True):
        register()


# ------------------------------------------------------------------ every tool as a plain command (CLI first, MCP is the thin wrapper)
# Same code behind both. --json where there is structure; exit 0 done, 1 failed, 2 bad usage, 3 refused (a write shown, not sent).
def _text(arg: str) -> str:
    """'-' reads stdin, '@file' reads a file, anything else is the text itself."""
    if arg == "-":
        return sys.stdin.read()
    if arg.startswith("@"):
        try:
            return Path(arg[1:]).expanduser().read_text()
        except OSError as e:
            raise typer.BadParameter(str(e))
    return arg


def _done(text: str) -> None:
    typer.echo(text)
    raise typer.Exit(1 if text.startswith("error:") else 0)


@app.command("ask")
def ask_cmd(prompt: str = typer.Argument(..., help="the question; - = stdin, @file = a file"), model: str = typer.Option("auto", "--model", "-m", help="auto, provider, or provider/model"),
            task: str = typer.Option("general", "--task", "-t", help="general, code, reason, fast, long"), system: str = typer.Option("", "--system"),
            max_tokens: int = typer.Option(0, "--max-tokens", help="per call; an answer cut by it is continued automatically"), paid: bool = typer.Option(False, "--paid"), public: bool = typer.Option(False, "--public", help="also use suspicious providers (a question with nothing private in it)"),
            as_json: bool = typer.Option(False, "--json")) -> None:
    """Ask one model. A cut-off answer is continued until it ends (OPENAPI_CONTINUE=n limits the extra calls)."""
    try:
        a = asyncio.run(router().ask(_text(prompt), model, system or None, max_tokens or None, task=task, paid=paid or None, public=public))
    except RouterError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(a.__dict__, ensure_ascii=False, indent=1) if as_json else _fmt(a))
    raise typer.Exit(1 if a.truncated else 0)


@app.command("research")
def research_cmd(question: str = typer.Argument(..., help="a general question; - = stdin, @file = a file")) -> None:
    """The question, scrubbed of anything private, to every suspicious provider in parallel. Answers are unverified leads."""
    try:
        clean, removed, res = asyncio.run(router().research(_text(question)))
    except RouterError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"sent as: {clean}" + (f"\nscrubbed: {', '.join(removed)}" if removed else ""))
    for n, r in res:
        typer.echo(f"\n## {n} (suspicious, unverified)\n{_fmt(r) if isinstance(r, Answer) else 'error: ' + str(r)}")


@app.command("queue")
def queue_cmd(file: str = typer.Argument(..., help="task list: one task per line (bullets, numbers and [ ] are stripped; blank lines and # headings skipped); - = stdin"),
              out: str = typer.Option("", "--out", "-o", help="result file, JSONL (default: ~/.cache/openapi/queue/<file name>.answers.jsonl, never next to your file)"),
              jobs: int = typer.Option(4, "--jobs", "-j", help="tasks in flight at once"), model: str = typer.Option("auto", "--model", "-m"),
              task: str = typer.Option("general", "--task", "-t"), prefix: str = typer.Option("", "--prefix", help="text put before every task, e.g. 'Give a short plan for this todo item: '"),
              system: str = typer.Option("", "--system"), skip: str = typer.Option("", "--skip", help="regex: lines matching it are not run (e.g. '-> zrobione|blocked')"),
              resume: bool = typer.Option(False, "--resume", help="skip tasks already answered in the result file"),
              open_only: bool = typer.Option(False, "--open", help="drop status notes ('-> zrobione', '(blocked', 'done', 'green'): only lines that are still to do"),
              context: str = typer.Option("", "--context", "-c", help="background for every task, put in the system prompt; text or @file"),
              max_tokens: int = typer.Option(0, "--max-tokens")) -> None:
    """Many questions at once: each line of FILE is one task, run in parallel with failover; answers are appended to the result file as they arrive, then printed."""
    import taskqueue as q
    text = sys.stdin.read() if file == "-" else _text("@" + file)
    path = Path(out).expanduser() if out else Path("~/.cache/openapi/queue").expanduser() / (("stdin" if file == "-" else Path(file).name) + ".answers.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    tasks = q.parse_tasks(text, "|".join(x for x in (skip, q.STATUS if open_only else "") if x))
    if context:
        system = _text(context) + ("\n\n" + system if system else "")
    if resume:
        have = q.done_ids(path)
        tasks = [t for t in tasks if t[0] not in have]
    if not tasks:
        typer.echo("nothing to run", err=True)
        raise typer.Exit(0)
    typer.echo(f"{len(tasks)} tasks, {jobs} at a time -> {path}", err=True)
    rows = asyncio.run(q.run(router(), tasks, path, jobs, model, task, system or None, prefix, max_tokens or None,
                             on_done=lambda r: typer.echo(f"  line {r['line']}: " + (f"{r['provider']}/{r['model']}" if "error" not in r else "FAILED"), err=True)))
    for r in rows:
        typer.echo(f"\n## line {r['line']}: {r['task'][:100]}\n" + (f"[{r['provider']}/{r['model']}]\n{r['text']}" if "error" not in r else f"error: {r['error']}"))
    raise typer.Exit(1 if any("error" in r for r in rows) else 0)


@app.command("council")
def council_cmd(prompt: str = typer.Argument(...), models: list[str] = typer.Option(..., "--model", "-m", help="repeat for each model"), max_tokens: int = typer.Option(0, "--max-tokens"),
                as_json: bool = typer.Option(False, "--json")) -> None:
    """The same question to several models in parallel."""
    res = asyncio.run(router().council(_text(prompt), models, None, max_tokens or None))
    if as_json:
        typer.echo(json.dumps([r.__dict__ if isinstance(r, Answer) else {"error": str(r)} for r in res], ensure_ascii=False, indent=1))
    else:
        typer.echo("\n\n".join(f"## {m}\n{_fmt(r) if isinstance(r, Answer) else 'error: ' + str(r)}" for m, r in zip(models, res)))


@app.command("models")
def models_cmd(provider: str = typer.Argument("")) -> None:
    """Models a provider (or every provider with a key) offers."""
    _done(asyncio.run(list_models(provider)))


@app.command("best")
def best_cmd(task: str = typer.Argument("general"), top: int = typer.Option(10, "--top"), paid: bool = typer.Option(False, "--paid")) -> None:
    """The top models you could get right now for a task."""
    _done(asyncio.run(best(task, top, paid)))


@app.command("budget")
def budget_cmd() -> None:
    """What is left of each provider's rate limit, cooldowns, OpenRouter credit."""
    _done(asyncio.run(budget()))


@app.command("status")
def status_cmd() -> None:
    """Which providers have a key and what this process used."""
    _done(status())


obsidian_app = typer.Typer(help="Notes in the folder OBSIDIAN_VAULT.", no_args_is_help=True)
notion_app = typer.Typer(help="Notion pages shared with the integration (NOTION_API_KEY).", no_args_is_help=True)
app.add_typer(obsidian_app, name="obsidian")
app.add_typer(notion_app, name="notion")


@obsidian_app.command("search")
def obsidian_search_cmd(query: str, limit: int = typer.Option(20, "--limit")) -> None:
    """Notes whose name or text matches."""
    _done(integrations.obsidian_search(query, limit))


@obsidian_app.command("read")
def obsidian_read_cmd(path: str) -> None:
    """One note, path relative to the vault."""
    _done(integrations.obsidian_read(path))


@obsidian_app.command("write")
def obsidian_write_cmd(path: str, text: str = typer.Argument(..., help="- = stdin, @file = a file"), append: bool = typer.Option(False, "--append")) -> None:
    """Create, overwrite or append to a note."""
    _done(integrations.obsidian_write(path, _text(text), append))


@notion_app.command("search")
def notion_search_cmd(query: str, limit: int = typer.Option(20, "--limit")) -> None:
    """Pages and databases that match."""
    _done(asyncio.run(notion.notion_search(query, limit)))


@notion_app.command("read")
def notion_read_cmd(page_id: str) -> None:
    """The text blocks of one page."""
    _done(asyncio.run(notion.notion_read(page_id)))


@notion_app.command("create")
def notion_create_cmd(parent_page_id: str, title: str, text: str = typer.Argument("", help="- = stdin, @file = a file")) -> None:
    """A new page under a parent page."""
    _done(asyncio.run(notion.notion_create(parent_page_id, title, _text(text))))


@app.command("coderabbit")
def coderabbit_cmd(path: str = typer.Argument("/v1/users")) -> None:
    """Read-only GET against the CodeRabbit admin API."""
    _done(asyncio.run(coderabbit_api.coderabbit_get(path)))


gmail_app = typer.Typer(help="Gmail over its REST API (GOOGLE_CLIENT_ID/SECRET/REFRESH_TOKEN, see src/google.py).", no_args_is_help=True)
outlook_app = typer.Typer(help="Outlook mail over Microsoft Graph (MS_CLIENT_ID/MS_REFRESH_TOKEN, see src/microsoft.py).", no_args_is_help=True)
hosting_app = typer.Typer(help="VPS providers in one shape: hetzner, digitalocean, vultr, linode.", no_args_is_help=True)
app.add_typer(gmail_app, name="gmail")
app.add_typer(outlook_app, name="outlook")
app.add_typer(hosting_app, name="hosting")


@gmail_app.command("search")
def gmail_search_cmd(query: str = typer.Argument(""), limit: int = typer.Option(10, "--limit")) -> None:
    """Messages matching a Gmail search."""
    _done(asyncio.run(google.gmail_search(query, limit)))


@gmail_app.command("read")
def gmail_read_cmd(message_id: str) -> None:
    """One message."""
    _done(asyncio.run(google.gmail_read(message_id)))


@gmail_app.command("draft")
def gmail_draft_cmd(to: str, subject: str, body: str = typer.Argument(..., help="- = stdin, @file = a file")) -> None:
    """Save a draft, nothing is sent."""
    _done(asyncio.run(google.gmail_draft(to, subject, _text(body))))


@gmail_app.command("send")
def gmail_send_cmd(to: str, subject: str, body: str = typer.Argument(..., help="- = stdin, @file = a file"), yes: bool = typer.Option(False, "--yes")) -> None:
    """Send; without --yes it only shows (exit 3)."""
    out = asyncio.run(google.gmail_send(to, subject, _text(body), yes))
    typer.echo(out)
    raise typer.Exit(1 if out.startswith("error:") else 3 if out.startswith("not sent") else 0)


@outlook_app.command("search")
def outlook_search_cmd(query: str = typer.Argument(""), limit: int = typer.Option(10, "--limit")) -> None:
    """Messages matching a text search, empty = newest."""
    _done(asyncio.run(microsoft.outlook_search(query, limit)))


@outlook_app.command("read")
def outlook_read_cmd(message_id: str) -> None:
    """One message."""
    _done(asyncio.run(microsoft.outlook_read(message_id)))


@outlook_app.command("draft")
def outlook_draft_cmd(to: str, subject: str, body: str = typer.Argument(..., help="- = stdin, @file = a file")) -> None:
    """Save a draft, nothing is sent."""
    _done(asyncio.run(microsoft.outlook_draft(to, subject, _text(body))))


@outlook_app.command("send")
def outlook_send_cmd(to: str, subject: str, body: str = typer.Argument(..., help="- = stdin, @file = a file"), yes: bool = typer.Option(False, "--yes")) -> None:
    """Send; without --yes it only shows (exit 3)."""
    out = asyncio.run(microsoft.outlook_send(to, subject, _text(body), yes))
    typer.echo(out)
    raise typer.Exit(1 if out.startswith("error:") else 3 if out.startswith("not sent") else 0)


@hosting_app.command("servers")
def hosting_servers_cmd(provider: str = typer.Argument("all")) -> None:
    """Servers of one provider or all with a key."""
    _done(asyncio.run(hosting.servers(provider)))


@hosting_app.command("plans")
def hosting_plans_cmd(provider: str = typer.Argument("all"), min_ram: float = typer.Option(0, "--ram", help="GB"), min_cpu: int = typer.Option(0, "--cpu"),
                      limit: int = typer.Option(10, "--limit")) -> None:
    """Cheapest plans first."""
    _done(asyncio.run(hosting.plans(provider, min_ram, min_cpu, limit)))


@hosting_app.command("create")
def hosting_create_cmd(provider: str, name: str, plan: str, region: str, image: str, yes: bool = typer.Option(False, "--yes")) -> None:
    """Create a server (hetzner, digitalocean); without --yes it only shows (exit 3)."""
    out = asyncio.run(hosting.create(provider, name, plan, region, image, yes))
    typer.echo(out)
    raise typer.Exit(1 if out.startswith("error:") else 3 if out.startswith("not sent") else 0)


@hosting_app.command("delete")
def hosting_delete_cmd(provider: str, server_id: str, yes: bool = typer.Option(False, "--yes")) -> None:
    """Destroy a server (hetzner, digitalocean); without --yes it only shows (exit 3)."""
    out = asyncio.run(hosting.delete(provider, server_id, yes))
    typer.echo(out)
    raise typer.Exit(1 if out.startswith("error:") else 3 if out.startswith("not sent") else 0)


@app.command("image")
def image_cmd(prompt: str, width: int = typer.Option(1024, "--width"), height: int = typer.Option(1024, "--height")) -> None:
    """Generate a picture, prints the file path."""
    _done(asyncio.run(images.image(_text(prompt), width, height)))


@app.command("speak")
def speak_cmd(text: str = typer.Argument(..., help="the text; - = stdin, @file = a file"), voice: str = typer.Option("", "--voice", "-v", help="provider voice (troy, alloy) or an espeak voice (en-us, pl)")) -> None:
    """Text to speech, prints the wav path: Groq/OpenAI if a key is set, else local espeak-ng."""
    _done(asyncio.run(audio.speak(_text(text), voice)))


@app.command("transcribe")
def transcribe_cmd(path: str, language: str = typer.Option("", "--language", "-l", help="ISO code, e.g. pl; empty = detect")) -> None:
    """Speech to text, prints the transcript: Groq whisper / OpenAI if a key is set, else local whisper-cli (WHISPER_MODEL)."""
    _done(asyncio.run(audio.transcribe(path, language)))


@app.command("jev")
def jev_cmd(state: str = typer.Argument(..., help="the text; - = stdin, @file = a file"), questions: str = typer.Argument(..., help='JSON, e.g. \'{"q1":{"type":"noul","text":"..."}}\''),
            model: str = typer.Option("jev-latest", "--model")) -> None:
    """Typed questions about a text (TypeSafe System One)."""
    try:
        q = json.loads(_text(questions))
    except ValueError as e:
        raise typer.BadParameter(f"questions is not JSON: {e}")
    _done(asyncio.run(jev_api.jev(_text(state), q, model)))


@app.command("video")
def video_cmd(url: str, mode: str = typer.Option("context", "--mode", help="info | transcript | context | audio | video"),
              out: str = typer.Option("", "--out", help="file for the text, or a directory for audio/video")) -> None:
    """A video's context via yt-dlp: title, chapters, description and the transcript as text (saved to --out when given)."""
    _done(asyncio.run(ytdlp.fetch(url, mode, out or None)))


@app.command("blender")
def blender_cmd(mode: str, file: str = typer.Argument("", help="the input file"), out: str = typer.Option("", "--out", help="output directory"),
                arg: str = typer.Argument("", help="format, frame, size, script or expression, by mode")) -> None:
    """Blender headless: version | info FILE | render FILE --out DIR [ARG=frame] | export FILE --out DIR ARG=fmt | script FILE.py [--out DIR]."""
    _done(asyncio.run(blender_app.run(mode, file, out, arg)))


@app.command("gimp")
def gimp_cmd(mode: str, file: str = typer.Argument("", help="the input file"), out: str = typer.Option("", "--out", help="output directory"),
             arg: str = typer.Argument("", help="format, frame, size, script or expression, by mode")) -> None:
    """GIMP 3 batch: version | convert FILE --out DIR ARG=fmt | scale FILE --out DIR ARG=WxH | scriptfu EXPR (Scheme) | python CODE (Python-Fu, @file ok) | script FILE (.py/.scm) | pdb [REGEX] (every PDB procedure)."""
    _done(asyncio.run(gimp_app.run(mode, file, out, arg)))


@app.command("krita")
def krita_cmd(mode: str, file: str = typer.Argument("", help="the input file"), out: str = typer.Option("", "--out", help="output directory"),
              arg: str = typer.Argument("", help="format, frame, size, script or expression, by mode")) -> None:
    """Krita export without a window: version | export FILE --out DIR ARG=fmt."""
    _done(asyncio.run(krita_app.run(mode, file, out, arg)))


@app.command("godot")
def godot_cmd(mode: str, file: str = typer.Argument("", help="the project directory"), out: str = typer.Option("", "--out", help="output directory"),
              arg: str = typer.Argument("", help="format, frame, size, script or expression, by mode")) -> None:
    """Godot 4 headless: version | script PROJECT ARG=file.gd | check PROJECT ARG=file.gd | import PROJECT | export PROJECT --out DIR ARG="PRESET|FILE"."""
    _done(asyncio.run(godot_app.run(mode, file, out, arg)))


@app.command("services")
def services_cmd(name: str = typer.Argument("", help="one service for its auth, docs and examples"), as_json: bool = typer.Option(False, "--json")) -> None:
    """The REST services `api` can drive: cheap VPS and PaaS hosts, DNS, CDN, notes. Add your own in ~/.config/openapi/services.json."""
    try:
        svc = rest.load()
    except rest.RestError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    if name and name not in svc:
        typer.echo(f"error: unknown service '{name}' (known: {', '.join(svc)})", err=True)
        raise typer.Exit(2)
    if as_json:
        typer.echo(json.dumps(svc[name] if name else list(svc.values()), indent=1))
    else:
        typer.echo(rest.info(svc[name]) if name else rest.listing(svc))


@app.command("api")
def api_cmd(service: str, method: str, path: str, query: list[str] = typer.Option([], "-q", "--query", help="k=v, repeatable"),
            data: str = typer.Option("", "-d", "--data", help="JSON body; - = stdin, @file = a file"), yes: bool = typer.Option(False, "--yes", help="send a POST/PUT/PATCH/DELETE (it changes or bills something)"),
            as_json: bool = typer.Option(False, "--json", help="{sent,status,request,body} instead of the bare body"), out: Path = typer.Option(None, "--out", help="write the body to a file")) -> None:
    """Call a REST service with its key from the environment. GET runs at once; a write is shown and exits 3 until --yes."""
    try:
        svc = rest.load()
        if service not in svc:
            typer.echo(f"error: unknown service '{service}' (known: {', '.join(svc)})", err=True)
            raise typer.Exit(2)
        qs = dict(q.split("=", 1) for q in query if "=" in q)
        r = asyncio.run(rest.call(svc[service], method, path, qs, _text(data) if data else None, yes))
    except rest.RestError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1)
    if as_json:
        typer.echo(json.dumps({"sent": r.sent, "status": r.status, "request": r.request, "body": r.text}, ensure_ascii=False, indent=1))
    elif out and r.sent:
        out.write_text(r.text)
        typer.echo(f"{r.status}: {len(r.text)} bytes -> {out}", err=True)
    else:
        typer.echo(r.text if r.sent else f"{r.text}\n{r.request}")
    raise typer.Exit(3 if not r.sent else 0 if r.status < 400 else 1)


@app.command("keys-example")
def keys_example_cmd(out: Path = typer.Option(None, "--out", help="write the file (default: print it)")) -> None:
    """Print or write keys.env.example: one line per key, grouped, from the built-in providers and the catalog."""
    tags = {r["name"]: r.get("tags", []) for r in catalog.load()}
    text = wizard.keys_example(user_providers(), tags)
    if out:
        out.write_text(text)
        typer.echo(f"wrote {out}: {sum(1 for l in text.splitlines() if '=' in l and not l.startswith('#'))} keys", err=True)
    else:
        typer.echo(text, nl=False)


@app.command("dump-api")
def dump_api() -> None:
    """Every command with its one-line help, as JSON: what an agent reads instead of tool schemas."""
    def walk(t: typer.Typer, prefix: str = "") -> list[dict]:
        rows = [{"command": (prefix + (c.name or c.callback.__name__.removesuffix("_cmd").replace("_", "-"))).strip(), "help": (c.help or (c.callback.__doc__ or "")).strip().splitlines()[0] if (c.help or c.callback.__doc__) else ""}
                for c in t.registered_commands]
        for g in t.registered_groups:
            rows += walk(g.typer_instance, prefix + (g.name or "") + " ")
        return rows
    typer.echo(json.dumps(walk(app), indent=1))


if __name__ == "__main__":
    app()

