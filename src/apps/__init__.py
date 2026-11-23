"""Wrappers that drive desktop programs through their own command line (headless, no GUI): blender, gimp, krita, godot.
Each module has `async def run(mode, ...) -> str` and returns 'error: ...' text instead of raising."""
