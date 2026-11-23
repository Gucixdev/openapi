"""providers.json - the one catalog file: every provider (endpoint, key variable, signup link, tags, status) and, for the ones that
answered, their models with tags (free, context, kind). `openapi.py catalog` writes and refreshes it; openapi.py reads it back, so a
provider added to the file (by hand or by a refresh) is used without touching the code. Only ever appended to: a refresh keeps every
row it did not produce itself. Pure functions here, no network (the listing and the live calls live in openapi.py).

Row:  {name, base_url, env, api, signup, tags[], free_only, prefer[], models[], status, checked, note, listed: [{id, tags[], ctx}]}
status: ok (a live call worked) | listed (catalog answers, no chat tried or no key) | no-key | down | FAIL <why> | unverified (endpoint not confirmed)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

SCHEMA = 1
REQUIRED = ("name", "base_url", "env")

# provider tags that are knowledge, not derivable from the row: names with a real free tier (no card), by kind of free
FREE_TIER = frozenset("groq cerebras gemini mistral nvidia openrouter sambanova huggingface github siliconflow zai cohere cloudflare "
                      "requesty aion llmgateway opencode vercel kilo kilo_key longcat hackclub sealion".split())
KEYLESS = frozenset("pollinations ovh kilo llm7 airforce".split())
GATEWAY = frozenset("openrouter requesty kilo kilo_key vercel opencode llmgateway aimlapi portkey helicone glama edenai cometapi aihubmix "
                    "tokenflux poe zenmux martian unify litellm_proxy nanogpt ppq fastrouter llmapi electronhub navy naga ai302 vsegpt".split())
SEARCH = frozenset("exa tavily brave_search serper firecrawl jina_reader you_com perplexity".split())
MEDIA = frozenset("elevenlabs stability fal assemblyai deepgram replicate neets".split())
REGIONAL = re.compile(r"_cn$|^(dashscope|moonshot|zai|hunyuan|volcengine|qianfan|yi|sensenova|spark|stepfun|minimax|baichuan|siliconflow|mimo|qiniu|gitee_ai|sophnet|longcat|bigmodel)$")


def path() -> Path:
    return Path(os.environ.get("OPENAPI_CATALOG", Path(__file__).resolve().parent.parent / "providers.json"))


def provider_tags(row: dict[str, Any]) -> list[str]:
    n, url, env, sign = row["name"], row["base_url"], row.get("env", ""), row.get("signup", "").lower()
    t: set[str] = set(row.get("tags", ()))
    if "localhost" in url or "127.0.0.1" in url:
        t.add("local")
    if not env:
        t.add("keyless")
    if n in KEYLESS:
        t.add("keyless")
    if n in FREE_TIER or row.get("free_only") or "free" in row.get("prefer", ()):
        t.add("free")
    if "no login" in sign or "no card" in sign or "free credit" in sign or "trial" in sign:
        t.add("free-credits" if "credit" in sign or "trial" in sign else "free")
    if "paid" in sign and "free" not in t:
        t.add("paid")
    if n in GATEWAY:
        t.add("gateway")
    if n in SEARCH:
        t.add("search")
    if n in MEDIA:
        t.add("media")
    if REGIONAL.search(n):
        t.add("regional")
    if row.get("api", "openai") != "openai":
        t.add("native-api")
    if "local" not in t and "keyless" not in t and not (t & {"free", "free-credits", "paid"}):
        t.add("credits-or-paid")
    return sorted(t)


def model_tags(mid: str, meta: dict[str, Any], free_provider: bool) -> list[str]:
    """Tags of one listed model from its id and whatever the listing carried (pricing, context, modalities)."""
    m, t = mid.lower(), set()
    price = {k: v for k, v in (meta.get("pricing") or {}).items() if k in ("prompt", "completion")}
    zero = bool(price) and all(str(v).strip() in ("0", "0.0", "0.00", "0.0000000") for v in price.values())
    if m.endswith(":free") or zero or (free_provider and ":free" not in m and meta.get("free")):
        t.add("free")
    if re.search(r"cod(er|ex|estral)|devstral|starcoder", m):
        t.add("code")
    if re.search(r"r1|reason|think|o1|o3|o4|qwq|gpt-oss|magistral", m):
        t.add("reason")
    if re.search(r"flash|instant|mini|haiku|small|lite|\b8b|7b|nano", m):
        t.add("fast")
    if (meta.get("ctx") or 0) >= 200_000 or re.search(r"1m|gemini|kimi", m):
        t.add("long")
    if re.search(r"vision|-vl|vl-|llava|pixtral|multimodal", m) or "image" in str(meta.get("modalities", "")):
        t.add("vision")
    if re.search(r"embed|bge-|e5-|rerank", m):
        t.add("embedding")
    if re.search(r"whisper|tts|speech|audio|transcrib", m):
        t.add("audio")
    if re.search(r"image|diffusion|flux|sdxl|stable-|dall|imagen|veo|sora", m):
        t.add("image-gen")
    return sorted(t)


def model_meta(obj: Any) -> dict[str, Any]:
    """id, context length, pricing and modalities out of one entry of a models listing (the OpenAI client keeps unknown fields as extras)."""
    d: dict[str, Any] = {}
    try:
        d = dict(obj.model_dump())
    except Exception:
        d = {k: getattr(obj, k) for k in ("id", "pricing", "context_length", "context_window") if hasattr(obj, k)}
    top = d.get("top_provider") if isinstance(d.get("top_provider"), dict) else {}
    ctx = d.get("context_length") or d.get("context_window") or d.get("max_model_len") or top.get("context_length") or 0
    arch = d.get("architecture") if isinstance(d.get("architecture"), dict) else {}
    return {"id": str(d.get("id", "")), "ctx": int(ctx) if str(ctx).isdigit() else 0, "pricing": d.get("pricing") if isinstance(d.get("pricing"), dict) else {},
            "modalities": arch.get("input_modalities") or "", "free": bool(d.get("is_free"))}


def listed_entry(obj: Any, free_provider: bool = False) -> dict[str, Any]:
    meta = model_meta(obj)
    out: dict[str, Any] = {"id": meta["id"], "tags": model_tags(meta["id"], meta, free_provider)}
    if meta["ctx"]:
        out["ctx"] = meta["ctx"]
    return out


# ------------------------------------------------------------------ file
def load(p: Path | None = None) -> list[dict[str, Any]]:
    """The rows of the catalog file ([] when there is none). A malformed file raises ValueError saying where, never silently loads half."""
    p = p or path()
    try:
        data = json.loads(p.read_text())
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as e:
        raise ValueError(f"{p}: not valid JSON ({e})") from None
    rows = data.get("providers") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError(f"{p}: expected an object with a 'providers' list")
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or any(k not in r for k in REQUIRED):
            raise ValueError(f"{p}: providers[{i}] needs {', '.join(REQUIRED)}")
    return rows


def extra_rows(rows: list[dict[str, Any]], known: set[str]) -> list[dict[str, Any]]:
    """Catalog rows whose name the code does not already define (built-ins win; the file only adds)."""
    seen, out = set(known), []
    for r in rows:
        if r["name"] not in seen:
            seen.add(r["name"])
            out.append(r)
    return out


def merge(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union by name: a new row replaces the old one of the same name field by field (fields the new row lacks are kept); no old row is dropped."""
    by = {r["name"]: dict(r) for r in old}
    for r in new:
        by[r["name"]] = {**by.get(r["name"], {}), **r}
    return [by[k] for k in sorted(by)]


FIRST = ("name", "status", "tags", "base_url", "env", "signup", "note", "checked")    # what a reader looks for goes on top of each block


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def dump(rows: list[dict[str, Any]], generated: str) -> str:
    """Stable, readable text: providers sorted by name, one block each, one key per line (name, status, tags, endpoint first, the rest sorted),
    short lists on one line, the listed models one per line. Valid JSON; a refresh diffs only what changed."""
    out = ["{", f' "schema": {SCHEMA},', f' "generated": {_j(generated)},', ' "providers": [']
    blocks = []
    for r in sorted(rows, key=lambda r: r["name"]):
        keys = [k for k in FIRST if k in r] + sorted(k for k in r if k not in FIRST)
        lines = []
        for k in keys:
            v = r[k]
            if k == "listed" and v:
                ms = [_j(m) for m in sorted(v, key=lambda m: m["id"])]
                lines.append(f'   "listed": [\n' + ",\n".join("    " + m for m in ms) + "\n   ]")
            else:
                lines.append(f"   {_j(k)}: {_j(v)}")
        blocks.append("  {\n" + ",\n".join(lines) + "\n  }")
    out.append(",\n".join(blocks))
    out += [" ]", "}"]
    return "\n".join(out) + "\n"


def write(rows: list[dict[str, Any]], generated: str, p: Path | None = None) -> Path:
    p = p or path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(dump(rows, generated))
    os.replace(tmp, p)
    return p
