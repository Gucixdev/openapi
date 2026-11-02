#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2", "openai>=3", "typer>=0.12"]
# ///
"""openapi - one file: an MCP server over the free tiers of many LLM providers, with failover, and the CLI that sets it up.

    ./openapi.py init          guided setup: a key per provider (hidden), what is available, register with Claude Code
    ./openapi.py stats         providers / models count        ./openapi.py test   one live call per provider
    ./openapi.py add NAME      set one key                     ./openapi.py        (no arguments) the MCP server on stdio
Tools it gives Claude: ask, council, list_models, stats, status.   Keys: environment, or ~/.config/openapi/keys.env (mode 600).
Any other OpenAI-compatible endpoint: three lines in ~/.config/openapi/providers.toml (see user_providers)."""
from __future__ import annotations

import asyncio
import getpass
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import logging

import openai
import typer
from adapters import ADAPTERS
import coderabbit as coderabbit_api
import integrations
import notion
import wizard
from mcp.server.mcpserver import MCPServer

# ------------------------------------------------------------------ providers: one row each, add yours here or in providers.toml
@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    env: str                          # env var holding the key; "" = no key needed (a local server)
    prefer: tuple[str, ...] = ()      # substrings, best first: the first listed model matching one is the default
    free_only: bool = False           # list only models marked free (OpenRouter: ids ending in ":free")
    signup: str = ""
    models: tuple[str, ...] = ()      # for an API with no model list (Perplexity): these, without asking
    api: str = "openai"               # "openai" = OpenAI chat protocol; others are adapters.py ADAPTERS (anthropic, gemini, cohere, cloudflare)

    def key(self) -> str | None:
        return (os.environ.get(self.env) or None) if self.env else "none"


logging.disable(logging.INFO)   # the http client logs every request at INFO; stdout belongs to the MCP protocol, stderr to errors

P = Provider
PROVIDERS: tuple[Provider, ...] = (
    # free tiers worth having first
    # no key, no login
    P("ollama", "http://localhost:11434/v1", "", (), signup="local: install ollama and pull a model, nothing to sign up for"),
    P("pollinations", "https://text.pollinations.ai/openai", "", ("openai",), signup="no login (anonymous tier, rate limited)"),
    P("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", ("llama-3.3-70b", "gpt-oss-120b", "llama"), signup="https://console.groq.com/keys"),
    P("cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", ("gpt-oss-120b", "llama", "qwen"), signup="https://cloud.cerebras.ai"),
    P("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", ("gemini-2.5-flash", "flash"), signup="https://aistudio.google.com/apikey"),
    P("mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY", ("mistral-small", "mistral-large", "mistral"), signup="https://console.mistral.ai/api-keys"),
    P("nvidia", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", ("llama-3.3-70b", "llama", "deepseek"), signup="https://build.nvidia.com"),
    P("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", ("llama-3.3-70b", "gpt-oss", "deepseek", "qwen"), True, "https://openrouter.ai/keys"),
    P("sambanova", "https://api.sambanova.ai/v1", "SAMBANOVA_API_KEY", ("llama", "deepseek", "qwen"), signup="https://cloud.sambanova.ai/apis"),
    P("huggingface", "https://router.huggingface.co/v1", "HF_TOKEN", ("llama", "qwen", "deepseek"), signup="https://huggingface.co/settings/tokens"),
    P("github", "https://models.github.ai/inference", "GITHUB_TOKEN", ("gpt-4o", "llama"), signup="https://github.com/marketplace/models  (may be closed to new users)"),
    P("siliconflow", "https://api.siliconflow.com/v1", "SILICONFLOW_API_KEY", ("qwen", "deepseek", "glm"), signup="https://cloud.siliconflow.com"),
    P("zai", "https://open.bigmodel.cn/api/paas/v4", "ZAI_API_KEY", ("flash", "glm"), signup="https://z.ai/manage-apikey/apikey-list"),
    P("cohere", "https://api.cohere.ai/compatibility/v1", "COHERE_API_KEY", ("command-r", "command"), signup="https://dashboard.cohere.com/api-keys"),
    # free credits or a cheap paid tier
    P("together", "https://api.together.xyz/v1", "TOGETHER_API_KEY", ("llama-3.3-70b", "free", "llama"), signup="https://api.together.ai/settings/api-keys"),
    P("fireworks", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY", ("llama", "qwen", "deepseek"), signup="https://fireworks.ai/account/api-keys"),
    P("hyperbolic", "https://api.hyperbolic.xyz/v1", "HYPERBOLIC_API_KEY", ("llama", "qwen", "deepseek"), signup="https://app.hyperbolic.xyz/settings"),
    P("novita", "https://api.novita.ai/v3/openai", "NOVITA_API_KEY", ("llama", "qwen", "deepseek"), signup="https://novita.ai/settings/key-management"),
    P("deepinfra", "https://api.deepinfra.com/v1/openai", "DEEPINFRA_API_KEY", ("llama-3.3-70b", "llama", "qwen"), signup="https://deepinfra.com/dash/api_keys"),
    P("nebius", "https://api.studio.nebius.com/v1", "NEBIUS_API_KEY", ("llama", "qwen", "deepseek"), signup="https://studio.nebius.com/settings/api-keys"),
    P("scaleway", "https://api.scaleway.ai/v1", "SCW_SECRET_KEY", ("llama", "qwen", "mistral"), signup="https://console.scaleway.com/iam/api-keys"),
    P("chutes", "https://llm.chutes.ai/v1", "CHUTES_API_KEY", ("deepseek", "qwen", "llama"), signup="https://chutes.ai"),
    P("featherless", "https://api.featherless.ai/v1", "FEATHERLESS_API_KEY", ("llama", "qwen"), signup="https://featherless.ai/account/api-keys"),
    P("venice", "https://api.venice.ai/api/v1", "VENICE_API_KEY", ("llama", "qwen", "deepseek"), signup="https://venice.ai/settings/api"),
    P("aimlapi", "https://api.aimlapi.com/v1", "AIMLAPI_KEY", ("llama", "gpt", "qwen"), signup="https://aimlapi.com/app/keys"),
    P("baseten", "https://inference.baseten.co/v1", "BASETEN_API_KEY", ("deepseek", "llama", "qwen"), signup="https://app.baseten.co/settings/api_keys"),
    P("dashscope", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY", ("qwen-plus", "qwen"), signup="https://bailian.console.alibabacloud.com"),
    P("moonshot", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY", ("kimi", "moonshot"), signup="https://platform.moonshot.ai/console/api-keys"),
    P("deepseek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", ("deepseek-chat", "deepseek"), signup="https://platform.deepseek.com/api_keys"),
    P("xai", "https://api.x.ai/v1", "XAI_API_KEY", ("grok",), signup="https://console.x.ai"),
    # more keyless: no login at all
    P("ovh", "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1", "", ("gpt-oss", "llama", "qwen"), signup="no login: 2 req/min per IP per model; a key (ovhcloud.com/en/public-cloud/ai-endpoints) lifts it"),
    P("kilo", "https://api.kilo.ai/api/gateway", "", ("kilo-auto/free", "free"), signup="no login: ~200 req/hour per IP on free models; key at https://app.kilo.ai/profile"),
    P("llm7", "https://api.llm7.io/v1", "", ("gpt-oss", "nemotron", "codestral"), signup="no login (turbo models); token at https://token.llm7.io lifts limits"),
    P("airforce", "https://api.airforce/v1", "", ("gpt", "llama"), signup="no login; free tier, often in outage"),
    # local servers: nothing to sign up for, each shows as down until you start it
    P("lmstudio", "http://localhost:1234/v1", "", (), signup="local: LM Studio -> Developer -> start server"),
    P("llamacpp", "http://localhost:8080/v1", "", (), signup="local: llama-server -m model.gguf  (also llamafile, LocalAI)"),
    P("jan", "http://localhost:1337/v1", "", (), signup="local: Jan -> Settings -> Local API Server"),
    P("vllm", "http://localhost:8000/v1", "", (), signup="local: vllm serve <model>"),
    # more free-key gateways
    P("requesty", "https://router.requesty.ai/v1", "REQUESTY_API_KEY", ("free",), signup="https://app.requesty.ai (200 req/day on zero-priced models, no card)"),
    P("kilo_key", "https://api.kilo.ai/api/gateway", "KILO_API_KEY", ("free",), signup="https://app.kilo.ai/profile"),
    P("llm7_key", "https://api.llm7.io/v1", "LLM7_API_KEY", ("gpt-oss", "nemotron", "codestral"), signup="https://token.llm7.io (higher limits than anonymous)"),
    P("aion", "https://api.aionlabs.ai/v1", "AION_API_KEY", (), signup="https://www.aionlabs.ai/app/api-keys/ (20K tokens/day, no card)"),
    P("ollama_cloud", "https://ollama.com/v1", "OLLAMA_API_KEY", ("gpt-oss", "qwen", "deepseek"), signup="https://ollama.com/settings/keys"),
    P("modelscope", "https://api-inference.modelscope.cn/v1", "MODELSCOPE_API_KEY", ("qwen", "deepseek"), signup="https://modelscope.cn/my/myaccesstoken (needs Alibaba Cloud binding + real-name check)"),
    P("gonka", "https://proxy.gonkabroker.com/v1", "GONKA_API_KEY", (), signup="https://app.gonkabroker.com/signup (SMS verification, ~1M tokens/month)"),
    P("inferencenet", "https://api.inference.net/v1", "INFERENCE_API_KEY", ("claude", "llama"), signup="https://inference.net"),
    P("vercel", "https://ai-gateway.vercel.sh/v1", "AI_GATEWAY_API_KEY", ("free",), signup="https://vercel.com/ai-gateway"),
    P("opencode", "https://opencode.ai/zen/v1", "OPENCODE_API_KEY", ("free",), signup="https://opencode.ai/zen (free models have a shared limit)"),
    # research / search-grounded
    P("perplexity", "https://api.perplexity.ai", "PERPLEXITY_API_KEY", ("sonar-deep-research", "sonar-reasoning-pro", "sonar-pro", "sonar"), signup="https://www.perplexity.ai/settings/api (paid credits)", models=("sonar", "sonar-pro", "sonar-reasoning-pro", "sonar-deep-research")),
    # more gateways and hosts (URLs answered 200/401 on GET /models; signup pages from memory, not opened)
    P("parasail", "https://api.parasail.io/v1", "PARASAIL_API_KEY", ("deepseek", "qwen", "llama"), signup="https://www.saas.parasail.io/keys"),
    P("upstage", "https://api.upstage.ai/v1", "UPSTAGE_API_KEY", ("solar",), signup="https://console.upstage.ai/api-keys (free credits on signup)"),
    P("reka", "https://api.reka.ai/v1", "REKA_API_KEY", ("reka-flash", "reka"), signup="https://platform.reka.ai/apikeys"),
    P("sarvam", "https://api.sarvam.ai/v1", "SARVAM_API_KEY", ("sarvam",), signup="https://dashboard.sarvam.ai"),
    P("ai21", "https://api.ai21.com/studio/v1", "AI21_API_KEY", ("jamba",), signup="https://studio.ai21.com/account/api-key (trial credit)"),
    P("nscale", "https://inference.api.nscale.com/v1", "NSCALE_API_KEY", ("deepseek", "qwen", "llama"), signup="https://console.nscale.com"),
    P("friendli", "https://api.friendli.ai/serverless/v1", "FRIENDLI_TOKEN", ("llama", "qwen", "deepseek"), signup="https://friendli.ai/suite/~/personal-settings/tokens"),
    P("llmgateway", "https://api.llmgateway.io/v1", "LLMGATEWAY_API_KEY", ("free", "deepseek", "glm"), signup="https://llmgateway.io/dashboard"),
    P("minimax", "https://api.minimax.io/v1", "MINIMAX_API_KEY", ("minimax",), signup="https://platform.minimax.io"),
    P("stepfun", "https://api.stepfun.com/v1", "STEPFUN_API_KEY", ("step",), signup="https://platform.stepfun.com"),
    P("volcengine", "https://ark.cn-beijing.volces.com/api/v3", "ARK_API_KEY", ("doubao", "deepseek"), signup="https://console.volcengine.com/ark"),
    P("hunyuan", "https://api.hunyuan.cloud.tencent.com/v1", "HUNYUAN_API_KEY", ("hunyuan",), signup="https://console.cloud.tencent.com/hunyuan/api-key"),
    P("arliai", "https://api.arliai.com/v1", "ARLIAI_API_KEY", ("llama", "qwen"), signup="https://www.arliai.com/account"),
    P("nanogpt", "https://api.nano-gpt.com/v1", "NANOGPT_API_KEY", ("deepseek", "glm", "kimi"), signup="https://nano-gpt.com/api"),
    P("ppq", "https://api.ppq.ai/v1", "PPQ_API_KEY", ("deepseek", "glm", "kimi"), signup="https://ppq.ai/api-docs"),
    P("writer", "https://api.writer.com/v1", "WRITER_API_KEY", ("palmyra",), signup="https://app.writer.com/aistudio"),
    P("publicai", "https://api.publicai.co/v1", "PUBLICAI_API_KEY", ("apertus", "llama"), signup="https://platform.publicai.co"),
    P("neuralwatt", "https://api.neuralwatt.com/v1", "NEURALWATT_API_KEY", ("qwen", "llama"), signup="https://portal.neuralwatt.com"),
    P("gmi", "https://api.gmi-serving.com/v1", "GMI_API_KEY", ("deepseek", "glm", "kimi"), signup="https://console.gmicloud.ai"),
    P("cortecs", "https://api.cortecs.ai/v1", "CORTECS_API_KEY", ("llama", "mistral", "qwen"), signup="https://cortecs.ai"),
    P("inception", "https://api.inceptionlabs.ai/v1", "INCEPTION_API_KEY", ("mercury",), signup="https://platform.inceptionlabs.ai"),
    P("recursal", "https://api.recursal.com/v1", "RECURSAL_API_KEY", ("rwkv", "qwen"), signup="https://recursal.ai"),
    P("llama_api", "https://api.llama.com/compat/v1", "LLAMA_API_KEY", ("llama",), signup="https://llama.developer.meta.com"),
    P("baichuan", "https://api.baichuan-ai.com/v1", "BAICHUAN_API_KEY", ("baichuan",), signup="https://platform.baichuan-ai.com"),
    P("avian", "https://api.avian.io/v1", "AVIAN_API_KEY", ("deepseek", "llama"), signup="https://avian.io"),
    P("lemonfox", "https://api.lemonfox.ai/v1", "LEMONFOX_API_KEY", ("llama", "mixtral"), signup="https://www.lemonfox.ai"),
    P("electronhub", "https://api.electronhub.ai/v1", "ELECTRONHUB_API_KEY", ("gpt", "claude", "deepseek"), signup="https://electronhub.ai"),
    P("navy", "https://api.navy/v1", "NAVY_API_KEY", ("gpt", "claude"), signup="https://api.navy"),
    P("naga", "https://api.naga.ac/v1", "NAGA_API_KEY", ("gpt", "claude", "gemini"), signup="https://naga.ac"),
    P("mimo", "https://api.xiaomimimo.com/v1", "MIMO_API_KEY", ("mimo",), signup="https://platform.xiaomimimo.com"),
    P("berget", "https://api.berget.ai/v1", "BERGET_API_KEY", ("llama", "mistral"), signup="https://berget.ai"),
    P("fuelix", "https://api.fuelix.ai/v1", "FUELIX_API_KEY", ("gpt", "claude"), signup="https://fuelix.ai"),
    P("ai302", "https://api.302.ai/v1", "AI302_API_KEY", ("gpt", "claude", "deepseek"), signup="https://302.ai"),
    P("chatanywhere", "https://api.chatanywhere.tech/v1", "CHATANYWHERE_API_KEY", ("gpt", "deepseek"), signup="https://github.com/chatanywhere/GPT_API_free"),
    P("zhizengzeng", "https://api.zhizengzeng.com/v1", "ZHIZENGZENG_API_KEY", ("gpt", "claude"), signup="https://zhizengzeng.com"),
    P("atlascloud", "https://api.atlascloud.ai/v1", "ATLASCLOUD_API_KEY", ("deepseek", "qwen", "glm"), signup="https://www.atlascloud.ai"),
    P("ionet", "https://api.intelligence.io.solutions/api/v1", "IOINTELLIGENCE_API_KEY", ("deepseek", "llama", "qwen"), signup="https://ai.io.net"),
    P("vsegpt", "https://api.vsegpt.ru/v1", "VSEGPT_API_KEY", ("gpt", "claude"), signup="https://vsegpt.ru"),
    P("portkey", "https://api.portkey.ai/v1", "PORTKEY_API_KEY", ("gpt",), signup="https://app.portkey.ai"),
    # native APIs through adapters.py
    P("anthropic", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY", ("haiku", "sonnet"), signup="https://console.anthropic.com/settings/keys (paid; a Claude subscription is not an API key)", api="anthropic"),
    P("cloudflare", "https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai", "CLOUDFLARE_API_TOKEN", ("llama", "qwen", "gpt-oss"), signup="https://dash.cloudflare.com/profile/api-tokens (also set CLOUDFLARE_ACCOUNT_ID; 10k neurons/day free)", api="cloudflare"),
    P("openai", "https://api.openai.com/v1", "OPENAI_API_KEY", ("gpt-4o-mini", "gpt"), signup="https://platform.openai.com/api-keys (paid)"),
)


def user_providers(path: Path | None = None) -> tuple[Provider, ...]:
    """Built-in providers plus ~/.config/openapi/providers.toml (OPENAPI_PROVIDERS = another path); a row with a built-in's name replaces it.
    Any OpenAI-compatible endpoint, a local Ollama too (env = "" means no key):
        [[provider]]
        name = "ollama"; base_url = "http://localhost:11434/v1"; env = ""
    """
    path = path or Path(os.environ.get("OPENAPI_PROVIDERS", Path.home() / ".config/openapi/providers.toml"))
    out = {p.name: p for p in PROVIDERS}
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError:
        return PROVIDERS
    for row in data.get("provider", []):
        try:
            out[row["name"]] = Provider(row["name"], row["base_url"], row["env"], tuple(row.get("prefer", ())), bool(row.get("free_only", False)), row.get("signup", ""), tuple(row.get("models", ())), row.get("api", "openai"))
        except KeyError as e:
            raise ValueError(f"{path}: a [[provider]] needs name, base_url and env (missing {e})") from None
    return tuple(out.values())


# ------------------------------------------------------------------ keys: environment first, then a private file
def keys_path() -> Path:
    return Path(os.environ.get("OPENAPI_KEYS", Path.home() / ".config/openapi/keys.env"))


def load_key_file(path: Path | None = None) -> int:
    """Put KEY=VALUE lines of the key file into os.environ (the environment wins). Refuses a file others can read. Returns how many were set."""
    path = path or keys_path()
    try:
        st = path.stat()
    except OSError:
        return 0
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(f"{path}: readable by others, chmod 600 it")
    n = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().removeprefix("export "), v.strip().strip("'\"")
        if k and v and k not in os.environ:
            os.environ[k] = v
            n += 1
    return n


def set_key(env: str, value: str, path: Path | None = None) -> Path:
    """Write ENV=value into the key file (created mode 600, other lines kept, an old line for the same variable replaced)."""
    path = path or keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [l for l in (path.read_text().splitlines() if path.exists() else []) if l.split("=", 1)[0].strip().removeprefix("export ") != env]
    lines.append(f"{env}={value}")
    tmp = path.with_name(path.name + ".tmp")
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return path


# ------------------------------------------------------------------ router: pick provider+model, ask, fail over
COOLDOWN = 60.0          # seconds a provider rests after a 429 without a retry-after
OUTAGE_COOLDOWN = 30.0
MODEL_TRIES = 3          # how many of a provider's models are tried before the next provider


class RouterError(Exception):
    pass


@dataclass
class Answer:
    text: str
    provider: str
    model: str
    tokens: int
    tried: list[str] = field(default_factory=list)     # "provider: why it was skipped", in order


@dataclass
class Survey:
    provider: str
    has_key: bool
    ok: bool             # the endpoint listed its models
    models: int          # usable: after the free filter (OpenRouter), else all listed; 0 without a key
    listed: int          # everything the endpoint listed (a public catalog counts even without a key)
    ms: int
    error: str


@dataclass
class Stat:
    calls: int = 0
    tokens: int = 0
    errors: int = 0
    last_error: str = ""


def _factory(p: Provider) -> Any:
    if p.api != "openai":
        return ADAPTERS[p.api](p.base_url, p.key() or "none")
    if not p.env:   # keyless: send no Authorization header at all (some endpoints reject a bogus bearer)
        async def strip(req: Any) -> None:
            req.headers.pop("authorization", None)
        import httpx2
        return openai.AsyncOpenAI(base_url=p.base_url, api_key="none", max_retries=0, timeout=60, http_client=httpx2.AsyncClient(event_hooks={"request": [strip]}))
    return openai.AsyncOpenAI(base_url=p.base_url, api_key=p.key() or "none", max_retries=0, timeout=60)


async def _stream(resp: Any) -> Any:
    """Read a streamed completion to the end; the result looks like a non-streamed one (.choices[0].message.content, .usage.total_tokens)."""
    parts: list[str] = []
    tokens = n = 0
    async for ch in resp:
        n += 1
        if ch.choices and ch.choices[0].delta.content:
            parts.append(ch.choices[0].delta.content)
        if getattr(ch, "usage", None):
            tokens = ch.usage.total_tokens or 0
    from types import SimpleNamespace as NS
    return NS(choices=[NS(message=NS(content="".join(parts)))], usage=NS(total_tokens=tokens or n))


def _retry_after(e: openai.RateLimitError) -> float:
    try:
        return min(600.0, max(1.0, float(e.response.headers.get("retry-after", COOLDOWN))))
    except (AttributeError, TypeError, ValueError):
        return COOLDOWN


class Router:
    def __init__(self, providers: tuple[Provider, ...] | None = None, client_factory: Callable[[Provider], Any] = _factory, clock: Callable[[], float] = time.monotonic):
        self.providers = {p.name: p for p in (providers if providers is not None else user_providers())}
        self._factory, self._clock = client_factory, clock
        self._clients: dict[str, Any] = {}
        self._models: dict[str, list[str]] = {}
        self._raw: dict[str, int] = {}           # models listed before the free filter
        self._bad: dict[str, set[str]] = {}      # provider -> models it listed but refused (not for this key/plan)
        self._rest: dict[str, float] = {}        # provider -> clock() until which it is skipped
        self._dead: set[str] = set()             # provider -> rejected our key this session
        self.stats: dict[str, Stat] = {n: Stat() for n in self.providers}

    def configured(self) -> list[Provider]:
        return [p for p in self.providers.values() if p.key() and p.name not in self._dead]

    def resting(self, name: str) -> float:
        return max(0.0, self._rest.get(name, 0.0) - self._clock())

    def _client(self, p: Provider) -> Any:
        if p.name not in self._clients:
            self._clients[p.name] = self._factory(p)
        return self._clients[p.name]

    async def models(self, p: Provider, refresh: bool = False) -> list[str]:
        if p.models:
            self._raw[p.name] = len(p.models)
            return list(p.models)
        if refresh or p.name not in self._models:
            ids = sorted(m.id for m in (await self._client(p).models.list()).data)
            self._raw[p.name] = len(ids)
            self._models[p.name] = [i for i in ids if i.endswith(":free")] if p.free_only else ids
        return self._models[p.name]

    @staticmethod
    def ranked(p: Provider, ids: list[str]) -> list[str]:
        """The provider's models, the ones matching its hints first (in hint order), the rest after."""
        out: list[str] = []
        for hint in p.prefer:
            out += [i for i in ids if hint in i.lower() and i not in out]
        return out + [i for i in ids if i not in out]

    @staticmethod
    def choose(p: Provider, ids: list[str]) -> str | None:
        return next(iter(Router.ranked(p, ids)), None)

    async def survey(self, timeout: float = 20.0) -> list[Survey]:
        """Ask every provider what it offers, all at once. Without a key the public catalog (if there is one) still counts as listed."""
        async def one(p: Provider) -> Survey:
            keyed = bool(os.environ.get(p.env)) if p.env else True
            t0 = self._clock()
            try:
                ids = await asyncio.wait_for(self.models(p, refresh=True), timeout)
            except Exception as e:  # any failure is a row in the table, not a crash
                return Survey(p.name, keyed, False, 0, 0, round((self._clock() - t0) * 1000), type(e).__name__ if keyed else "no key")
            return Survey(p.name, keyed, True, len(ids) if keyed else 0, self._raw.get(p.name, len(ids)), round((self._clock() - t0) * 1000), "")
        return list(await asyncio.gather(*(one(p) for p in self.providers.values())))

    def plan(self, spec: str) -> list[tuple[Provider, str | None]]:
        """"auto" / "groq" / "groq/llama-3.3-70b-versatile" -> an ordered list of (provider, model or None = its preferred ones)."""
        if spec in ("", "auto"):
            return [(p, None) for p in self.configured()]
        head, _, rest = spec.partition("/")
        if head in self.providers:
            p = self.providers[head]
            if not p.key():
                raise RouterError(f"{p.name}: no key (set {p.env})")
            return [(p, rest or None)]
        raise RouterError(f"unknown provider '{head}' (known: {', '.join(self.providers)}); use auto, provider or provider/model")

    async def ask(self, prompt: str, model: str = "auto", system: str | None = None, max_tokens: int | None = None, temperature: float | None = None) -> Answer:
        plan, explicit = self.plan(model), model not in ("", "auto")
        if not plan:
            raise RouterError("no provider has a key: openapi init  (or set one of " + ", ".join(p.env for p in self.providers.values() if p.env) + ")")
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        tried: list[str] = []
        for p, want in plan:
            if not explicit and self.resting(p.name):
                tried.append(f"{p.name}: resting {self.resting(p.name):.0f}s")
                continue
            try:
                cands = [want] if want else [m for m in self.ranked(p, await self.models(p)) if m not in self._bad.get(p.name, ())][:MODEL_TRIES]
                if not cands:
                    tried.append(f"{p.name}: no models")
                    continue
                r, mid = await self._first_model(p, cands, msgs, max_tokens, temperature, tried, explicit=bool(want))
            except openai.AuthenticationError as e:
                self._dead.add(p.name)
                self._fail(p, f"key rejected ({e.status_code})", tried)
            except openai.RateLimitError as e:
                self._rest[p.name] = self._clock() + _retry_after(e)
                self._fail(p, "rate limited", tried)
            except (openai.APIConnectionError, openai.APITimeoutError):
                self._rest[p.name] = self._clock() + OUTAGE_COOLDOWN
                self._fail(p, "unreachable", tried)
            except openai.APIStatusError as e:
                if e.status_code >= 500:
                    self._rest[p.name] = self._clock() + OUTAGE_COOLDOWN
                self._fail(p, f"HTTP {e.status_code}", tried)
            else:
                tokens = getattr(r.usage, "total_tokens", 0) or 0
                st = self.stats[p.name]
                st.calls, st.tokens = st.calls + 1, st.tokens + tokens
                return Answer((r.choices[0].message.content or "") if r.choices else "", p.name, mid, tokens, tried)
        raise RouterError("all providers failed: " + "; ".join(tried))

    async def _first_model(self, p: Provider, cands: list[str], msgs: list[dict], max_tokens: int | None, temperature: float | None, tried: list[str], explicit: bool) -> tuple[Any, str]:
        """Try the provider's candidate models in order: one it lists but refuses (404/400/403) is remembered as bad and the next is tried."""
        last: Exception | None = None
        for mid in cands:
            kw: dict[str, Any] = {"model": mid, "messages": msgs}
            if max_tokens:        # unset = the provider's own limit; streaming means a long answer never hits a read timeout
                kw["max_tokens"] = max_tokens
            if temperature is not None:
                kw["temperature"] = temperature
            try:
                c = self._client(p)
                if isinstance(c, openai.AsyncOpenAI):
                    return await _stream(await c.chat.completions.create(stream=True, stream_options={"include_usage": True}, **kw)), mid
                return await c.chat.completions.create(**kw), mid
            except (openai.NotFoundError, openai.BadRequestError, openai.PermissionDeniedError, openai.UnprocessableEntityError) as e:
                if explicit:
                    raise
                self._bad.setdefault(p.name, set()).add(mid)
                tried.append(f"{p.name}/{mid}: refused ({e.status_code})")
                last = e
        assert last is not None
        raise last

    def _fail(self, p: Provider, why: str, tried: list[str]) -> None:
        st = self.stats[p.name]
        st.errors, st.last_error = st.errors + 1, why
        tried.append(f"{p.name}: {why}")

    async def council(self, prompt: str, models: list[str], system: str | None = None, max_tokens: int | None = None) -> list[Answer | RouterError]:
        """The same question to several models at once; each result is an Answer or the error that stopped that model."""
        return await asyncio.gather(*(self.ask(prompt, m, system, max_tokens) for m in models), return_exceptions=True)  # type: ignore[return-value]


def render(rows: list[Survey]) -> str:
    """The survey as a plain-text table with the totals line (used by `stats` and the stats tool)."""
    w = max([len(r.provider) for r in rows] + [8])
    out = [f"{'provider':<{w}}  {'key':<4} {'status':<14} {'usable':>6} {'listed':>6} {'ms':>6}"]
    for r in rows:
        status = ("ok" if r.has_key else "catalog only") if r.ok else ("no key" if not r.has_key else f"FAIL {r.error}")
        out.append(f"{r.provider:<{w}}  {'yes' if r.has_key else '-':<4} {status:<14} {r.models if r.ok else '-':>6} {r.listed if r.ok else '-':>6} {r.ms if r.ok or r.has_key else '-':>6}")
    keyed, live = [r for r in rows if r.has_key], [r for r in rows if r.ok and r.has_key]
    out += ["", f"{len(rows)} providers known, {len(keyed)} with a key, {len(live)} answering; {sum(r.models for r in live)} usable models ({sum(r.listed for r in live)} listed)",
            f"public catalogs without a key: {sum(r.listed for r in rows if r.ok and not r.has_key)} more models listed by {sum(1 for r in rows if r.ok and not r.has_key)} providers (need a key to use)"]
    if len(keyed) < len(rows):
        out.append("no key yet: " + ", ".join(r.provider for r in rows if not r.has_key) + "   (openapi init)")
    return "\n".join(out)


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
    return f"{a.text}\n\n[{a.provider}/{a.model}, {a.tokens} tokens]" + (f"\n[skipped: {'; '.join(a.tried)}]" if a.tried else "")


@mcp.tool()
async def ask(prompt: str, model: str = "auto", system: str = "", max_tokens: int = 0) -> str:
    """Ask another model. model: "auto" (first provider that answers, failing over on rate limits), a provider ("groq"), or "provider/model-id"."""
    try:
        return _fmt(await router().ask(prompt, model, system or None, max_tokens or None))
    except RouterError as e:
        return f"error: {e}"


@mcp.tool()
async def council(prompt: str, models: list[str], max_tokens: int = 0) -> str:
    """Ask several models the same question in parallel and show every answer side by side (models as for ask)."""
    res = await router().council(prompt, models, None, max_tokens or None)
    return "\n\n".join(f"## {m}\n{_fmt(r) if isinstance(r, Answer) else 'error: ' + str(r)}" for m, r in zip(models, res))


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


# ------------------------------------------------------------------ the command line
app = typer.Typer(add_completion=False, help="MCP server over many free LLM providers. No arguments = start the server; init = guided setup.", invoke_without_command=True)


@app.callback()
def _default(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        mcp.run()


@app.command()
def serve() -> None:
    """Start the MCP server on stdio."""
    mcp.run()


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


if __name__ == "__main__":
    app()
