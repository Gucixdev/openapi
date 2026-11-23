# openapi - issues

One list. New problem: add a line under **Open** (date, what, how it was seen). Fixed: move it to **Fixed** with the
commit and the date, never delete a line. Every so often Claude goes through **Open** and fixes what it can: one
fix, one test, one commit that holds only that fix. Things that need a decision from the owner stay under
**Waiting** with the question.

## Open

- 2026-11-15  GitHub Models is reported closed to new users (PROVIDERS.txt, "unverified"); no live call has confirmed or refuted it. Check with a key, then mark `ok` or `down` in providers.json.
- 2026-11-15  `services.json`: rows not marked `verified` are from memory (base URL and auth style never read in the docs). Verify them one provider per pass, mark `verified`.
- 2026-11-15  No test for `install`/`test` scripts and no CI; the 76 tests run only locally (about 20 s).

## Waiting for the owner

- 2026-11-15  The working tree holds an unfinished restructure (sources moved to `src/`, old root `*.py` deleted, README/providers/services/tests modified). Claude commits only its own files here (`test`, `install`, `issues.md`); the restructure itself needs your commit.
- 2026-11-15  README still says "No install step"; `install` now exists (a link to `src/openapi.py` in `~/.local/bin`). README is yours to edit, so it was left alone.

## Fixed

- 2026-11-15  `pytest` with the system python failed at import (`No module named openai` / `httpx2`) and nothing said the tests live in `.venv`. Fix: `./test` (uses `.venv`, else builds a throw-away one with uv). 76 passed.
- 2026-11-15  No `install` script although every runnable program gets one. Fix: `./install` (no arguments, `PREFIX`/`DESTDIR`, default `~/.local`), checked into a temp prefix.
