# Personas: one folder per persona in ~/AIcove/personas/<id>/, with persona.txt: first line
# "name: ...", optional "models: <launcher>, <launcher>" (what it runs on, the first one is
# started; launchers are per machine, so the same persona finds its model wherever it is),
# setting lines ("temperature: 0.3", "thinking: off", see SAMPLERS), optional "files: read" or
# "files: write" (what it may do with the files of this machine), empty line, then the system
# prompt. Next to it: picture.png (or .jpg/.webp/.gif), knowledge/, notes/ and chats/.

import os
import re
import shutil
import time
import zipfile

from .settings import PERSONA_DIR

TRASH_DIR = os.path.join(PERSONA_DIR, ".trash")
PERSONA_FILE = "persona.txt"
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# Settings a persona can carry (samplers, answer length, seed, thinking on/off). They go to
# llama-server with every message of that persona and win over the launcher's defaults.
SAMPLERS = ["thinking", "max_tokens", "temperature", "min_p", "top_p", "top_k", "repeat_penalty",
            "dry_multiplier", "xtc_probability", "xtc_threshold", "seed"]
WHOLE = {"top_k", "max_tokens", "seed"}
# Picture formats, and the file ending each one gets here.
PICTURE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}


def sampler_value(key, value):
    """A sampler value in the one spelling used for files and comparing, or None if unusable."""
    if key == "thinking":
        value = str(value).strip().lower()
        return value if value in ("on", "off") else None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if key in WHOLE:
        return str(int(number))
    text = f"{number:.3f}".rstrip("0")
    return text + "0" if text.endswith(".") else text


def folder(pid):
    """The folder of a persona: its file, picture, knowledge, notes and chats."""
    return os.path.join(PERSONA_DIR, pid)


def picture_path(pid):
    """The picture file of a persona, or None."""
    for ext in PICTURE_TYPES.values():
        path = os.path.join(folder(pid), "picture" + ext)
        if os.path.isfile(path):
            return path
    return None


def remove_picture(pid):
    """Move a persona's picture to the trash folder."""
    path = picture_path(pid)
    while path:
        os.makedirs(TRASH_DIR, exist_ok=True)
        ext = os.path.splitext(path)[1]
        os.replace(path, os.path.join(TRASH_DIR, f"{pid}.{time.strftime('%Y%m%d-%H%M%S')}{ext}"))
        path = picture_path(pid)


def set_picture(pid, data, ext=".png"):
    """Make data (the bytes of an image file) the picture of a persona."""
    remove_picture(pid)
    os.makedirs(folder(pid), exist_ok=True)
    with open(os.path.join(folder(pid), "picture" + ext), "wb") as f:
        f.write(data)


class Persona:
    def __init__(self, pid, name="", system="", samplers=None, launcher="", files=""):
        self.id = pid
        self.name = name
        self.system = system
        self.samplers = dict(samplers or {})
        # The launchers it may run on, the one it is started with first.
        self.models = [launcher] if launcher else []
        # What it may do with the files of this machine: "" nothing, "read", "write".
        self.files = files
        # Its voice: a profile of the voice server (cove/voice.py), "" = none.
        self.voice = ""

    @property
    def launcher(self):
        """The launcher it is started with, or ""."""
        return self.models[0] if self.models else ""

    @launcher.setter
    def launcher(self, name):
        self.models = [name] + [m for m in self.models if m != name] if name else []

    @property
    def path(self):
        return os.path.join(folder(self.id), PERSONA_FILE)

    @classmethod
    def read(cls, pid):
        p = cls(pid)
        with open(p.path, encoding="utf-8") as f:
            text = f.read()
        first, _, rest = text.partition("\n")
        if not first.lower().startswith("name:"):
            p.name, p.system = pid, text.strip()
            return p
        p.name = first[5:].strip()
        # Launcher and sampler lines directly under the name, up to the first empty line.
        while True:
            line, _, after = rest.partition("\n")
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if key in ("models", "launcher"):
                # "launcher:" is from before there could be several. Files from the old ~/bin
                # setup name the script: defiant-start.
                names = [n.strip() for n in value.split(",")]
                names = [n[:-len("-start")] if n.endswith("-start") else n for n in names]
                if not names or not all(SAFE_ID.fullmatch(n) for n in names):
                    break
                p.models = list(dict.fromkeys(names))
            elif key in SAMPLERS and sampler_value(key, value) is not None:
                p.samplers[key] = sampler_value(key, value)
            elif key == "files" and value in ("read", "write"):
                p.files = value
            elif key == "voice" and re.fullmatch(r"[\w.-]+", value):
                p.voice = value
            else:
                break
            rest = after
        p.system = rest.strip()
        return p

    def write(self):
        os.makedirs(folder(self.id), exist_ok=True)
        head = (f"models: {', '.join(self.models)}\n" if self.models else "") \
            + "".join(f"{k}: {self.samplers[k]}\n" for k in SAMPLERS if k in self.samplers) \
            + (f"files: {self.files}\n" if self.files else "") \
            + (f"voice: {self.voice}\n" if self.voice else "")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(f"name: {self.name}\n{head}\n{self.system}\n")

    def mtime(self):
        return os.path.getmtime(self.path)


def chat_messages(data):
    """The messages of a chat file's data, with the earlier answers kept with its last question
    (chat.py), for tidying up the files messages need."""
    messages = data.get("messages", [])
    found = list(messages)
    for m in messages:
        for turn in m.get("answers") or []:
            found += turn
    return found


def load_all():
    try:
        names = sorted(os.listdir(PERSONA_DIR))
    except OSError:
        return {}
    ids = [n for n in names if SAFE_ID.fullmatch(n) and os.path.isfile(os.path.join(PERSONA_DIR, n, PERSONA_FILE))]
    return {i: Persona.read(i) for i in ids}


def new_id(name):
    """A free id made from the name, e.g. "Dr. Faust" -> dr-faust."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "persona"
    taken = set(os.listdir(PERSONA_DIR)) if os.path.isdir(PERSONA_DIR) else set()
    pid, n = base, 2
    while pid in taken:
        pid, n = f"{base}-{n}", n + 1
    return pid


def trash(pid):
    """Move a persona's whole folder (chats, knowledge and notes too) to the trash folder."""
    os.makedirs(TRASH_DIR, exist_ok=True)
    os.replace(folder(pid), os.path.join(TRASH_DIR, f"{pid}.{time.strftime('%Y%m%d-%H%M%S')}"))



delete = trash


# --- one persona as one file --------------------------------------------------------
# A .zip with the persona's folder in it: persona.txt and picture always, its knowledge (and
# notes), facts and chats if wanted. Hidden files (caches) stay out.

# What can go along, besides persona.txt and the picture: (part, folders, what it is).
PARTS = [("knowledge", ["knowledge", "notes"], "Knowledge files (and notes)"),
         ("facts", ["facts"], "Facts: what it remembers about you"),
         ("chats", ["chats"], "Chats (with their pictures and audio)"),
         ("bestof", ["best-of.md"], "Best of: the lines of it you kept")]
MAX_IMPORT = 2 * 1024 ** 3


def export(pid, path, parts=("knowledge",)):
    """Write persona pid with the wanted PARTS to the .zip at path. Returns the number of files."""
    root = folder(pid)
    names = [PERSONA_FILE] + [os.path.basename(p) for p in [picture_path(pid)] if p]
    for part, dirs, _ in PARTS:
        if part not in parts:
            continue
        for d in dirs:
            if os.path.isfile(os.path.join(root, d)):
                names.append(d)
            for top, subdirs, files in os.walk(os.path.join(root, d)):
                subdirs[:] = sorted(x for x in subdirs if not x.startswith("."))
                names += [os.path.relpath(os.path.join(top, f), root) for f in sorted(files)
                          if not f.startswith(".") and os.path.isfile(os.path.join(top, f))]
    tmp = path + ".new"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name in names:
            z.write(os.path.join(root, name), f"{pid}/{name}")
    os.replace(tmp, path)
    return len(names)


def import_(path):
    """Add the persona in the .zip at path as a new persona (a new id if its own is taken, and
    "(2)" after its name if a persona has that name). Returns the new Persona. Raises ValueError
    for a file that isn't an exported persona, OSError when it can't be read or written."""
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ValueError("not a .zip file") from None
    with z:
        members = [m for m in z.infolist() if not m.is_dir()]
        tops = {m.filename.split("/", 1)[0] for m in members}
        if len(tops) != 1 or not SAFE_ID.fullmatch(next(iter(tops))):
            raise ValueError("not an exported persona (it should hold one persona folder)")
        top = next(iter(tops))
        for m in members:
            parts = m.filename.split("/")[1:]
            if not parts or any(p in ("", ".", "..") for p in parts) or "\\" in m.filename:
                raise ValueError(f"a file with a strange path: {m.filename}")
        if f"{top}/{PERSONA_FILE}" not in {m.filename for m in members}:
            raise ValueError(f"there is no {PERSONA_FILE} in it")
        if sum(m.file_size for m in members) > MAX_IMPORT:
            raise ValueError("it is bigger than 2 GB unpacked")
        pid = new_id(top)
        os.makedirs(PERSONA_DIR, exist_ok=True)
        work = os.path.join(PERSONA_DIR, f".import-{pid}")
        shutil.rmtree(work, ignore_errors=True)
        try:
            for m in members:
                target = os.path.join(work, *m.filename.split("/")[1:])
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(m) as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out)
            os.replace(work, folder(pid))
        finally:
            shutil.rmtree(work, ignore_errors=True)
    persona = Persona.read(pid)
    names = {p.name for i, p in load_all().items() if i != pid}
    if persona.name in names:
        base, n = persona.name, 2
        while f"{base} ({n})" in names:
            n += 1
        persona.name = f"{base} ({n})"
        persona.write()
    return persona
