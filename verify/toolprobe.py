"""Which providers/models really call a tool? One request per provider through Router.chat, then the tool result is sent back and the model must answer from it.
Prints a table and exits 0. Run:  .venv/bin/python verify/toolprobe.py   (spends a few tokens on every provider with a key)"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import proxy  # noqa: E402
from providers import load_key_file  # noqa: E402
from router import Router  # noqa: E402

TOOLS = [{"name": "get_temp", "description": "Current temperature in a city, in Celsius", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}]


async def probe(rt: Router, spec: str) -> str:
    q = {"model": "x", "max_tokens": 400, "tools": TOOLS, "messages": [{"role": "user", "content": "What is the temperature in Gdansk right now? Use the tool."}]}
    try:
        kw = proxy.to_openai(q)
        r, p, mid, _ = await asyncio.wait_for(rt.chat(kw.pop("messages"), model=spec, task="code", **kw), 60)
        m = proxy.from_openai(r, f"{p.name}/{mid}")
        use = next((b for b in m["content"] if b["type"] == "tool_use"), None)
        if not use or use["name"] != "get_temp" or "city" not in use["input"]:
            return f"NO-CALL     {mid}"
        q["messages"] += [{"role": "assistant", "content": m["content"]}, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": use["id"], "content": "17"}]}]
        kw = proxy.to_openai(q)
        r2, _, _, _ = await asyncio.wait_for(rt.chat(kw.pop("messages"), model=spec, task="code", **kw), 60)
        txt = "".join(b.get("text", "") for b in proxy.from_openai(r2, "x")["content"])
        return f"{'OK' if '17' in txt else 'NO-ANSWER':<11} {mid}"
    except Exception as e:  # a probe reports, it never dies
        return f"FAIL        {type(e).__name__}: {str(e)[:70]}"


async def main() -> None:
    load_key_file()
    rt = Router()
    names = [p.name for p in rt.configured() if p.api == "openai" and not p.base_url.startswith("http://localhost")]
    rows = await asyncio.gather(*(probe(rt, n) for n in names))
    for n, r in sorted(zip(names, rows), key=lambda x: x[1]):
        print(f"{n:<14}{r}")
    ok = sum(r.startswith("OK") for r in rows)
    print(f"\n{ok}/{len(names)} providers completed a full tool round trip")

asyncio.run(main())
