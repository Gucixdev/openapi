"""audio.py: keyed APIs against a fake transport, the local fallbacks against fake binaries on PATH."""
import os
import stat

import pytest

import audio

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 32


class Resp:
    def __init__(self, status=200, js=None, content=b""):
        self.status_code, self._js, self.content = status, js or {}, content

    def json(self):
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            raise audio.httpx.HTTPStatusError("x", request=None, response=self)


def fake_client(monkeypatch, replies):
    calls = []

    class C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            calls.append((url, kw))
            return replies.pop(0)

    monkeypatch.setattr(audio, "_client", lambda: C())
    return calls


def fake_bin(tmp_path, monkeypatch, name, script):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text("#!/bin/sh\n" + script)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{d}:/usr/bin:/bin")


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    for k in ("GROQ_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY", "WHISPER_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AUDIO_DIR", str(tmp_path / "out"))


async def test_transcribe_missing_file():
    assert (await audio.transcribe("/nonexistent.wav")).startswith("error: no such file")


async def test_transcribe_groq(monkeypatch, tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(WAV)
    monkeypatch.setenv("GROQ_API_KEY", "k")
    calls = fake_client(monkeypatch, [Resp(js={"text": " hello there "})])
    assert await audio.transcribe(str(f), "en") == "hello there"
    url, kw = calls[0]
    assert url == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert kw["data"]["language"] == "en" and kw["headers"]["Authorization"] == "Bearer k"


async def test_transcribe_falls_through_to_next_provider(monkeypatch, tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(WAV)
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k2")
    calls = fake_client(monkeypatch, [Resp(status=429), Resp(js={"text": "ok"})])
    assert await audio.transcribe(str(f)) == "ok"
    assert "openai.com" in calls[1][0]


async def test_transcribe_local_whisper_cli(monkeypatch, tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(WAV)
    fake_bin(tmp_path, monkeypatch, "whisper-cli", "echo ' local words '\n")
    monkeypatch.setenv("WHISPER_MODEL", "/m.bin")
    assert await audio.transcribe(str(f)) == "local words"


async def test_transcribe_nothing_available(monkeypatch, tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(WAV)
    monkeypatch.setenv("PATH", "/nonexistent")
    assert (await audio.transcribe(str(f))).startswith("error: no speech-to-text provider worked")


async def test_speak_empty():
    assert (await audio.speak("  ")).startswith("error")


async def test_speak_keyed_writes_wav(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    fake_client(monkeypatch, [Resp(content=WAV)])
    out = await audio.speak("hi")
    assert out.startswith("ok: ") and out.endswith("(groq)")
    path = out[4:].rsplit(" (", 1)[0]
    assert open(path, "rb").read() == WAV


async def test_speak_groq_terms_error_falls_back_to_espeak(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    fake_client(monkeypatch, [Resp(status=400, js={"error": {"message": "model_terms_required"}})])
    fake_bin(tmp_path, monkeypatch, "espeak-ng", 'while [ $# -gt 1 ]; do [ "$1" = -w ] && out=$2; shift; done\nprintf RIFF%060d 0 > "$out"\n')
    out = await audio.speak("hi")
    assert "espeak-ng, local" in out and "groq: HTTP 400 model_terms_required" in out


async def test_speak_nothing_available(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    assert (await audio.speak("hi")).startswith("error: no text-to-speech provider worked")


async def test_transcribe_elevenlabs(monkeypatch, tmp_path):
    f = tmp_path / "a.wav"
    f.write_bytes(WAV)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi")
    calls = fake_client(monkeypatch, [Resp(js={"text": " czesc "})])
    assert await audio.transcribe(str(f), "pl") == "czesc"
    url, kw = calls[0]
    assert url == "https://api.elevenlabs.io/v1/speech-to-text"
    assert kw["headers"] == {"xi-api-key": "xi"} and kw["data"] == {"model_id": "scribe_v1", "language_code": "pl"}


async def test_speak_elevenlabs_writes_mp3(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi")
    calls = fake_client(monkeypatch, [Resp(content=b"ID3" + b"\x00" * 20)])
    out = await audio.speak("hello", voice="VOICE1")
    assert out.startswith("ok: ") and out.endswith(".mp3 (elevenlabs)")
    url, kw = calls[0]
    assert url == "https://api.elevenlabs.io/v1/text-to-speech/VOICE1"
    assert kw["json"]["model_id"] == "eleven_flash_v2_5" and kw["headers"]["xi-api-key"] == "xi"


async def test_speak_elevenlabs_follows_a_failed_keyed_provider(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi")
    calls = fake_client(monkeypatch, [Resp(status=400), Resp(content=b"\xff\xfb" + b"\x00" * 20)])
    out = await audio.speak("hi")
    assert out.endswith("(elevenlabs)") and len(calls) == 2 and "text-to-speech/JBFqnCBsd6RMkjVDRZzb" in calls[1][0]


async def test_speak_elevenlabs_error_is_reported(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi")
    monkeypatch.setenv("PATH", "/nonexistent")
    fake_client(monkeypatch, [Resp(status=401)])
    out = await audio.speak("hi")
    assert out.startswith("error:") and "elevenlabs: HTTP 401" in out
