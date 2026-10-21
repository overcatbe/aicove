# AIcove's window: the launchers (model + settings) as forms, the personas, and the settings
# of this machine. The files stay the source of truth: launchers in ~/.config/aicove/launchers/,
# personas in ~/.config/aicove/personas/. Editing them by hand keeps working; an option this
# window doesn't know ends up in "Extra options" untouched.

import json
import os
import re
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zlib

from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtCore import QBuffer, QIODevice, QLocale, QObject, QProcess, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QDesktopServices, QFontDatabase, QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSpinBox,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import fetch, personas, profiles, server, settings, sync, tools, voice
from . import launchers as launcher_files
from .gui_chat import FACE_SIZE, ChatTab, face
from .gui_agents import AgentsTab
from .gui_docs import DocsTab
from .gui_passwords import PasswordsTab
from .gui_writing import WritingTab
from .launchers import find_launchers
from .settings import BACKUP_DIR, CONFIG_FILE, JOB_DIR, S, cove_cmd

# Where the Settings tab is in the row of tabs.

# What a new launcher starts with.
NEW_CONTEXT = 32768
NEW_ARGS = ["--parallel", "1", "--jinja"]
WORK_BYTES = 0.8e9

STYLE_GROUP = "Writing style (defaults — a persona can set its own)"
# One-click settings for the writing-style group; anything not named gets unticked.
PRESETS = {
    "Model defaults": {},
    "Factual": {"--temp": "0.3"},
    "Creative": {"--temp": "1.0", "--min-p": "0.05", "--dry-multiplier": "0.8"},
    "Story": {"--temp": "1.1", "--min-p": "0.05", "--dry-multiplier": "0.8",
              "--xtc-probability": "0.5", "--xtc-threshold": "0.1"},
}
# The writing-style options a persona can carry: launcher flag -> name in the persona file.
PERSONA_SAMPLERS = {"--temp": "temperature", "--min-p": "min_p", "--top-p": "top_p", "--top-k": "top_k",
                    "--repeat-penalty": "repeat_penalty", "--dry-multiplier": "dry_multiplier",
                    "--xtc-probability": "xtc_probability", "--xtc-threshold": "xtc_threshold"}

# What a persona can carry besides the writing style; the preset buttons leave these alone.
PERSONA_EXTRA = {"thinking": "thinking", "-n": "max_tokens", "--seed": "seed"}

# Persona pictures are stored square, this many pixels a side (the chat shows them small).
PICTURE_SIZE = 256

KV_TYPES = ["f16", "q8_0", "q5_1", "q5_0", "q4_1", "q4_0"]
# Bytes per number in the context memory for each -ctk/-ctv type.
KV_BYTES = {"f16": 2.0, "q8_0": 34 / 32, "q5_1": 24 / 32, "q5_0": 22 / 32, "q4_1": 20 / 32, "q4_0": 18 / 32}
SPEC_TYPES = ["draft-mtp", "draft-simple", "draft-eagle3", "draft-dflash", "draft-dspark",
              "ngram-simple", "ngram-map-k", "ngram-map-k4v", "ngram-mod", "ngram-cache", "none"]


def opt(group, flag, kind, label, hint, default=None, aliases=(), **kw):
    return dict(group=group, flag=flag, kind=kind, label=label, hint=hint, default=default,
                aliases=(flag,) + tuple(aliases), **kw)


# One entry per form field. Order here = order in the window and in the written script.
OPTIONS = [
    opt("Memory / length", "-ngl", "combo", "On the GPU",
        "How much of the model goes on the GPU: all, auto (as much as fits; the rest runs from "
        "normal RAM, slower) or a number of layers. Unticked = the default on the Settings tab.",
        "auto", ["--gpu-layers", "--n-gpu-layers"], editable=True, pattern=r"all|auto|\d+",
        items=["all", "auto", "8", "16", "24", "32"]),
    opt("Memory / length", "-c", "combo", "Context size",
        "Tokens it \"remembers\". Bigger = more RAM. 0 = the model's own maximum (can be huge).",
        "32768", ["--ctx-size"], numeric=True, editable=True,
        items=["4096", "8192", "16384", "32768", "65536", "131072", "262144", "0"]),
    opt("Memory / length", "--parallel", "int", "Parallel chats",
        "1 = one chat at a time, the whole context goes to that one chat.",
        1, ["-np"], min=1, max=64),
    opt("Memory / length", "-ctk", "combo", "Compress context (K)",
        "q8_0 = about half the RAM, tiny quality loss. Set K and V the same. Needs flash attention on.",
        "q8_0", ["--cache-type-k"], items=KV_TYPES),
    opt("Memory / length", "-ctv", "combo", "Compress context (V)",
        "Second half of the same setting.",
        "q8_0", ["--cache-type-v"], items=KV_TYPES),
    opt("Memory / length", "-fa", "combo", "Flash attention",
        "Faster + less memory. Unset = auto.",
        "on", ["--flash-attn"], items=["on", "off", "auto"]),
    opt("Memory / length", "-nkvo", "flag", "Context in normal RAM",
        "Keep the context out of GPU memory.",
        aliases=["--no-kv-offload"]),
    opt("Memory / length", "--cpu-moe", "flag", "Experts in normal RAM",
        "For mixture-of-experts models (Qwen3.6-35B-A3B and the like): the experts, most of the "
        "file, stay in normal RAM and only the small always-used part goes on the GPU.",
        aliases=["-cmoe"]),
    opt("Memory / length", "--n-cpu-moe", "int", "Experts in normal RAM, first layers",
        "The same for the first N layers only; the experts of the other layers go on the GPU.",
        8, ["-ncmoe"], min=0, max=999),
    opt("Memory / length", "-n", "int", "Max answer length",
        "Tokens per answer. -1 = no limit.",
        4096, ["--predict", "--n-predict"], min=-1, max=10000000, step=256),

    opt("Chat format / thinking", "--jinja", "flag", "Model's own chat template",
        "On by default in this llama.cpp, harmless to keep."),
    opt("Chat format / thinking", "--mmproj", "file", "Eyes file (mmproj)",
        "For image models. Only works with the model it was made for.",
        aliases=["-mm"]),
    opt("Chat format / thinking", "--reasoning-budget", "int", "Thinking budget",
        "Thinking models: 0 = skip thinking, 1000 = think max ~1000 tokens, -1 = unlimited.",
        1000, min=-1, max=1000000, step=100),
    opt("Chat format / thinking", "--reasoning-budget-message", "text", "When the budget is used up",
        "Said to the model when its thinking budget runs out, so it moves on to answering cleanly.",
        "Enough thinking. Answer now."),
    opt("Chat format / thinking", "--chat-template-kwargs", "text", "Template settings",
        "The Qwen way to turn thinking off: {\"enable_thinking\":false}",
        '{"enable_thinking":false}'),

    opt("Writing style (defaults — a persona can set its own)", "--temp", "float",
        "Temperature",
        "Randomness. 0.1-0.3 focused/factual, 0.7-1.0 creative, >1.2 wild. Unset = 0.8.",
        0.8, ["--temperature"], min=0.0, max=5.0, step=0.05),
    opt("Writing style (defaults — a persona can set its own)", "--min-p", "float", "Min-p",
        "Cuts nonsense words; good with high temperature (0.05-0.1).",
        0.05, min=0.0, max=1.0, step=0.01),
    opt("Writing style (defaults — a persona can set its own)", "--top-p", "float", "Top-p",
        "Older way to do the same, usually leave alone. Unset = 0.95.",
        0.95, min=0.0, max=1.0, step=0.01),
    opt("Writing style (defaults — a persona can set its own)", "--top-k", "int", "Top-k",
        "Same, usually leave alone. Unset = 40, 0 = off.",
        40, min=0, max=100000),
    opt("Writing style (defaults — a persona can set its own)", "--repeat-penalty", "float",
        "Repeat penalty",
        "Discourages repeating itself. 1.0 = off, don't go above 1.2.",
        1.1, min=0.0, max=3.0, step=0.01),
    opt("Writing style (defaults — a persona can set its own)", "--dry-multiplier", "float",
        "DRY",
        "Smarter anti-repetition for long stories. 0 = off, 0.8 is a good start.",
        0.8, min=0.0, max=10.0, step=0.1),
    opt("Writing style (defaults — a persona can set its own)", "--xtc-probability", "float",
        "XTC probability",
        "Sometimes skips the most obvious word -> less cliche prose. Try 0.5.",
        0.5, min=0.0, max=1.0, step=0.05),
    opt("Writing style (defaults — a persona can set its own)", "--xtc-threshold", "float",
        "XTC threshold",
        "Goes with XTC probability. Try 0.1.",
        0.1, min=0.0, max=1.0, step=0.01),
    opt("Writing style (defaults — a persona can set its own)", "--seed", "int", "Seed",
        "Same seed + same prompt = same answer (reproducible tests).",
        42, ["-s"], min=-1, max=2147483647),

    opt("Speed", "--spec-type", "combo", "Speculation",
        "draft-mtp = use the built-in MTP heads; only with \"MTP\" GGUF files. Test if it's faster.",
        "draft-mtp", items=SPEC_TYPES),
    opt("Speed", "-t", "int", "CPU threads",
        "Only matters if part of the model runs on the CPU.",
        8, ["--threads"], min=1, max=os.cpu_count() or 64),
    opt("Speed", "--mlock", "flag", "Lock in RAM",
        "Keep the model locked in RAM (no swapping out)."),

    opt("Other", "-a", "text", "Name the API reports",
        "Model name the API reports.", "", ["--alias"]),
    opt("Other", "--api-key", "text", "Password (API key)",
        "Only needed if others on the network could reach it.", ""),
]
BY_ALIAS = {a: o for o in OPTIONS for a in o["aliases"]}
# Not a launcher option: only the persona form has it.
PERSONA_THINKING = opt("", "thinking", "combo", "Thinking",
                       "off = answer straight away, on = think first (slower, shown as "
                       "\"Thought for ...\"). Unticked = whatever the launcher says.",
                       "off", items=["off", "on"])


def fmt_float(v):
    s = f"{float(v):.3f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def normalize(spec, value):
    """Check a value read from a script; raises ValueError if this field can't hold it."""
    kind = spec["kind"]
    if kind == "int":
        n = int(value)
        if not spec["min"] <= n <= spec["max"]:
            raise ValueError(value)
        return str(n)
    if kind == "float":
        f = float(value)
        if not spec["min"] <= f <= spec["max"]:
            raise ValueError(value)
        return fmt_float(f)
    if kind == "combo":
        if spec.get("numeric"):
            return str(int(value))
        if not spec.get("editable") and value not in spec["items"]:
            raise ValueError(value)
        if spec.get("pattern") and not re.fullmatch(spec["pattern"], value):
            raise ValueError(value)
    return value


def parse_args(tokens):
    """Split llama-server arguments into known options and everything else."""
    opts, extra = {}, []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        flag, val = tok, None
        if tok.startswith("--") and "=" in tok:
            flag, val = tok.split("=", 1)
        spec = BY_ALIAS.get(flag)
        if spec is None or (spec["kind"] == "flag" and val is not None):
            extra.append(tok)
            i += 1
            continue
        if spec["kind"] == "flag":
            opts[spec["flag"]] = True
            i += 1
            continue
        used = 1
        if val is None:
            if i + 1 >= len(tokens):
                extra.append(tok)
                i += 1
                continue
            val = tokens[i + 1]
            used = 2
        try:
            opts[spec["flag"]] = normalize(spec, val)
        except ValueError:
            extra.extend(tokens[i:i + used])
        i += used
    return opts, extra


def build_args(opts, extra):
    args = []
    for spec in OPTIONS:
        val = opts.get(spec["flag"])
        if val is None:
            continue
        args.append(spec["flag"])
        if spec["kind"] != "flag":
            args.append(val)
    return args + list(extra)


def running_server():
    """(model path, args after the fixed ones) of the running llama-server, or None."""
    return server.running()


def file_size(path):
    try:
        return f"{os.path.getsize(path) / 1e9:.1f} GB"
    except OSError:
        return None


def html(text):
    return text.replace("&", "&amp;").replace("<", "&lt;")


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


_GGUF_SIZE = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
_GGUF_FMT = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}
_gguf_cache = {}


def gguf_meta(path):
    """The model's own description from the head of a GGUF file (layers, widths, max context)."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return {}
    if key in _gguf_cache:
        return _gguf_cache[key]
    meta = {}
    try:
        with open(path, "rb") as f:
            def string():
                n, = struct.unpack("<Q", f.read(8))
                if n > 1 << 26:
                    raise ValueError("not a text length — damaged or not a GGUF file we can read")
                return f.read(n).decode(errors="replace")

            def value(t):
                if t == 8:
                    return string()
                if t == 9:
                    et, n = struct.unpack("<IQ", f.read(12))
                    if et not in _GGUF_SIZE:
                        for _ in range(n):
                            value(et)
                        return None
                    if n > 512:
                        f.seek(n * _GGUF_SIZE[et], 1)
                        return None
                    return list(struct.unpack(f"<{n}{_GGUF_FMT[et]}", f.read(n * _GGUF_SIZE[et])))
                return struct.unpack("<" + _GGUF_FMT[t], f.read(_GGUF_SIZE[t]))[0]

            if f.read(4) == b"GGUF":
                f.read(4)
                _, count = struct.unpack("<QQ", f.read(16))
                for _ in range(count):
                    name = string()
                    # The word list comes after the numbers we want and is slow to skip.
                    if name.startswith("tokenizer.") and any(k.endswith(".block_count") for k in meta):
                        break
                    t, = struct.unpack("<I", f.read(4))
                    meta[name] = value(t)
    except (OSError, struct.error, KeyError, ValueError, OverflowError, MemoryError):
        pass
    _gguf_cache[key] = meta
    return meta


MOE_TENSOR = re.compile(r"blk\.(\d+)\.ffn_(up|down|gate)_(ch|)exps")
_expert_cache = {}


def expert_bytes(path):
    """{layer: bytes} of the expert weights of a mixture-of-experts model, which is what
    --cpu-moe keeps in normal RAM. Empty for other models and for files we can't read."""
    try:
        key = (path, os.path.getmtime(path))
        size = os.path.getsize(path)
    except OSError:
        return {}
    if key in _expert_cache:
        return _expert_cache[key]
    found = {}
    try:
        with open(path, "rb") as f:
            def string():
                n, = struct.unpack("<Q", f.read(8))
                if n > 1 << 26:
                    raise ValueError("not a text length")
                return f.read(n)

            def skip(t):
                if t == 8:
                    string()
                elif t == 9:
                    et, n = struct.unpack("<IQ", f.read(12))
                    if et in _GGUF_SIZE:
                        f.seek(n * _GGUF_SIZE[et], 1)
                    else:
                        for _ in range(n):
                            skip(et)
                else:
                    f.seek(_GGUF_SIZE[t], 1)

            if f.read(4) == b"GGUF":
                f.read(4)
                tensors, count = struct.unpack("<QQ", f.read(16))
                align = 32
                for _ in range(count):
                    name = string()
                    t, = struct.unpack("<I", f.read(4))
                    if name == b"general.alignment" and t == 4:
                        align, = struct.unpack("<I", f.read(4))
                    else:
                        skip(t)
                # Each tensor: name, its shape, its type, where its data starts.
                starts = []
                for _ in range(tensors):
                    name = string().decode(errors="replace")
                    dims, = struct.unpack("<I", f.read(4))
                    f.seek(8 * dims + 4, 1)
                    starts.append((struct.unpack("<Q", f.read(8))[0], name))
                data = -(-f.tell() // align) * align
                starts.sort()
                ends = [start for start, _ in starts[1:]] + [size - data]
                for (start, name), end in zip(starts, ends):
                    m = MOE_TENSOR.search(name)
                    if m:
                        found[int(m.group(1))] = found.get(int(m.group(1)), 0) + end - start
    except (OSError, struct.error, KeyError, ValueError, OverflowError, MemoryError):
        found = {}
    _expert_cache[key] = found
    return found


def context_bytes(model, ctx, ctk, ctv):
    """Rough size of the context memory (KV cache), or None if the file doesn't say enough.
    ctx None or 0 = the model's own maximum."""
    meta = gguf_meta(model)
    arch = meta.get("general.architecture")
    get = lambda k: meta.get(f"{arch}.{k}")
    layers, embd, heads = get("block_count"), get("embedding_length"), get("attention.head_count")
    if isinstance(heads, list):
        heads = max(heads) if heads else None
    if not (layers and embd and heads):
        return None
    kv_heads = get("attention.head_count_kv") or heads
    klen = get("attention.key_length") or embd // heads
    vlen = get("attention.value_length") or embd // heads
    ctx = ctx or get("context_length") or 0
    # Some layers keep less than the full context: hybrid models (Qwen3.5/3.6) only have real
    # attention in every Nth layer, and Gemma's "sliding window" layers only keep the last
    # ~1000 tokens (5 of every 6 layers unless the file lists them).
    interval = get("full_attention_interval") or 1
    window = get("attention.sliding_window") or 0
    pattern = get("attention.sliding_window_pattern")
    window_cells = min(ctx, -(-(window + 512) // 256) * 256)
    kb, vb = KV_BYTES.get(ctk, 2.0), KV_BYTES.get(ctv, 2.0)
    total = 0.0
    for il in range(layers):
        if il % interval != interval - 1:
            continue
        sliding = bool(window) and (pattern[il] if isinstance(pattern, list) and il < len(pattern)
                                    else il % 6 < 5)
        n_heads = kv_heads[il] if isinstance(kv_heads, list) and il < len(kv_heads) else kv_heads
        if isinstance(n_heads, list):
            n_heads = heads
        k = (get("attention.key_length_swa") or klen) if sliding else klen
        v = (get("attention.value_length_swa") or vlen) if sliding else vlen
        total += (window_cells if sliding else ctx) * n_heads * (k * kb + v * vb)
    return total


def scan_models(launchers):
    """Every model file in the usual places: [(path, bytes, [launchers that use it])]."""
    used = {}
    for l in launchers:
        if not l.error:
            used.setdefault(os.path.realpath(l.model), []).append(l.name)
    found = []
    for folder in S.all_scan_dirs():
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            path = os.path.join(folder, name)
            if name.endswith(".gguf") and "mmproj" not in name.lower() and os.path.isfile(path):
                found.append((path, os.path.getsize(path), used.get(os.path.realpath(path), [])))
    return found


def eyes_next_to(model):
    """The mmproj file that belongs to a model file on disk (same folder, same name stem), or None."""
    folder, stem = os.path.dirname(model), os.path.basename(model)[:-len(".gguf")]
    try:
        for name in sorted(os.listdir(folder)):
            if name.startswith(stem) and "mmproj" in name.lower() and name.endswith(".gguf"):
                return os.path.join(folder, name)
    except OSError:
        pass
    return None


def fit_text(size, eyes, kv, budget):
    """(text, colour) for one model file at the context a new launcher gets."""
    if kv is None:
        return "can't estimate", ""
    need = size + eyes + WORK_BYTES + kv
    text = f"needs ~{need / 1e9:.1f} GB"
    if not budget:
        return text, ""
    if need > budget:
        if S.gpu_layers == "all":
            return text + " — too big", "#c0392b"
        return text + " — only partly on the GPU (slower)", "#b9770e"
    if need > 0.85 * budget:
        return text + " — tight", "#b9770e"
    return text + " — fits", ""


def setup_add_line(name, desc):
    """Add a launcher to the list in the notes file (Settings tab). False if there is no such list."""
    path = os.path.expanduser(S.notes_file)
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
    except OSError:
        return False
    # The list: lines that start with a launcher's name (or an old <name>-start command).
    names = {l.name for l in find_launchers()}
    last = None
    for i, line in enumerate(lines):
        m = re.match(r"(\S+)\s+[^\s/]", line)
        if m and (m.group(1).endswith("-start") or m.group(1) in names):
            last = i
        elif last is not None:
            break
    if last is None:
        return False
    lines.insert(last + 1, f"{name:<20} {desc}")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    shutil.copy2(path, os.path.join(BACKUP_DIR, f"{os.path.basename(path)}.{time.strftime('%Y%m%d-%H%M%S')}"))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return True


# --- Hugging Face ------------------------------------------------------------

HF_LINK = re.compile(r"(?:https?://)?(?:www\.)?huggingface\.co/([\w.-]+/[\w.-]+)"
                     r"(?:/(?:blob|resolve|tree)/([^/?#]+)(?:/([^?#]+))?)?/?(?:[?#].*)?")
HF_SPLIT = re.compile(r"-\d{5}-of-\d{5}\.gguf$")
QUANT = re.compile(r"(?i)(?<![a-z0-9])(i?q\d+(?:_[a-z0-9]+)*|bf16|f16|f32)(?![a-z0-9])")


def parse_hf(text):
    """(repo, revision, file or None) from a Hugging Face link or a bare "owner/repo", else None."""
    text = text.strip()
    m = HF_LINK.fullmatch(text)
    if m:
        return m.group(1), m.group(2) or "main", urllib.parse.unquote(m.group(3)) if m.group(3) else None
    if re.fullmatch(r"[\w.-]+/[\w.-]+", text):
        return text, "main", None
    return None


def hf_url(repo, rev, path):
    return f"https://huggingface.co/{repo}/resolve/{urllib.parse.quote(rev)}/{urllib.parse.quote(path)}"


def hf_open(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": "aicove", **(headers or {})})
    try:
        return urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            # It answers the same way for a mistyped link as for a repository that needs a login.
            raise RuntimeError("Hugging Face doesn't show this one: either the link is wrong, or it needs a "
                               "login (gated or private). Logins aren't supported here — download such a "
                               "model by hand and use \"On this disk\".")
        raise RuntimeError(f"Hugging Face answered with error {e.code}.")
    except (OSError, ValueError) as e:
        raise RuntimeError(f"Can't reach Hugging Face ({getattr(e, 'reason', e)}).")


def hf_lookup(repo, rev, wanted=None):
    """The GGUF files of a repository and the head of one of them (for the memory estimate):
    {"files": [(path, bytes)], "head": path of a temporary file or None}. wanted = the file
    the link pointed at, if any."""
    with hf_open(f"https://huggingface.co/api/models/{repo}/tree/{urllib.parse.quote(rev)}?recursive=true") as r:
        tree = json.load(r)
    files = [(e["path"], e.get("size") or 0) for e in tree
             if e.get("type") == "file" and e["path"].lower().endswith(".gguf")]
    head = None
    whole = [f for f in files if "mmproj" not in f[0].lower() and not HF_SPLIT.search(f[0])]
    if whole:
        # Every version of a model has the same layers, so the smallest file will do.
        path = wanted if wanted in dict(whole) else min(whole, key=lambda f: f[1])[0]
        try:
            with hf_open(hf_url(repo, rev, path), {"Range": "bytes=0-8388607"}) as r:
                data = r.read(8 << 20)
            with tempfile.NamedTemporaryFile(prefix="aicove-head-", suffix=".gguf", delete=False) as t:
                t.write(data)
                head = t.name
        except (RuntimeError, OSError):
            pass
    return {"files": files, "head": head}


class Lookup(QThread):
    """Runs one slow function next to the window; done(result, error text)."""
    done = pyqtSignal(object, str)

    def __init__(self, fn, parent):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(), "")
        except Exception as e:
            self.done.emit(None, str(e))


# --- download jobs -----------------------------------------------------------

def read_jobs():
    """The downloads that aren't finished: {job name: {"files": [...], "pid": pid or None}}."""
    jobs = {}
    try:
        names = sorted(os.listdir(JOB_DIR))
    except OSError:
        return jobs
    for name in names:
        if not name.endswith(".json"):
            continue
        base = os.path.join(JOB_DIR, name[:-len(".json")])
        try:
            with open(base + ".json", encoding="utf-8") as f:
                job = {"files": json.load(f)["files"], "pid": None, "base": base}
        except (OSError, ValueError, KeyError):
            continue
        try:
            with open(base + ".pid") as f:
                pid = int(f.read().strip())
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                if fetch.is_fetch(f.read()):
                    job["pid"] = pid
        except (OSError, ValueError):
            pass
        jobs[name[:-len(".json")]] = job
    return jobs


def job_progress(job):
    """(bytes here, bytes in total) of a download job."""
    have = total = 0
    for item in job["files"]:
        total += item.get("size") or 0
        for path in (item["dest"], item["dest"] + ".part"):
            if os.path.exists(path):
                have += os.path.getsize(path)
                break
    return have, total


def start_job(base):
    """Run "aicove fetch" for a job on its own, so it outlives this window."""
    with open(base + ".log", "a") as log:
        job = subprocess.Popen(cove_cmd("fetch", base + ".json"), stdin=subprocess.DEVNULL, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
    # The fetcher writes this too; doing it here means the window never sees a gap.
    with open(base + ".pid", "w") as f:
        f.write(str(job.pid))


def stop_job(job):
    if job["pid"]:
        try:
            os.killpg(job["pid"], signal.SIGTERM)
        except OSError:
            pass


class Health(threading.Thread):
    """Asks llama-server every 2 s whether the model is ready; state is "ready", "loading" or None."""

    def __init__(self):
        super().__init__(daemon=True)
        self.state = None

    def run(self):
        while True:
            self.state = server.health()
            time.sleep(2)


class Follow(QObject):
    """Asks the other machines we have launchers of what they have now, so their names,
    abilities, new and deleted launchers follow here: when the window opens and on "Update from
    other machines" (Start asks too). A machine that is off is skipped. A plain thread that ends
    with the window (like Health); news reaches the window as signals."""
    news = pyqtSignal(str, list, list, bool)
    done = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.asking = False

    def start(self):
        if not self.asking:
            self.asking = True
            threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        off = []
        for host in launcher_files.hosts():
            before = launcher_files.remote_info(host)
            try:
                info = server.ask(host)
                added, removed = launcher_files.follow(host, info)
            except OSError as e:
                off.append(str(e))
                continue
            if added or removed or launcher_files.remote_info(host) != before:
                self.news.emit(host, added, removed, True)
        self.asking = False
        self.done.emit(off)


class Syncer(QObject):
    """Keeps the AIcove folder and its stories in step with the other machines (sync.py): when
    the window opens, on "Update from other machines", and once what changed here has been left
    alone for a minute. A plain thread like Follow; the result reaches the window as a signal."""
    done = pyqtSignal(list, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.busy = False
        self.last = ""

    def start(self, force=True):
        if not self.busy:
            self.busy = True
            threading.Thread(target=self.run, args=(force,), daemon=True).start()

    def run(self, force):
        lines, changed = [], []
        try:
            now = sync.pending()
            # Unforced: only once the same changes have stood for a whole round.
            if force or (now and now == self.last):
                lines, changed = sync.sync()
                now = sync.pending()
            self.last = now
        except Exception as e:
            lines = [f"Sync: {e}"]
        self.busy = False
        self.done.emit(lines, changed)


class NoWheel:
    """Spin boxes and combos inside the scrolling form: the wheel scrolls the form, not the value."""

    def wheelEvent(self, event):
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class IntBox(NoWheel, QSpinBox):
    pass


class FloatBox(NoWheel, QDoubleSpinBox):
    def textFromValue(self, value):
        return fmt_float(value)


class Combo(NoWheel, QComboBox):
    pass


class OptRow:
    """Checkbox (option present or not) + value editor for one OPTIONS entry."""

    def __init__(self, spec, on_change, parent):
        self.spec = spec
        self.check = QCheckBox(spec["label"])
        self.check.setToolTip(spec["flag"])
        self.widget = None
        self.edit = None
        kind = spec["kind"]
        if kind == "int":
            self.edit = IntBox()
            self.edit.setRange(spec["min"], spec["max"])
            self.edit.setSingleStep(spec.get("step", 1))
            self.edit.setValue(spec["default"])
            self.edit.valueChanged.connect(on_change)
        elif kind == "float":
            self.edit = FloatBox()
            self.edit.setLocale(QLocale(QLocale.Language.C))
            self.edit.setDecimals(3)
            self.edit.setRange(spec["min"], spec["max"])
            self.edit.setSingleStep(spec["step"])
            self.edit.setValue(spec["default"])
            self.edit.valueChanged.connect(on_change)
        elif kind == "combo":
            self.edit = Combo()
            self.edit.setEditable(bool(spec.get("editable")))
            self.edit.addItems(spec["items"])
            self.edit.setCurrentText(spec["default"])
            self.edit.currentTextChanged.connect(on_change)
        elif kind in ("text", "file"):
            self.edit = QLineEdit(spec["default"] or "")
            self.edit.textChanged.connect(on_change)
        if kind == "file":
            self.widget = QWidget()
            box = QHBoxLayout(self.widget)
            box.setContentsMargins(0, 0, 0, 0)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda: self.browse(parent))
            box.addWidget(self.edit)
            box.addWidget(browse)
        elif self.edit is not None:
            self.widget = self.edit
        if self.edit is not None:
            self.edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if self.widget is not None:
            self.widget.setEnabled(False)
            self.check.toggled.connect(self.widget.setEnabled)
        self.check.toggled.connect(on_change)

    def browse(self, parent):
        start = self.edit.text() or launcher_files.models_dir()
        path, _ = QFileDialog.getOpenFileName(parent, self.spec["label"], start, "GGUF (*.gguf);;All files (*)")
        if path:
            self.edit.setText(path)

    def get(self):
        if not self.check.isChecked():
            return None
        kind = self.spec["kind"]
        if kind == "flag":
            return True
        if kind == "int":
            return str(self.edit.value())
        if kind == "float":
            return fmt_float(self.edit.value())
        if kind == "combo":
            return self.edit.currentText().strip()
        return self.edit.text()

    def set(self, value):
        self.check.setChecked(value is not None)
        kind = self.spec["kind"]
        if kind == "flag":
            return
        if value is None:
            value = self.spec["default"]
        if kind == "int":
            self.edit.setValue(int(value))
        elif kind == "float":
            self.edit.setValue(float(value))
        elif kind == "combo":
            self.edit.setCurrentText(str(value))
        else:
            self.edit.setText(value or "")


class AddModel(QDialog):
    """Add a model from a Hugging Face link or from a file on this machine. After accept(),
    plan = {"command", "desc", "model", "friendly", "args", "persona", "files"}; files is what
    the fetcher still has to bring into place (empty when the file is used where it is)."""

    NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"

    def __init__(self, parent, launchers, budget):
        super().__init__(parent)
        self.setWindowTitle("Add model")
        self.budget = budget
        self.repo = None          # (repo, revision) of the list that is showing
        self.head = None          # temporary file with the first bytes of one of its files
        self.wanted = None        # the file a pasted link pointed at
        self.disk_path = None
        self.auto = {}            # field -> what we filled in, so typing is never overwritten
        self.thread = None
        self.plan = None

        self.link = QLineEdit()
        self.link.setPlaceholderText("https://huggingface.co/owner/model-GGUF   (or the link of one .gguf file)")
        self.link.returnPressed.connect(self.look_up)
        self.lookup_btn = QPushButton("Look up")
        self.lookup_btn.setAutoDefault(False)
        self.lookup_btn.clicked.connect(self.look_up)
        top = QHBoxLayout()
        top.addWidget(self.link, 1)
        top.addWidget(self.lookup_btn)
        self.hf_note = QLabel("Paste the link of a model page, press Look up, pick a version.")
        self.hf_note.setWordWrap(True)
        self.hf_table = self.table(["File", "Size", f"With {NEW_CONTEXT // 1024}K context"])
        self.eyes = Combo()
        self.eyes.currentIndexChanged.connect(self.fill_fit)
        self.eyes_row = QWidget()
        erow = QHBoxLayout(self.eyes_row)
        erow.setContentsMargins(0, 0, 0, 0)
        erow.addWidget(QLabel("Eyes file (mmproj), so it can see images:"))
        erow.addWidget(self.eyes, 1)
        self.eyes_row.hide()
        hf = QWidget()
        hbox = QVBoxLayout(hf)
        hbox.addLayout(top)
        hbox.addWidget(self.hf_note)
        hbox.addWidget(self.hf_table, 1)
        hbox.addWidget(self.eyes_row)

        self.disk_table = self.table(["File", "Size", "Launcher"])
        for path, size, used in scan_models(launchers):
            self.add_row(self.disk_table, path, [path, f"{size / 1e9:.1f} GB", ", ".join(used) or "— none yet —"])
        browse = QPushButton("Somewhere else…")
        browse.setAutoDefault(False)
        browse.clicked.connect(self.browse)
        self.disk_note = QLabel()
        self.disk_note.setWordWrap(True)
        self.copy = QCheckBox(f"Copy it into {launcher_files.models_dir()}/ (the original stays where it is)")
        self.copy.setChecked(True)
        self.copy.toggled.connect(self.picked)
        drow = QHBoxLayout()
        drow.addWidget(self.disk_note, 1)
        drow.addWidget(browse)
        disk = QWidget()
        dbox = QVBoxLayout(disk)
        dbox.addWidget(QLabel("Model files found in " + " and ".join(S.all_scan_dirs()) + ":"))
        dbox.addWidget(self.disk_table, 1)
        dbox.addLayout(drow)
        dbox.addWidget(self.copy)

        self.pages = QTabWidget()
        self.pages.addTab(hf, "From Hugging Face")
        self.pages.addTab(disk, "On this disk")
        self.pages.currentChanged.connect(self.picked)

        self.file_name = QLineEdit()
        self.file_name.setPlaceholderText("short name for the file, e.g. qwen3.8-27b-heretic")
        self.command = QLineEdit()
        self.command.setPlaceholderText("its name in the launcher list, e.g. qwen38-heretic")
        self.friendly = QLineEdit()
        self.desc = QLineEdit()
        self.space = QLabel()
        self.space.setWordWrap(True)
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        for r, (label, widget, tail) in enumerate((
                ("File name", self.file_name, ".gguf"), ("Launcher name", self.command, ""),
                ("Display name", self.friendly, ""), ("Description", self.desc, ""))):
            grid.addWidget(QLabel(label), r, 0)
            grid.addWidget(widget, r, 1)
            grid.addWidget(QLabel(tail), r, 2)
        grid.addWidget(self.space, 4, 1, 1, 2)

        self.ok_btn = QPushButton("Add")
        self.ok_btn.clicked.connect(self.accept)
        cancel = QPushButton("Cancel")
        cancel.setAutoDefault(False)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.ok_btn)
        buttons.addWidget(cancel)

        box = QVBoxLayout(self)
        box.addWidget(self.pages, 1)
        box.addLayout(grid)
        box.addLayout(buttons)
        self.resize(900, 640)
        self.hf_table.itemSelectionChanged.connect(self.picked)
        self.disk_table.itemSelectionChanged.connect(self.pick_disk)
        self.picked()

    def table(self, headers):
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.verticalHeader().hide()
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, len(headers)):
            t.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        return t

    def add_row(self, table, data, cells, enabled=True):
        r = table.rowCount()
        table.insertRow(r)
        for c, text in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, data)
            if not enabled:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            table.setItem(r, c, item)
        return r

    def selected(self, table):
        items = table.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def set_auto(self, field, text):
        if field.text() == self.auto.get(field, ""):
            field.setText(text)
            field.setCursorPosition(0)
            self.auto[field] = text

    # --- Hugging Face page ---------------------------------------------------

    def look_up(self):
        if self.thread is not None:
            return
        link = parse_hf(self.link.text())
        if link is None:
            self.hf_note.setText("That isn't a Hugging Face link. It should look like "
                                 "https://huggingface.co/owner/model-GGUF")
            return
        repo, rev, self.wanted = link
        self.hf_note.setText(f"Asking Hugging Face about {repo}…")
        self.lookup_btn.setEnabled(False)
        self.thread = Lookup(lambda: hf_lookup(repo, rev, self.wanted), self.parent())
        self.thread.done.connect(lambda result, error: self.looked_up(repo, rev, result, error))
        self.thread.start()

    def looked_up(self, repo, rev, result, error):
        self.thread = None
        self.lookup_btn.setEnabled(True)
        self.drop_head()
        self.hf_table.setRowCount(0)
        self.eyes.clear()
        self.repo = None
        if error:
            self.hf_note.setText(error)
            return
        self.repo, self.head = (repo, rev), result["head"]
        models = [f for f in result["files"] if "mmproj" not in f[0].lower()]
        eyes = [f for f in result["files"] if "mmproj" in f[0].lower()]
        if not models:
            self.hf_note.setText(f"{repo} has no .gguf files. llama-server needs the GGUF version of a "
                                 "model — look for a repository with GGUF in its name.")
            return
        self.eyes.addItem("— none —", None)
        for path, size in eyes:
            self.eyes.addItem(f"{path}  ({size / 1e9:.2f} GB)", (path, size))
        self.eyes_row.setVisible(bool(eyes))
        if eyes:
            # f16 is what the existing vision launchers use.
            best = next((i for i, f in enumerate(eyes) if "f16" in f[0].lower()), 0)
            self.eyes.setCurrentIndex(best + 1)
        for path, size in sorted(models, key=lambda f: f[1]):
            split = bool(HF_SPLIT.search(path))
            self.add_row(self.hf_table, (path, size), [path, f"{size / 1e9:.1f} GB", ""], enabled=not split)
        self.fill_fit()
        meta = gguf_meta(self.head) if self.head else {}
        if not meta.get("general.architecture"):
            self.hf_note.setText(f"{repo}: couldn't read the model's description, so no memory estimate.")
        else:
            self.hf_note.setText(f"{repo}: bigger file = closer to the original model, and slower. "
                                 "Pick one.")
        target = None
        for r in range(self.hf_table.rowCount()):
            path, size = self.hf_table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            if not self.hf_table.item(r, 0).flags() & Qt.ItemFlag.ItemIsEnabled:
                continue
            if path == self.wanted:
                target = r
                break
            if self.suggested == path:
                target = r
        if target is not None:
            self.hf_table.selectRow(target)

    def context(self, model):
        """(context size for the new launcher, bytes it takes) for a GGUF file or the head of one."""
        meta = gguf_meta(model) if model else {}
        most = meta.get(f"{meta.get('general.architecture')}.context_length")
        ctx = min(NEW_CONTEXT, most) if isinstance(most, int) and most > 0 else NEW_CONTEXT
        return ctx, (context_bytes(model, ctx, "f16", "f16") if model else None)

    def fill_fit(self, *_):
        """The "does it fit" column, and which row we'd take: the biggest that fits with room to spare."""
        eyes = (self.eyes.currentData() or (None, 0))[1]
        kv = self.context(self.head)[1]
        self.suggested, best = None, 0
        rows = []
        for r in range(self.hf_table.rowCount()):
            path, size = self.hf_table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            if HF_SPLIT.search(path):
                rows.append((r, path, "in several parts — not supported", ""))
                continue
            text, colour = fit_text(size, eyes, kv, self.budget)
            rows.append((r, path, text, colour))
            if kv is not None and self.budget and best < size <= 22e9 \
                    and size + eyes + WORK_BYTES + kv <= 0.85 * self.budget:
                self.suggested, best = path, size
        for r, path, text, colour in rows:
            item = self.hf_table.item(r, 2)
            item.setText(text + ("   ← I'd take this one" if path == self.suggested else ""))
            if colour:
                item.setForeground(QBrush(QColor(colour)))

    def drop_head(self):
        if self.head:
            try:
                os.remove(self.head)
            except OSError:
                pass
        self.head = None

    # --- disk page -----------------------------------------------------------

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Model file", os.path.expanduser("~"),
                                              "GGUF (*.gguf);;All files (*)")
        if path:
            self.disk_table.clearSelection()
            self.disk_path = path
            self.picked()

    def pick_disk(self):
        path = self.selected(self.disk_table)
        if path:
            self.disk_path = path
            self.picked()

    # --- what is chosen ------------------------------------------------------

    def on_disk(self):
        return self.pages.currentIndex() == 1

    def in_place(self):
        """The file is used where it is: nothing to fetch, no file name to ask for."""
        if not self.on_disk() or not self.disk_path:
            return False
        inside = os.path.dirname(os.path.realpath(self.disk_path)) == os.path.realpath(launcher_files.models_dir())
        return inside or not self.copy.isChecked()

    def picked(self, *_):
        """Something was chosen: suggest the texts and say what it will take."""
        disk, need = self.on_disk(), 0
        if disk:
            path = self.disk_path
            inside = bool(path) and os.path.dirname(os.path.realpath(path)) == os.path.realpath(launcher_files.models_dir())
            self.copy.setEnabled(bool(path) and not inside)
            self.disk_note.setText(path or "Pick one from the list, or a file from somewhere else.")
            if path and os.path.isfile(path):
                size = os.path.getsize(path)
                eyes = eyes_next_to(path)
                ctx, kv = self.context(path)
                fit = fit_text(size, os.path.getsize(eyes) if eyes else 0, kv, self.budget)[0]
                self.disk_note.setText(f"{path} — {size / 1e9:.1f} GB, with {ctx // 1024}K context {fit}"
                                       + (f". Eyes file found: {os.path.basename(eyes)}" if eyes else ""))
                name = gguf_meta(path).get("general.name")
                friendly = name if isinstance(name, str) and name else os.path.basename(path)[:-len(".gguf")]
                self.set_auto(self.friendly, friendly)
                self.set_auto(self.desc, f"{friendly} — {ctx // 1024}K context (~{size / 1e9:.1f}GB)")
                if not self.in_place():
                    need = size + (os.path.getsize(eyes) if eyes else 0)
        else:
            pick = self.selected(self.hf_table)
            if pick and self.repo:
                path, size = pick
                owner, repo = self.repo[0].split("/")
                name = gguf_meta(self.head).get("general.name") if self.head else None
                friendly = name if isinstance(name, str) and name else re.sub(r"(?i)[-_.]?gguf$", "", repo)
                quant = QUANT.search(os.path.basename(path)[:-len(".gguf")])
                ctx = self.context(self.head)[0]
                self.set_auto(self.friendly, friendly)
                self.set_auto(self.desc, f"{friendly} ({owner}) — " + (f"{quant.group(1).upper()}, " if quant else "")
                              + f"{ctx // 1024}K context (~{size / 1e9:.1f}GB)")
                need = size + (self.eyes.currentData() or (None, 0))[1]
        self.file_name.setEnabled(not self.in_place())
        self.ok_btn.setText("Add" if self.in_place() or not need else
                            ("Copy && add" if disk else "Download && add"))
        try:
            free = shutil.disk_usage(launcher_files.models_dir()).free
        except OSError:
            free = None
        self.need, self.free = need, free
        text = f"Free on {launcher_files.models_dir()}: {free / 1e9:.0f} GB." if free is not None else ""
        if need:
            text = f"This takes {need / 1e9:.1f} GB. " + text
            if free is not None and need + 2e9 > free:
                text += " Not enough room."
        self.space.setText(text)

    def fail(self, text):
        QMessageBox.warning(self, "Add model", text)

    def accept(self):
        disk = self.on_disk()
        pick = self.disk_path if disk else self.selected(self.hf_table)
        if not pick or (disk and not os.path.isfile(pick)):
            return self.fail("Pick a model file first.")
        command = self.command.text().strip()
        if not re.fullmatch(self.NAME, command):
            return self.fail("Launcher name: use only letters, digits, dot, dash and underscore.")
        if os.path.exists(launcher_files.path_of(command)):
            return self.fail(f"There is already a launcher called {command}.")
        if os.path.exists(os.path.join(JOB_DIR, command + ".json")):
            return self.fail(f"There is still an unfinished download called {command}.")
        friendly = self.friendly.text().strip()
        if not friendly:
            return self.fail("Display name is empty.")
        files, eyes = [], None
        if self.in_place():
            model = pick
            eyes = eyes_next_to(pick)
            ctx = self.context(pick)[0]
        else:
            name = self.file_name.text().strip()
            if name.endswith(".gguf"):
                name = name[:-len(".gguf")]
            if not re.fullmatch(self.NAME, name):
                return self.fail("File name: use only letters, digits, dot, dash and underscore.")
            model = os.path.join(launcher_files.models_dir(), name + ".gguf")
            if disk:
                files.append({"src": pick, "dest": model, "size": os.path.getsize(pick)})
                near = eyes_next_to(pick)
                if near:
                    eyes = os.path.join(launcher_files.models_dir(), name + "-mmproj.gguf")
                    files.append({"src": near, "dest": eyes, "size": os.path.getsize(near)})
                ctx = self.context(pick)[0]
            else:
                repo, rev = self.repo
                files.append({"url": hf_url(repo, rev, pick[0]), "dest": model, "size": pick[1]})
                if self.eyes.currentData():
                    path, size = self.eyes.currentData()
                    eyes = os.path.join(launcher_files.models_dir(), name + "-mmproj.gguf")
                    files.append({"url": hf_url(repo, rev, path), "dest": eyes, "size": size})
                ctx = self.context(self.head)[0]
            for item in files:
                if os.path.exists(item["dest"]) or os.path.exists(item["dest"] + ".part"):
                    return self.fail(f"{item['dest']} already exists (or a half-finished download of it). "
                                     "Pick another file name.")
            if self.free is not None and self.need + 2e9 > self.free:
                return self.fail(f"Not enough room on {launcher_files.models_dir()}: this takes {self.need / 1e9:.1f} GB, "
                                 f"{self.free / 1e9:.0f} GB are free.")
        args = (["--mmproj", eyes] if eyes else []) + ["-c", str(ctx)] + NEW_ARGS
        self.plan = {"command": command, "desc": self.desc.text().strip().replace("\n", " "), "model": model,
                     "friendly": friendly, "args": args, "persona": "",
                     "files": files}
        super().accept()

    def done(self, result):
        if self.thread is not None:
            # Still asking Hugging Face: let it finish on its own and throw the answer away.
            self.thread.done.disconnect()
            self.thread.done.connect(lambda res, _: res and res["head"] and os.remove(res["head"]))
        self.drop_head()
        super().done(result)


class ToolDialog(QDialog):
    """Add or edit one tool (a file in ~/.config/aicove/tools/). saved is its file name afterwards,
    deleted is set when it was removed."""

    def __init__(self, parent, tool=None):
        super().__init__(parent)
        self.tool, self.saved, self.deleted = tool, None, False
        self.setWindowTitle(f"Tool: {tool.title}" if tool else "Add tool")
        self.resize(720, 0)
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)

        def row(label, widget, hint=None):
            r = grid.rowCount()
            grid.addWidget(QLabel(label), r, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(widget, r, 1)
            if hint:
                note = QLabel(hint)
                note.setWordWrap(True)
                note.setEnabled(False)
                grid.addWidget(note, r + 1, 1)
            return widget

        v = tool.values() if tool else {}
        self.title = row("Name", QLineEdit(v.get("title", "")))
        self.short = row("Short name", QLineEdit(tool.name if tool else ""),
                         "The file name, and the name for a terminal: aicove tools start <short name>. "
                         "Letters, digits, . _ -")
        self.short.setEnabled(tool is None)
        self.desc = row("Description", QLineEdit(v.get("desc", "")))
        self.start = row("Start command", QLineEdit(v.get("start", "")), "A shell command.")
        self.stop = row("Stop command", QLineEdit(v.get("stop", "")),
                        "Empty = the start command is the program itself: AIcove keeps it running "
                        "and ends it on Stop.")
        self.folder = row("Folder", QLineEdit(v.get("folder", "")), "Optional: where the commands run.")
        self.address = row("Address", QLineEdit(v.get("address", "")),
                           "Optional, e.g. http://localhost:7871 — for the Open button, and running = "
                           "something answers there.")
        self.terminal = row("", QCheckBox("Start and stop in a terminal window (they ask for a password "
                                          "or a yes/no)"))
        self.terminal.setChecked(bool(v.get("terminal")))
        self.with_all = row("", QCheckBox("\"Stop everything\" stops it too"))
        self.with_all.setChecked(bool(v.get("with_all")))
        self.not_with = row("Not together with", QLineEdit(", ".join(v.get("not_with", []))),
                            "Optional: short names of tools that are stopped before this one starts.")
        self.not_with_model = row("", QCheckBox("Never on together with a loaded model: starting one "
                                                "stops the other (for tools that need the GPU memory)"))
        self.not_with_model.setChecked(bool(v.get("not_with_model")))
        self.buttons = row("Extra buttons", QPlainTextEdit(
            "\n".join(f"{label} | {command}" for label, command in v.get("buttons", []))),
            "Optional, one per line: Label | shell command. They run in a terminal window.")
        self.buttons.setFixedHeight(90)
        self.buttons.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))

        save = QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(self.save)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        bar = QHBoxLayout()
        if tool is not None:
            delete = QPushButton("Delete")
            delete.clicked.connect(self.delete)
            bar.addWidget(delete)
        bar.addStretch(1)
        bar.addWidget(cancel)
        bar.addWidget(save)
        box = QVBoxLayout(self)
        box.addLayout(grid)
        box.addLayout(bar)

    def fail(self, text):
        QMessageBox.warning(self, "Tool", text)

    def save(self):
        name = self.short.text().strip()
        values = {
            "title": self.title.text().strip() or name, "desc": self.desc.text().strip(),
            "start": self.start.text().strip(), "stop": self.stop.text().strip(),
            "folder": self.folder.text().strip(), "address": self.address.text().strip(),
            "terminal": self.terminal.isChecked(), "with_all": self.with_all.isChecked(),
            "not_with": [n.strip() for n in self.not_with.text().split(",") if n.strip()],
            "not_with_model": self.not_with_model.isChecked(), "buttons": [],
        }
        for line in self.buttons.toPlainText().split("\n"):
            label, bar, command = line.partition("|")
            if line.strip() and not (bar and label.strip() and command.strip()):
                return self.fail(f"Extra buttons: a line must be \"Label | command\", this one isn't:\n{line}")
            if line.strip():
                values["buttons"].append((label.strip(), command.strip()))
        if not launcher_files.NAME.fullmatch(name):
            return self.fail("The short name can have letters, digits, . _ - and must start with a letter or digit.")
        if not (values["start"] or values["address"] or values["buttons"]):
            return self.fail("A tool needs at least a start command, an address or an extra button.")
        if values["stop"] and not values["start"]:
            return self.fail("A stop command needs a start command.")
        if values["folder"] and not os.path.isdir(os.path.expanduser(values["folder"])):
            return self.fail(f"The folder {values['folder']} doesn't exist.")
        try:
            tools.write(name, values, new=self.tool is None)
        except FileExistsError:
            return self.fail(f"There is already a tool with the short name {name}.")
        except OSError as e:
            return self.fail(str(e))
        self.saved = name
        self.accept()

    def delete(self):
        if QMessageBox.question(self, "Delete tool", f"Remove {self.tool.title} from AIcove?\n"
                                "Only the entry here goes; the program itself is not touched.") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            dest = tools.retire(self.tool.name)
        except OSError as e:
            return self.fail(str(e))
        self.deleted, self.saved = True, dest
        self.accept()


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.launcher = None
        self.baseline = None
        self.loading = False
        self.switching = False
        self.rows = {}

        self.list = QListWidget()
        self.grouped = False
        self.list.currentItemChanged.connect(self.on_select)
        add_btn = QPushButton("Add model…")
        add_btn.setToolTip("From a Hugging Face link, or a model file that is already on this machine")
        add_btn.clicked.connect(self.add_model)
        new_btn = QPushButton("New from this one…")
        new_btn.clicked.connect(self.new_launcher)
        persona_btn = QPushButton("New persona on this one…")
        persona_btn.setToolTip("A new persona with its own copy of this launcher's model and settings")
        persona_btn.clicked.connect(self.new_persona_on_launcher)
        remote_btn = QPushButton("From another machine…")
        remote_btn.setToolTip("A launcher of another machine with AIcove (reached with ssh): its model runs "
                              "there, the chat here")
        remote_btn.clicked.connect(self.add_remote)
        self.update_btn = QPushButton("Update from other machines")
        self.update_btn.setToolTip("Ask the other machines what launchers they have now (AIcove also asks "
                                   "when the window opens and when one of theirs is started)")
        self.update_btn.clicked.connect(self.update_remote)
        rename_btn = QPushButton("Rename…")
        rename_btn.clicked.connect(self.rename_launcher)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self.delete_launcher)
        lbuttons = QHBoxLayout()
        lbuttons.addWidget(rename_btn)
        lbuttons.addWidget(delete_btn)
        left = QWidget()
        lbox = QVBoxLayout(left)
        lbox.setContentsMargins(0, 0, 0, 0)
        lbox.addWidget(QLabel("Launchers"))
        lbox.addWidget(self.list)
        lbox.addWidget(add_btn)
        lbox.addWidget(new_btn)
        lbox.addWidget(persona_btn)
        lbox.addWidget(remote_btn)
        lbox.addWidget(self.update_btn)
        lbox.addLayout(lbuttons)

        form = QWidget()
        fbox = QVBoxLayout(form)
        fbox.addWidget(self.model_group())
        groups = {}
        for spec in OPTIONS:
            if spec["group"] not in groups:
                box = QGroupBox(spec["group"])
                grid = QGridLayout(box)
                grid.setColumnMinimumWidth(0, 200)
                grid.setColumnMinimumWidth(1, 230)
                grid.setColumnStretch(2, 1)
                groups[spec["group"]] = grid
                fbox.addWidget(box)
                if spec["group"] == STYLE_GROUP:
                    presets = QHBoxLayout()
                    presets.addWidget(QLabel("Set all at once:"))
                    for name in PRESETS:
                        b = QPushButton(name)
                        b.clicked.connect(lambda _, n=name: self.apply_preset(n))
                        presets.addWidget(b)
                    presets.addStretch(1)
                    grid.addLayout(presets, 0, 0, 1, 3)
            grid = groups[spec["group"]]
            row = OptRow(spec, self.changed, self)
            self.rows[spec["flag"]] = row
            r = grid.rowCount()
            grid.addWidget(row.check, r, 0)
            if row.widget is not None:
                grid.addWidget(row.widget, r, 1)
            hint = QLabel(spec["hint"])
            hint.setWordWrap(True)
            hint.setEnabled(False)
            grid.addWidget(hint, r, 2)
        extra_box = QGroupBox("Extra options")
        ebox = QVBoxLayout(extra_box)
        self.extra = QLineEdit()
        self.extra.setPlaceholderText("anything else for llama-server, written as on the command line")
        self.extra.textChanged.connect(self.changed)
        ebox.addWidget(self.extra)
        note = QLabel("Full list: llama-server --help")
        note.setEnabled(False)
        ebox.addWidget(note)
        fbox.addWidget(extra_box)
        fbox.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form)
        self.form_scroll = scroll

        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono)
        self.preview.setFixedHeight(84)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(mono)
        self.output.setFixedHeight(110)
        self.output.setPlaceholderText("Output of Start / Stop shows up here.")

        self.revert_btn = QPushButton("Revert")
        self.revert_btn.clicked.connect(self.revert)
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.save)
        self.start_btn = QPushButton("Save && Start")
        self.start_btn.clicked.connect(self.save_and_start)
        self.stop_btn = QPushButton("Stop llama-server")
        self.stop_btn.clicked.connect(self.stop_server)
        self.status = QLabel()
        buttons = QHBoxLayout()
        buttons.addWidget(self.status, 1)
        for b in (self.revert_btn, self.save_btn, self.start_btn, self.stop_btn):
            buttons.addWidget(b)

        right = QWidget()
        rbox = QVBoxLayout(right)
        rbox.setContentsMargins(0, 0, 0, 0)
        rbox.addWidget(scroll, 1)
        rbox.addWidget(QLabel("What will run:"))
        rbox.addWidget(self.preview)
        rbox.addLayout(buttons)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([230, 900])
        self.tabs = QTabWidget()
        self.launcher_page = split
        self.persona_page = self.persona_tab()
        self.tabs.addTab(self.persona_page, "Personas")
        self.tabs.addTab(split, "Launchers")
        self.tabs.addTab(self.tools_tab(), "Tools")
        self.pw_tab = PasswordsTab(self)
        self.tabs.addTab(self.pw_tab, "Passwords")
        self.tabs.currentChanged.connect(lambda i: self.tabs.widget(i) is self.pw_tab and self.pw_tab.refresh())
        self.settings_page = self.settings_tab()
        self.tabs.addTab(self.settings_page, "Settings")
        self.docs_tab = DocsTab()
        self.tabs.addTab(self.docs_tab, "Documentation")
        self.settings_changed()
        self.jobs = set()
        self.tools_label = QLabel()
        # A long list of running tools is cut at the edge (the tooltip has it all) instead of
        # making the window wider than the screen.
        self.tools_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.tools_label.setMinimumWidth(1)
        self.keyboard_buttons = {}
        self.keyboard_pressed = None
        self.keyboard_job = None
        self.services = QHBoxLayout()
        self.build_services()
        self.health = Health()
        self.health.start()
        self.follow = Follow(self)
        self.follow.news.connect(self.follow_done)
        self.follow.done.connect(self.follow_finished)
        self.syncer = Syncer(self)
        self.syncer.done.connect(self.synced)

        central = QWidget()
        cbox = QVBoxLayout(central)
        cbox.addLayout(self.services)
        cbox.addWidget(self.tabs, 1)
        # One line per unfinished download, only there while there are any.
        self.dl_rows = QVBoxLayout()
        self.dl_jobs, self.dl_shape, self.dl_labels, self.dl_rate = {}, None, {}, {}
        cbox.addLayout(self.dl_rows)
        cbox.addWidget(self.output)
        self.setCentralWidget(central)
        self.resize(1180, 860)

        self.reload_personas()

        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.read_output)
        self.proc.finished.connect(self.proc_done)
        self.proc.errorOccurred.connect(self.proc_done)

        self.reload_list()
        self.follow.start()
        self.syncer.start()
        self.sync_timer = QTimer(self)
        self.sync_timer.timeout.connect(lambda: self.syncer.start(force=False))
        self.sync_timer.start(60000)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_status)
        self.timer.start(3000)
        # The chat first: it is what the window is mostly for.
        self.chat_tab = ChatTab(self)
        self.tabs.insertTab(0, self.chat_tab, "Chat")
        self.agents_tab = AgentsTab(self)
        self.tabs.insertTab(1, self.agents_tab, "Agents")
        self.tabs.currentChanged.connect(lambda i: self.tabs.widget(i) is self.agents_tab
                                         and not self.agents_tab.dirty() and self.agents_tab.reload())
        self.writing_tab = WritingTab(self)
        self.tabs.insertTab(2, self.writing_tab, "Writing")
        self.tabs.currentChanged.connect(lambda i: self.tabs.widget(i) is not self.writing_tab
                                         and self.writing_tab.leave())
        self.tabs.setCurrentIndex(0)
        self.tabs.currentChanged.connect(lambda i: self.tabs.widget(i) is self.chat_tab and self.chat_tab.reload())
        if not S.exists:
            # First start on this machine: show what was found before anything else.
            self.tabs.setCurrentWidget(self.settings_page)

    def model_group(self):
        box = QGroupBox("Model")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        self.desc = QLineEdit()
        self.desc.textChanged.connect(self.changed)
        self.model = Combo()
        self.model.setEditable(True)
        self.model.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.fill_models()
        self.model.currentTextChanged.connect(self.changed)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_model)
        self.model_info = QLabel()
        self.friendly = QLineEdit()
        self.friendly.textChanged.connect(self.changed)
        self.fixed = QLabel()
        self.fixed.setEnabled(False)
        self.show_fixed()
        self.mem_info = QLabel()
        self.mem_info.setWordWrap(True)
        self.budget = settings.gpu_budget()
        grid.addWidget(QLabel("Description"), 0, 0)
        grid.addWidget(self.desc, 0, 1, 1, 3)
        grid.addWidget(QLabel("Model file"), 1, 0)
        grid.addWidget(self.model, 1, 1)
        grid.addWidget(browse, 1, 2)
        grid.addWidget(self.model_info, 1, 3)
        grid.addWidget(QLabel("Display name"), 2, 0)
        grid.addWidget(self.friendly, 2, 1, 1, 3)
        grid.addWidget(QLabel("Memory"), 4, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.mem_info, 4, 1, 1, 3)
        grid.addWidget(self.fixed, 5, 1, 1, 3)
        return box

    def show_fixed(self):
        self.fixed.setText(f"From the Settings tab: port {S.llama_port}, reachable from "
                           f"{'this machine only' if S.host.startswith('127.') else 'the network'}"
                           f"; on the GPU when not set above: {S.gpu_layers}")

    def fill_models(self):
        """The model files in launcher_files.models_dir() as choices; what is typed in the field stays."""
        text = self.model.currentText()
        try:
            ggufs = sorted(f for f in os.listdir(launcher_files.models_dir()) if f.endswith(".gguf") and "mmproj" not in f)
        except OSError:
            ggufs = []
        was_loading, self.loading = self.loading, True
        self.model.clear()
        self.model.addItems([os.path.join(launcher_files.models_dir(), f) for f in ggufs])
        self.model.setCurrentText(text)
        self.loading = was_loading

    def browse_model(self):
        path, _ = QFileDialog.getOpenFileName(self, "Model file", launcher_files.models_dir(), "GGUF (*.gguf);;All files (*)")
        if path:
            self.model.setCurrentText(path)

    # --- launcher list -------------------------------------------------------

    def launcher_items(self):
        """(item, launcher) of the list, without the headings of other machines."""
        items = [self.list.item(i) for i in range(self.list.count())]
        return [(it, it.data(Qt.ItemDataRole.UserRole)) for it in items
                if it.data(Qt.ItemDataRole.UserRole) is not None]

    def list_text(self, l):
        """A launcher in the list: under its machine's heading (when there are other machines),
        by its name there."""
        if l.host:
            return "    " + l.label(l.remote_name, where=False)
        return ("    " if self.grouped else "") + l.label(l.name)

    def heading(self, text, tip):
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setForeground(QBrush(self.palette().color(self.palette().ColorRole.Text)))
        item.setToolTip(tip)
        self.list.addItem(item)

    def reload_list(self, select=None):
        self.switching = True
        self.list.clear()
        found = find_launchers()
        others = sorted({l.host for l in found if l.host})
        self.grouped = bool(others)
        # This machine's first, then each other machine's under its name.
        for host in [""] + others:
            if host:
                self.heading(f"🖥 {host}", f"Launchers of {host}: they start there, the chat stays here")
            elif others:
                self.heading("This machine", "Launchers whose model runs here")
            for l in sorted((l for l in found if l.host == host), key=lambda l: l.remote_name or l.name):
                item = QListWidgetItem(self.list_text(l))
                item.setData(Qt.ItemDataRole.UserRole, l)
                if l.error:
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                    item.setToolTip(l.error)
                else:
                    item.setToolTip(f"{l.name}: {l.desc}" if l.host else l.desc)
                self.list.addItem(item)
        self.switching = False
        target = None
        for item, l in self.launcher_items():
            if l.error:
                continue
            if target is None or l.name == select:
                target = item
            if l.name == select:
                break
        if target is not None:
            self.list.setCurrentItem(target)
        self.refresh_status()
        if not self.persona_dirty():
            self.load_persona(self.persona)

    def on_select(self, cur, prev):
        if self.switching or cur is None:
            return
        wanted = cur.data(Qt.ItemDataRole.UserRole)
        if prev is not None and self.launcher is not None and self.dirty():
            if not self.resolve_unsaved():
                self.switching = True
                self.list.setCurrentItem(prev)
                self.switching = False
                return
            # Saving fills the list again, which deletes cur and picks the saved one: pick the
            # wanted one in the new list.
            self.reload_list(select=wanted.name)
            return
        self.load(wanted)

    def load(self, launcher):
        self.loading = True
        self.launcher = launcher
        opts, extra = parse_args(launcher.args)
        self.desc.setText(launcher.desc)
        self.desc.setCursorPosition(0)
        self.model.setCurrentText(launcher.model)
        self.friendly.setText(launcher.friendly)
        for flag, row in self.rows.items():
            row.set(opts.get(flag))
        self.extra.setText(shlex.join(extra))
        # Another machine's launcher is set up there.
        self.form_scroll.setEnabled(not launcher.host)
        self.loading = False
        self.baseline = self.state()
        self.changed()

    # --- form state ----------------------------------------------------------

    def state(self):
        return (self.desc.text().strip(), self.model.currentText().strip(), self.friendly.text().strip(),
                tuple((f, r.get()) for f, r in self.rows.items()), self.extra.text().strip())

    def apply_preset(self, name):
        values = PRESETS[name]
        self.loading = True
        for spec in OPTIONS:
            if spec["group"] == STYLE_GROUP:
                self.rows[spec["flag"]].set(values.get(spec["flag"]))
        self.loading = False
        self.changed()

    def memory_text(self, model):
        """(text, colour) for the Memory line."""
        opts = {f: r.get() for f, r in self.rows.items()}
        size = os.path.getsize(model) if os.path.isfile(model) else None
        if size is None:
            return "", ""
        ctx_opt = opts["-c"]
        ctx = int(ctx_opt) if ctx_opt and ctx_opt.isdigit() else 0
        kv = context_bytes(model, ctx, opts["-ctk"] or "f16", opts["-ctv"] or "f16")
        if kv is None:
            return "Can't estimate — the model file doesn't describe itself.", ""
        eyes = opts["--mmproj"]
        eyes = os.path.getsize(eyes) if eyes and os.path.isfile(eyes) else 0
        work = WORK_BYTES
        in_ram = bool(opts["-nkvo"])
        # The experts of a mixture-of-experts model that the launcher keeps in normal RAM.
        experts = expert_bytes(model) if opts["--cpu-moe"] or opts["--n-cpu-moe"] else {}
        first = 999 if opts["--cpu-moe"] else int(opts["--n-cpu-moe"] or 0)
        moe = sum(b for layer, b in experts.items() if layer < first)
        # What goes on the GPU layer by layer, so it can be split between GPU and normal RAM.
        split = size - moe + (0 if in_ram else kv)
        need = split + eyes + work
        gb = lambda b: f"{b / 1e9:.1f}"
        parts = (f"model without its experts {gb(size - moe)}" if moe else f"model {gb(size)}") \
            + (f" + eyes {gb(eyes)}" if eyes else "") \
            + ("" if in_ram else f" + context {gb(kv)}") + f" + working space {gb(work)}"
        text = f"Needs about {gb(need)} GB of GPU memory ({parts})"
        if moe:
            text += f", plus {gb(moe)} GB of normal RAM for the experts"
        if in_ram:
            text += f", plus {gb(kv)} GB of normal RAM for the context"
        meta = gguf_meta(model)
        arch = meta.get("general.architecture")
        if not ctx:
            text += f". No context size set = the model's maximum ({meta.get(f'{arch}.context_length')} tokens)"
        if not self.budget:
            return text + ". How much the GPU has is not known — set it on the Settings tab.", ""
        text += f". The GPU can use about {gb(self.budget)} GB."
        layers = opts["-ngl"] or S.gpu_layers
        total = (meta.get(f"{arch}.block_count") or 0) + 1
        share = 1.0
        if layers.isdigit() and total > 1:
            share = min(int(layers) / total, 1.0)
        elif layers == "auto" and need > self.budget:
            # llama-server keeps about 1 GB free and puts on the GPU as many layers as fit.
            share = max(0.0, min(1.0, (self.budget - 1e9 - eyes - work) / split))
        if share < 1.0:
            on_gpu, normal = share * split + eyes + work, (1 - share) * split
            what = f"{layers} of {total} layers go" if layers.isdigit() else f"About {share:.0%} of it goes"
            text += f" {what} on the GPU ({gb(on_gpu)} GB), the rest ({gb(normal)} GB) runs from normal RAM — slower."
            ram = settings.ram_total()
            if on_gpu > self.budget:
                return text + " Even that is more than the GPU has — use fewer layers.", "#c0392b"
            if ram and normal + moe > 0.9 * ram:
                return text + " That is more normal RAM than this machine has.", "#c0392b"
            return text, "#b9770e"
        colour = ""
        if need > self.budget and not ctx and not in_ram:
            # llama-server's --fit (on by default) only adjusts what the launcher leaves unset.
            text, colour = text + (" Too much, so llama-server will quietly shrink the context until it "
                                   "fits — set a context size to know what you get."), "#b9770e"
        elif need > self.budget:
            text, colour = text + " Probably won't fit — lower the context or compress it.", "#c0392b"
        elif need > 0.85 * self.budget:
            text, colour = text + " Tight.", "#b9770e"
        return text, colour

    def dirty(self):
        return self.launcher is not None and self.state() != self.baseline

    def current_args(self):
        """Arguments as the form has them; raises ValueError with a message if something is off."""
        opts = {f: r.get() for f, r in self.rows.items()}
        for spec in OPTIONS:
            val = opts[spec["flag"]]
            if val is None or val is True:
                continue
            if val == "":
                raise ValueError(f"\"{spec['label']}\" is ticked but empty.")
            if spec.get("numeric") and not re.fullmatch(r"\d+", val):
                raise ValueError(f"\"{spec['label']}\" must be a number.")
            if spec.get("pattern") and not re.fullmatch(spec["pattern"], val):
                raise ValueError(f"\"{spec['label']}\" must be all, auto or a number.")
        try:
            extra = shlex.split(self.extra.text())
        except ValueError:
            raise ValueError("Extra options: a quote is not closed.")
        return build_args(opts, extra)

    def changed(self, *_):
        if self.loading or self.launcher is None:
            return
        if self.launcher.host:
            return self.show_remote()
        model = self.model.currentText().strip()
        size = file_size(model)
        coming = self.download_of(model)
        tools = launcher_files.can_use_tools(model) if size else None
        self.model_info.setText((size or coming or "file not found")
                                + {True: "  ·  can use files", False: "  ·  can't use files"}.get(tools, ""))
        self.model_info.setToolTip({True: "Its chat template knows tools, so a persona on it can be given file access.",
                                    False: "Its chat template has no tools: a persona on it chats normally but "
                                           "can't read or write files."}.get(tools, ""))
        self.model_info.setStyleSheet("" if size or coming else "color: #c0392b;")
        text, colour = self.memory_text(model)
        self.mem_info.setText(text)
        self.mem_info.setStyleSheet(f"color: {colour};" if colour else "")
        try:
            args = self.current_args()
            self.preview.setPlainText(shlex.join(["llama-server"] + launcher_files.command(model, args)[1:]))
        except ValueError as e:
            self.preview.setPlainText(str(e))
        dirty = self.dirty()
        self.setWindowTitle(f"AIcove — {self.launcher.name}{' *' if dirty else ''}")
        self.revert_btn.setEnabled(dirty)
        self.save_btn.setEnabled(dirty)
        self.start_btn.setText("Save && Start" if dirty else "Start")

    def show_remote(self):
        l = self.launcher
        tools = l.info.get("tools")
        self.model_info.setText(f"On {l.host}: " + (l.info.get("model") or "not asked yet — Start asks")
                                + {True: "  ·  can use files", False: "  ·  can't use files"}.get(tools, ""))
        self.model_info.setToolTip(f"Its settings are {l.remote_name}'s on {l.host}: change them there.")
        self.model_info.setStyleSheet("")
        self.mem_info.setText(f"Memory: {l.host}'s, not this machine's.")
        self.mem_info.setStyleSheet("")
        self.preview.setPlainText(f"ssh {l.host} aicove start {l.remote_name}\n"
                                  f"then a tunnel brings it here to port {S.llama_port}")
        self.setWindowTitle(f"AIcove — {l.name}")
        self.revert_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.start_btn.setText("Start")

    def add_remote(self):
        """New launchers for the launchers of another machine."""
        if self.dirty() and not self.resolve_unsaved():
            return
        known = launcher_files.hosts() or [""]
        host, ok = QInputDialog.getText(self, "From another machine",
                                        "The machine, as ssh knows it (a Host in ~/.ssh/config):", text=known[0])
        host = host.strip()
        if not ok or not host:
            return
        if not launcher_files.NAME.fullmatch(host):
            QMessageBox.warning(self, "From another machine", "That is not a plain name.")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            info = server.ask(host)
            added, removed = launcher_files.follow(host, info, again=True)
        except OSError as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "From another machine",
                                f"{e}\n\nThat machine needs AIcove, and this one an ssh key there that works "
                                f"without a password (try: ssh {host} true).")
            return
        QApplication.restoreOverrideCursor()
        self.followed(host, added, removed)
        if not added:
            QMessageBox.information(self, "From another machine", f"Nothing new on {host}: "
                                    + ("all its launchers are here already." if info["launchers"]
                                       else "it has no launchers."))
            return
        self.launcher = None
        self.reload_list(select=added[0])

    def followed(self, host, added, removed):
        """After our launchers of host were made to match it."""
        if added:
            self.say(f"From {host}: {', '.join(added)}")
        if removed:
            self.say(f"{host} no longer has these, so they went here too (copies in {BACKUP_DIR}): "
                     + ", ".join(removed))

    def update_remote(self):
        self.syncer.start()
        if not launcher_files.hosts():
            QMessageBox.information(self, "Update", "No launchers of another machine yet: "
                                    "add one with \"From another machine…\".")
            return
        self.update_btn.setEnabled(False)
        self.say(f"Asking {', '.join(launcher_files.hosts())}...")
        self.follow.start()

    def synced(self, lines, changed):
        """A sync is done: say what happened, and show what the other machines changed."""
        for line in lines:
            self.say(line)
        if not changed:
            return
        if not self.persona_dirty():
            self.reload_personas(select=self.persona.id if self.persona else None)
        if not self.chat_tab.worker:
            self.chat_tab.reload()
        if not self.agents_tab.dirty():
            self.agents_tab.reload()

    def follow_finished(self, off):
        if not self.update_btn.isEnabled():
            for line in off:
                self.say(line)
            self.say("Up to date." if not off else "The others are up to date.")
        self.update_btn.setEnabled(True)

    def follow_done(self, host, added, removed, changed):
        """The watcher asked host again; show what changed (unless the form is being edited)."""
        self.followed(host, added, removed)
        if changed and not self.dirty() and not self.busy():
            self.reload_list(select=self.launcher.name if self.launcher else None)

    def resolve_unsaved(self, name=None, save=None):
        """Ask what to do with unsaved changes. False = stay where we are."""
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved changes")
        box.setText(f"{name or self.launcher.name} has unsaved changes.")
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                               | QMessageBox.StandardButton.Cancel)
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Save:
            return (save or self.save)()
        return answer == QMessageBox.StandardButton.Discard

    def revert(self):
        if self.launcher is not None:
            self.load(self.launcher)

    # --- saving --------------------------------------------------------------

    def checked_form(self):
        """(desc, model, friendly, args, persona) ready to write, or None after telling the user why not."""
        desc, model, friendly = self.desc.text().strip(), self.model.currentText().strip(), self.friendly.text().strip()
        try:
            if not model:
                raise ValueError("Model file is empty.")
            if not friendly:
                raise ValueError("Display name is empty.")
            if "\n" in desc:
                raise ValueError("Description must be one line.")
            args = self.current_args()
        except ValueError as e:
            QMessageBox.warning(self, "Can't save", str(e))
            return None
        return desc, model, friendly, args, ""

    def save(self):
        if self.launcher is None:
            return False
        if not self.dirty():
            return True
        form = self.checked_form()
        if form is None:
            return False
        name = self.launcher.name
        try:
            self.launcher.write(*form)
        except OSError as e:
            QMessageBox.critical(self, "Can't save", str(e))
            return False
        self.say(f"Saved {name} (previous version kept in {BACKUP_DIR})")
        self.launcher = None
        self.reload_list(select=name)
        return True

    def new_launcher(self):
        if self.launcher is None or self.not_here(self.launcher, "New launcher"):
            return
        form = self.checked_form()
        if form is None:
            return
        name = self.ask_launcher_name("New launcher")
        if name is None:
            return
        try:
            path = launcher_files.create(name, *form)
        except OSError as e:
            QMessageBox.critical(self, "New launcher", str(e))
            return
        self.say(f"Created {path}")
        # The form went into the new launcher; the one it was copied from stays as it is on disk.
        self.launcher = None
        self.reload_list(select=name)

    def add_model(self):
        if self.dirty() and not self.resolve_unsaved():
            return
        known = [l for _, l in self.launcher_items()]
        dialog = AddModel(self, known, self.budget)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        plan = dialog.plan
        name = plan["command"]
        try:
            path = launcher_files.create(name, plan["desc"], plan["model"], plan["friendly"], plan["args"],
                                         plan["persona"])
            if plan["files"]:
                os.makedirs(launcher_files.models_dir(), exist_ok=True)
                os.makedirs(JOB_DIR, exist_ok=True)
                base = os.path.join(JOB_DIR, name)
                with open(base + ".json", "w", encoding="utf-8") as f:
                    json.dump({"files": plan["files"]}, f, indent=1)
                start_job(base)
        except OSError as e:
            QMessageBox.critical(self, "Add model", str(e))
            return
        self.say(f"Created {path}")
        if plan["files"]:
            self.say(f"Fetching {plan['model']} — this keeps going when AIcove is closed. Log: {base}.log")
        if S.notes_file:
            if setup_add_line(name, (plan["desc"] or plan["friendly"]) + time.strftime(" — added %Y-%m-%d")):
                self.say(f"Added {name} to {S.notes_file}")
            else:
                self.say(f"Couldn't find the launcher list in {S.notes_file} — add {name} there by hand.")
        self.fill_models()
        self.launcher = None
        self.reload_list(select=name)

    def ask_launcher_name(self, title):
        """A launcher name typed by the user that is still free, or None."""
        name, ok = QInputDialog.getText(self, title, "Name of the launcher:")
        name = name.strip()
        if not ok or not name:
            return None
        if not launcher_files.NAME.fullmatch(name):
            QMessageBox.warning(self, title, "Use only letters, digits, dot, dash and underscore.")
            return None
        if os.path.exists(launcher_files.path_of(name)):
            QMessageBox.warning(self, title, f"There is already a launcher called {name}.")
            return None
        return name

    def rename_launcher(self):
        if self.launcher is None or (self.dirty() and not self.resolve_unsaved()):
            return
        name = self.ask_launcher_name(f"Rename {self.launcher.name}")
        if name is None:
            return
        try:
            os.rename(self.launcher.path, launcher_files.path_of(name))
        except OSError as e:
            QMessageBox.critical(self, "Rename", str(e))
            return
        self.say(f"Renamed {self.launcher.name} to {name}")
        for p in personas.load_all().values():
            if p.launcher == self.launcher.name:
                p.launcher = name
                p.write()
        self.launcher = None
        self.reload_list(select=name)

    def delete_launcher(self):
        if self.launcher is None:
            return
        others = [l for _, l in self.launcher_items() if l is not self.launcher and not l.error]
        # Files only this launcher uses can go with it; never one that is loaded right now.
        own = [f for f in self.launcher.files() + [self.launcher.model + ".part"]
               if os.path.exists(f) and not any(f in l.files() for l in others)]
        loaded = running_server()
        loaded = loaded is not None and loaded[0] in own
        users = [p.name for p in personas.load_all().values() if self.launcher.name in p.models]
        box = QMessageBox(self)
        box.setWindowTitle("Delete launcher")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(f"Delete {self.launcher.name}?")
        box.setInformativeText(
            f"The launcher is kept in {BACKUP_DIR}."
            + (f"\n\nRuns {', '.join(users)}; pick another model for "
               f"{'it' if len(users) == 1 else 'them'} on the Personas tab." if users else ""))
        files_box = None
        if own:
            size = sum(os.path.getsize(f) for f in own)
            files_box = QCheckBox(f"Also delete the model file{'s' if len(own) > 1 else ''} from the disk "
                                  f"({', '.join(os.path.basename(f) for f in own)}, {size / 1e9:.1f} GB)")
            if loaded:
                files_box.setEnabled(False)
                files_box.setToolTip("This model is loaded right now. Stop it first to delete its file.")
                files_box.setText(files_box.text() + " — stop the model first")
            box.setCheckBox(files_box)
        elif self.launcher.files() and os.path.exists(self.launcher.model):
            box.setInformativeText(box.informativeText() + "\n\nThe model file stays: another launcher uses it.")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        drop_files = files_box is not None and files_box.isEnabled() and files_box.isChecked()
        try:
            dest = self.launcher.retire()
        except OSError as e:
            QMessageBox.critical(self, "Delete launcher", str(e))
            return
        self.say(f"Deleted {self.launcher.name} (kept as {dest})")
        if self.launcher.host:
            launcher_files.skip_remote(self.launcher)
            self.say(f"It won't come back from {self.launcher.host} (unless you add {self.launcher.host} again "
                     "with \"From another machine…\").")
        if drop_files:
            for f in own:
                try:
                    os.remove(f)
                    self.say(f"Deleted the file {f}")
                except OSError as e:
                    self.say(f"Can't delete {f}: {e}")
        if not any(l.model == self.launcher.model for l in others):
            for name, job in self.dl_jobs.items():
                if any(item["dest"] == self.launcher.model for item in job["files"]):
                    # Nothing would use the file any more, so the download stops too.
                    stop_job(job)
                    for ext in (".json", ".pid"):
                        try:
                            os.remove(job["base"] + ext)
                        except OSError:
                            pass
                    part = self.launcher.model + ".part"
                    if drop_files and os.path.exists(part):
                        os.remove(part)
                        self.say(f"Stopped the download {name} and deleted what had come ({part}).")
                    else:
                        self.say(f"Stopped the download {name}. What is already here stays as "
                                 f"{part} — remove it by hand if you don't want it.")
        self.launcher = None
        self.reload_list()

    # --- start / stop --------------------------------------------------------

    def spawn(self, program, args=(), title=None):
        """Run one of the service commands on the side; its output goes to the pane below."""
        job = QProcess(self)
        job.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.jobs.add(job)

        def read():
            text = ANSI.sub("", bytes(job.readAllStandardOutput()).decode(errors="replace")).rstrip("\n")
            if text:
                self.say(text)

        def done(*_):
            if job in self.jobs and job.state() == QProcess.ProcessState.NotRunning:
                self.jobs.discard(job)
                job.deleteLater()
                self.refresh_status()
                if self.tabs.currentWidget() is self.pw_tab:
                    self.pw_tab.refresh()

        job.readyReadStandardOutput.connect(read)
        job.finished.connect(done)
        job.errorOccurred.connect(done)
        self.say(f"--- {title or os.path.basename(program)} ---")
        job.start(program, list(args))

    def spawn_cove(self, *args):
        """Run one of our own subcommands on the side."""
        cmd = cove_cmd(*args)
        self.spawn(cmd[0], cmd[1:], " ".join(args))

    def busy(self):
        return self.proc.state() != QProcess.ProcessState.NotRunning

    def save_and_start(self):
        if self.launcher is None or self.busy() or not self.save():
            return
        if not self.launcher.present:
            coming = self.download_of(self.launcher.model)
            QMessageBox.warning(self, "Start", f"The model is still on its way ({coming}):\n{self.launcher.model}"
                                if coming else f"Model file not found:\n{self.launcher.model}")
            return
        self.say(f"--- {self.launcher.name} ---")
        self.run_cove("start", self.launcher.name)

    def stop_server(self):
        if self.busy():
            return
        self.say("--- stop ---")
        self.run_cove("stop")

    def run_cove(self, *args):
        """Run one of our own subcommands in the foreground slot (Start / Stop wait for it)."""
        cmd = cove_cmd(*args)
        self.run(cmd[0], cmd[1:])

    def run(self, program, args):
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.proc.start(program, args)

    def read_output(self):
        text = bytes(self.proc.readAllStandardOutput()).decode(errors="replace").rstrip("\n")
        if text:
            self.say(text)

    def proc_done(self, *_):
        if self.busy():
            return
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.refresh_status()

    def say(self, text):
        self.output.appendPlainText(text)

    # --- downloads -------------------------------------------------------------

    def download_of(self, model):
        """"downloading 43%" / "download stopped at 43%" for a model file that is on its way, or None."""
        for job in self.dl_jobs.values():
            if any(item["dest"] == model for item in job["files"]) and not os.path.exists(model):
                have, total = job_progress(job)
                pct = f"{100 * have // total}%" if total else f"{have / 1e9:.1f} GB"
                return f"downloading {pct}" if job["pid"] else f"download stopped at {pct}"
        return None

    def refresh_downloads(self):
        before, self.dl_jobs = self.dl_jobs, read_jobs()
        for name, job in before.items():
            if name not in self.dl_jobs and all(os.path.exists(item["dest"]) for item in job["files"]):
                self.say(f"Download finished: {job['files'][0]['dest']} — ready to start.")
                self.dl_rate.pop(name, None)
                self.fill_models()
        shape = [(name, bool(job["pid"])) for name, job in self.dl_jobs.items()]
        if shape != self.dl_shape:
            self.dl_shape, self.dl_labels = shape, {}
            while self.dl_rows.count():
                row = self.dl_rows.takeAt(0).widget()
                row.deleteLater()
            for name, job in self.dl_jobs.items():
                row = QWidget()
                box = QHBoxLayout(row)
                box.setContentsMargins(0, 0, 0, 0)
                self.dl_labels[name] = QLabel()
                box.addWidget(self.dl_labels[name], 1)
                button = QPushButton("Stop download" if job["pid"] else "Resume download")
                button.clicked.connect(lambda _, j=job: stop_job(j) if j["pid"] else start_job(j["base"]))
                button.clicked.connect(lambda: QTimer.singleShot(700, self.refresh_status))
                box.addWidget(button)
                self.dl_rows.addWidget(row)
        now = time.time()
        for name, job in self.dl_jobs.items():
            have, total = job_progress(job)
            text = f"⇣ {name}: {have / 1e9:.1f} of {total / 1e9:.1f} GB"
            if total:
                text += f" ({100 * have // total}%)"
            if job["pid"]:
                old_have, old_time, rate = self.dl_rate.get(name, (have, now, None))
                if now - old_time >= 2:
                    new = (have - old_have) / (now - old_time)
                    rate = new if rate is None else 0.7 * rate + 0.3 * new
                    self.dl_rate[name] = (have, now, rate)
                else:
                    self.dl_rate.setdefault(name, (have, now, None))
                if rate:
                    left = (total - have) / rate
                    text += f" · {rate / 1e6:.1f} MB/s · " + (f"{left / 3600:.1f} h left" if left > 5400
                                                              else f"{max(left / 60, 1):.0f} min left")
            else:
                self.dl_rate.pop(name, None)
                text += " — stopped. " + self.job_last_words(job)
            self.dl_labels[name].setText(text)
        if before or self.dl_jobs:
            self.changed()

    def job_last_words(self, job):
        try:
            with open(job["base"] + ".log", encoding="utf-8", errors="replace") as f:
                lines = [l.strip() for l in f.read().split("\n") if l.strip()]
            return lines[-1][20:] if lines else ""
        except OSError:
            return ""

    def refresh_status(self):
        self.refresh_downloads()
        self.ask_keyboard()
        server = running_server()
        active = None
        for item, l in self.launcher_items():
            is_running = server is not None and not l.error and l.model == server[0]
            exact = is_running and launcher_files.run_args(l.args) == server[1]
            coming = None if l.error else self.download_of(l.model)
            item.setText(("▶ " if is_running else "⇣ " if coming else "") + self.list_text(l)
                         + (f"  ({coming.split()[-1]})" if coming else ""))
            font = item.font()
            font.setBold(bool(is_running))
            item.setFont(font)
            if exact or (is_running and active is None):
                active = (l, exact)
        word = "Ready" if self.health.state == "ready" else "Loading"
        if server is None:
            self.status.setText("No model loaded.")
        elif active is None:
            self.status.setText(f"{word}: {os.path.basename(server[0])} (port {S.llama_port})")
        elif active[1]:
            l = active[0]
            where = f"  —  on {l.host} 🖥" if l.host else f" (port {S.llama_port})"
            self.status.setText(f"{word}: {l.friendly}{where}")
        else:
            self.status.setText(f"{word}: {os.path.basename(server[0])} with other settings — "
                                "Start again to apply the saved ones.")
        self.refresh_tools()

    # --- personas ------------------------------------------------------------

    def persona_tab(self):
        self.persona = None
        self.p_baseline = None
        self.p_switching = False
        self.p_list = QListWidget()
        self.p_list.setIconSize(QSize(FACE_SIZE, FACE_SIZE))
        self.p_list.currentItemChanged.connect(self.on_persona_select)
        new_btn = QPushButton("New…")
        new_btn.clicked.connect(self.new_persona)
        self.p_delete_btn = QPushButton("Delete")
        self.p_delete_btn.clicked.connect(self.delete_persona)
        lbuttons = QHBoxLayout()
        lbuttons.addWidget(new_btn)
        lbuttons.addWidget(self.p_delete_btn)
        self.p_export_btn = QPushButton("Export…")
        self.p_export_btn.setToolTip("Save this persona as one .zip file: to keep, to move to another "
                                     "machine, or to give to someone")
        self.p_export_btn.clicked.connect(self.export_persona)
        import_btn = QPushButton("Import…")
        import_btn.setToolTip("Add a persona from a .zip file made with Export…")
        import_btn.clicked.connect(self.import_persona)
        lbuttons2 = QHBoxLayout()
        lbuttons2.addWidget(self.p_export_btn)
        lbuttons2.addWidget(import_btn)
        left = QWidget()
        lbox = QVBoxLayout(left)
        lbox.setContentsMargins(0, 0, 0, 0)
        lbox.addWidget(QLabel("Personas"))
        lbox.addWidget(self.p_list)
        lbox.addLayout(lbuttons)
        lbox.addLayout(lbuttons2)

        self.p_name = QLineEdit()
        self.p_name.textChanged.connect(self.persona_changed)
        self.p_system = QPlainTextEdit()
        self.p_system.textChanged.connect(self.persona_changed)
        self.p_launcher = Combo()
        self.p_launcher.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.p_launcher.setToolTip("The launcher (model + all its settings) this persona is started with")
        self.p_launcher.currentIndexChanged.connect(self.persona_changed)
        self.p_files = Combo()
        self.p_files.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.p_files.addItem("No access to the files of this machine", "")
        self.p_files.addItem("May read files (what the Settings tab allows)", "read")
        self.p_files.addItem("May read files, and write after asking you each time", "write")
        self.p_files.setToolTip("Reading and writing happen through the chat's file tools. It can never "
                                "run commands. Where it may read and write: Settings tab.")
        self.p_files.currentIndexChanged.connect(self.persona_changed)
        self.p_start_btn = QPushButton("Start")
        self.p_start_btn.setToolTip("Load this persona's model with its launcher's settings")
        self.p_start_btn.clicked.connect(self.start_persona)
        self.p_edit_btn = QPushButton("Edit model settings…")
        self.p_edit_btn.clicked.connect(self.edit_persona_launcher)
        self.p_copy_btn = QPushButton("Own copy…")
        self.p_copy_btn.setToolTip("Give this persona its own copy of the chosen launcher, so changing the "
                                   "original (or another persona's) later doesn't change this one")
        self.p_copy_btn.clicked.connect(self.copy_for_persona)
        model_row = QHBoxLayout()
        model_row.addWidget(self.p_launcher, 1)
        for b in (self.p_start_btn, self.p_edit_btn, self.p_copy_btn):
            model_row.addWidget(b)
        self.p_launcher_info = QLabel()
        self.p_launcher_info.setWordWrap(True)
        self.p_launcher_info.setEnabled(False)
        # The other launchers it may run on (chosen in the Chat tab), after the one above.
        self.p_others = ()
        self.p_others_label = QLabel()
        self.p_others_label.setWordWrap(True)
        self.p_others_btn = QPushButton("Choose…")
        self.p_others_btn.setToolTip("Other models this persona may run on; the Chat tab lets you pick among them")
        self.p_others_btn.clicked.connect(self.choose_other_models)
        others_row = QHBoxLayout()
        others_row.addWidget(QLabel("Also runs on:"))
        others_row.addWidget(self.p_others_label, 1)
        others_row.addWidget(self.p_others_btn)
        info_box = QVBoxLayout()
        info_box.addWidget(self.p_launcher_info)
        info_box.addLayout(others_row)
        # None = picture as saved, b"" = to be removed, anything else = new PNG to be saved.
        self.p_picture = None
        self.p_preview = QLabel()
        self.p_preview.setFixedSize(72, 72)
        self.p_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.p_preview.setStyleSheet("border: 1px solid palette(mid);")
        self.p_pick_btn = QPushButton("Choose picture…")
        self.p_pick_btn.clicked.connect(self.pick_picture)
        self.p_unpick_btn = QPushButton("Remove picture")
        self.p_unpick_btn.clicked.connect(self.unpick_picture)
        picture = QHBoxLayout()
        picture.addWidget(self.p_preview)
        picture.addWidget(self.p_pick_btn)
        picture.addWidget(self.p_unpick_btn)
        hint = QLabel("Shown next to the persona's name and answers in the chat. "
                      f"Any image; it is cut square and saved small ({PICTURE_SIZE} px).")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        picture.addWidget(hint, 1)
        # Same fields as the launcher's writing style, two side by side to leave room for the prompt.
        samplers = QGroupBox("Settings for this persona")
        sgrid = QGridLayout(samplers)
        # Which settings the fields show: the persona's own (every model), or what differs on one
        # model on this machine (profiles.py).
        self.p_base, self.p_prof, self.p_used, self.p_forgotten = {}, {}, {}, set()
        self.p_target, self.p_switching = "", False
        self.p_profile = Combo()
        self.p_profile.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.p_profile.setToolTip("All models: the persona's own settings. A model: what is different when "
                                  "it runs on that one, on this machine")
        self.p_profile.currentIndexChanged.connect(self.switch_profile)
        self.p_forget_btn = QPushButton("Forget this model")
        self.p_forget_btn.setToolTip("Remove this model's settings for the persona (on Save)")
        self.p_forget_btn.clicked.connect(self.forget_profile)
        self.p_take_btn = QPushButton("Take these")
        self.p_take_btn.setToolTip("Use the other machine's settings for this model here")
        self.p_take_btn.clicked.connect(self.take_profile)
        self.p_profile_info = QLabel()
        self.p_profile_info.setWordWrap(True)
        self.p_profile_info.setEnabled(False)
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Settings for:"))
        profile_row.addWidget(self.p_profile, 1)
        profile_row.addWidget(self.p_forget_btn)
        sgrid.addLayout(profile_row, 0, 0, 1, 6)
        info_row = QHBoxLayout()
        info_row.addWidget(self.p_profile_info, 1)
        info_row.addWidget(self.p_take_btn)
        sgrid.addLayout(info_row, 1, 0, 1, 6)
        presets = QHBoxLayout()
        presets.addWidget(QLabel("Writing style at once:"))
        for name in PRESETS:
            b = QPushButton("None (use the launcher's)" if name == "Model defaults" else name)
            b.clicked.connect(lambda _, n=name: self.apply_persona_preset(n))
            presets.addWidget(b)
        presets.addStretch(1)
        sgrid.addLayout(presets, 2, 0, 1, 5)
        sgrid.setColumnMinimumWidth(2, 30)
        sgrid.setColumnStretch(5, 1)
        self.p_rows = {}
        keys = {**PERSONA_EXTRA, **PERSONA_SAMPLERS}
        specs = [PERSONA_THINKING] + [o for o in OPTIONS if o["flag"] in keys]
        half = (len(specs) + 1) // 2
        for i, spec in enumerate(specs):
            row = OptRow(spec, self.persona_changed, self)
            row.check.setToolTip(spec["hint"])
            row.widget.setToolTip(spec["hint"])
            row.widget.setMinimumWidth(110)
            self.p_rows[keys[spec["flag"]]] = row
            sgrid.addWidget(row.check, 3 + i % half, 3 * (i // half))
            sgrid.addWidget(row.widget, 3 + i % half, 3 * (i // half) + 1)
        note = QLabel(
            "Start loads this persona's model. To talk to it, use the Chat tab, which loads the "
            "model by itself when needed.")
        note.setWordWrap(True)
        note.setEnabled(False)
        self.p_revert_btn = QPushButton("Revert")
        self.p_revert_btn.clicked.connect(self.revert_persona)
        self.p_save_btn = QPushButton("Save")
        self.p_save_btn.clicked.connect(self.save_persona)
        rbuttons = QHBoxLayout()
        rbuttons.addStretch(1)
        for b in (self.p_revert_btn, self.p_save_btn):
            rbuttons.addWidget(b)
        right = QWidget()
        grid = QGridLayout(right)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(4, 1)
        grid.addWidget(QLabel("Name"), 0, 0)
        grid.addWidget(self.p_name, 0, 1)
        grid.addWidget(QLabel("Model"), 1, 0)
        grid.addLayout(model_row, 1, 1)
        grid.addLayout(info_box, 2, 1)
        grid.addWidget(QLabel("Picture"), 3, 0)
        grid.addLayout(picture, 3, 1)
        grid.addWidget(QLabel("System prompt"), 4, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.p_system, 4, 1)
        grid.addWidget(samplers, 5, 1)
        grid.addWidget(QLabel("Files"), 6, 0)
        files_row = QHBoxLayout()
        files_row.addWidget(self.p_files, 1)
        self.p_folder_btn = QPushButton("Its own folder…")
        self.p_folder_btn.setToolTip("The folder only this persona has: put files for it into \"knowledge\"; "
                                     "it keeps what it should remember in \"notes\". Needs file access.")
        self.p_folder_btn.clicked.connect(self.open_persona_folder)
        files_row.addWidget(self.p_folder_btn)
        grid.addLayout(files_row, 6, 1)
        self.p_files_warn = QLabel()
        self.p_files_warn.setWordWrap(True)
        self.p_files_warn.setStyleSheet("color: #d35400;")
        self.p_files_warn.hide()
        grid.addWidget(self.p_files_warn, 7, 1)
        self.p_voice = Combo()
        self.p_voice.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.p_voice.setToolTip("The voice its answers are read in (🔊 in the chat): a saved profile of the "
                                "voice configurator")
        self.p_voice.currentIndexChanged.connect(self.persona_changed)
        self.p_voices_btn = QPushButton("Get the voices")
        self.p_voices_btn.setToolTip("Ask the voice configurator for its saved profiles (starts it if needed)")
        self.p_voices_btn.clicked.connect(self.fetch_voices)
        self.p_voices = None   # [{id, name, ...}] once fetched
        voice_row = QHBoxLayout()
        voice_row.addWidget(self.p_voice, 1)
        voice_row.addWidget(self.p_voices_btn)
        grid.addWidget(QLabel("Voice"), 8, 0)
        grid.addLayout(voice_row, 8, 1)
        grid.addWidget(note, 9, 1)
        grid.addLayout(rbuttons, 10, 1)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([230, 900])
        return split

    def reload_personas(self, select=None):
        if select is None and self.persona is not None:
            select = self.persona.id
        self.p_switching = True
        self.p_list.clear()
        target = None
        for p in personas.load_all().values():
            item = QListWidgetItem(p.name or p.id)
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setToolTip(p.path)
            item.setIcon(QIcon(face(p)))
            self.p_list.addItem(item)
            if target is None or p.id == select:
                target = item
        self.p_list.setCurrentItem(target)
        self.p_switching = False
        self.load_persona(target.data(Qt.ItemDataRole.UserRole) if target is not None else None)

    def on_persona_select(self, cur, prev):
        if self.p_switching or cur is None:
            return
        wanted = cur.data(Qt.ItemDataRole.UserRole)
        if prev is not None and self.persona_dirty():
            if not self.resolve_unsaved(self.persona.name, self.save_persona):
                self.p_switching = True
                self.p_list.setCurrentItem(prev)
                self.p_switching = False
                return
            # Saving fills the list again, which deletes cur and picks the saved persona again (the
            # form stayed on it, so the next Save went to it): pick the wanted one in the new list.
            self.reload_personas(select=wanted.id)
            return
        self.load_persona(wanted)

    def load_persona(self, persona):
        self.persona = None
        self.p_name.setText(persona.name if persona else "")
        self.p_system.setPlainText(persona.system if persona else "")
        for key, row in self.p_rows.items():
            row.set(persona.samplers.get(key) if persona else None)
        self.p_launcher.clear()
        self.p_launcher.addItem("— none: only runs on whatever model is loaded —", "")
        self.p_launchers = {l.name: l for l in find_launchers() if not l.error}
        for l in self.p_launchers.values():
            self.p_launcher.addItem(l.label(f"{l.name}  —  {l.friendly}"), l.name)
        wanted = persona.launcher if persona else ""
        if wanted and wanted not in self.p_launchers:
            self.p_launcher.addItem(f"{wanted} (no such launcher)", wanted)
        self.p_launcher.setCurrentIndex(max(self.p_launcher.findData(wanted), 0))
        self.p_files.setCurrentIndex(max(self.p_files.findData(persona.files if persona else ""), 0))
        self.p_others = tuple(persona.models[1:]) if persona else ()
        self.load_profiles(persona)
        self.fill_voices(persona.voice if persona else "")
        self.persona = persona
        # To notice at Save that the file was changed behind the form (a sync, an editor).
        self.p_seen = self.persona_stamp()
        self.p_picture = None
        self.p_baseline = self.persona_state()
        for w in (self.p_name, self.p_system, self.p_delete_btn, self.p_pick_btn, self.p_launcher, self.p_files,
                  self.p_voice, self.p_voices_btn):
            w.setEnabled(persona is not None)
        self.persona_changed()

    def revert_persona(self):
        """Back to what is in the file now, which may be newer than what this form was filled from."""
        persona = self.persona
        try:
            persona = personas.Persona.read(persona.id)
        except (OSError, AttributeError):
            pass
        item = self.p_list.currentItem()
        if item is not None and persona is not None:
            item.setData(Qt.ItemDataRole.UserRole, persona)
            item.setText(persona.name or persona.id)
        self.load_persona(persona)

    def persona_stamp(self):
        try:
            return os.stat(self.persona.path).st_mtime_ns if self.persona else None
        except OSError:
            return None

    def persona_state(self):
        return (self.p_name.text().strip(), self.p_system.toPlainText().strip(),
                self.samplers_state(), self.p_picture,
                self.p_launcher.currentData() or "", self.p_files.currentData() or "", self.p_others,
                self.p_voice.currentData() or "")

    # --- a persona's own model ---------------------------------------------------

    def persona_launcher(self):
        """The launcher chosen in the persona form, or None."""
        return self.p_launchers.get(self.p_launcher.currentData() or "")

    def start_persona(self):
        if self.persona is None or self.busy():
            return
        if self.persona_dirty() and not self.save_persona():
            return
        launcher = self.p_launchers.get(self.persona.launcher)
        if launcher is None:
            QMessageBox.warning(self, "Start", f"Pick a model for {self.persona.name} first.")
            return
        if not launcher.present:
            coming = self.download_of(launcher.model)
            QMessageBox.warning(self, "Start", f"The model is still on its way ({coming}):\n{launcher.model}"
                                if coming else f"Model file not found:\n{launcher.model}")
            return
        self.say(f"--- {self.persona.name} on {launcher.name} ---")
        self.run_cove("start", launcher.name)

    def edit_persona_launcher(self):
        launcher = self.persona_launcher()
        if launcher is None:
            return
        self.tabs.setCurrentWidget(self.launcher_page)
        for item, l in self.launcher_items():
            if l.name == launcher.name:
                self.list.setCurrentItem(item)

    def not_here(self, launcher, title):
        """True (after saying so) for another machine's launcher, which can't be copied here."""
        if launcher.host:
            QMessageBox.information(self, title, f"{launcher.name} is {launcher.remote_name} on {launcher.host}: "
                                    f"make the copy there, then add it with \"From another machine…\".")
        return bool(launcher.host)

    def own_copy(self, persona, launcher):
        """Give a persona its own copy of a launcher (model + settings); the new launcher's name or None."""
        if self.not_here(launcher, "Own copy"):
            return None
        name = persona.id.strip("-._")
        if os.path.exists(launcher_files.path_of(name)):
            name = self.ask_launcher_name(f"{name} is taken — name for {persona.name}'s launcher")
            if name is None:
                return None
        desc = f"{persona.name}'s own model and settings ({os.path.basename(launcher.model)}, " \
               f"copied from {launcher.name})"
        try:
            path = launcher_files.create(name, desc, launcher.model, launcher.friendly, launcher.args, persona.id)
            persona.launcher = name
            persona.write()
        except OSError as e:
            QMessageBox.critical(self, "Own copy", str(e))
            return None
        self.say(f"Created {path} for {persona.name}")
        if S.notes_file:
            setup_add_line(name, desc + time.strftime(" — added %Y-%m-%d"))
        return name

    def copy_for_persona(self):
        if self.persona is None or self.persona_launcher() is None:
            return
        if self.dirty() and not self.resolve_unsaved():
            return
        if self.persona_dirty() and not self.save_persona():
            return
        launcher = self.p_launchers.get(self.persona.launcher)
        if launcher is None or self.own_copy(self.persona, launcher) is None:
            return
        current = self.launcher.name if self.launcher is not None else None
        pid = self.persona.id
        self.persona = None
        self.launcher = None
        self.reload_list(select=current)
        self.reload_personas(select=pid)

    def new_persona_on_launcher(self):
        if self.launcher is None or (self.dirty() and not self.resolve_unsaved()):
            return
        if self.persona_dirty() and not self.resolve_unsaved(self.persona.name, self.save_persona):
            return
        source = self.launcher
        if self.not_here(source, "New persona"):
            return
        name, ok = QInputDialog.getText(self, "New persona", f"Name of the persona that gets {source.name}'s "
                                                             "model and settings:")
        name = name.strip()
        if not ok or not name:
            return
        persona = personas.Persona(personas.new_id(name), name, "")
        try:
            persona.write()
        except OSError as e:
            QMessageBox.critical(self, "New persona", str(e))
            return
        self.own_copy(persona, source)
        self.persona = None
        self.launcher = None
        self.reload_list(select=source.name)
        self.reload_personas(select=persona.id)
        self.tabs.setCurrentWidget(self.persona_page)
        self.p_system.setFocus()

    def pick_picture(self):
        if self.persona is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Picture", os.path.expanduser("~"),
                                              "Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp);;All files (*)")
        if not path:
            return
        image = QImage(path)
        if image.isNull():
            QMessageBox.warning(self, "Picture", f"Can't read {path} as an image.")
            return
        # Fill the square and cut off what sticks out, so nothing gets squeezed.
        image = image.scaled(PICTURE_SIZE, PICTURE_SIZE, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                             Qt.TransformationMode.SmoothTransformation)
        image = image.copy((image.width() - PICTURE_SIZE) // 2, (image.height() - PICTURE_SIZE) // 2,
                           PICTURE_SIZE, PICTURE_SIZE)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        self.p_picture = bytes(buffer.data())
        self.persona_changed()

    def unpick_picture(self):
        if self.persona is not None:
            self.p_picture = b"" if personas.picture_path(self.persona.id) else None
            self.persona_changed()

    def show_picture(self):
        """The preview: the picture waiting to be saved, else the saved one."""
        pixmap = QPixmap()
        if self.p_picture:
            pixmap.loadFromData(self.p_picture)
        elif self.p_picture is None and self.persona is not None:
            path = personas.picture_path(self.persona.id)
            if path:
                pixmap.load(path)
        if pixmap.isNull():
            self.p_preview.setPixmap(QPixmap())
            self.p_preview.setText("none")
        else:
            self.p_preview.setPixmap(pixmap.scaled(70, 70, Qt.AspectRatioMode.KeepAspectRatio,
                                                   Qt.TransformationMode.SmoothTransformation))
        self.p_unpick_btn.setEnabled(not pixmap.isNull())

    def apply_persona_preset(self, name):
        if self.persona is None:
            return
        for flag, key in PERSONA_SAMPLERS.items():
            self.p_rows[key].set(PRESETS[name].get(flag))
        self.persona_changed()

    # --- its settings per model (profiles.py) -------------------------------------------

    def load_profiles(self, persona):
        """Fill the per-model part from the file, showing the persona's own settings."""
        self.p_base = {k: r.get() for k, r in self.p_rows.items() if r.get() is not None}
        found = profiles.read(persona.id) if persona else {}
        self.p_prof = {l: dict(e["samplers"]) for l, e in found.items()}
        self.p_used = {l: e["used"] for l, e in found.items()}
        self.p_forgotten = set()
        self.p_target = ""
        self.fill_profiles()
        self.show_profile_info()

    def fill_profiles(self):
        """All models first, then the models it has run on or has settings for (last used first),
        then the other launchers."""
        self.p_switching = True
        self.p_profile.clear()
        self.p_profile.addItem("All models: the persona's own settings", "")
        known = sorted(set(self.p_prof) | set(self.p_used), key=lambda l: (self.p_used.get(l, ""), l), reverse=True)
        for l in known:
            bits = ([f"used {self.p_used[l]}"] if self.p_used.get(l) else []) \
                + ([f"{len(self.p_prof[l])} own setting{'s' if len(self.p_prof[l]) != 1 else ''}"]
                   if self.p_prof.get(l) else [])
            self.p_profile.addItem(f"{l}  ·  {', '.join(bits) or 'as the persona'}", l)
        rest = [l for l in self.p_launchers if l not in known]
        if rest:
            self.p_profile.insertSeparator(self.p_profile.count())
            for l in rest:
                self.p_profile.addItem(l, l)
        self.p_profile.setCurrentIndex(max(self.p_profile.findData(self.p_target), 0))
        self.p_switching = False

    def flush_rows(self):
        """What the fields say now, into the settings they show."""
        values = {k: r.get() for k, r in self.p_rows.items() if r.get() is not None}
        if not self.p_target:
            self.p_base = values
        elif values or self.p_target in self.p_prof:
            self.p_prof[self.p_target] = values

    def samplers_state(self):
        if not self.p_switching:
            self.flush_rows()
        return (tuple(sorted(self.p_base.items())),
                tuple(sorted((l, tuple(sorted(v.items()))) for l, v in self.p_prof.items() if v)),
                tuple(sorted(self.p_forgotten)))

    def show_rows(self, values):
        self.p_switching = True
        for key, row in self.p_rows.items():
            row.set(values.get(key))
        self.p_switching = False

    def switch_profile(self, *_):
        if self.p_switching or self.persona is None:
            return
        self.flush_rows()
        self.p_target = self.p_profile.currentData() or ""
        self.show_rows(self.p_prof.get(self.p_target, {}) if self.p_target else self.p_base)
        self.show_profile_info()
        self.persona_changed()

    def other_profile(self):
        """(machine, settings) of another machine for the shown model, or (None, {})."""
        if not self.p_target or self.persona is None:
            return None, {}
        for who, found in profiles.others(self.persona.id).items():
            # Here "omen-qwen36" is the Omen's "qwen36".
            names = [self.p_target] + ([self.p_target[len(who) + 1:]] if self.p_target.startswith(who + "-") else [])
            for name in names:
                if found.get(name, {}).get("samplers"):
                    return who, found[name]["samplers"]
        return None, {}

    def show_profile_info(self):
        if not self.p_target:
            text = ("Ticked ones win over the launcher's, on every model. Pick a model above to set "
                    "what is different on that one.")
        else:
            text = (f"On {self.p_target}: ticked ones win over the persona's own settings; unticked = "
                    f"as the persona's. Kept for {profiles.machine()} only.")
        who, theirs = self.other_profile()
        if who:
            text += f"\nOn {who}: " + ", ".join(f"{k} {v}" for k, v in sorted(theirs.items()))
        self.p_profile_info.setText(text)
        self.p_take_btn.setVisible(bool(who))
        self.p_forget_btn.setEnabled(bool(self.p_target) and
                                     (self.p_target in self.p_prof or self.p_target in self.p_used))

    def forget_profile(self):
        if not self.p_target:
            return
        self.p_prof.pop(self.p_target, None)
        self.p_used.pop(self.p_target, None)
        self.p_forgotten.add(self.p_target)
        self.show_rows({})
        self.fill_profiles()
        self.show_profile_info()
        self.persona_changed()

    def take_profile(self):
        who, theirs = self.other_profile()
        if who:
            self.show_rows(theirs)
            self.flush_rows()
            self.show_profile_info()
            self.persona_changed()

    def persona_dirty(self):
        return self.persona is not None and self.persona_state() != self.p_baseline

    def persona_changed(self, *_):
        self.show_picture()
        launcher = self.persona_launcher() if self.persona is not None else None
        for b in (self.p_start_btn, self.p_edit_btn, self.p_copy_btn, self.p_others_btn):
            b.setEnabled(launcher is not None)
        others = [o for o in self.p_others if not launcher or o != launcher.name]
        self.p_others_label.setText(", ".join(others) if others and launcher else
                                    "only this one" if launcher else "pick a model first")
        if launcher is None:
            self.p_launcher_info.setText("No model of its own yet: pick a launcher, then \"Own copy…\" "
                                         "if this persona should keep its settings whatever happens to that one.")
        else:
            others = [p.name for p in personas.load_all().values()
                      if p.launcher == launcher.name and p.id != self.persona.id]
            self.p_launcher_info.setText(
                (f"{launcher.remote_name} on {launcher.host}" if launcher.host else
                 f"{os.path.basename(launcher.model)}  ·  {shlex.join(launcher.args)}")
                + (f"\nShared with {', '.join(others)} — a change to its settings changes them too."
                   if others else ""))
        # File access needs a model whose chat template knows tools.
        tools = launcher_files.can_use_tools(launcher.model) if launcher is not None else None
        self.p_files_warn.setText(f"{launcher.name if launcher else ''}'s model can't use files (its chat template "
                                  "has no tools): this persona chats normally on it but won't touch any file.")
        self.p_files_warn.setVisible(tools is False and bool(self.p_files.currentData()))
        dirty = self.persona_dirty()
        self.p_revert_btn.setEnabled(dirty)
        self.p_save_btn.setEnabled(dirty)
        if hasattr(self, "persona_page"):
            self.tabs.setTabText(self.tabs.indexOf(self.persona_page), "Personas *" if dirty else "Personas")

    def fill_voices(self, wanted):
        """The voice menu: no voice, the fetched profiles, and the persona's own even if not fetched."""
        self.p_voice.blockSignals(True)
        self.p_voice.clear()
        self.p_voice.addItem("— no voice —", "")
        for v in self.p_voices or []:
            if not v.get("bad") or v["id"] == wanted:
                self.p_voice.addItem(voice.label(v), v["id"])
        if wanted and self.p_voice.findData(wanted) < 0:
            self.p_voice.addItem(wanted + ("  (not among the saved voices)" if self.p_voices is not None else ""),
                                 wanted)
        self.p_voice.setCurrentIndex(max(self.p_voice.findData(wanted), 0))
        self.p_voice.blockSignals(False)

    def fetch_voices(self):
        self.p_voices_btn.setEnabled(False)
        self.p_voices_btn.setText("Asking…")
        self.say("Asking the voice configurator for its voices (it starts first if it isn't running)…")
        self.voice_lookup = Lookup(lambda: voice.voices(say=lambda t: None), self)
        self.voice_lookup.done.connect(self.voices_fetched)
        self.voice_lookup.start()

    def voices_fetched(self, found, error):
        self.p_voices_btn.setEnabled(self.persona is not None)
        self.p_voices_btn.setText("Get the voices")
        if error:
            self.say(f"No voices: {error}")
            return
        self.p_voices = found
        self.say(f"{len(found)} saved voices." + ("" if found else " Save a profile in the voice configurator first."))
        self.fill_voices(self.p_voice.currentData() or "")
        self.persona_changed()

    def choose_other_models(self):
        launcher = self.persona_launcher()
        if self.persona is None or launcher is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{self.p_name.text().strip() or self.persona.name} also runs on")
        box = QVBoxLayout(dialog)
        note = QLabel(f"Starts with {launcher.name}. Tick the other models it may run on; the Chat tab "
                      "then offers them. Same prompt, knowledge and facts on each.")
        note.setWordWrap(True)
        box.addWidget(note)
        ticks = QListWidget()
        names = [n for n in self.p_launchers if n != launcher.name]
        names += [o for o in self.p_others if o not in self.p_launchers and o != launcher.name]
        for name in names:
            l = self.p_launchers.get(name)
            item = QListWidgetItem(l.label(f"{name}  —  {l.friendly}") if l else f"{name} (not on this machine)")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in self.p_others else Qt.CheckState.Unchecked)
            ticks.addItem(item)
        box.addWidget(ticks)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        box.addWidget(buttons)
        dialog.resize(520, 420)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        # Keep the order they had, new ones at the end.
        chosen = {ticks.item(i).data(Qt.ItemDataRole.UserRole) for i in range(ticks.count())
                  if ticks.item(i).checkState() == Qt.CheckState.Checked}
        self.p_others = tuple([o for o in self.p_others if o in chosen] +
                              sorted(chosen - set(self.p_others)))
        self.persona_changed()

    def save_persona(self):
        if self.persona is None:
            return False
        name, system, samplers, picture, launcher, files, others, voice_ = self.persona_state()
        if not name:
            QMessageBox.warning(self, "Can't save", "Name is empty.")
            return False
        if self.persona_stamp() != self.p_seen and QMessageBox.question(
                self, "Changed in the meantime",
                f"{self.persona.name}'s file was changed after this form was filled (by hand, "
                "or in another window). Saving puts what you see here over it.\n\n"
                "Save anyway? No = keep the file; Revert then shows what is in it now.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return False
        self.persona.name, self.persona.system = name, system
        self.persona.models = [launcher] + [o for o in others if o != launcher] if launcher else []
        self.persona.files = files
        self.persona.voice = voice_
        self.persona.samplers = dict(samplers[0])
        try:
            self.persona.write()
            profiles.save_samplers(self.persona.id, {l: v for l, v in self.p_prof.items() if v or l in self.p_used},
                                   self.p_forgotten)
            if picture:
                personas.set_picture(self.persona.id, picture)
            elif picture is not None:
                personas.remove_picture(self.persona.id)
        except OSError as e:
            QMessageBox.critical(self, "Can't save", str(e))
            return False
        self.say(f"Saved persona {self.persona.id}")
        self.p_seen = self.persona_stamp()
        self.p_picture = None
        self.p_forgotten = set()
        self.p_baseline = self.persona_state()
        self.reload_personas()
        return True

    def new_persona(self):
        if self.persona_dirty() and not self.resolve_unsaved(self.persona.name, self.save_persona):
            return
        name, ok = QInputDialog.getText(self, "New persona", "Name:")
        name = name.strip()
        if not ok or not name:
            return
        persona = personas.Persona(personas.new_id(name), name, "")
        try:
            persona.write()
        except OSError as e:
            QMessageBox.critical(self, "New persona", str(e))
            return
        self.persona = None
        self.reload_personas(select=persona.id)
        self.p_system.setFocus()

    def export_persona(self):
        if self.persona is None:
            return
        if self.persona_dirty() and not self.resolve_unsaved(self.persona.name, self.save_persona):
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Export {self.persona.name}")
        box = QVBoxLayout(dialog)
        note = QLabel("The persona (name, prompt, settings, models it runs on) and its picture always "
                      "go along. Also:")
        note.setWordWrap(True)
        box.addWidget(note)
        ticks = {}
        for part, _, label in personas.PARTS:
            ticks[part] = QCheckBox(label)
            ticks[part].setChecked(part == "knowledge")
            box.addWidget(ticks[part])
        warn = QLabel("Facts and chats are about you: leave them out when you give the persona away.")
        warn.setWordWrap(True)
        warn.setEnabled(False)
        box.addWidget(warn)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export…")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        box.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export persona",
                                              os.path.expanduser(f"~/{self.persona.id}.persona.zip"),
                                              "Persona (*.zip)")
        if not path:
            return
        if not path.endswith(".zip"):
            path += ".zip"
        try:
            n = personas.export(self.persona.id, path, [p for p, t in ticks.items() if t.isChecked()])
        except OSError as e:
            QMessageBox.critical(self, "Export persona", str(e))
            return
        self.say(f"Exported {self.persona.name} to {path} ({n} files)")

    def import_persona(self):
        if self.persona_dirty() and not self.resolve_unsaved(self.persona.name, self.save_persona):
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import persona", os.path.expanduser("~"),
                                              "Persona (*.zip);;All files (*)")
        if not path:
            return
        try:
            persona = personas.import_(path)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import persona", f"Can't import {os.path.basename(path)}: {e}")
            return
        here = [m for m in persona.models if launcher_files.get(m) is not None]
        self.say(f"Imported {persona.name} ({persona.id})" + (
            "" if here or not persona.models else
            f". None of its models ({', '.join(persona.models)}) is on this machine: pick one and Save."))
        self.persona = None
        self.reload_personas(select=persona.id)
        self.chat_tab.reload()

    def delete_persona(self):
        if self.persona is None:
            return
        answer = QMessageBox.question(
            self, "Delete persona",
            f"Delete \"{self.persona.name}\"?\n\n"
            f"Its folder (with its chats, knowledge and notes) is kept in {personas.TRASH_DIR}.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            personas.delete(self.persona.id)
        except OSError as e:
            QMessageBox.critical(self, "Delete persona", str(e))
            return
        self.say(f"Deleted persona {self.persona.id}")
        self.persona = None
        self.reload_personas()

    # --- tools ---------------------------------------------------------------

    def tools_tab(self):
        """One row per tool file: state, name, Start / Stop / Open, its extra buttons, Edit."""
        self.t_stamp, self.t_rows = None, []
        self.t_grid = QGridLayout()
        self.t_grid.setColumnStretch(1, 1)
        self.t_grid.setVerticalSpacing(10)
        inner = QWidget()
        box = QVBoxLayout(inner)
        note = QLabel("The other things of this machine, started and stopped from here. "
                      f"One text file each in {tools.TOOL_DIR}.")
        note.setWordWrap(True)
        box.addWidget(note)
        box.addLayout(self.t_grid)
        box.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        add = QPushButton("Add tool…")
        add.clicked.connect(lambda: self.edit_tool(None))
        bar = QHBoxLayout()
        bar.addWidget(add)
        bar.addStretch(1)
        page = QWidget()
        pbox = QVBoxLayout(page)
        pbox.setContentsMargins(0, 0, 0, 0)
        pbox.addWidget(scroll, 1)
        pbox.addLayout(bar)
        return page

    def build_tools(self):
        while self.t_grid.count():
            item = self.t_grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.t_rows = []
        for r, tool in enumerate(tools.find_tools()):
            state = QLabel()
            state.setMinimumWidth(90)
            text = QLabel(f"<b>{html(tool.title)}</b>"
                          + (f"<br>{html(tool.desc)}" if tool.desc else "")
                          + (f"<br><span style='color:#c0392b'>{html(tool.error)}</span>" if tool.error else ""))
            text.setTextFormat(Qt.TextFormat.RichText)
            text.setWordWrap(True)
            buttons = QHBoxLayout()
            start = stop = open_btn = None
            if tool.start and not tool.error:
                start = QPushButton("Start")
                start.clicked.connect(lambda _, t=tool: self.tool_action(t, "start"))
                stop = QPushButton("Stop")
                stop.clicked.connect(lambda _, t=tool: self.tool_action(t, "stop"))
                buttons.addWidget(start)
                buttons.addWidget(stop)
            if tool.address and not tool.error:
                open_btn = QPushButton("Open")
                open_btn.clicked.connect(lambda _, t=tool: self.open_tool(t))
                buttons.addWidget(open_btn)
            for label, command in ([] if tool.error else tool.buttons):
                b = QPushButton(label)
                b.setToolTip(command)
                b.clicked.connect(lambda _, t=tool, c=command, l=label: self.in_terminal(
                    c, f"{t.title}: {l}", t.cwd() if t.folder else None))
                buttons.addWidget(b)
            edit = QPushButton("Edit…")
            edit.clicked.connect(lambda _, n=tool.name: self.edit_tool(n))
            buttons.addWidget(edit)
            holder = QWidget()
            holder.setLayout(buttons)
            buttons.setContentsMargins(0, 0, 0, 0)
            self.t_grid.addWidget(state, r, 0)
            self.t_grid.addWidget(text, r, 1)
            self.t_grid.addWidget(holder, r, 2, Qt.AlignmentFlag.AlignRight)
            self.t_rows.append((tool, state, start, stop))

    def refresh_tools(self):
        stamp = tools.stamp()
        if stamp != self.t_stamp:
            # A tool was added, removed or edited (here or by hand).
            self.t_stamp = stamp
            self.build_tools()
        up = []
        for tool, label, start, stop in self.t_rows:
            state = None if tool.error else tool.state()
            mark, colour = {"running": ("● running", "#1e8449"), "starting": ("◌ starting", "#b9770e"),
                            "stopped": ("○ stopped", "")}.get(state, ("", ""))
            label.setText(mark)
            label.setStyleSheet(f"color: {colour}" if colour else "")
            if state in ("running", "starting"):
                up.append(tool.title)
            if start is not None and state is not None:
                start.setEnabled(state == "stopped")
                stop.setEnabled(state != "stopped")
        self.tools_label.setText("Tools running: " + ", ".join(up) if up else "")
        self.tools_label.setToolTip(self.tools_label.text())

    def in_terminal(self, script, title, folder=None):
        cmd = tools.terminal_command(script, folder)
        if cmd is None:
            self.say("No terminal set — Settings tab, \"Terminal\".")
            return
        self.say(f"--- {title} (in a terminal window) ---")
        try:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, start_new_session=True)
        except OSError as e:
            self.say(f"Can't open the terminal: {e}")

    def tool_action(self, tool, action):
        if tool.terminal:
            self.in_terminal(shlex.join(cove_cmd("tools", action, tool.name)), f"{action} {tool.title}")
        else:
            self.spawn_cove("tools", action, tool.name)
        QTimer.singleShot(1500, self.refresh_status)

    def open_tool(self, tool):
        if tool.state() == "stopped":
            self.say(f"{tool.title} is not running — press Start first.")
            return
        QDesktopServices.openUrl(QUrl(tool.address))

    def edit_tool(self, name):
        tool = tools.get(name) if name else None
        dialog = ToolDialog(self, tool)
        if dialog.exec() and dialog.saved:
            self.say(f"Removed the tool {tool.title} (kept as {dialog.saved})" if dialog.deleted
                     else f"Saved {tools.path_of(dialog.saved)}")
        self.refresh_tools()

    # --- top bar and settings ------------------------------------------------

    def build_services(self):
        """The top bar: which tools run, the keyboard buttons and Stop everything."""
        while self.services.count():
            item = self.services.takeAt(0)
            if item.widget() is not None and item.widget() is not self.tools_label:
                item.widget().deleteLater()
        self.services.addWidget(self.tools_label, 1)
        self.keyboard_buttons = {}
        if S.keyboards():
            self.services.addWidget(QLabel("Keyboard:"))
        for label, command in S.keyboards():
            b = QPushButton(label)
            b.setCheckable(True)
            b.setToolTip(command)
            b.clicked.connect(lambda _, label=label, command=command: self.set_keyboard(label, command))
            self.services.addWidget(b)
            self.keyboard_buttons[label] = b
        if self.keyboard_buttons:
            self.services.addSpacing(24)
            self.mark_keyboard(self.keyboard_pressed)
            self.ask_keyboard()
        stop_all = QPushButton("Stop everything")
        stop_all.setToolTip("The model and the tools marked for it")
        stop_all.clicked.connect(lambda: self.spawn_cove("stop", "all"))
        self.services.addWidget(stop_all)

    def open_persona_folder(self):
        if self.persona is None:
            self.say("Save the persona first; its folder is named after it.")
            return
        folder = personas.folder(self.persona.id)
        try:
            for sub in ("knowledge", "notes"):
                os.makedirs(os.path.join(folder, sub), exist_ok=True)
        except OSError as e:
            self.say(f"Can't make {folder}: {e}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def set_keyboard(self, label, command):
        self.keyboard_pressed = label
        self.mark_keyboard(label)
        self.spawn("sh", ["-c", command], f"Keyboard: {label}")

    def mark_keyboard(self, active):
        for label, button in self.keyboard_buttons.items():
            button.setChecked(label == active)

    def ask_keyboard(self):
        """Ask the machine which keyboard is active (it can be switched outside this window too)."""
        if not self.keyboard_buttons or not S.keyboard_now.strip() or self.keyboard_job is not None:
            return
        job = self.keyboard_job = QProcess(self)
        job.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)

        def done(*_):
            if self.keyboard_job is not job:
                return
            self.keyboard_job = None
            try:
                answer = bytes(job.readAllStandardOutput()).decode(errors="replace").lower()
                job.deleteLater()
            except RuntimeError:
                # The window is closing and took the job with it.
                return
            said = [label for label in self.keyboard_buttons if label.lower() in answer]
            if said:
                self.mark_keyboard(max(said, key=len))

        job.finished.connect(done)
        job.errorOccurred.connect(done)
        job.start("sh", ["-c", S.keyboard_now])

    def settings_tab(self):
        self.s_fields = {}
        self.s_baseline = None
        self.s_loading = False
        form = QWidget()
        outer = QVBoxLayout(form)
        self.s_note = QLabel()
        self.s_note.setWordWrap(True)
        outer.addWidget(self.s_note)

        def group(title):
            box = QGroupBox(title)
            grid = QGridLayout(box)
            grid.setColumnMinimumWidth(0, 200)
            grid.setColumnStretch(1, 1)
            outer.addWidget(box)
            return grid

        def row(grid, key, label, hint, widget, browse=None):
            r = grid.rowCount()
            grid.addWidget(QLabel(label), r, 0)
            grid.addWidget(widget, r, 1)
            if browse is not None:
                b = QPushButton("Browse…")
                b.clicked.connect(lambda: self.browse_setting(widget, label, browse))
                grid.addWidget(b, r, 2)
            note = QLabel(hint)
            note.setWordWrap(True)
            note.setEnabled(False)
            grid.addWidget(note, r + 1, 1, 1, 2)
            self.s_fields[key] = widget
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(self.settings_changed)
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(self.settings_changed)
            elif isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(self.settings_changed)
            elif isinstance(widget, QPlainTextEdit):
                widget.setFixedHeight(4 * widget.fontMetrics().lineSpacing() + 12)
                widget.textChanged.connect(self.settings_changed)
            else:
                widget.valueChanged.connect(self.settings_changed)

        def port():
            box = IntBox()
            box.setRange(1, 65535)
            box.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            return box

        grid = group("Programs")
        row(grid, "llama_server", "llama-server", "The program that runs the models (from llama.cpp).",
            QLineEdit(), "file")
        row(grid, "web_search", "Web search",
            "What the chat searches the web with (Web: on above the input): duckduckgo needs nothing "
            "else; or the address of a SearXNG of your own, e.g. http://localhost:8081.", QLineEdit())
        row(grid, "pictures", "Pictures",
            "What makes pictures in chats with Pictures on: the name of the tool (Tools tab) that is the "
            "image server (sd-server), started when needed; or the address of one running elsewhere.", QLineEdit())
        row(grid, "voice", "Voice",
            "What reads answers aloud for personas with a voice: the name of the tool (Tools tab) that is "
            "the voice configurator, started when needed; or the address of one running elsewhere.", QLineEdit())

        grid = group("Models")
        row(grid, "models_dir", "Model folder",
            "Where the .gguf files are and where downloads go. Launchers name their file relative to it.",
            QLineEdit(), "folder")
        row(grid, "scan_dirs", "Also look in",
            "Other folders \"Add model\" lists model files from, separated by commas.", QLineEdit())

        grid = group("GPU")
        self.s_gpu = QLabel()
        grid.addWidget(QLabel("Found"), 0, 0)
        grid.addWidget(self.s_gpu, 0, 1, 1, 2)
        budget = FloatBox()
        budget.setLocale(QLocale(QLocale.Language.C))
        budget.setDecimals(1)
        budget.setRange(0.0, 1024.0)
        budget.setSingleStep(0.5)
        budget.setSuffix(" GB")
        budget.setSpecialValueText("as found")
        budget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row(grid, "gpu_budget_gb", "Count with",
            "The GPU memory the estimates use. \"as found\" (0) = what was detected above.", budget)
        layers = Combo()
        layers.setEditable(True)
        layers.addItems(["all", "auto"])
        layers.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row(grid, "gpu_layers", "On the GPU by default",
            "For launchers that don't say: all = the whole model on the GPU (it must fit), "
            "auto = as much as fits and the rest from normal RAM (slower), or a number of layers.", layers)

        grid = group("Addresses")
        row(grid, "llama_port", "Model port", "Where llama-server answers. The chat talks to it there.", port())
        host = Combo()
        host.addItem("this machine only", "127.0.0.1")
        host.addItem("everyone on the network", "0.0.0.0")
        host.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row(grid, "host", "Model reachable from", "Who can talk to the loaded model directly.", host)

        grid = group("Tools")
        row(grid, "terminal", "Terminal",
            "For tools that ask something (a password, yes/no): the terminal program with the option "
            "that takes a command after it, e.g. \"xfce4-terminal -x\", \"konsole -e\", \"xterm -e\".",
            QLineEdit())

        grid = group("Keyboard")
        row(grid, "keyboard", "Keyboard buttons",
            "Buttons in the top bar that switch the keyboard, one per line: Label | shell command. "
            "For example: Game | setxkbmap -layout us -variant game", QPlainTextEdit())
        row(grid, "keyboard_now", "Which one is active",
            "Optional: a command that says which keyboard is active. The button whose label is in "
            "its answer is marked. Without it, the last one pressed is marked.", QLineEdit())

        grid = group("File access for the AIs")
        row(grid, "access_read", "May read",
            "Folders that personas with file access may read, separated by commas. "
            "Which persona has access is set on the Personas tab (Files).", QLineEdit())
        row(grid, "access_write", "May also write in",
            "Folders they may create and change text files in. Every single change is shown to you "
            "in the chat and only happens after your OK; the old version is kept.", QLineEdit())
        row(grid, "access_system", "System files",
            "Read only: /etc, /var/log, /proc, /sys, /usr, /boot and the like, so a model can look "
            "at the machine and explain. They can never be changed, and no commands are run.",
            QCheckBox("May read system files"))
        row(grid, "access_never", "Never read",
            "On top of what is always off limits (keys, password stores, browser profiles): more "
            "folders, or name patterns such as *.sql, separated by commas.", QLineEdit())

        row(grid, "cove_folder", "Your AIcove folder",
            "Everything that is yours: every persona's folder (its file, picture, chats, \"knowledge\" "
            "you give it, \"notes\" it writes for later), documents and work. Copy it to back up or "
            "to move to another machine. A change counts from the next start and moves nothing.",
            QLineEdit(), "folder")

        grid = group("Passwords")
        row(grid, "password_store", "Password store",
            "The folder with the encrypted password files (Passwords tab). ~/.password-store is also "
            "where the \"pass\" program looks.", QLineEdit(), "folder")

        grid = group("Notes")
        row(grid, "notes_file", "Notes file",
            "Optional: a text file with a list of your launchers. \"Add model\" adds a line to it.",
            QLineEdit(), "file")

        self.s_check = QLabel()
        self.s_check.setWordWrap(True)
        self.s_check.setTextFormat(Qt.TextFormat.RichText)
        outer.addWidget(self.s_check)
        outer.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form)

        find_btn = QPushButton("Find again")
        find_btn.setToolTip("Look for llama-server, the model folder, the GPU and a terminal "
                            "on this machine")
        find_btn.clicked.connect(self.find_settings)
        check_btn = QPushButton("Check")
        check_btn.setToolTip("Try everything these settings point to")
        check_btn.clicked.connect(self.check_settings)
        self.s_revert_btn = QPushButton("Revert")
        self.s_revert_btn.clicked.connect(self.load_settings)
        self.s_save_btn = QPushButton("Save")
        self.s_save_btn.clicked.connect(self.save_settings)
        buttons = QHBoxLayout()
        buttons.addWidget(find_btn)
        buttons.addWidget(check_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.s_revert_btn)
        buttons.addWidget(self.s_save_btn)
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(scroll, 1)
        box.addLayout(buttons)
        self.load_settings()
        return page

    def browse_setting(self, field, label, kind):
        start = os.path.expanduser(field.text().split(",")[0].strip()) or os.path.expanduser("~")
        if kind == "folder":
            path = QFileDialog.getExistingDirectory(self, label, start)
        else:
            path, _ = QFileDialog.getOpenFileName(self, label, start)
        if path:
            field.setText(path)

    def settings_form(self):
        """The settings as the form has them."""
        values = {}
        for key, w in self.s_fields.items():
            if isinstance(w, QLineEdit):
                values[key] = w.text().strip()
            elif isinstance(w, QCheckBox):
                values[key] = w.isChecked()
            elif isinstance(w, QComboBox):
                values[key] = w.currentText().strip() if w.isEditable() else w.currentData()
            elif isinstance(w, QPlainTextEdit):
                values[key] = [line.strip() for line in w.toPlainText().split("\n") if line.strip()]
            else:
                values[key] = w.value()
        for key in ("scan_dirs", "access_read", "access_write", "access_never"):
            values[key] = [d.strip() for d in values[key].split(",") if d.strip()]
        return values

    def fill_settings(self, values):
        self.s_loading = True
        for key, w in self.s_fields.items():
            value = values[key]
            if isinstance(w, QLineEdit):
                w.setText(", ".join(value) if isinstance(value, list) else value)
                w.setCursorPosition(0)
            elif isinstance(w, QCheckBox):
                w.setChecked(bool(value))
            elif isinstance(w, QPlainTextEdit):
                w.setPlainText("\n".join(value))
            elif isinstance(w, QComboBox) and w.isEditable():
                w.setCurrentText(value)
            elif isinstance(w, QComboBox):
                if w.findData(value) < 0:
                    w.addItem(value, value)
                w.setCurrentIndex(w.findData(value))
            else:
                w.setValue(value)
        self.s_loading = False
        self.settings_changed()

    def load_settings(self):
        """Fill the form from the saved settings (or, before the first Save, from what was found)."""
        name, memory = settings.gpu()
        self.s_gpu.setText(f"{name}, {memory / 1e9:.1f} GB" if name else
                           "none recognised (looked for NVIDIA and AMD) — set the memory by hand below")
        self.fill_settings(S.values())
        self.s_baseline = self.settings_form() if S.exists else None
        self.settings_changed()

    def settings_dirty(self):
        return S.exists and self.s_baseline is not None and self.settings_form() != self.s_baseline

    def settings_changed(self, *_):
        if self.s_loading:
            return
        dirty = self.settings_dirty()
        self.s_revert_btn.setEnabled(dirty)
        self.s_save_btn.setEnabled(dirty or not S.exists)
        if S.exists:
            self.s_note.setText(f"What AIcove needs to know about this machine. Kept in {CONFIG_FILE}.")
        else:
            self.s_note.setText("<b>First start on this machine.</b> This is what AIcove found here. "
                                "Correct what is wrong, press Check to try it, then Save.")
        if hasattr(self, "settings_page"):
            self.tabs.setTabText(self.tabs.indexOf(self.settings_page),
                                 "Settings *" if dirty or not S.exists else "Settings")

    def find_settings(self):
        found = settings.detect()
        settings.gpu(fresh=True)
        values = self.settings_form()
        for key in ("llama_server", "models_dir", "terminal"):
            if found[key]:
                values[key] = found[key]
        self.fill_settings(values)
        name, memory = settings.gpu()
        self.s_gpu.setText(f"{name}, {memory / 1e9:.1f} GB" if name else
                           "none recognised (looked for NVIDIA and AMD) — set the memory by hand below")

    def checked_settings(self):
        """The form's values if they can be used, else None after saying why not."""
        values = self.settings_form()
        if not launcher_files.LAYERS.fullmatch(values["gpu_layers"]):
            QMessageBox.warning(self, "Settings", "\"On the GPU by default\" must be all, auto or a number.")
            return None
        return values

    def check_settings(self):
        values = self.checked_settings()
        if values is None:
            return
        # Check looks at what is in the form, saved or not.
        saved = S.values()
        S.set(values)
        try:
            lines = settings.check()
        finally:
            S.set(saved)
        colour = {True: "#1e8449", False: "#c0392b", None: "palette(mid)"}
        mark = {True: "✓", False: "✗", None: "·"}
        self.s_check.setText("<br>".join(
            f"<span style='color:{colour[ok]}'>{mark[ok]}</span> "
            + html(text) for ok, text in lines))

    def save_settings(self):
        values = self.checked_settings()
        if values is None:
            return False
        S.set(values)
        try:
            S.save()
        except OSError as e:
            QMessageBox.critical(self, "Can't save", str(e))
            return False
        self.say(f"Settings saved to {CONFIG_FILE}")
        self.load_settings()
        # Everything that showed something from the settings.
        self.budget = settings.gpu_budget()
        self.show_fixed()
        self.build_services()
        self.refresh_status()
        self.pw_tab.names = None
        self.pw_tab.refresh()
        self.docs_tab.refresh()
        self.fill_models()
        if self.dirty():
            self.changed()
        else:
            current = self.launcher.name if self.launcher is not None else None
            self.launcher = None
            self.reload_list(select=current)
        return True

    def closeEvent(self, event):
        if self.dirty() and not self.resolve_unsaved():
            event.ignore()
        elif self.persona_dirty() and not self.resolve_unsaved(self.persona.name, self.save_persona):
            event.ignore()
        elif self.settings_dirty() and not self.resolve_unsaved("Settings", self.save_settings):
            event.ignore()
        elif not self.pw_tab.leaving():
            event.ignore()
        elif not self.writing_tab.leave():
            event.ignore()
        else:
            if self.chat_tab.measuring is not None:
                self.chat_tab.measuring.wait(3000)   # a thread still running when the window goes ends the program badly
            if sync.synced(settings.COVE_DIR):
                # The last changes go out on their own, so closing doesn't wait for vlgr.
                subprocess.Popen(cove_cmd("sync"), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("aicove")
    app.setDesktopFileName("aicove")
    app.setWindowIcon(QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "aicove.svg")))
    # One AIcove at a time: two would both start and stop llama-server. A second start only
    # brings the first one's window to the front.
    name = f"aicove-{os.getuid()}-{zlib.crc32(settings.STATE_DIR.encode()):08x}"
    other = QLocalSocket()
    other.connectToServer(name)
    if other.waitForConnected(500):
        other.write(b"show")
        other.waitForBytesWritten(500)
        print("AIcove is already running; its window is brought to the front.", file=sys.stderr)
        return 0
    QLocalServer.removeServer(name)  # left behind by one that crashed
    server_ = QLocalServer()
    server_.listen(name)
    win = Window()

    def come_forward():
        while server_.hasPendingConnections():
            server_.nextPendingConnection()
        win.setWindowState(win.windowState() & ~Qt.WindowState.WindowMinimized)
        win.show()
        win.raise_()
        win.activateWindow()
    server_.newConnection.connect(come_forward)

    # PyQt ends the whole program on an error nobody caught. Write it down and keep going.
    def caught(kind, error, trace):
        text = "".join(traceback.format_exception(kind, error, trace))
        sys.__stderr__.write(text)
        try:
            os.makedirs(settings.STATE_DIR, exist_ok=True)
            with open(os.path.join(settings.STATE_DIR, "errors.log"), "a", encoding="utf-8") as f:
                f.write(time.strftime("--- %Y-%m-%d %H:%M:%S\n") + text)
        except OSError:
            pass
        try:
            win.say(f"Something went wrong inside AIcove (written to {settings.STATE_DIR}/errors.log):\n"
                    + text.strip().splitlines()[-1])
        except Exception:  # noqa: BLE001
            pass
    sys.excepthook = caught
    threading.excepthook = lambda a: caught(a.exc_type, a.exc_value, a.exc_traceback)
    win.show()
    return app.exec()
