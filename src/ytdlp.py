"""yt-dlp wrapper: a video's context as text. No API key, no shell; the yt-dlp program does the network work.
    info        title, channel, date, length, chapters and description as JSON (the context of the video)
    transcript  subtitles (manual first, else automatic) as plain text, without the rolling duplicates of auto captions
    context     info + transcript in one text, ready to be saved or fed to a model
    audio|video the file itself, only into an explicit out directory
`out` for info/transcript/context is a file the text is written to (then only its path and size come back)."""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path

LANGS = "pl.*,en.*"
MODES = ("info", "transcript", "context", "audio", "video")


async def _run(*args: str, timeout: int = 180) -> tuple[int, str, str]:
    try:
        p = await asyncio.create_subprocess_exec("yt-dlp", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except FileNotFoundError:
        return 127, "", "yt-dlp is not installed (pacman -S yt-dlp)"
    try:
        o, e = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        await p.wait()
        return 124, "", f"yt-dlp took longer than {timeout}s"
    return p.returncode or 0, o.decode("utf-8", "replace"), e.decode("utf-8", "replace")


def vtt_to_text(vtt: str) -> str:
    """WebVTT -> plain lines. Auto captions repeat each line while it scrolls, so a line equal to the previous one is dropped."""
    out: list[str] = []
    for line in vtt.splitlines():
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or "-->" in line or line.isdigit():
            continue
        if not out or out[-1] != line:
            out.append(line)
    return "\n".join(out)


async def info(url: str) -> str:
    code, o, e = await _run("--no-playlist", "-J", "--skip-download", "--", url)
    if code:
        return f"error: {e.strip()[-300:]}"
    d = json.loads(o)
    keep = {k: d.get(k) for k in ("title", "channel", "uploader", "upload_date", "duration", "view_count", "webpage_url", "chapters", "description")}
    return json.dumps({k: v for k, v in keep.items() if v not in (None, "", [])}, ensure_ascii=False, indent=1)


async def transcript(url: str, langs: str = LANGS) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        code, _, e = await _run("--no-playlist", "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", langs,
                                "--sub-format", "vtt/best", "-o", f"{tmp}/s", "--", url)
        files = sorted(Path(tmp).glob("s*.vtt"))
        if not files:
            return "error: " + (e.strip()[-300:] if code else "no subtitles for this video in " + langs)
        return vtt_to_text(files[0].read_text("utf-8", "replace"))


async def fetch(url: str, mode: str = "context", out: str | None = None, langs: str = LANGS) -> str:
    if mode not in MODES:
        return f"error: mode is one of {', '.join(MODES)}"
    if not re.match(r"https?://", url):
        return "error: url must start with http:// or https://"
    if mode in ("audio", "video"):
        if not out:
            return "error: audio/video need out, a directory to save into"
        Path(out).mkdir(parents=True, exist_ok=True)
        fmt = ["-x", "--audio-format", "mp3"] if mode == "audio" else ["-f", "bv*[height<=720]+ba/b[height<=720]"]
        code, _, e = await _run("--no-playlist", *fmt, "-o", f"{out}/%(title).80B [%(id)s].%(ext)s", "--", url, timeout=1800)
        return f"error: {e.strip()[-300:]}" if code else f"saved in {out}"
    parts = []
    if mode in ("info", "context"):
        parts.append(await info(url))
    if mode in ("transcript", "context"):
        t = await transcript(url, langs)
        parts.append(("## transcript\n" if mode == "context" else "") + t)
    text = "\n\n".join(parts)
    if text.startswith("error") or not out:
        return text
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(text + "\n", "utf-8")
    return f"wrote {out} ({len(text)} chars)"
