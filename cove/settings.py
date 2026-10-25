# Where everything is on this machine. Kept in ~/.config/aicove/config.toml and edited on the
# Settings tab; by hand works too. Nothing else in AIcove knows a path of its own.

import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import tomllib


def env(name):
    return os.environ.get("AICOVE_" + name)


CONFIG_DIR = env("CONFIG") or os.path.expanduser("~/.config/aicove")
STATE_DIR = env("STATE") or os.path.expanduser("~/.cache/aicove")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.toml")
LAUNCHER_DIR = os.path.join(CONFIG_DIR, "launchers")
BACKUP_DIR = os.path.join(STATE_DIR, "launcher-backups")
JOB_DIR = os.path.join(STATE_DIR, "downloads")
# The aicove command of this copy of the program, for starting parts of ourselves.
MAIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "aicove")

# Every setting with its default and the comment it gets in config.toml.
SETTINGS = [
    ("cove_folder", "~/AIcove", "Your own folder: the personas with their chats, facts, knowledge and notes,\n"
                                "# documents for all of them, and work, where they may write. Copy this one folder\n"
                                "# to back up or to move to another machine; the models stay on each machine."),
    ("llama_server", "", "The llama-server program."),
    ("models_dir", "", "Folder with the model files (.gguf). Launchers name their file relative to it."),
    ("scan_dirs", ["~/Downloads"], "Other folders \"Add model\" looks in for model files."),
    ("gpu_layers", "auto", "How much of a model goes on the GPU when a launcher doesn't say:\n"
                           "# \"all\", \"auto\" (as much as fits) or a number of layers."),
    ("gpu_budget_gb", 0.0, "GPU memory AIcove counts with, in GB. 0 = what it detects."),
    ("host", "127.0.0.1", "Who can reach the model: 127.0.0.1 = this machine only, 0.0.0.0 = the network."),
    ("llama_port", 8080, "Port of llama-server."),
    ("web_search", "duckduckgo", "What the chat searches the web with: \"duckduckgo\" (nothing to install) or the\n"
                                 "# address of a SearXNG of your own, e.g. \"http://localhost:8081\"."),
    ("voice", "voice", "What reads answers aloud for personas with a voice: the tool (Tools tab) that is\n"
                       "# the voice configurator, started when needed; or the address of one, e.g. \"http://127.0.0.1:7871\"."),
    ("pictures", "pictures", "What makes pictures in chats with Pictures on: the tool (Tools tab) that is the\n"
                             "# image server (sd-server), started when needed; or the address of one."),
    ("terminal", "", "Terminal window for tools that ask something (sudo, yes/no): the command that\n"
                     "# takes the program to run after it, e.g. \"xfce4-terminal -x\" or \"konsole -e\"."),
    ("keyboard", [], "Keyboard buttons in the top bar, each \"Label | command\", e.g.\n"
                     "# [\"English | setxkbmap us\", \"German | setxkbmap de\"]."),
    ("keyboard_now", "", "Optional: a command that says which keyboard is active. The button whose\n"
                         "# label is in its answer is marked."),
    ("password_store", "~/.password-store", "Folder of the password store (Passwords tab): one GnuPG-encrypted\n"
                                            "# file per entry, the layout of the \"pass\" program."),
    ("access_read", [], "File access for the AIs (personas that have it): folders they may read."),
    ("access_write", [], "Folders they may also write text files in. Every change is shown first."),
    ("access_system", False, "true = they may read system files (/etc, /var/log, /proc, ...). Never change them."),
    ("access_never", [], "More that they may never read: folders, or name patterns like \"*.sql\"."),
    ("machine", "", "This machine's name in the AIcove folder (each persona's settings per model are kept\n"
                    "# per machine). Empty = the hostname."),
    ("notes_file", "", "A text file with a list of your launchers; \"Add model\" adds a line to it."),
]
DEFAULTS = {key: default for key, default, _ in SETTINGS}


def toml_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


class Settings:
    def __init__(self):
        self.exists = False
        self.set(DEFAULTS)

    def set(self, values):
        """Take over the values that have the right kind; anything else keeps what it had."""
        for key, default in DEFAULTS.items():
            value = values.get(key, getattr(self, key, default))
            if isinstance(default, float) and isinstance(value, int) and not isinstance(value, bool):
                value = float(value)
            if type(value) is not type(default):
                value = getattr(self, key, default)
            setattr(self, key, list(value) if isinstance(value, list) else value)

    def values(self):
        return {key: getattr(self, key) for key in DEFAULTS}

    def load(self):
        """Read config.toml; without one, start from what can be found on this machine."""
        try:
            with open(CONFIG_FILE, "rb") as f:
                data = tomllib.load(f)
            self.exists = True
        except FileNotFoundError:
            data, self.exists = detect(), False
        except (OSError, tomllib.TOMLDecodeError) as e:
            print(f"Can't read {CONFIG_FILE} ({e}) — using what was found on this machine.", file=sys.stderr)
            data, self.exists = detect(), False
        self.set(DEFAULTS)
        self.set(data)
        if "terminal" not in data:
            # A config.toml from before there were tools.
            self.terminal = find_terminal()

    def save(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        text = "# AIcove settings for this machine. Edited on the Settings tab; by hand works too.\n"
        for key, _, comment in SETTINGS:
            text += f"\n# {comment}\n{key} = {toml_value(getattr(self, key))}\n"
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(text)
        self.exists = True

    def llama_url(self):
        return f"http://localhost:{self.llama_port}"

    def keyboards(self):
        """The keyboard buttons: [(label, command)]."""
        found = []
        for line in self.keyboard:
            label, bar, command = line.partition("|")
            if bar and label.strip() and command.strip():
                found.append((label.strip(), command.strip()))
        return found

    def all_scan_dirs(self):
        """The model folder and the other folders to look in, without repeats."""
        dirs = []
        for d in [self.models_dir] + self.scan_dirs:
            d = os.path.expanduser(d)
            if d and d not in dirs:
                dirs.append(d)
        return dirs


def cove_cmd(*args):
    """The command line that runs one of our own subcommands."""
    return [sys.executable, MAIN, *args]


# --- finding things ----------------------------------------------------------

def find_program(name, patterns):
    hit = shutil.which(name)
    if hit:
        return hit
    for pattern in patterns:
        for path in sorted(glob.glob(os.path.expanduser(pattern))):
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    return ""


def find_models_dir():
    places = [os.path.expanduser(p) for p in (
        "~/models", "~/Models", "~/ai/models", "~/AI/models", "~/llm/models",
        "~/.local/share/models")]
    with_models = [p for p in places if glob.glob(os.path.join(p, "*.gguf"))]
    # A models folder inside some other folder of the home directory (~/ai-stuff/models).
    with_models += sorted({os.path.dirname(f) for f in glob.glob(os.path.expanduser("~/*/models/*.gguf"))}
                          - set(with_models))
    there = [p for p in places if os.path.isdir(p)]
    return (with_models or there or [os.path.expanduser("~/models")])[0]


TERMINALS = ["xfce4-terminal -x", "konsole -e", "gnome-terminal --", "alacritty -e", "kitty", "foot", "xterm -e"]


def find_terminal():
    return next((t for t in TERMINALS if shutil.which(t.split()[0])), "")


def detect():
    """A first guess at every setting from what is on this machine."""
    found = dict(DEFAULTS)
    found["llama_server"] = find_program("llama-server", [
        "~/src/llama.cpp/build/bin/llama-server", "~/llama.cpp/build/bin/llama-server",
        "~/*/llama.cpp/build/bin/llama-server", "/opt/llama.cpp/build/bin/llama-server",
        "/opt/llama.cpp/bin/llama-server"])
    found["models_dir"] = find_models_dir()
    found["terminal"] = find_terminal()
    return found


_gpu = None


def gpu(fresh=False):
    """(name, bytes it can use) of the graphics chip models run on, or (None, None)."""
    global _gpu
    if _gpu is not None and not fresh:
        return _gpu
    _gpu = (None, None)
    # A separate NVIDIA card wins over the chip built into the processor.
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        cards = [line.rsplit(",", 1) for line in out.splitlines() if "," in line]
        if cards:
            name, mib = max(cards, key=lambda c: int(c[1]))
            _gpu = (name.strip(), int(mib) * 1024 * 1024)
            return _gpu
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    # amdgpu: its own memory + what it may borrow from RAM.
    try:
        for card in sorted(os.listdir("/sys/class/drm")):
            base = f"/sys/class/drm/{card}/device/"
            if card.startswith("card") and card[4:].isdigit() and os.path.exists(base + "mem_info_gtt_total"):
                with open(base + "mem_info_vram_total") as v, open(base + "mem_info_gtt_total") as g:
                    _gpu = ("AMD graphics (shares the machine's RAM)", int(v.read()) + int(g.read()))
                    return _gpu
    except (OSError, ValueError):
        pass
    return _gpu


def gpu_budget():
    """Bytes of GPU memory to count with: the setting, else what was detected, else None."""
    if S.gpu_budget_gb > 0:
        return S.gpu_budget_gb * 1e9
    return gpu()[1]


def ram_total():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def port_open(port, host="127.0.0.1"):
    """Whether something listens on the port (of this machine, unless a host is named)."""
    try:
        with socket.create_connection((host, int(port)), timeout=0.5):
            return True
    except (OSError, ValueError):
        return False


def check():
    """Look at everything the settings point to: [(True good / False bad / None just so you know, text)]."""
    lines = []
    prog = S.llama_server
    if not prog:
        lines.append((False, "llama-server: not set. Without it no model can be started."))
    elif not (os.path.isfile(prog) and os.access(prog, os.X_OK)):
        lines.append((False, f"llama-server: {prog} is not a program that can be run."))
    else:
        try:
            out = subprocess.run([prog, "--version"], capture_output=True, text=True, timeout=20)
            version = next((l.strip() for l in (out.stdout + out.stderr).splitlines() if "version" in l), "")
            lines.append((True, f"llama-server: runs ({version or 'no version shown'})"))
        except (OSError, subprocess.SubprocessError) as e:
            lines.append((False, f"llama-server: {prog} doesn't start ({e})"))
    folder = os.path.expanduser(S.models_dir)
    if not folder:
        lines.append((False, "Model folder: not set."))
    elif not os.path.isdir(folder):
        lines.append((False, f"Model folder: {folder} doesn't exist yet (create it, or pick another)."))
    else:
        count = len(glob.glob(os.path.join(folder, "*.gguf")))
        free = shutil.disk_usage(folder).free / 1e9
        ok = os.access(folder, os.W_OK)
        lines.append((ok, f"Model folder: {folder}, {count} .gguf files, {free:.0f} GB free"
                      + ("" if ok else " — but you can't write there, so no downloads")))
    name, memory = gpu(fresh=True)
    if name is None:
        lines.append((None if S.gpu_budget_gb > 0 else False,
                      "GPU: none recognised (looked for NVIDIA and AMD)"
                      + (f"; counting with the {S.gpu_budget_gb:g} GB set here." if S.gpu_budget_gb > 0
                         else " — set the memory by hand to get estimates.")))
    else:
        lines.append((True, f"GPU: {name}, {memory / 1e9:.1f} GB"
                      + (f"; counting with the {S.gpu_budget_gb:g} GB set here" if S.gpu_budget_gb > 0 else "")))
    ram = ram_total()
    if ram:
        lines.append((None, f"RAM: {ram / 1e9:.0f} GB"))
    lines.append((True, f"Model port {S.llama_port}: a model is loaded") if port_open(S.llama_port)
                 else (None, f"Model port {S.llama_port}: free, no model loaded"))
    from . import tools
    found = tools.find_tools()
    broken = [t for t in found if t.error]
    for t in broken:
        lines.append((False, f"Tool {t.name}: {t.error}"))
    if found:
        lines.append((None, f"Tools: {len(found)} in {tools.TOOL_DIR}"
                      + (f", {len(broken)} of them can't be used" if broken else "")))
    wants = [t.title for t in found if t.terminal or t.buttons]
    prog = S.terminal.split()[0] if S.terminal.strip() else ""
    if prog and not shutil.which(prog):
        lines.append((False, f"Terminal: {prog} is not a program that can be run."))
    elif wants and not prog:
        lines.append((False, f"Terminal: not set, but needed by {', '.join(wants)}."))
    elif prog:
        lines.append((True, f"Terminal: {prog} found"))
    from . import passwords
    if not passwords.gpg_program():
        lines.append((None, "Passwords: gpg is not installed, so the Passwords tab can't be used"))
    elif not passwords.ready():
        lines.append((None, f"Passwords: no store in {passwords.store()} yet (Passwords tab sets one up)"))
    else:
        key = passwords.key_name()
        lines.append((key is not None, f"Passwords: {len(passwords.entries())} entries in {passwords.store()}"
                      + (f", key: {key}" if key else " — but its key is not on this machine")))
    lines.append((True, "curl: found (used for downloads)") if shutil.which("curl")
                 else (False, "curl: not found — \"Add model\" can't download without it"))
    if S.notes_file and not os.path.isfile(os.path.expanduser(S.notes_file)):
        lines.append((False, f"Notes file: {S.notes_file} doesn't exist."))
    return lines


S = Settings()
S.load()

# The AIcove folder (setting cove_folder; AICOVE_FOLDER for tests). A change counts from the
# next start.
COVE_DIR = os.path.expanduser(env("FOLDER") or S.cove_folder.strip() or DEFAULTS["cove_folder"])
PERSONA_DIR = os.path.join(COVE_DIR, "personas")
