import asyncio
import json

from router import RouterError
import taskqueue as tq


def test_parse_strips_bullets_and_skips_headings_blanks_and_skip_regex():
    text = "# todo\n\n1. first thing  -> zrobione\n2) second\n- [ ] third\n* fourth\n  plain fifth\n"
    assert tq.parse_tasks(text) == [(3, "first thing  -> zrobione"), (4, "second"), (5, "third"), (6, "fourth"), (7, "plain fifth")]
    assert [n for n, _ in tq.parse_tasks(text, "zrobione")] == [4, 5, 6, 7]


class FakeRouter:
    def __init__(self):
        self.live = self.peak = 0
        self.calls = []

    async def ask(self, prompt, model, system, max_tokens, task="general", **kw):
        self.calls.append(prompt)
        self.live += 1
        self.peak = max(self.peak, self.live)
        await asyncio.sleep(0.01)
        self.live -= 1
        if "bad" in prompt:
            raise RouterError("all providers failed: x")
        if "boom" in prompt:
            raise ValueError("weird")
        return type("A", (), {"provider": "p", "model": "m", "text": "ans:" + prompt, "truncated": False})()


async def test_run_limits_workers_keeps_order_records_errors_and_resumes(tmp_path):
    rt, out = FakeRouter(), tmp_path / "o.jsonl"
    tasks = [(i, f"t{i}") for i in range(1, 7)] + [(7, "bad one"), (8, "boom")]
    rows = await tq.run(rt, tasks, out, jobs=3, prefix="Q: ")
    assert rt.peak == 3 and [r["line"] for r in rows] == list(range(1, 9)) and rows[0]["text"] == "ans:Q: t1"
    assert "error" in rows[6] and "ValueError" in rows[7]["error"]
    saved = [json.loads(x) for x in out.read_text().splitlines()]
    assert len(saved) == 8 and tq.done_ids(out) == {1, 2, 3, 4, 5, 6}          # errors are not "done": a resume retries them


def test_open_filter_drops_status_notes_but_keeps_open_items():
    text = "fix the parser\nlazytui 2026-11-16: 5 napraw, make test zielone -> done\nwrite docs -> (blocked: needs his word)\nadd elevenlabs\nfoo -> zrobione 2026-11-15\n"
    assert [t for _, t in tq.parse_tasks(text, tq.STATUS)] == ["fix the parser", "add elevenlabs"]
