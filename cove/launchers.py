# Launchers: one small text file per model setup in ~/.config/aicove/launchers/<name>.txt
#
#   name: Qwen3.5-9B Defiant Fable          what the window and the chat call it
#   description: uncensored creative, Q8    one line, optional
#   model: qwen3.5-9b-defiant-fable.gguf    relative to the model folder in the settings, or a full path
#   eyes: some-mmproj.gguf                  optional: the mmproj file of an image model
#   persona: assistant                      optional: the persona new chats start with
#   gpu_layers: all                         optional: all, auto or a number (else the default in the settings)
#   options: -c 32768 --parallel 1 --jinja  everything else for llama-server, as on the command line
#
# A launcher of another machine (reached with ssh, a name from ~/.ssh/config) has instead:
#
#   name: Qwen3.6 35B (big card)            optional; else the other machine's name for it
#   host: gpubox                            the machine
#   launcher: qwen36                        its launcher there (else the same name as this file)
#
# That machine needs AIcove too. Its model runs there; a tunnel brings it to our port, so the
# chat doesn't know the difference. What that machine said about its launchers (aicove info)
# is kept in ~/.cache/aicove/remote/<host>.json and asked again now and then: its names and what
# its models can do follow from there, its new launchers are added here (as <name>-<host>) and
# the ones it no longer has go (into the backup folder). One deleted here isn't added again
# ("skip" in that file) until the machine is added again with "From another machine…".
#
# The window edits these; editing them by hand keeps working.

import json
import os
import re
import shlex
import shutil
import struct
import time

from .settings import BACKUP_DIR, LAUNCHER_DIR, S, STATE_DIR

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
EYES_FLAGS = ("--mmproj", "-mm")
# After the name of a model that can see pictures, wherever models are listed.
SEES = "  👁"
# After the name of another machine's launcher.
ELSEWHERE = "  🖥 {}"
LAYER_FLAGS = ("-ngl", "--gpu-layers", "--n-gpu-layers")
BATCH_FLAGS = ("-b", "--batch-size")
UBATCH_FLAGS = ("-ub", "--ubatch-size")
# Models with eyes take a picture in one batch (Gemma's do, all its tokens at once), and a picture
# is often more than llama-server's default of 512 tokens: it then aborts. So they get this much,
# unless the launcher says otherwise.
EYES_BATCH = "2048"
LAYERS = re.compile(r"all|auto|\d+")
REMOTE_DIR = os.path.join(STATE_DIR, "remote")
# The model "path" of a launcher of another machine: remote:<host>/<its launcher there>.
REMOTE = "remote:"


def models_dir():
    return os.path.expanduser(S.models_dir)


def full_path(path):
    """A model path from a launcher file as a full path."""
    path = os.path.expanduser(path)
    return path if os.path.isabs(path) or not models_dir() else os.path.join(models_dir(), path)


def short_path(path):
    """The way a model path is written into a launcher file: relative when it is in the model folder."""
    base = models_dir()
    if base and os.path.isabs(path) and os.path.commonpath([base, path]) == base and path != base:
        return os.path.relpath(path, base)
    return path


def take(args, flags):
    """(value of the first of these flags or None, the arguments without it)."""
    for i, arg in enumerate(args):
        if arg in flags and i + 1 < len(args):
            return args[i + 1], args[:i] + args[i + 2:]
        for flag in flags:
            if flag.startswith("--") and arg.startswith(flag + "="):
                return arg[len(flag) + 1:], args[:i] + args[i + 1:]
    return None, list(args)


def text_of(desc, model, friendly, args, persona):
    """What goes into a launcher file. args is everything for llama-server after the model."""
    eyes, rest = take(list(args), EYES_FLAGS)
    layers, rest = take(rest, LAYER_FLAGS)
    lines = [f"name: {friendly}"]
    if desc:
        lines.append(f"description: {desc}")
    lines.append(f"model: {short_path(model)}")
    if eyes:
        lines.append(f"eyes: {short_path(eyes)}")
    if persona:
        lines.append(f"persona: {persona}")
    if layers:
        lines.append(f"gpu_layers: {layers}")
    lines.append(f"options: {shlex.join(rest)}")
    return "\n".join(lines) + "\n"


def path_of(name):
    return os.path.join(LAUNCHER_DIR, name + ".txt")


class Launcher:
    """One launcher file. error is set when it can't be used or edited here.
    args holds everything for llama-server after the model, eyes file and GPU layers included."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)[:-len(".txt")]
        self.error = None
        self.desc = self.model = self.friendly = self.persona = ""
        self.args = []
        self.host = self.remote_name = ""
        self.info = {}
        fields = {}
        with open(path, encoding="utf-8") as f:
            for line in f.read().split("\n"):
                key, sep, value = line.partition(":")
                if sep and not line.startswith("#") and key.strip() not in fields:
                    fields[key.strip()] = value.strip()
        if fields.get("host"):
            self.remote(fields)
            return
        if not fields.get("model"):
            self.error = "no \"model:\" line"
            return
        self.model = full_path(fields["model"])
        self.friendly = fields.get("name") or self.name
        self.desc = fields.get("description", "")
        self.persona = fields.get("persona", "")
        try:
            self.args = shlex.split(fields.get("options", ""))
        except ValueError as e:
            self.error = f"can't read the \"options:\" line ({e})"
            return
        layers = fields.get("gpu_layers", "")
        if layers:
            if not LAYERS.fullmatch(layers):
                self.error = "\"gpu_layers:\" must be all, auto or a number"
                return
            self.args = ["-ngl", layers] + self.args
        if fields.get("eyes"):
            self.args = ["--mmproj", full_path(fields["eyes"])] + self.args

    def remote(self, fields):
        self.host = fields["host"]
        self.remote_name = fields.get("launcher") or self.name
        if not (NAME.fullmatch(self.host) and NAME.fullmatch(self.remote_name)):
            self.error = "\"host:\" and \"launcher:\" must be plain names"
            return
        self.model = f"{REMOTE}{self.host}/{self.remote_name}"
        known = remote_info(self.host).get("launchers")
        self.info = (known or {}).get(self.remote_name, {})
        self.friendly = fields.get("name") or self.info.get("friendly") or self.remote_name
        self.desc = fields.get("description") or self.info.get("desc", "")
        self.persona = fields.get("persona", "")
        if known is not None and self.remote_name not in known:
            self.error = f"{self.host} no longer has {self.remote_name}"

    @property
    def present(self):
        """Whether it can be started from here: its model file is there, or it is another
        machine's (that one finds out when asked)."""
        return bool(self.host) or os.path.isfile(self.model)

    def backup(self):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        return os.path.join(BACKUP_DIR, f"{self.name}.{time.strftime('%Y%m%d-%H%M%S')}.txt")

    @property
    def sees(self):
        """Whether it has eyes (an mmproj file that is there), so it can look at pictures."""
        if self.host:
            return bool(self.info.get("sees"))
        eyes, _ = take(list(getattr(self, "args", [])), EYES_FLAGS)
        return bool(eyes) and os.path.isfile(eyes)

    def label(self, text=None):
        """text (else its friendly name) with an eye when it can see pictures."""
        text = text or self.friendly or self.name
        text += SEES if self.sees else ""
        return text + ELSEWHERE.format(self.host) if self.host else text

    def files(self):
        """The files on disk it runs: the model, and the eyes file if it has one."""
        if self.host:
            return []
        eyes, _ = take(list(self.args), EYES_FLAGS)
        return [self.model] + ([eyes] if eyes else [])

    def retire(self):
        """Move the file into the backup folder; returns where it went."""
        dest = self.backup()
        shutil.move(self.path, dest)
        return dest

    def write(self, desc, model, friendly, args, persona):
        shutil.copy2(self.path, self.backup())
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text_of(desc, model, friendly, args, persona))


def create(name, desc, model, friendly, args, persona):
    """Write a new launcher file; raises FileExistsError if the name is taken. Returns its path."""
    os.makedirs(LAUNCHER_DIR, exist_ok=True)
    with open(path_of(name), "x", encoding="utf-8") as f:
        f.write(text_of(desc, model, friendly, args, persona))
    return path_of(name)


def find_launchers():
    try:
        names = sorted(os.listdir(LAUNCHER_DIR))
    except OSError:
        return []
    found = []
    for name in names:
        if name.endswith(".txt") and NAME.fullmatch(name[:-len(".txt")]):
            try:
                found.append(Launcher(os.path.join(LAUNCHER_DIR, name)))
            except (OSError, UnicodeDecodeError):
                continue
    return found


def get(name):
    """The launcher of that name, or None."""
    if NAME.fullmatch(name) and os.path.isfile(path_of(name)):
        try:
            return Launcher(path_of(name))
        except (OSError, UnicodeDecodeError):
            pass
    return None


def run_args(args):
    """A launcher's arguments the way llama-server gets them: GPU layers from the launcher, else
    from the settings. "all" is sent as a number no model reaches, which every version understands;
    "auto" is llama-server's own choice, so nothing is sent."""
    layers, rest = take(list(args), LAYER_FLAGS)
    if take(rest, EYES_FLAGS)[0]:
        if not take(rest, BATCH_FLAGS)[0]:
            rest += ["-b", EYES_BATCH]
        if not take(rest, UBATCH_FLAGS)[0]:
            rest += ["-ub", EYES_BATCH]
    layers = layers or S.gpu_layers
    if layers == "auto" or not LAYERS.fullmatch(layers):
        return rest
    return ["-ngl", "999" if layers == "all" else layers] + rest


def command(model, args):
    """The whole llama-server command line for a model file and a launcher's arguments."""
    return [S.llama_server, "-m", model, "--host", S.host, "--port", str(S.llama_port)] + run_args(args)


# --- what a model file can do ----------------------------------------------------------

_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
_template_cache = {}


def chat_template(path):
    """The chat template stored in a GGUF file, "" if it has none, None if it can't be read.
    The word list in front of it is stepped over, not read."""
    try:
        with open(path, "rb") as f:
            def length():
                n, = struct.unpack("<Q", f.read(8))
                if n > 1 << 26:
                    raise ValueError("not a GGUF file we can read")
                return n

            def skip(t):
                if t == 8:
                    f.seek(length(), 1)
                elif t == 9:
                    et, n = struct.unpack("<IQ", f.read(12))
                    if et in _SIZES:
                        f.seek(n * _SIZES[et], 1)
                    else:
                        for _ in range(n):
                            skip(et)
                else:
                    f.seek(_SIZES[t], 1)

            if f.read(4) != b"GGUF":
                return None
            f.read(4)
            _, count = struct.unpack("<QQ", f.read(16))
            for _ in range(count):
                name = f.read(length()).decode(errors="replace")
                t, = struct.unpack("<I", f.read(4))
                if name == "tokenizer.chat_template" and t == 8:
                    return f.read(length()).decode(errors="replace")
                skip(t)
            return ""
    except (OSError, struct.error, KeyError, ValueError, OverflowError, MemoryError):
        return None


def template_of(path):
    """chat_template(), remembered per file."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    if key not in _template_cache:
        _template_cache[key] = chat_template(path)
    return _template_cache[key]


def remote_info(host):
    """What another machine said about its launchers when last asked ({} if never)."""
    try:
        with open(os.path.join(REMOTE_DIR, host + ".json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_remote_info(host, info):
    """Keep what host said; the launchers deleted here (skip) stay remembered."""
    skip = info["skip"] if "skip" in info else remote_info(host).get("skip", [])
    info = dict(info, skip=sorted(set(skip)))
    os.makedirs(REMOTE_DIR, exist_ok=True)
    with open(os.path.join(REMOTE_DIR, host + ".json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)


def skip_remote(launcher):
    """A launcher of another machine was deleted here: don't add it back."""
    info = remote_info(launcher.host)
    info["skip"] = sorted(set(info.get("skip", [])) | {launcher.remote_name})
    save_remote_info(launcher.host, info)


def follow(host, info, again=False):
    """Make our launchers of host match what it has now (info from aicove info there).
    again: the machine is added again, the ones deleted here come back too.
    Returns (added, removed): names of our launcher files."""
    if again:
        info = dict(info, skip=[])
    save_remote_info(host, info)
    skip = set(remote_info(host).get("skip", []))
    have = {l.remote_name: l for l in find_launchers() if l.host == host}
    added, removed = [], []
    for name, there in sorted(info["launchers"].items()):
        mine = f"{name}-{host}"
        if name in have or name in skip or there.get("error") or not there.get("present") \
                or os.path.exists(path_of(mine)) or not NAME.fullmatch(name):
            continue
        os.makedirs(LAUNCHER_DIR, exist_ok=True)
        with open(path_of(mine), "x", encoding="utf-8") as f:
            f.write(f"host: {host}\nlauncher: {name}\n")
        added.append(mine)
    for name, l in have.items():
        if name not in info["launchers"]:
            l.retire()
            removed.append(l.name)
    return added, removed


def hosts():
    """The other machines we have launchers of."""
    return sorted({l.host for l in find_launchers() if l.host})


def remote_said(path, key):
    """For a model of another machine (remote:<host>/<launcher>): what it said about key, or None."""
    host, _, name = path[len(REMOTE):].partition("/")
    return remote_info(host).get("launchers", {}).get(name, {}).get(key)


def info():
    """What this machine tells another one about its launchers (aicove info)."""
    found = {}
    for l in find_launchers():
        if l.host:
            continue
        found[l.name] = {"friendly": l.friendly, "desc": l.desc, "error": l.error,
                         "model": os.path.basename(l.model), "present": not l.error and l.present,
                         "sees": not l.error and l.sees,
                         "tools": None if l.error else can_use_tools(l.model),
                         "think": None if l.error else can_think(l.model)}
    return {"port": S.llama_port, "host": S.host, "launchers": found}


def can_use_tools(path):
    """Whether the model's chat template knows tools (file access needs them): True, False,
    or None when the file can't be read."""
    if path.startswith(REMOTE):
        return remote_said(path, "tools")
    template = template_of(path)
    return None if template is None else ("tools" in template or "tool_call" in template)


def can_think(path):
    """Whether thinking can be switched on and off for this model (its template reads
    enable_thinking): True, False, or None when the file can't be read."""
    if path.startswith(REMOTE):
        return remote_said(path, "think")
    template = template_of(path)
    return None if template is None else "enable_thinking" in template
