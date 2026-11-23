"""Krita from the command line: `krita --export` converts a document (.kra .ora .psd .png ...) without opening a window
(QT_QPA_PLATFORM=offscreen); the flatpak org.kde.krita is used when there is no krita on PATH.
    version                  the version line
    export FILE OUT FMT      FILE as FMT (png jpg ora psd kra webp tiff bmp) into the directory OUT"""
from __future__ import annotations

from . import common

MODES = ("version", "export")
FORMATS = ("png", "jpg", "jpeg", "ora", "psd", "kra", "webp", "tiff", "bmp")


async def run(mode: str, src: str = "", out: str = "", arg: str = "", timeout: int = 180) -> str:
    if mode not in MODES:
        return f"error: mode is one of {', '.join(MODES)}"
    app = common.locate(("krita",), "org.kde.krita")
    if not app:
        return common.missing("krita", "pacman -S krita, or flatpak install org.kde.krita")
    env = {"QT_QPA_PLATFORM": "offscreen"}
    if mode == "version":
        argv, penv = app.build(["--version"], (), env)
        code, o, e = await common.run(argv, 60, penv)
        return common.finish(code, o, e, o.strip().splitlines()[0] if o.strip() else "krita")
    f = common.need_file(src)
    if isinstance(f, str):
        return f
    fmt = arg.lower().lstrip(".")
    if fmt not in FORMATS:
        return f"error: format is one of {', '.join(FORMATS)}"
    outdir = common.need_dir(out)
    if isinstance(outdir, str):
        return outdir
    target = outdir / f"{f.stem}.{fmt}"
    argv, penv = app.build(["--nosplash", "--export", "--export-filename", str(target), str(f)], (str(f.parent), str(outdir)), env)
    code, o, e = await common.run(argv, timeout, penv)
    if code or not target.exists():
        return "error: " + (common.tail(e) or common.tail(o) or "krita wrote nothing")
    return f"wrote {target}"
