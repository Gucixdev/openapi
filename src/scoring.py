"""Model scoring and rate-limit bookkeeping for openapi.

score(model_id, task): a number, higher = better for that task. It is a heuristic over the model NAME (family, size, coder/reasoning/fast
markers), because most endpoints list nothing but ids. Unknown ids score a neutral 0, so a provider's own `prefer` hints and the
configured order still decide between them. Tasks: general, code, reason, fast, long.

LIMITS: the last x-ratelimit-* headers each provider sent back (remaining requests/tokens, reset), filled by the http response hook.
"""
from __future__ import annotations

import math
import re
import time
from typing import Any

TASKS = ("general", "code", "reason", "fast", "long")

# never a chat answer
_EXCLUDE = re.compile(r"embed|whisper|tts|speech|audio|guard|moderat|rerank|ocr|image|diffusion|flux|sdxl|stable-|dall|imagen|veo|sora|transcrib|bge-|e5-")

# (pattern, base quality). The strongest families first; first match wins, later ones are ignored.
_FAMILY: tuple[tuple[str, float], ...] = (
    (r"claude.*(opus|fable)|fable", 95), (r"gpt-5|o3|o4|gemini-(2\.5|3).*pro|opus", 92),
    (r"kimi-k2|qwen3-coder|deepseek-(v3\.[1-9]|r1)|glm-4\.[5-9]|glm-5|minimax-m2", 88),
    (r"gpt-oss-120b|qwen3-(235|480)|deepseek-v3|mistral-large|llama-4|command-a", 84),
    (r"gemini-2\.5-flash|gemini-3.*flash|sonnet|qwen3-(32|30)|qwen2\.5-coder-32|devstral|codestral", 78),
    (r"llama-3\.3-70|llama-3\.1-405|gpt-oss-20b|mistral-medium|gemma-3-27|qwen2\.5-72", 72),
    (r"llama-3\.1-70|mixtral|mistral-small|gemma-3|qwen", 62),
    (r"llama|gemma|phi|mistral|deepseek|glm|hermes|yi-", 52),
)
_FAMILY_RE = tuple((re.compile(p), s) for p, s in _FAMILY)


def _size_b(mid: str) -> float:
    """Parameter count in billions read from the id ("llama-3.3-70b" -> 70), 0 when the id has none. MoE "a22b" suffixes are ignored."""
    nums = [float(m) for m in re.findall(r"(?<![a-z\d.])(\d+(?:\.\d+)?)b(?![a-z])", mid)]
    return max(nums) if nums else 0.0


def score(model_id: str, task: str = "general") -> float:
    m = model_id.lower()
    if _EXCLUDE.search(m):
        return -1000.0
    base = next((s for rx, s in _FAMILY_RE if rx.search(m)), 40.0)
    known = base > 40.0       # an unknown family earns no task bonus: a local fine-tune that merely has "coder" in its name proved useless
    size = _size_b(m)
    big = min(30.0, 4 * math.log2(size)) if size >= 1 else 0.0
    s = base + (-big if task == "fast" else big * 0.5)
    if re.search(r"preview|exp(erimental)?\b|beta", m):
        s -= 3
    if m.endswith(":free"):
        s -= 1                    # free tiers are rate limited harder: a tie goes to the paid id when paid is allowed
    if not known:
        return s
    if task == "code":
        s += 25 if re.search(r"cod(er|ex|estral)|devstral|starcoder", m) else 0
        s += 6 if re.search(r"deepseek|qwen|kimi|glm|gpt-5|claude|sonnet|opus", m) else 0
    elif task == "reason":
        s += 18 if re.search(r"r1|reason|think|o1|o3|o4|qwq|gpt-oss|magistral|k2-thinking", m) else 0
    elif task == "fast":
        s += 16 if re.search(r"flash|instant|mini|haiku|small|lite|8b|7b|nano", m) else 0
    elif task == "long":
        s += 14 if re.search(r"gemini|kimi|flash|1m|128k|llama-4|qwen3", m) else 0
    return s


def rank(ids: list[str], task: str = "general") -> list[str]:
    """ids best first for the task, stable (ties keep the given order), unusable kinds (embeddings, image, tts...) dropped."""
    scored = [(score(i, task), n, i) for n, i in enumerate(ids)]
    return [i for sc, _, i in sorted(scored, key=lambda t: (-t[0], t[1])) if sc > -500]


# ------------------------------------------------------------------ rate-limit headers
LIMITS: dict[str, dict[str, Any]] = {}
_KEYS = ("limit-requests", "remaining-requests", "limit-tokens", "remaining-tokens", "reset-requests", "reset-tokens")


def record(provider: str, headers: Any) -> None:
    """Remember the rate-limit headers of one response (x-ratelimit-* and ratelimit-*); silent when there are none."""
    got: dict[str, Any] = {}
    for k in _KEYS:
        v = headers.get("x-ratelimit-" + k) or headers.get("ratelimit-" + k)
        if v is not None:
            got[k] = v
    if got:
        got["at"] = time.time()
        LIMITS[provider] = got


def describe(provider: str) -> str:
    g = LIMITS.get(provider)
    if not g:
        return ""
    parts = []
    if "remaining-requests" in g:
        parts.append(f"req {g['remaining-requests']}/{g.get('limit-requests', '?')}")
    if "remaining-tokens" in g:
        parts.append(f"tok {g['remaining-tokens']}/{g.get('limit-tokens', '?')}")
    if "reset-tokens" in g or "reset-requests" in g:
        parts.append("reset " + str(g.get("reset-tokens") or g.get("reset-requests")))
    return ", ".join(parts) + f" ({time.time() - g['at']:.0f}s ago)"
