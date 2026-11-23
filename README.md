# openapi

Sources in `src/` (the server is `src/openapi.py`): an MCP server over the free tiers of 200+ LLM providers, with failover, plus the CLI that sets it up.

Layout of `src/`: `openapi.py` (entry point only), `providers.py` (provider rows and the key file), `router.py` (pick, ask, fail over, continue a cut answer), `mcp_server.py` (the tools Claude gets), `cli.py` (the commands), `scoring.py` (model ratings and limits), and one small module per integration (`rest`, `notion`, `google`, `microsoft`, `hosting`, `images`, `jev`, `coderabbit`, `integrations`, `adapters`, `catalog`, `wizard`).
No install step: `uv` reads the dependencies (mcp, openai, typer) from the header of the file.

```sh
src/openapi.py init       # a key per provider (hidden input, Enter skips), the table of what is available, register with Claude Code
src/openapi.py stats      # providers known / with a key / answering, usable models, public catalogs
src/openapi.py test       # one live call per provider with a key
src/openapi.py add groq   # one key later;   src/openapi.py register   # claude mcp add
```

## CLI first
Every tool is also a plain command over the same code (MCP is the thin wrapper; `src/openapi.py dump-api` lists all commands as JSON):
`ask`, `council`, `models`, `best`, `budget`, `status`, `obsidian search|read|write`, `notion search|read|create`, `gmail search|read|draft|send`, `outlook search|read|draft|send`, `hosting servers|plans|create|delete`, `coderabbit`, `image`, `speak`, `transcribe`, `jev`, `video`, `services`, `api`.
A prompt can be `-` (stdin) or `@file`; `--json` where there is structure. Exit codes: 0 done, 1 failed, 2 bad usage, 3 a write was shown and not sent.

**Answers are not cut off.** When a model stops at the token limit (`finish_reason=length`) the router hands it its own text back and asks it to continue, up to
`OPENAPI_CONTINUE` extra calls (default 8; 0 = off). The footer says how many parts; an answer that is still cut says `STILL TRUNCATED` (and `ask` exits 1).

## Claude Code through the router: `proxy`
```sh
src/openapi.py proxy                                            # 127.0.0.1:8787, an Anthropic-compatible /v1/messages
ANTHROPIC_BASE_URL=http://127.0.0.1:8787 ANTHROPIC_AUTH_TOKEN=x claude
```
Tool calls are translated both ways, so an agent loop works; the request goes through the same failover as `ask` (a 402/429/5xx or a model that refuses tools moves on to the next), and when every provider fails the client gets `529 overloaded_error` and backs off. `claude-*` model names mean `auto`; `provider` or `provider/model` is passed through (`OPENAPI_PROXY_MODEL` forces one). `max_tokens` is capped at `OPENAPI_PROXY_MAX_TOKENS` (8192) because most free models answer 400 to Claude Code's 32k. Streaming is replayed from a whole answer (failover needs the whole answer first), so text arrives at once. Local servers (ollama, llama.cpp) are tried last unless `OPENAPI_LOCAL_FIRST=1`.
Run against live providers once (a tool call and a stream), not yet for a long agent session; how good the free models are at tools is the open question.

## Verified: `verify/run`
Lean 4 (`verify/Failover.lean`, `verify/Translate.lean`, core only, no `sorry`): the answer comes from the first provider that answers, none after it is called, "no answer" means every provider was tried, the loop is bounded; the Anthropic to OpenAI translation keeps every tool id in order and puts the `tool` messages straight after the assistant message that announced them. These prove a model of the code; `tests/test_router.py` and `tests/test_proxy.py` check the same properties on the real code. AMPL (`verify/signup.mod`, HiGHS via amplpy) picks which free providers to sign up for next from the catalog; its three weights are a policy, not a measurement, and ties are arbitrary. `verify/run` runs all of it, no arguments. `verify/toolprobe.py` asks every provider with a key for a full tool round trip (call, result, answer); 2026-11-23: groq, cohere, llm7, pollinations passed, aion answered without calling, six were down (402, 401, rate limit).

## One wrapper for every REST API: `api`
`services.json` lists the REST services (cheap VPS and PaaS hosts: Hetzner, Hostinger, Fly.io, DigitalOcean, Vultr, Linode, Scaleway, UpCloud, Render, Koyeb, Railway; Cloudflare, Bunny, Netlify, Vercel; Gandi; Stripe, Sentry, Resend, SendGrid, Linear; GitHub, Notion, CodeRabbit)
with base URL, auth style, key variable, docs link and example calls. Rows marked `verified` had their base URL and auth read in the docs; the rest are from memory.
```sh
src/openapi.py services                       # the list, key set or not
src/openapi.py services hetzner               # auth, docs, examples
src/openapi.py api hetzner GET /servers       # runs at once; the key comes from the environment / keys.env and is never printed
src/openapi.py api hetzner POST /servers -d '{"name":"x","server_type":"cpx22","image":"ubuntu-24.04","location":"nbg1"}'   # shown, exit 3, nothing sent
src/openapi.py api hetzner POST /servers -d @server.json --yes                                                              # sent (this bills)
```
A path cannot name another host, so a key only ever goes to its own service. The body comes back whole. Your own services (or an override of a built-in) go in `~/.config/openapi/services.json`
(same shape; the file is append-only like the catalog). Not supported: APIs that need request signing or OAuth (OVHcloud, Contabo, Oracle Cloud, AWS). Not run against any live service yet: only a local stand-in server.

Tools Claude gets: `ask(prompt, model="auto")` (`auto` = first provider that answers, `groq`, or `groq/llama-3.3-70b-versatile`),
`council(prompt, models[])`, `list_models`, `stats`, `status`, `services`, `api`. A 429 rests that provider and `auto` moves on; a model a provider
lists but refuses is remembered and skipped; a rejected key drops the provider for the session.

`./signup` opens the signup page of the next free providers you hold no key for (direct first), then asks for the key (hidden) and stores it; `DRY=1 ./signup` only lists them. Without any keys these already answer: blockrun (NVIDIA-hosted free models, tools work, `nemotron-3-ultra-550b` among them), pollinations, ovh (2 req/min), llm7 (anonymous), chat_at (text only, no tools), plus any local server. **Suspicious** (`suspicious=True` in `providers.py`): apisfreellm.com and chat_at have an operator we could not verify, so they are used only for a plain question or research and never for anything private. `auto` leaves them out; they answer when named (`--model apisfreellm`) or with `--public` / `public=true`, never with tools (so never through the proxy), and `privacy.py` refuses the call when the prompt holds a file path, code block, e-mail, IP, key or token, `NAME=value`, or your user or host name (the error names the kind, never the text). Used on purpose, not just allowed: `research "question"` (MCP `research`) scrubs the question (`<path>`, `<email>`, `<ip>`, `<secret>`, `<name>`; code blocks are refused, ask the idea not the code), sends it to every suspicious provider in parallel and prints what was sent, what was scrubbed and each answer as an unverified lead; and `ask` falls back to them, for a clean question and only when every normal provider failed (`OPENAPI_NO_SUSPICIOUS_FALLBACK=1` turns that off). Not added: AI Horde (no answer in 90 s), mlvoca (tiny models), anonymous OpenRouter (now 401).

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
src/openapi.py catalog                 # list every provider, one tiny live call per usable one (sequential), rewrite providers.json
src/openapi.py catalog --no-chat       # listing only, no tokens spent
src/openapi.py catalog --only groq     # refresh one or a few;   --print: table to the terminal, write nothing
```
Status: `ok` (a live call worked) | `listed` (catalog answers, no chat tried) | `needs-key` (endpoint exists, answers 401) | `unreachable` | `down` (local server not running) | `FAIL <why>` | `unverified` (endpoint never confirmed).
The file is only ever appended to: a refresh keeps every row it did not produce. A row added by hand (name, base_url, env) is used by the server like a built-in;
the built-in tuple wins on a name clash and `providers.toml` has the last word. Keys are never written to it.

`providers.json` is written as one block per provider, one key per line, short lists on one line, listed models one per line (valid JSON, 5.6k lines instead of 21k, a refresh diffs only what changed).
`keys.env.example` is generated, one line per key grouped by kind (`src/openapi.py keys-example --out keys.env.example`); do not edit it by hand.

## Notes integrations and setup (all optional)

- Obsidian: set `OBSIDIAN_VAULT` to a vault folder -> tools `obsidian_search`, `obsidian_read`, `obsidian_write` (paths cannot leave the vault).
- Notion (`notion.py`): `NOTION_API_KEY` (internal integration token) -> `notion_search`, `notion_read`, `notion_create`.
- CodeRabbit (`coderabbit.py`): `CODERABBIT_API_KEY` -> read-only `coderabbit` tool for its admin API (not an LLM).
- Gmail (`google.py`): OAuth refresh token in `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` (or a short-lived `GOOGLE_ACCESS_TOKEN`) -> `gmail_search`, `gmail_read`, `gmail_draft`, `gmail_send`. Sending needs `--yes`/`yes=True`. Calendar and Drive would live in the same file.
- Outlook (`microsoft.py`): `MS_CLIENT_ID`, `MS_REFRESH_TOKEN` (`MS_CLIENT_SECRET` for a confidential app, `MS_TENANT` default `common`) -> `outlook_search`, `outlook_read`, `outlook_draft`, `outlook_send`, over Microsoft Graph.
- Hosting (`hosting.py`): one shape over Hetzner, DigitalOcean, Vultr and Linode (keys as in `services.json`) -> `hosting_servers`, `hosting_plans` (cheapest first), `hosting_create` and `hosting_delete` (Hetzner and DigitalOcean; shown and not sent without `--yes`). Anything else: `openapi api`.
- The Gmail, Graph and hosting paths and field names are from the providers' documentation and have only run against a local stand-in server (`tests/test_mail_hosting.py`), not a real account.
- `src/openapi.py init` runs `wizard.py`: one hidden prompt per key with its signup link; Enter skips. Keys go to `~/.config/openapi/keys.env`, never into the repo.
- Speech (`audio.py`, tools `speak` and `transcribe`): speech to text uses Groq `whisper-large-v3-turbo` or OpenAI `whisper-1` when the key is set (files up to 25 MB), then ElevenLabs `scribe_v1` (`ELEVENLABS_API_KEY`), else a local `whisper-cli` with `WHISPER_MODEL=/path/ggml-*.bin`. Text to speech tries Groq Orpheus (the account admin must accept the model terms once in the Groq console) and OpenAI `tts-1`, then ElevenLabs (`eleven_flash_v2_5`, mp3, `voice` = a voice id), else local `espeak-ng` (robotic but offline). Spoken files go to `~/Music/openapi` (`AUDIO_DIR` overrides).
- Images (`images.py`, tool `image`): uses the first of TOGETHER / OPENAI / NEBIUS / FIREWORKS keys that is set (OpenAI-style `/images/generations`), else keyless Pollinations, which is flaky (402/503 on some prompts). Files go to `~/Pictures/openapi` (`IMAGE_DIR` overrides).
- Jev (`jev.py`, tool `jev`): TypeSafe AI System One, typed noul/choice/score answers, `TYPESAFE_API_KEY`. Request shape from the docs quickstart, never run against the live API.
- Video (`ytdlp.py`, tool `video`): wraps the installed `yt-dlp`, no key; mode info | transcript | context (both, written to `--out`) | audio | video. Tested against a fake `yt-dlp`, never against YouTube.
- Apps (`src/apps/`, tools `blender` `gimp` `krita` `godot`): headless wrappers over each program's own CLI, output only into an explicit `--out` dir; PATH first, then the flatpak. Needs the program installed (blender: not here).
- `blender` version | info | render | export glb/obj/fbx/stl/usd/ply | script; `gimp` version | convert | scale | scriptfu (GIMP 3, throwaway profile); `krita` version | export; `godot` version | script | check | import | export.
- Run for real here: godot 4.7 (version, script, check), GIMP 3.2.6 and Krita 5.3.4 via flatpak (version, convert, scale, scriptfu, export). Blender, godot import/export are only tested against fakes. Never aim godot at a project open in the editor.

## Choosing models
- `ask(task=...)`: general, code, reason, fast, long. `auto` ranks providers by their best model for the task (name heuristic in `scoring.py`: coder / reasoning / size / fast markers; embeddings, image and speech models are dropped), then fails over as before.
- Everything available is used, in order: free and free-tier providers first (best model first), a free_only provider's paid models after its free ones, paid-only providers (`paid_only=True`: anthropic, openai, perplexity) last. Only providers with a key take part. `OPENAPI_FREE_ONLY=1` (or `paid=false` via the library) keeps it to free; `OPENAPI_ONLY=a,b` / `OPENAPI_EXCLUDE=a,b` limit the pool.
- `best(task)`: the top models you could get right now. `budget()`: remaining requests/tokens from each provider's `x-ratelimit-*` headers, cooldowns, session use and OpenRouter credit. Every `ask` footer shows what is left.


## Queue: many questions at once

`openapi queue FILE` runs every line of FILE as its own task, `-j N` at a time (default 4), each with the normal failover (free first, paid last). Bullets, numbers and `[ ]` are stripped; blank lines and `#` headings are skipped. Each answer is appended to `FILE.answers.jsonl` (`-o` to change) the moment it arrives, so an interrupted run loses nothing; `--resume` skips lines already answered, and a failed line is retried. `--prefix` goes before every task, `--skip REGEX` drops lines, `-t`/`-m` pick task kind and model. Never uses the suspicious providers. The answers are drafts and plans, not work done: the models cannot touch your files.

    openapi queue todo.md --skip 'zrobione|blocked' --prefix 'Give a 3-line plan for this todo item: ' -j 4

### todoq: a todo list with the repo as context

`src/todoq FILE` (one bash file) turns every open line of a todo list into one question and puts the repo the line names (git status, last commits, file list, README head, a few grep hits; keys masked) in front of it, so the answer uses real paths and not invented ones. It asks through `openapi ask`, `-j N` at a time (default 4), saves one `NNNN.md` per line under `~/.cache/todoq/<FILE name>` and reuses saved answers on the next run. `-n` is a dry run, `-a` also asks status notes, `-f` asks again, `-r DIR` is the folder holding the repos. Nothing touches your files; answers are drafts and plans. Tests: `bats tests/todoq.bats`. The difference to `queue`: `queue` sends the bare line, `todoq` sends the line plus the repo.
