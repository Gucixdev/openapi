"""Blender in background mode (`blender -b`): its own CLI, bpy scripts generated for the common jobs.
    version                      the version line
    info FILE.blend              scenes, objects, frame range, render engine as JSON
    render FILE.blend OUT [N]    frame N (default 1) as PNG into the directory OUT
    export FILE.blend OUT FMT    glb | gltf | obj | fbx | stl | usd | ply  into OUT
    script FILE.py [OUT]         run your own bpy script; OUT (if given) is passed in the env as BLENDER_OUT"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from . import common

MODES = ("version", "info", "render", "export", "script")
FORMATS = {"glb": "bpy.ops.export_scene.gltf(filepath=OUT, export_format='GLB')", "gltf": "bpy.ops.export_scene.gltf(filepath=OUT, export_format='GLTF_SEPARATE')",
           "obj": "bpy.ops.wm.obj_export(filepath=OUT)", "fbx": "bpy.ops.export_scene.fbx(filepath=OUT)",
           "stl": "bpy.ops.wm.stl_export(filepath=OUT)", "usd": "bpy.ops.wm.usd_export(filepath=OUT)", "ply": "bpy.ops.wm.ply_export(filepath=OUT)"}
INFO = """import bpy, json
s = bpy.context.scene
print("OPENAPI_JSON " + json.dumps({"scene": s.name, "engine": s.render.engine, "frames": [s.frame_start, s.frame_end], "fps": s.render.fps,
    "scenes": [x.name for x in bpy.data.scenes], "objects": [{"name": o.name, "type": o.type} for o in bpy.data.objects],
    "materials": [m.name for m in bpy.data.materials]}))
"""


async def run(mode: str, src: str = "", out: str = "", arg: str = "", timeout: int = 600) -> str:
    if mode not in MODES:
        return f"error: mode is one of {', '.join(MODES)}"
    app = common.locate(("blender",), "org.blender.Blender")
    if not app:
        return common.missing("blender", "pacman -S blender")
    if mode == "version":
        argv, env = app.build(["-b", "--version"])
        code, o, e = await common.run(argv, 60, env)
        return common.finish(code, o, e, o.strip().splitlines()[0] if o.strip() else "blender")
    f = common.need_file(src, "script" if mode == "script" else ".blend")
    if isinstance(f, str):
        return f
    with tempfile.TemporaryDirectory() as tmp:
        dirs = [str(f.parent), tmp]
        env_extra: dict[str, str] = {}
        if mode == "script":
            outdir = common.need_dir(out) if out else None
            if isinstance(outdir, str):
                return outdir
            if outdir:
                env_extra["BLENDER_OUT"] = str(outdir)
                dirs.append(str(outdir))
            argv, env = app.build(["-b", "-P", str(f)], tuple(dirs), env_extra)
            code, o, e = await common.run(argv, timeout, env)
            return common.finish(code, o, e, common.tail(o, 4000) or "done")
        if mode == "info":
            script = Path(tmp, "info.py")
            script.write_text(INFO)
            argv, env = app.build(["-b", str(f), "-P", str(script)], tuple(dirs))
            code, o, e = await common.run(argv, timeout, env)
            line = next((ln for ln in o.splitlines() if ln.startswith("OPENAPI_JSON ")), "")
            if code or not line:
                return "error: " + (common.tail(e) or common.tail(o) or f"exit {code}")
            return json.dumps(json.loads(line[len("OPENAPI_JSON "):]), indent=1)
        outdir = common.need_dir(out)
        if isinstance(outdir, str):
            return outdir
        dirs.append(str(outdir))
        if mode == "render":
            if arg and not arg.lstrip("-").isdigit():
                return "error: the frame must be a number"
            argv, env = app.build(["-b", str(f), "-o", f"{outdir}/frame_####", "-F", "PNG", "-f", arg or "1"], tuple(dirs))
            code, o, e = await common.run(argv, timeout, env)
            files = sorted(p.name for p in outdir.glob("frame_*.png"))
            return common.finish(code, o, e, f"rendered {', '.join(files) or '(nothing written)'} in {outdir}")
        fmt = arg.lower()
        if fmt not in FORMATS:
            return f"error: format is one of {', '.join(FORMATS)}"
        target = outdir / f"{f.stem}.{fmt}"
        script = Path(tmp, "export.py")
        script.write_text(f"import bpy\nOUT = {str(target)!r}\n{FORMATS[fmt]}\n")
        argv, env = app.build(["-b", str(f), "-P", str(script)], tuple(dirs))
        code, o, e = await common.run(argv, timeout, env)
        return common.finish(code, o, e, f"wrote {target}" if target.exists() else "error: blender ran but wrote nothing (is the add-on for this format enabled?)")
