"""Shared bits of the app wrappers: find the program (PATH first, then its flatpak), run it without a shell, validate paths."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Launcher:
    exe: list[str]
    flatpak: str | None = None
    command: str | None = None  # flatpak --command= (a different binary inside the same flatpak)

    def build(self, args: list[str], dirs: tuple[str, ...] = (), env: dict[str, str] | None = None) -> tuple[list[str], dict[str, str] | None]:
        """(argv, process env). A flatpak needs the directories it reads and writes opened with --filesystem and its env as --env."""
        env = env or {}
        if self.flatpak:
            o = ["flatpak", "run", *[f"--filesystem={d}" for d in dirs], *[f"--env={k}={v}" for k, v in env.items()]]
            if self.command:
                o.append(f"--command={self.command}")
            return [*o, self.flatpak, *args], None
        return [*self.exe, *args], ({**os.environ, **env} if env else None)


def locate(names: tuple[str, ...], flatpak_id: str | None = None, command: str | None = None) -> Launcher | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return Launcher([p])
    if flatpak_id and shutil.which("flatpak"):
        try:
            if subprocess.run(["flatpak", "info", flatpak_id], capture_output=True, timeout=20).returncode == 0:
                return Launcher(["flatpak"], flatpak_id, command)
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def missing(app: str, hint: str) -> str:
    return f"error: {app} is not installed here ({hint})"


async def run(argv: list[str], timeout: int = 120, env: dict[str, str] | None = None, cwd: str | None = None) -> tuple[int, str, str]:
    try:
        p = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, cwd=cwd,
                                                 stdin=asyncio.subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return 127, "", str(e)
    try:
        o, e = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        try:
            os.killpg(p.pid, 9)  # its own process group: a flatpak/bwrap child must not outlive the timeout
        except OSError:
            p.kill()
        await p.wait()
        return 124, "", f"took longer than {timeout}s"
    return p.returncode or 0, o.decode("utf-8", "replace"), e.decode("utf-8", "replace")


def tail(s: str, n: int = 400) -> str:
    return s.strip()[-n:]


def need_file(path: str, what: str = "input") -> Path | str:
    if not path:
        return f"error: {what} file is required"
    p = Path(path).expanduser().resolve()
    return p if p.is_file() else f"error: {what} file not found: {path}"


def need_dir(path: str, what: str = "out") -> Path | str:
    """An explicit output directory is required; it is created."""
    if not path:
        return f"error: {what} (an output directory) is required"
    p = Path(path).expanduser().resolve()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"error: cannot create {path}: {e}"
    return p


def finish(code: int, out: str, err: str, ok: str) -> str:
    if code:
        return "error: " + (tail(err) or tail(out) or f"exit {code}")
    return ok
