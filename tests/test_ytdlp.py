import json
import os
import stat

import ytdlp


def fake(tmp_path, monkeypatch, script):
    p = tmp_path / "yt-dlp"
    p.write_text("#!/bin/sh\n" + script)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")


VTT = "WEBVTT\nKind: captions\n\n00:00.000 --> 00:02.000\nhello <c>world</c>\n\n00:02.000 --> 00:04.000\nhello world\nsecond line\n"


def test_vtt_drops_headers_times_and_rolling_duplicates():
    assert ytdlp.vtt_to_text(VTT) == "hello world\nsecond line"


async def test_context_info_plus_transcript_written_to_file(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, """
case "$*" in
  *-J*) echo '{"title":"T","channel":"C","duration":61,"description":"d","formats":[1,2]}' ;;
  *) while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; shift; done
     printf 'WEBVTT\\n\\n00:00.000 --> 00:01.000\\nhi there\\n' > "${out}.pl.vtt" ;;
esac""")
    out = tmp_path / "ctx" / "v.txt"
    msg = await ytdlp.fetch("https://example.com/v", "context", str(out))
    assert msg.startswith("wrote ")
    text = out.read_text()
    assert json.loads(text.split("\n\n## transcript")[0])["title"] == "T"
    assert "formats" not in text and text.rstrip().endswith("hi there")


async def test_errors_are_text_not_exceptions(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "echo boom >&2; exit 1")
    assert (await ytdlp.fetch("https://example.com/v", "info")).startswith("error: boom")
    assert (await ytdlp.fetch("file:///etc/passwd", "info")).startswith("error")
    assert (await ytdlp.fetch("https://x", "nope")).startswith("error")
    assert (await ytdlp.fetch("https://x", "audio")).startswith("error: audio/video need out")


async def test_option_looking_url_goes_after_double_dash(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, 'for a in "$@"; do echo "$a" >> "%s/args"; done; echo "{}"' % tmp_path)
    await ytdlp.info("https://x/--exec=evil")
    args = (tmp_path / "args").read_text().split()
    assert args[args.index("--") + 1] == "https://x/--exec=evil"
