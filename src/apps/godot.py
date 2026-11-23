"""Godot 4 headless (`godot --headless`): the engine's own CLI. Never point it at a project that is open in the editor.
    version                              the version line
    script PROJECT FILE.gd               run a SceneTree/MainLoop script of the project; its output is returned
    check PROJECT FILE.gd                parse the script only (--check-only), errors come back as text
    import PROJECT                       (re)import the assets of the project (writes PROJECT/.godot)
    export PROJECT OUT "PRESET|FILE"     export a preset from export_presets.cfg into OUT/FILE (needs the export templates)
PROJECT is a directory with a project.godot. Except for export, Godot runs with a throwaway config/data directory."""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

from . import common

MODES = ("version", "script", "check", "import", "export")


async def run(mode: str, project: str = "", out: str = "", arg: str = "", timeout: int = 300) -> str:
    if mode not in MODES:
        return f"error: mode is one of {', '.join(MODES)}"
    app = common.locate(("godot", "godot4"), "org.godotengine.Godot")
    if not app:
        return common.missing("godot", "install Godot 4 and put it on PATH")
    with tempfile.TemporaryDirectory() as tmp:
        env = {"XDG_CONFIG_HOME": f"{tmp}/c", "XDG_DATA_HOME": f"{tmp}/d", "XDG_CACHE_HOME": f"{tmp}/x"}
        if mode == "version":
            argv, penv = app.build(["--headless", "--version"], (), env)
            code, o, e = await common.run(argv, 60, penv)
            return common.finish(code, o, e, o.strip().splitlines()[0] if o.strip() else "godot")
        pdir = Path(project).expanduser().resolve() if project else None
        if not pdir or not (pdir / "project.godot").is_file():
            return "error: project must be a directory with a project.godot"
        base = ["--headless", "--path", str(pdir)]
        dirs = (str(pdir),)
        if mode in ("script", "check"):
            if not arg or arg.startswith("-") or ".." in Path(arg).parts:
                return "error: the script is a path inside the project, e.g. tools/run.gd"
            s = (pdir / arg).resolve()
            if not s.is_file() or pdir not in s.parents:
                return f"error: script not found inside the project: {arg}"
            argv, penv = app.build([*base, *(["--check-only"] if mode == "check" else []), "--script", str(s)], dirs, env)
            code, o, e = await common.run(argv, timeout, penv)
            text = common.tail(o + "\n" + e, 4000)
            return ("error: " + text if code else text or "ok")
        if mode == "import":
            argv, penv = app.build([*base, "--import"], dirs, env)
            code, o, e = await common.run(argv, timeout, penv)
            return common.finish(code, o, e, "imported")
        # export: the templates live in the real data dir, so no throwaway environment here
        preset, _, name = arg.partition("|")
        if not re.fullmatch(r"[\w .+-]{1,60}", preset) or not re.fullmatch(r"[\w .+-]{1,80}", name) or name.startswith("."):
            return 'error: arg is "PRESET|FILE", e.g. "Linux|game.x86_64"'
        outdir = common.need_dir(out)
        if isinstance(outdir, str):
            return outdir
        target = outdir / name
        argv, penv = app.build([*base, "--export-release", preset, str(target)], (str(pdir), str(outdir)))
        code, o, e = await common.run(argv, timeout, penv)
        if code or not target.exists():
            return "error: " + (common.tail(e + o) or "godot wrote nothing")
        return f"wrote {target}"
