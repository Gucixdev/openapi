"""proxy: an Anthropic-compatible /v1/messages endpoint in front of the router, so Claude Code (or anything speaking the Anthropic API) runs on
whichever provider is up:   openapi proxy   then   ANTHROPIC_BASE_URL=http://127.0.0.1:8787 ANTHROPIC_AUTH_TOKEN=x claude

Pure functions first (to_openai, from_openai, sse), the HTTP layer last, so the translation is testable without a network.
Streaming is emulated: the router answers whole (it must, to fail over cleanly), then the reply is replayed as Anthropic SSE events."""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

from router import Router, RouterError

STOP = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use", "function_call": "tool_use", "content_filter": "end_turn"}


def _text(x: Any) -> str:
    """tool_result content / system: a string or a list of blocks -> plain text."""
    if isinstance(x, str):
        return x
    return "\n".join(b.get("text", "") for b in (x or []) if isinstance(b, dict) and b.get("type") == "text")


def to_openai(body: dict) -> dict:
    """Anthropic /v1/messages request body -> kwargs for Router.chat (messages, tools, tool_choice, max_tokens, temperature)."""
    msgs: list[dict] = []
    system = _text(body.get("system"))
    if system:
        msgs.append({"role": "system", "content": system})
    for m in body.get("messages", []):
        blocks = [{"type": "text", "text": m["content"]}] if isinstance(m["content"], str) else m["content"]
        if m["role"] == "assistant":
            text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
            calls = [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b.get("input", {}))}}
                     for b in blocks if b.get("type") == "tool_use"]
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            if text or calls:
                msgs.append(msg)
        else:   # user: every tool_result becomes its own role=tool message, in order, before the user's own text
            for b in blocks:
                if b.get("type") == "tool_result":
                    msgs.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": _text(b.get("content")) or ("error" if b.get("is_error") else "ok")})
            text = "".join(b.get("text", "") if b.get("type") == "text" else "[image]" for b in blocks if b.get("type") in ("text", "image"))
            if text:
                msgs.append({"role": "user", "content": text})
    cap = int(os.environ.get("OPENAPI_PROXY_MAX_TOKENS", "8192"))   # Claude Code asks for 32k+; most free models answer 400 to that, and a 400 would blacklist them
    out: dict[str, Any] = {"messages": msgs, "max_tokens": min(body["max_tokens"], cap) if body.get("max_tokens") else None, "temperature": body.get("temperature")}
    tools = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("input_schema") or {"type": "object", "properties": {}}}}
             for t in body.get("tools", []) if "name" in t]
    if tools:
        out["tools"] = tools
        tc = body.get("tool_choice") or {}
        kind = tc.get("type")
        out["tool_choice"] = {"auto": "auto", "any": "required", "none": "none"}.get(kind) or ({"type": "function", "function": {"name": tc["name"]}} if kind == "tool" else None)
    return out


def from_openai(r: Any, model: str) -> dict:
    """Router response (choices[0].message / finish_reason / usage) -> an Anthropic message."""
    ch = r.choices[0]
    content: list[dict] = []
    if ch.message.content:
        content.append({"type": "text", "text": ch.message.content})
    for tc in getattr(ch.message, "tool_calls", None) or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except ValueError:
            args = {}
        content.append({"type": "tool_use", "id": tc.id or "toolu_" + uuid.uuid4().hex[:20], "name": tc.function.name, "input": args if isinstance(args, dict) else {}})
    if not content:
        content.append({"type": "text", "text": ""})
    stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else STOP.get(ch.finish_reason or "stop", "end_turn")
    total = getattr(r.usage, "total_tokens", 0) or 0
    return {"id": "msg_" + uuid.uuid4().hex[:24], "type": "message", "role": "assistant", "model": model, "content": content,
            "stop_reason": stop, "stop_sequence": None, "usage": {"input_tokens": 0, "output_tokens": total}}


def sse(msg: dict) -> list[str]:
    """A whole Anthropic message -> the SSE events a streaming client expects."""
    def ev(name: str, data: dict) -> str:
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"
    head = {**msg, "content": [], "stop_reason": None}
    out = [ev("message_start", {"type": "message_start", "message": head})]
    for i, b in enumerate(msg["content"]):
        if b["type"] == "text":
            out += [ev("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}}),
                    ev("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": b["text"]}})]
        else:
            out += [ev("content_block_start", {"type": "content_block_start", "index": i, "content_block": {"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}}),
                    ev("content_block_delta", {"type": "content_block_delta", "index": i, "delta": {"type": "input_json_delta", "partial_json": json.dumps(b["input"])}})]
        out.append(ev("content_block_stop", {"type": "content_block_stop", "index": i}))
    out += [ev("message_delta", {"type": "message_delta", "delta": {"stop_reason": msg["stop_reason"], "stop_sequence": None}, "usage": {"output_tokens": msg["usage"]["output_tokens"]}}),
            ev("message_stop", {"type": "message_stop"})]
    return out


def pick_model(requested: str) -> str:
    """Claude Code sends claude-* names: those mean 'any provider'. `provider` or `provider/model` is passed through; OPENAPI_PROXY_MODEL overrides."""
    forced = os.environ.get("OPENAPI_PROXY_MODEL")
    if forced:
        return forced
    return "auto" if not requested or requested.startswith("claude") else requested


def log(line: str) -> None:
    """OPENAPI_PROXY_LOG=1: one line per request on stderr (provider/model, tools, messages, why others were skipped); never the content."""
    if os.environ.get("OPENAPI_PROXY_LOG"):
        import sys
        print("proxy " + line, file=sys.stderr, flush=True)


def app(router: Router | None = None) -> Any:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, StreamingResponse
    from starlette.routing import Route

    rt = router or Router()

    def err(status: int, kind: str, msg: str) -> JSONResponse:
        return JSONResponse({"type": "error", "error": {"type": kind, "message": msg}}, status_code=status)

    async def messages(req: Request) -> Any:
        try:
            body = await req.json()
            kw = to_openai(body)
        except (ValueError, KeyError, TypeError) as e:
            return err(400, "invalid_request_error", f"bad request: {e}")
        try:
            nmsg = len(kw["messages"])
            r, p, mid, skipped = await rt.chat(kw.pop("messages"), model=pick_model(body.get("model", "")), task="code" if kw.get("tools") else "general", **kw)
        except RouterError as e:
            log(f"529 tools={len(body.get('tools', []))} msgs={nmsg} {e}")
            return err(529, "overloaded_error", str(e))   # 529 = Anthropic's "overloaded": clients back off and retry
        try:
            msg = from_openai(r, f"{p.name}/{mid}")
        except Exception as e:   # a provider answered with something that is no chat completion: that is a failed call, not a crashed proxy
            log(f"502 {p.name}/{mid} unreadable answer: {type(e).__name__}")
            return err(529, "overloaded_error", f"{p.name} sent an unreadable answer")
        log(f"200 {p.name}/{mid} tools={len(body.get('tools', []))} msgs={nmsg} stop={msg['stop_reason']} skipped={'; '.join(skipped)[:160]}")
        if body.get("stream"):
            return StreamingResponse(iter(sse(msg)), media_type="text/event-stream")
        return JSONResponse(msg)

    async def count(req: Request) -> Any:
        body = await req.json()
        chars = len(json.dumps(body.get("messages", []))) + len(_text(body.get("system"))) + len(json.dumps(body.get("tools", [])))
        return JSONResponse({"input_tokens": chars // 4 + 1})

    async def models(_: Request) -> Any:
        return JSONResponse({"data": [{"id": "auto", "type": "model", "display_name": "openapi auto"}], "has_more": False})

    return Starlette(routes=[Route("/v1/messages", messages, methods=["POST"]), Route("/v1/messages/count_tokens", count, methods=["POST"]),
                             Route("/v1/models", models, methods=["GET"])])


def run(host: str = "127.0.0.1", port: int = 8787) -> None:
    import uvicorn
    uvicorn.run(app(), host=host, port=port, log_level="warning")
