# A persona's best of: lines of its answers you kept (★ keep under an answer, or a selected bit
# with the right mouse button), in best-of.md in its folder. Plain Markdown: each entry is the
# date and the chat it came from, then the kept text as a quote. Edit it freely.

import os
import time

from . import personas

FILE = "best-of.md"


def path(pid):
    return os.path.join(personas.folder(pid), FILE)


def read(pid):
    try:
        with open(path(pid), encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def write(pid, text):
    tmp = path(pid) + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text.strip() + "\n" if text.strip() else "")
    os.replace(tmp, path(pid))


def add(pid, text, where=""):
    """Keep text (from the chat called where) at the end of the persona's best of."""
    quote = "\n".join("> " + line if line.strip() else ">" for line in text.strip().splitlines())
    head = time.strftime("%Y-%m-%d") + (f" · {where}" if where else "")
    old = read(pid).rstrip()
    write(pid, (old + "\n\n" if old else "") + f"**{head}**\n\n{quote}\n")
