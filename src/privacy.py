"""privacy: what must never reach a provider marked `suspicious` (unknown operator, no policy we could read). Questions and research only.
leaks(text) returns the categories found, never the matched text, so an error message cannot repeat the secret."""
from __future__ import annotations

import getpass
import os
import re
import socket

_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.I)
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("file path", re.compile(r"(?:(?<![\w.])~/|(?<![\w:/.])\.{1,2}/|(?<![\w.:/])/(?:home|Users|root|etc|var|usr|opt|data|mnt|tmp|srv|run|media|proc|sys|boot)\b|\b[A-Za-z]:\\|(?<![\w:/.])[\w.-]+(?:/[\w.-]+){2,}\.\w{1,6}\b|\\\\[\w.-]+\\)")),
    ("email address", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("ip address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|\b(?:[0-9a-f]{1,4}:){3,}[0-9a-f]{1,4}\b", re.I)),
    ("secret or token", re.compile(r"\b(?:sk|gsk|pk|ghp|gho|ghs|xox[bap]|hf|glpat)[-_][A-Za-z0-9_-]{16,}|\bAKIA[0-9A-Z]{12,}|\bAIza[0-9A-Za-z_-]{20,}|\b[A-Za-z0-9+/_-]{40,}\b|-----BEGIN [A-Z ]*KEY")),
    ("env assignment", re.compile(r"\b[A-Z][A-Z0-9_]{3,}=\S+")),
    ("code block", re.compile(r"```")),
]


def _identity() -> list[re.Pattern[str]]:
    names = {n for n in (os.environ.get("USER"), os.environ.get("LOGNAME"), _try(getpass.getuser), _try(socket.gethostname), os.path.basename(os.path.expanduser("~"))) if n and len(n) >= 3}
    return [re.compile(rf"(?<![A-Za-z0-9]){re.escape(n)}(?![A-Za-z0-9])", re.I) for n in names]


def _try(f):
    try:
        return f()
    except Exception:
        return None


def leaks(text: str) -> list[str]:
    """The kinds of private material in `text` (empty = fine to send to a suspicious provider)."""
    bare = _URL.sub(" ", text)           # a public URL is a question about the web, not a path on this machine
    found = [name for name, rx in _RULES if rx.search(bare)]
    if any(rx.search(bare) for rx in _identity()):
        found.append("your user or host name")
    return found


_SCRUB: list[tuple[str, re.Pattern[str], str]] = [
    ("file path", re.compile("(?:" + _RULES[0][1].pattern + r")[^\s,;)\"'\]>?!]*"), "<path>"), ("email address", _RULES[1][1], "<email>"), ("ip address", _RULES[2][1], "<ip>"),
    ("secret or token", _RULES[3][1], "<secret>"), ("env assignment", _RULES[4][1], "<assignment>"),
]


def scrub(text: str) -> tuple[str, list[str]]:
    """Make a question safe to send: private tokens become <path>, <email>, <ip>, <secret>, <assignment>, your user/host name becomes <name>.
    Returns (clean text, what was replaced). Code blocks cannot be abstracted: they are left in, so leaks() still refuses them - ask the idea, not the code.
    The result is checked again by the caller with leaks(); scrub never claims the text is clean."""
    urls: list[str] = []
    def keep(m: re.Match[str]) -> str:
        urls.append(m.group(0))
        return f"\x00{len(urls) - 1}\x00"
    out, done = _URL.sub(keep, text), []
    for name, rx, repl in _SCRUB:
        out, n = rx.subn(repl, out)
        if n:
            done.append(name)
    for rx in _identity():
        out, n = rx.subn("<name>", out)
        if n and "your user or host name" not in done:
            done.append("your user or host name")
    return re.sub("\x00(\\d+)\x00", lambda m: urls[int(m.group(1))], out), done
