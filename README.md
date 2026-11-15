# openapi

One file, `openapi.py`: an MCP server over the free tiers of 200+ LLM providers, with failover, plus the CLI that sets it up.
No install step: `uv` reads the dependencies (mcp, openai, typer) from the header of the file.

```sh
./openapi.py init       # a key per provider (hidden input, Enter skips), the table of what is available, register with Claude Code
./openapi.py stats      # providers known / with a key / answering, usable models, public catalogs
./openapi.py test       # one live call per provider with a key
./openapi.py add groq   # one key later;   ./openapi.py register   # claude mcp add
```

## CLI first
Every tool is also a plain command over the same code (MCP is the thin wrapper; `./openapi.py dump-api` lists all commands as JSON):
`ask`, `council`, `models`, `best`, `budget`, `status`, `obsidian search|read|write`, `notion search|read|create`, `coderabbit`, `image`, `jev`, `services`, `api`.
A prompt can be `-` (stdin) or `@file`; `--json` where there is structure. Exit codes: 0 done, 1 failed, 2 bad usage, 3 a write was shown and not sent.

**Answers are not cut off.** When a model stops at the token limit (`finish_reason=length`) the router hands it its own text back and asks it to continue, up to
`OPENAPI_CONTINUE` extra calls (default 8; 0 = off). The footer says how many parts; an answer that is still cut says `STILL TRUNCATED` (and `ask` exits 1).

## One wrapper for every REST API: `api`
`services.json` lists the REST services (cheap VPS and PaaS hosts: Hetzner, Hostinger, Fly.io, DigitalOcean, Vultr, Linode, Scaleway, UpCloud, Render, Koyeb, Railway; Cloudflare, Bunny, Netlify, Vercel; Gandi; GitHub, Notion, CodeRabbit)
with base URL, auth style, key variable, docs link and example calls. Rows marked `verified` had their base URL and auth read in the docs; the rest are from memory.
```sh
./openapi.py services                       # the list, key set or not
./openapi.py services hetzner               # auth, docs, examples
./openapi.py api hetzner GET /servers       # runs at once; the key comes from the environment / keys.env and is never printed
./openapi.py api hetzner POST /servers -d '{"name":"x","server_type":"cpx22","image":"ubuntu-24.04","location":"nbg1"}'   # shown, exit 3, nothing sent
./openapi.py api hetzner POST /servers -d @server.json --yes                                                              # sent (this bills)
```
A path cannot name another host, so a key only ever goes to its own service. The body comes back whole. Your own services (or an override of a built-in) go in `~/.config/openapi/services.json`
(same shape; the file is append-only like the catalog). Not supported: APIs that need request signing or OAuth (OVHcloud, Contabo, Oracle Cloud, AWS). Not run against any live service yet: only a local stand-in server.

Tools Claude gets: `ask(prompt, model="auto")` (`auto` = first provider that answers, `groq`, or `groq/llama-3.3-70b-versatile`),
`council(prompt, models[])`, `list_models`, `stats`, `status`, `services`, `api`. A 429 rests that provider and `auto` moves on; a model a provider
lists but refuses is remembered and skipped; a rejected key drops the provider for the session.

Keys: environment variables or `~/.config/openapi/keys.env` (mode 600 or it is refused). Any other OpenAI-compatible endpoint,
a local Ollama included, is three lines in `~/.config/openapi/providers.toml`:

```toml
[[provider]]
name = "ollama"; base_url = "http://localhost:11434/v1"; env = ""   # env = "" : no key
```

Tests: `.venv/bin/python -m pytest -q` (fake clients and a local HTTP server standing in for a provider) and
`.venv/bin/python tests/stdio_check.py` (the real MCP protocol). Not run against live providers with keys yet.

## The catalog: `providers.json`
One file with every provider (endpoint, key variable, signup link, tags `free` / `keyless` / `local` / `gateway` / `regional` / `search` / `media` ..., `status`, `checked`)
and, for the ones that answer with a key (or need none), their models with tags (`free`, `code`, `reason`, `fast`, `long`, `vision`) and context length.

```sh
./openapi.py catalog                 # list every provider, one tiny live call per usable one (sequential), rewrite providers.json
./openapi.py catalog --no-chat       # listing only, no tokens spent
./openapi.py catalog --only groq     # refresh one or a few;   --print: table to the terminal, write nothing
```
Status: `ok` (a live call worked) | `listed` (catalog answers, no chat tried) | `needs-key` (endpoint exists, answers 401) | `unreachable` | `down` (local server not running) | `FAIL <why>` | `unverified` (endpoint never confirmed).
The file is only ever appended to: a refresh keeps every row it did not produce. A row added by hand (name, base_url, env) is used by the server like a built-in;
the built-in tuple wins on a name clash and `providers.toml` has the last word. Keys are never written to it.

## Notes integrations and setup (all optional)

- Obsidian: set `OBSIDIAN_VAULT` to a vault folder -> tools `obsidian_search`, `obsidian_read`, `obsidian_write` (paths cannot leave the vault).
- Notion (`notion.py`): `NOTION_API_KEY` (internal integration token) -> `notion_search`, `notion_read`, `notion_create`.
- CodeRabbit (`coderabbit.py`): `CODERABBIT_API_KEY` -> read-only `coderabbit` tool for its admin API (not an LLM).
- `./openapi.py init` runs `wizard.py`: one hidden prompt per key with its signup link; Enter skips. Keys go to `~/.config/openapi/keys.env`, never into the repo.
- Images (`images.py`, tool `image`): uses the first of TOGETHER / OPENAI / NEBIUS / FIREWORKS keys that is set (OpenAI-style `/images/generations`), else keyless Pollinations, which is flaky (402/503 on some prompts). Files go to `~/Pictures/openapi` (`IMAGE_DIR` overrides).
- Jev (`jev.py`, tool `jev`): TypeSafe AI System One, typed noul/choice/score answers, `TYPESAFE_API_KEY`. Request shape from the docs quickstart, never run against the live API.

## Choosing models
- `ask(task=...)`: general, code, reason, fast, long. `auto` ranks providers by their best model for the task (name heuristic in `scoring.py`: coder / reasoning / size / fast markers; embeddings, image and speech models are dropped), then fails over as before.
- `paid=true` (or `OPENAPI_PAID=1`): OpenRouter's paid models count too, not only `:free`. Free is still preferred on a tie.
- `best(task)`: the top models you could get right now. `budget()`: remaining requests/tokens from each provider's `x-ratelimit-*` headers, cooldowns, session use and OpenRouter credit. Every `ask` footer shows what is left.
