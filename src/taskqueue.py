"""taskqueue: many questions at once. A task list (one task per line) goes through the router with a worker limit; every finished task is appended to a JSONL file
at once, so an interrupted run loses nothing and --resume carries on. Each task gets the normal failover (free first, paid last)."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable

from router import Router, RouterError

_BULLET = re.compile(r"^\s*(?:[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)")


# lines that are a status note, not a task ("... -> zrobione 2026-11-15", "(blocked: ...)", "green", "done"): --open drops them
STATUS = r"->\s*(zrobion|done|czesciowo|w toku|\(?blocked)|\(blocked|\bdone\b|\bGOTOWE\b|zielon"


def parse_tasks(text: str, skip: str = "") -> list[tuple[int, str]]:
    """Lines -> [(line number, task)]. Blank lines and '#' headings are skipped, list bullets / numbers / checkboxes are stripped, `skip` is a regex for lines to drop."""
    rx = re.compile(skip) if skip else None
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        t = _BULLET.sub("", line).strip()
        if t and not line.lstrip().startswith("#") and not (rx and rx.search(line)):
            out.append((n, t))
    return out


def done_ids(path: Path) -> set[int]:
    """Line numbers already answered (without an error) in an existing result file."""
    ids: set[int] = set()
    if path.exists():
        for row in path.read_text().splitlines():
            try:
                d = json.loads(row)
            except ValueError:
                continue
            if "error" not in d:
                ids.add(d["line"])
    return ids


async def run(rt: Router, tasks: list[tuple[int, str]], out: Path, jobs: int = 4, model: str = "auto", task: str = "general", system: str | None = None,
              prefix: str = "", max_tokens: int | None = None, on_done: Callable[[dict[str, Any]], None] | None = None) -> list[dict[str, Any]]:
    """Run every task with at most `jobs` in flight; rows are appended to `out` as they finish (order of finishing) and returned in task order."""
    sem, lock, rows = asyncio.Semaphore(max(1, jobs)), asyncio.Lock(), {}

    async def one(line: int, text: str) -> None:
        async with sem:
            try:
                a = await rt.ask(prefix + text, model, system, max_tokens, task=task)
                row: dict[str, Any] = {"line": line, "task": text, "provider": a.provider, "model": a.model, "text": a.text, "truncated": a.truncated}
            except RouterError as e:
                row = {"line": line, "task": text, "error": str(e)}
            except Exception as e:      # one broken task must not stop the queue
                row = {"line": line, "task": text, "error": f"{type(e).__name__}: {e}"}
        async with lock:
            rows[line] = row
            with out.open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if on_done:
                on_done(row)

    await asyncio.gather(*(one(n, t) for n, t in tasks))
    return [rows[n] for n, _ in tasks]
