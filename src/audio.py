"""Speech, own file, same shape as images.py: providers are tried in order and any failure falls through.
  transcribe(path): an OpenAI-style /audio/transcriptions API that has a key (Groq whisper-large-v3-turbo, OpenAI whisper-1),
                    then ElevenLabs scribe_v1, else a local whisper.cpp (`whisper-cli` + WHISPER_MODEL=/path/ggml-*.bin).
  speak(text):      an OpenAI-style /audio/speech API that has a key (Groq Orpheus - the account must accept the model terms in
                    the Groq console once -, OpenAI tts-1), then ElevenLabs (mp3; `voice` is a voice id), else local espeak-ng
                    (robotic, offline, always there).
A transcript comes back as text; a spoken file is written and its path returned (never the bytes). AUDIO_DIR overrides ~/Music/openapi.
Errors are text starting with "error:"."""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path

try:
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

MAX_UPLOAD = 25 * 1024 * 1024  # Groq's and OpenAI's limit for one file

# (name, env var, base url, model)
STT = (
    ("groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1", "whisper-large-v3-turbo"),
    ("openai", "OPENAI_API_KEY", "https://api.openai.com/v1", "whisper-1"),
)
# (name, env var, base url, model, default voice)
TTS = (
    ("groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1", "canopylabs/orpheus-v1-english", "troy"),
    ("openai", "OPENAI_API_KEY", "https://api.openai.com/v1", "tts-1", "alloy"),
)


ELEVEN_KEY = "ELEVENLABS_API_KEY"
ELEVEN = "https://api.elevenlabs.io/v1"
ELEVEN_STT_MODEL = "scribe_v1"
ELEVEN_TTS_MODEL = "eleven_flash_v2_5"
ELEVEN_VOICE = "JBFqnCBsd6RMkjVDRZzb"  # George, one of the stock voices every account has


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=180, follow_redirects=True)


def _out(ext: str) -> Path:
    d = Path(os.environ.get("AUDIO_DIR") or "~/Music/openapi").expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"speech-{int(time.time() * 1000)}.{ext}"


async def _run(*argv: str) -> tuple[int, str]:
    p = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate()
    return p.returncode or 0, (out or err).decode(errors="replace")


async def transcribe(path: str, language: str = "") -> str:
    f = Path(path).expanduser()
    if not f.is_file():
        return f"error: no such file: {path}"
    if f.stat().st_size > MAX_UPLOAD:
        return f"error: {f.name} is over 25 MB; cut it first, e.g. ffmpeg -i in -t 600 -ac 1 -b:a 48k out.mp3"
    tried: list[str] = []
    async with _client() as c:
        for name, env, base, model in STT:
            if not os.environ.get(env):
                continue
            try:
                data = {"model": model, "response_format": "json"}
                if language:
                    data["language"] = language
                r = await c.post(f"{base}/audio/transcriptions", data=data, files={"file": (f.name, f.read_bytes())}, headers={"Authorization": f"Bearer {os.environ[env]}"})
                r.raise_for_status()
                return r.json()["text"].strip()
            except Exception as e:
                tried.append(f"{name}: {_why(e)}")
        if os.environ.get(ELEVEN_KEY):
            try:
                data = {"model_id": ELEVEN_STT_MODEL}
                if language:
                    data["language_code"] = language
                r = await c.post(f"{ELEVEN}/speech-to-text", data=data, files={"file": (f.name, f.read_bytes())}, headers={"xi-api-key": os.environ[ELEVEN_KEY]})
                r.raise_for_status()
                return r.json()["text"].strip()
            except Exception as e:
                tried.append(f"elevenlabs: {_why(e)}")
    model = os.environ.get("WHISPER_MODEL", "")
    cli = shutil.which("whisper-cli")
    if cli and model:
        argv = [cli, "-m", model, "-f", str(f), "-nt", "-np"] + (["-l", language] if language else [])
        rc, text = await _run(*argv)
        if rc == 0 and text.strip():
            return text.strip()
        tried.append(f"whisper-cli: exit {rc}")
    return "error: no speech-to-text provider worked: " + ("; ".join(tried) or "no GROQ_API_KEY/OPENAI_API_KEY/ELEVENLABS_API_KEY and no whisper-cli with WHISPER_MODEL")


def _why(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        try:
            return f"HTTP {e.response.status_code} {e.response.json()['error']['message'][:70]}"
        except Exception:
            return f"HTTP {e.response.status_code}"
    return str(e)[:80]


async def speak(text: str, voice: str = "") -> str:
    text = text.strip()
    if not text:
        return "error: nothing to say"
    tried: list[str] = []
    async with _client() as c:
        for name, env, base, model, default_voice in TTS:
            if not os.environ.get(env):
                continue
            try:
                r = await c.post(f"{base}/audio/speech", json={"model": model, "input": text, "voice": voice or default_voice, "response_format": "wav"},
                                 headers={"Authorization": f"Bearer {os.environ[env]}"})
                r.raise_for_status()
                if not r.content[:4] == b"RIFF":
                    raise RuntimeError("not a wav")
                p = _out("wav")
                p.write_bytes(r.content)
                return f"ok: {p} ({name})"
            except Exception as e:
                tried.append(f"{name}: {_why(e)}")
        if os.environ.get(ELEVEN_KEY):
            try:
                r = await c.post(f"{ELEVEN}/text-to-speech/{voice or ELEVEN_VOICE}", params={"output_format": "mp3_44100_128"},
                                 json={"text": text, "model_id": ELEVEN_TTS_MODEL}, headers={"xi-api-key": os.environ[ELEVEN_KEY]})
                r.raise_for_status()
                if not (r.content[:3] == b"ID3" or r.content[:1] == b"\xff"):
                    raise RuntimeError("not an mp3")
                p = _out("mp3")
                p.write_bytes(r.content)
                return f"ok: {p} (elevenlabs)"
            except Exception as e:
                tried.append(f"elevenlabs: {_why(e)}")
    esp = shutil.which("espeak-ng")
    if esp:
        p = _out("wav")
        rc, msg = await _run(esp, "-v", voice or "en-us", "-w", str(p), text)
        if rc == 0 and p.is_file() and p.stat().st_size > 44:
            return f"ok: {p} (espeak-ng, local)" + (f" [{'; '.join(tried)}]" if tried else "")
        tried.append(f"espeak-ng: {msg[:60]}")
    return "error: no text-to-speech provider worked: " + ("; ".join(tried) or "no GROQ_API_KEY/OPENAI_API_KEY/ELEVENLABS_API_KEY and no espeak-ng")
