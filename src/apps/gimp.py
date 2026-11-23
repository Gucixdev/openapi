"""GIMP 3 in batch mode (`gimp-console -i`), images through its python-fu interpreter; the flatpak org.gimp.GIMP is used when there is no gimp on PATH.
    version                       the version line
    convert FILE OUT FMT          FILE as FMT (png jpg webp tiff bmp gif xcf ...) into the directory OUT
    scale FILE OUT WxH            FILE scaled to W by H pixels, PNG, into OUT
    scriptfu EXPR                 your own Script-Fu (Scheme) expression(s), result text returned (no files are checked)
    python CODE                   your own Python-Fu (GIMP 3 `gi` API) code, printed output returned; CODE may be @file
    script FILE                   run a .scm (Script-Fu) or .py (Python-Fu) file the same way
    pdb [PATTERN]                 names of the PDB procedures (everything GIMP can do) matching the regex PATTERN"""
from __future__ import annotations

import re
import tempfile

from . import common

MODES = ("version", "convert", "scale", "scriptfu", "python", "script", "pdb")
FORMATS = ("png", "jpg", "jpeg", "webp", "tiff", "bmp", "gif", "xcf", "tga", "ico")

# GIMP 3 API: Gimp.file_load / Gimp.file_save with Gio.File; the marker is printed only after the save call returned
PY = """import gi
gi.require_version('Gimp', '3.0')
from gi.repository import Gimp, Gio
img = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path({src!r}))
{scale}Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, img, Gio.File.new_for_path({out!r}), None)
print('OPENAPI_OK')
"""


PDB = """import gi, re
gi.require_version('Gimp', '3.0')
from gi.repository import Gimp
names = sorted(n for n in Gimp.get_pdb().query_procedures('', '', '', '', '', '', '', '') if re.search({pat!r}, n))
print(len(names), 'procedures'); print('\\n'.join(names[:400]))
"""


def _python_source(mode: str, src: str, arg: str) -> str:
    """The code to hand to the batch interpreter: your Python, your Scheme, or the PDB listing. A leading '(' means Scheme."""
    if mode == "pdb":
        pat = arg or src or "."
        try:
            re.compile(pat)
        except re.error as e:
            return f"error: bad regex: {e}"
        return PDB.format(pat=pat)
    text = arg or src
    if mode == "script" or text.startswith("@"):
        f = common.need_file(text.lstrip("@") if mode == "python" else text)
        if isinstance(f, str):
            return f
        if mode == "script" and f.suffix not in (".py", ".scm"):
            return "error: script takes a .py (Python-Fu) or .scm (Script-Fu) file"
        text = f.read_text("utf-8", "replace")
    return text if text.strip() else f"error: {mode} needs code"


async def run(mode: str, src: str = "", out: str = "", arg: str = "", timeout: int = 180) -> str:
    if mode not in MODES:
        return f"error: mode is one of {', '.join(MODES)}"
    app = common.locate(("gimp-console", "gimp"), "org.gimp.GIMP", "gimp-console")
    if not app:
        return common.missing("gimp", "pacman -S gimp, or flatpak install org.gimp.GIMP")
    with tempfile.TemporaryDirectory() as cfg:
        env = {"GIMP3_DIRECTORY": cfg}  # a throwaway profile: the real one (and a GIMP that is open) is never touched
        if mode == "version":
            argv, penv = app.build(["--version"], (), env)
            code, o, e = await common.run(argv, 60, penv)
            return common.finish(code, o, e, o.strip().splitlines()[0] if o.strip() else "gimp")
        if mode == "scriptfu":
            arg = arg or src  # `openapi gimp scriptfu '(...)'` puts the expression in the first slot
            if not arg.strip():
                return "error: scriptfu needs an expression"
            argv, penv = app.build(["-i", "-d", "-f", "--quit", "--batch-interpreter=plug-in-script-fu-eval", "-b", arg], (), env)
            code, o, e = await common.run(argv, timeout, penv)
            return common.finish(code, o, e, common.tail(o, 4000) or "done")
        if mode in ("python", "script", "pdb"):
            code_text = _python_source(mode, src, arg)
            if code_text.startswith("error:"):
                return code_text
            interp = "plug-in-script-fu-eval" if code_text.startswith("(") else "python-fu-eval"
            argv, penv = app.build(["-i", "-d", "-f", "--quit", f"--batch-interpreter={interp}", "-b", code_text], (), env)
            code, o, e = await common.run(argv, timeout, penv)
            return common.finish(code, o, e, common.tail(o, 4000) or "done")
        f = common.need_file(src)
        if isinstance(f, str):
            return f
        outdir = common.need_dir(out)
        if isinstance(outdir, str):
            return outdir
        if mode == "convert":
            fmt = arg.lower().lstrip(".")
            if fmt not in FORMATS:
                return f"error: format is one of {', '.join(FORMATS)}"
            target, scale = outdir / f"{f.stem}.{fmt}", ""
        else:
            m = re.fullmatch(r"(\d{1,5})x(\d{1,5})", arg.strip())
            if not m or int(m[1]) < 1 or int(m[2]) < 1:
                return "error: the size is WxH, e.g. 800x600"
            target, scale = outdir / f"{f.stem}_{m[1]}x{m[2]}.png", f"img.scale({m[1]}, {m[2]})\n"
        code_text = PY.format(src=str(f), out=str(target), scale=scale)
        argv, penv = app.build(["-i", "-d", "-f", "--quit", "--batch-interpreter=python-fu-eval", "-b", code_text], (str(f.parent), str(outdir)), env)
        code, o, e = await common.run(argv, timeout, penv)
        if code or "OPENAPI_OK" not in o or not target.exists():
            return "error: " + (common.tail(e) or "gimp wrote nothing")
        return f"wrote {target}"
