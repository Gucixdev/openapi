"""The app wrappers against fake binaries on PATH (no Blender/GIMP/Krita/Godot needed), plus one real headless run where godot exists."""
import json
import os
import shutil
import stat

import pytest

from apps import blender, gimp, godot, krita


def fake(tmp_path, monkeypatch, name, script):
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text("#!/bin/sh\n" + script)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{d}:{os.environ['PATH']}")


@pytest.fixture(autouse=True)
def no_flatpak(monkeypatch):
    import apps.common as c
    real = c.shutil.which
    monkeypatch.setattr(c.shutil, "which", lambda n, *a, **k: None if n == "flatpak" else real(n, *a, **k))


async def test_missing_program_is_an_error_text(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    for mod in (blender, gimp, krita, godot):
        assert (await mod.run("version")).startswith("error: ") and "not installed" in await mod.run("version")


async def test_bad_mode_and_inputs(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "blender", "echo Blender 9.9")
    assert (await blender.run("nope")).startswith("error: mode is one of")
    assert (await blender.run("info", "/no/such.blend")).startswith("error: .blend file not found")
    assert (await blender.run("version")) == "Blender 9.9"
    f = tmp_path / "a.blend"
    f.write_text("x")
    assert (await blender.run("render", str(f))).startswith("error: ")  # no out dir
    assert (await blender.run("export", str(f), str(tmp_path / "o"), "exe")).startswith("error: format is one of")
    assert (await blender.run("render", str(f), str(tmp_path / "o"), "1;rm")).startswith("error: the frame must be a number")


async def test_blender_info_parses_the_marker_line(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "blender", """echo 'noise'; echo 'OPENAPI_JSON {"scene":"S","objects":[{"name":"Cube","type":"MESH"}]}'""")
    f = tmp_path / "a.blend"
    f.write_text("x")
    assert json.loads(await blender.run("info", str(f)))["objects"][0]["name"] == "Cube"


async def test_blender_render_and_export_write_into_out(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "blender", """
while [ $# -gt 0 ]; do case "$1" in -o) o=$2;; -P) py=$2;; esac; shift; done
if [ -n "$o" ]; then : > "$(echo "$o" | sed 's/####/0001/').png"; fi
if [ -n "$py" ]; then t=$(sed -n "s/^OUT = '\\(.*\\)'$/\\1/p" "$py"); : > "$t"; fi""")
    f = tmp_path / "a.blend"
    f.write_text("x")
    out = tmp_path / "o"
    assert "frame_0001.png" in await blender.run("render", str(f), str(out), "3")
    assert (await blender.run("export", str(f), str(out), "glb")) == f"wrote {out}/a.glb"


async def test_gimp_scale_validates_size_and_checks_the_file(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "gimp-console", "echo OPENAPI_OK")  # claims success but writes nothing
    f = tmp_path / "in.png"
    f.write_bytes(b"x")
    assert (await gimp.run("scale", str(f), str(tmp_path / "o"), "0x5")).startswith("error: the size is WxH")
    assert (await gimp.run("convert", str(f), str(tmp_path / "o"), "exe")).startswith("error: format is one of")
    r = await gimp.run("convert", str(f), str(tmp_path / "o"), "jpg")
    assert r.startswith("error: ") and "wrote nothing" in r
    fake(tmp_path, monkeypatch, "gimp-console", """for a in "$@"; do case "$a" in *OPENAPI_OK*) t=$(echo "$a" | sed -n "s/.*Gio.File.new_for_path('\\([^']*\\.jpg\\)'), None.*/\\1/p"); : > "$t"; echo OPENAPI_OK;; esac; done""")
    assert (await gimp.run("convert", str(f), str(tmp_path / "o"), "jpg")).startswith("wrote ")


async def test_gimp_uses_a_throwaway_profile(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "gimp-console", 'echo "$GIMP3_DIRECTORY"')
    r = await gimp.run("scriptfu", "(+ 1 2)")
    assert r and os.path.isabs(r.strip()) and ".config" not in r  # a temp dir, not ~/.config/GIMP


async def test_krita_export(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "krita", 'while [ $# -gt 0 ]; do [ "$1" = --export-filename ] && : > "$2"; shift; done')
    f = tmp_path / "d.kra"
    f.write_bytes(b"x")
    assert (await krita.run("export", str(f), str(tmp_path / "o"), "bmp")).startswith("wrote ")
    assert (await krita.run("export", str(f), str(tmp_path / "o"), "exe")).startswith("error: format")


async def test_godot_paths_are_checked(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "godot", "echo fake")
    assert (await godot.run("script", str(tmp_path), arg="t.gd")).startswith("error: project must be")
    (tmp_path / "project.godot").write_text("config_version=5\n")
    assert (await godot.run("script", str(tmp_path), arg="../x.gd")).startswith("error: the script is a path inside")
    assert (await godot.run("script", str(tmp_path), arg="missing.gd")).startswith("error: script not found")
    assert (await godot.run("export", str(tmp_path), str(tmp_path / "o"), "Linux;rm|x")).startswith("error: arg is")
    (tmp_path / "t.gd").write_text("x")
    assert (await godot.run("script", str(tmp_path), arg="t.gd")) == "fake"


@pytest.mark.skipif(shutil.which("godot") is None, reason="godot is not installed")
async def test_godot_real_headless_script_and_check(tmp_path):
    (tmp_path / "project.godot").write_text('config_version=5\n[application]\nconfig/name="smoke"\n')
    (tmp_path / "t.gd").write_text('extends SceneTree\nfunc _init():\n\tprint("hello from gd")\n\tquit()\n')
    (tmp_path / "bad.gd").write_text("extends SceneTree\nfunc _init(:\n")
    assert (await godot.run("version")).startswith("4.")
    assert "hello from gd" in await godot.run("script", str(tmp_path), arg="t.gd")
    assert (await godot.run("check", str(tmp_path), arg="bad.gd")).startswith("error: ")


async def test_gimp_python_script_and_pdb_modes(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "gimp-console", 'for a in "$@"; do case "$a" in --batch-interpreter=*) i=${a#*=};; esac; done; echo "$i"')
    assert (await gimp.run("python", "", "", "print(1)")).strip() == "python-fu-eval"
    assert (await gimp.run("scriptfu", "", "", "(+ 1 2)")).strip() == "plug-in-script-fu-eval"
    s = tmp_path / "a.scm"
    s.write_text("(+ 1 2)")
    assert (await gimp.run("script", str(s))).strip() == "plug-in-script-fu-eval"
    p = tmp_path / "a.py"
    p.write_text("print(1)")
    assert (await gimp.run("script", str(p))).strip() == "python-fu-eval"
    assert (await gimp.run("script", str(tmp_path / "a.txt"))).startswith("error")
    assert (await gimp.run("pdb", "", "", "(")).startswith("error: bad regex")
    assert (await gimp.run("python", "", "", "  ")).startswith("error: python needs code")
