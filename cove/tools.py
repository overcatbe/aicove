# Tools: the other things of this machine that are started and stopped from AIcove (voice
# tools, a local web server, a browser profile, ...). One small text file per tool in
# ~/.config/aicove/tools/<name>.txt:
#
#   name: Voice configurator                what the window calls it
#   description: VoxCPM2, pick and tune voices
#   group: Voice                            optional: the heading it is listed under ("Other" without one)
#   start: tts-env/bin/python voxcpm_config_ui.py    a shell command
#   stop:                                   a shell command; without one AIcove keeps the program
#                                           running itself and ends it on Stop
#   folder: ~/voice-ui                      optional: where the commands run
#   address: http://localhost:7871          optional: the Open button, and how "running" is seen
#   terminal: yes                           optional: start and stop in a terminal window (sudo, questions)
#   stop_everything: yes                    optional: "Stop everything" stops it too
#   not_with: voice-quick                   optional: tools (file names) that are stopped before this one starts
#   not_with_model: yes                     optional: it and a loaded model are never on together; starting
#                                           one stops the other (for tools that need the GPU memory)
#   button: Logs | mysite logs     optional, any number: an extra button; runs in a terminal window
#
# The window edits these; editing them by hand keeps working.

import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.parse

from .launchers import NAME
from .settings import BACKUP_DIR, CONFIG_DIR, S, STATE_DIR, port_open

TOOL_DIR = os.path.join(CONFIG_DIR, "tools")
RUN_DIR = os.path.join(STATE_DIR, "tools")
YES = ("yes", "true", "on", "1")


def path_of(name):
    return os.path.join(TOOL_DIR, name + ".txt")


def text_of(v):
    """What goes into a tool file. v has the fields of a Tool (title, desc, start, ...)."""
    lines = [f"name: {v['title']}"]
    for key, field in (("description", "desc"), ("group", "group"), ("start", "start"), ("stop", "stop"),
                       ("folder", "folder"), ("address", "address")):
        if v.get(field):
            lines.append(f"{key}: {v[field]}")
    if v.get("terminal"):
        lines.append("terminal: yes")
    if v.get("with_all"):
        lines.append("stop_everything: yes")
    if v.get("not_with"):
        lines.append("not_with: " + ", ".join(v["not_with"]))
    if v.get("not_with_model"):
        lines.append("not_with_model: yes")
    lines += [f"button: {label} | {command}" for label, command in v.get("buttons", [])]
    return "\n".join(lines) + "\n"


class Tool:
    """One tool file. error is set when it can't be used."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)[:-len(".txt")]
        self.error = None
        self.buttons = []
        fields = {}
        with open(path, encoding="utf-8") as f:
            for line in f.read().split("\n"):
                key, sep, value = line.partition(":")
                key, value = key.strip(), value.strip()
                if not sep or line.startswith("#"):
                    continue
                if key == "button":
                    label, bar, command = value.partition("|")
                    if bar and label.strip() and command.strip():
                        self.buttons.append((label.strip(), command.strip()))
                elif key not in fields:
                    fields[key] = value
        self.title = fields.get("name") or self.name
        self.desc = fields.get("description", "")
        self.group = fields.get("group", "")
        self.start = fields.get("start", "")
        self.stop = fields.get("stop", "")
        self.folder = fields.get("folder", "")
        self.address = fields.get("address", "")
        self.terminal = fields.get("terminal", "").lower() in YES
        self.with_all = fields.get("stop_everything", "").lower() in YES
        self.not_with = [n.strip() for n in fields.get("not_with", "").split(",") if n.strip()]
        self.not_with_model = fields.get("not_with_model", "").lower() in YES
        if not (self.start or self.address or self.buttons):
            self.error = "no \"start:\", \"address:\" or \"button:\" line"
        elif self.stop and not self.start:
            self.error = "a \"stop:\" line but no \"start:\""
        elif self.folder and not os.path.isdir(self.cwd()):
            self.error = f"the folder {self.folder} doesn't exist"

    def values(self):
        return {k: getattr(self, k) for k in ("title", "desc", "group", "start", "stop", "folder", "address",
                                              "terminal", "with_all", "not_with", "not_with_model",
                                              "buttons")}

    def cwd(self):
        return os.path.expanduser(self.folder) if self.folder else os.path.expanduser("~")

    def kept(self):
        """Whether AIcove keeps the program running itself (a start command and no stop command)."""
        return bool(self.start) and not self.stop

    def port(self):
        """The port of the address, or None."""
        return self.where()[1]

    def where(self):
        """(host, port) of the address, or (None, None)."""
        if not self.address:
            return None, None
        try:
            url = urllib.parse.urlsplit(self.address if "//" in self.address else "//" + self.address)
            return url.hostname or "localhost", url.port or {"https": 443}.get(url.scheme, 80)
        except ValueError:
            return None, None

    def answers(self):
        """Whether something answers at the address. By its host name: "localhost" can be
        the IPv6 one only (PHP's built-in server does that)."""
        host, port = self.where()
        return bool(port) and port_open(port, host)

    def pidfile(self):
        return os.path.join(RUN_DIR, self.name + ".pid")

    def logfile(self):
        return os.path.join(RUN_DIR, self.name + ".log")

    def pid(self):
        """Pid of the program AIcove started for this tool, if it still runs."""
        try:
            with open(self.pidfile()) as f:
                pid = int(f.read().strip())
            # We start it as the leader of its own session; a stranger that got the number isn't one.
            if os.getsid(pid) == pid:
                return pid
        except (OSError, ValueError):
            pass
        return None

    def state(self):
        """"running", "starting", "stopped", or None when there is no way to tell."""
        port = self.port()
        if self.kept() and self.pid() is not None:
            return "starting" if port and not self.answers() else "running"
        if port:
            return "running" if self.answers() else "stopped"
        return "stopped" if self.kept() else None


def write(name, values, new=False):
    """Write a tool file; new=True raises FileExistsError if the name is taken."""
    os.makedirs(TOOL_DIR, exist_ok=True)
    if not new and os.path.isfile(path_of(name)):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        shutil.copy2(path_of(name), os.path.join(BACKUP_DIR, f"tool-{name}.{time.strftime('%Y%m%d-%H%M%S')}.txt"))
    with open(path_of(name), "x" if new else "w", encoding="utf-8") as f:
        f.write(text_of(values))


def retire(name):
    """Move a tool file into the backup folder; returns where it went."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dest = os.path.join(BACKUP_DIR, f"tool-{name}.{time.strftime('%Y%m%d-%H%M%S')}.txt")
    shutil.move(path_of(name), dest)
    return dest


def find_tools():
    try:
        names = sorted(os.listdir(TOOL_DIR))
    except OSError:
        return []
    found = []
    for name in names:
        if name.endswith(".txt") and NAME.fullmatch(name[:-len(".txt")]):
            try:
                found.append(Tool(os.path.join(TOOL_DIR, name)))
            except (OSError, UnicodeDecodeError):
                continue
    return sorted(found, key=lambda t: t.title.lower())


OTHER = "Other"


def grouped(found=None):
    """The tools as [(group, [tools])]: groups by name, the ones without a group last as "Other"."""
    found = find_tools() if found is None else found
    groups = {}
    for tool in found:
        groups.setdefault(tool.group or OTHER, []).append(tool)
    return sorted(groups.items(), key=lambda g: (g[0] == OTHER, g[0].lower()))


def get(name):
    """The tool of that name, or None."""
    if NAME.fullmatch(name) and os.path.isfile(path_of(name)):
        try:
            return Tool(path_of(name))
        except (OSError, UnicodeDecodeError):
            pass
    return None


def stamp():
    """Changes whenever a tool file is added, removed or edited."""
    try:
        return sorted((n, os.stat(os.path.join(TOOL_DIR, n)).st_mtime_ns) for n in os.listdir(TOOL_DIR))
    except OSError:
        return []


# --- terminal windows --------------------------------------------------------

def terminal_command(script, folder=None):
    """The command line that runs a shell command in a terminal window which stays until Enter,
    or None when the settings name no terminal."""
    try:
        prefix = shlex.split(S.terminal)
    except ValueError:
        prefix = []
    if not prefix:
        return None
    if folder:
        script = f"cd {shlex.quote(folder)} && {{ {script}\n}}"
    return prefix + ["sh", "-c", script + "\nprintf '\\n[finished - press Enter to close] '; read _"]


# --- start / stop ------------------------------------------------------------

def tail(path, count=6):
    try:
        with open(path, errors="replace") as f:
            return "".join(f.readlines()[-count:]).rstrip()
    except OSError:
        return ""


def keyboard():
    """What a start or stop command gets to read from: the terminal if we are in one (su and
    the like ask there), else nothing, so that it can't sit waiting for input nobody can give."""
    return None if sys.stdin is not None and sys.stdin.isatty() else subprocess.DEVNULL


def start(tool, say=print):
    """Start a tool; returns 0 if it runs (or its start command went through)."""
    if tool.error:
        say(f"{tool.title}: {tool.error}")
        return 1
    if not tool.start:
        say(f"{tool.title} has no start command.")
        return 1
    for other in tool.not_with:
        other = get(other)
        if other is not None and other.name != tool.name and not other.error \
                and other.state() in ("running", "starting"):
            stop(other, say)
    if tool.not_with_model and tool.state() not in ("running", "starting"):
        from . import server
        # A model of another machine takes no memory here.
        if server.llama_pid() is not None and not server.remote_host():
            server.llama_stop(say)
    if not tool.kept():
        return subprocess.call(["sh", "-c", tool.start], stdin=keyboard(), cwd=tool.cwd())
    if tool.state() != "stopped":
        say(f"{tool.title} is already running" + (f": {tool.address}" if tool.address else "."))
        return 0
    os.makedirs(RUN_DIR, exist_ok=True)
    with open(tool.logfile(), "w") as log:
        proc = subprocess.Popen(["sh", "-c", tool.start], stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True, cwd=tool.cwd())
    with open(tool.pidfile(), "w") as f:
        f.write(str(proc.pid))
    say(f"Starting {tool.title} (pid {proc.pid}) — log: {tool.logfile()}")
    port = tool.port()
    # Without an address there is nothing to wait for: see that it doesn't fall over at once.
    wait = int(os.environ.get("AI_TOOL_WAIT", "180")) if port else 2
    for i in range(1, wait + 1):
        time.sleep(1)
        code = proc.poll()
        if code is not None:
            os.remove(tool.pidfile())
            if code == 0 and not port:
                say(f"{tool.title} ran and ended.")
                return 0
            say(f"{tool.title} ended while starting (exit {code}). Last lines of {tool.logfile()}:")
            say(tail(tool.logfile()))
            return 1
        if tool.answers():
            say(f"{tool.title} is up after {i}s: {tool.address}")
            return 0
        if i % 10 == 0:
            say(f"  still starting... {i}s")
    if port:
        say(f"{tool.title} doesn't answer on {tool.address} after {wait}s — check {tool.logfile()}")
        return 1
    say(f"{tool.title} is running.")
    return 0


def stop(tool, say=print):
    if tool.error:
        say(f"{tool.title}: {tool.error}")
        return 1
    if tool.stop:
        return subprocess.call(["sh", "-c", tool.stop], stdin=keyboard(), cwd=tool.cwd())
    pid = tool.pid()
    if pid is None:
        say(f"{tool.title} runs, but AIcove didn't start it and can't stop it." if tool.answers()
            else f"{tool.title} wasn't running.")
        return 0
    # The whole group: the shell that ran the start command and what it started.
    try:
        os.killpg(pid, signal.SIGTERM)
        for _ in range(50):
            time.sleep(0.2)
            os.killpg(pid, 0)
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        os.remove(tool.pidfile())
    except OSError:
        pass
    say(f"{tool.title} (pid {pid}) stopped.")
    return 0


def stop_running(marked, say):
    """Stop the running tools that marked(tool) picks."""
    for tool in find_tools():
        if marked(tool) and not tool.error and tool.start and tool.state() in ("running", "starting"):
            if tool.terminal:
                say(f"{tool.title} is still running: its stop command asks something, stop it by itself.")
            else:
                stop(tool, say)


def stop_marked(say=print):
    """What "Stop everything" does to the tools: stop the ones marked for it that run."""
    stop_running(lambda tool: tool.with_all, say)


def stop_for_model(say=print):
    """Before a model is loaded: stop the tools that are never on together with one."""
    stop_running(lambda tool: tool.not_with_model, say)


def show(say=print):
    found = find_tools()
    if not found:
        say(f"No tools yet. Add one in the window (Tools tab) or as a file in {TOOL_DIR}.")
    groups = grouped(found)
    for group, members in groups:
        if len(groups) > 1:
            say(f"{group}:")
        for tool in members:
            state = tool.error or tool.state() or "-"
            say(f"  {tool.name:22} {state:10} {tool.title}" + (f"  {tool.address}" if tool.address else ""))


def main(argv):
    if not argv:
        show()
        return 0
    if len(argv) == 2 and argv[0] in ("start", "stop"):
        tool = get(argv[1])
        if tool is None:
            print(f"No tool called {argv[1]}. See: aicove tools")
            return 1
        return start(tool) if argv[0] == "start" else stop(tool)
    print("usage: aicove tools | aicove tools start NAME | aicove tools stop NAME")
    return 2
