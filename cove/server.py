# Starting and stopping llama-server (one model at a time). The other things of the machine
# (voice tools, web servers, ...) are in tools.py. Pid file and log are in ~/.cache/aicove.
# A llama-server on our port that something else started is found too, so it is never
# started twice.

import os
import signal
import subprocess
import time
import urllib.error
import urllib.request

from . import launchers, tools
from .settings import S, STATE_DIR

LLAMA_PID = os.path.join(STATE_DIR, "llama-server.pid")
LLAMA_LOG = os.path.join(STATE_DIR, "llama-server.log")


def cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return [c.decode(errors="replace") for c in f.read().split(b"\0")[:-1]]
    except OSError:
        return []


def port_arg(cmd, default):
    return cmd[cmd.index("--port") + 1] if "--port" in cmd[:-1] else default


def is_llama(cmd):
    return bool(cmd) and os.path.basename(cmd[0]) == "llama-server" and port_arg(cmd, "8080") == str(S.llama_port)


def find(pidfile, ours):
    """Pid of the running process a pid file names, or of any process of ours that fits, or None."""
    try:
        with open(pidfile) as f:
            pid = int(f.read().strip())
        if ours(cmdline(pid)):
            return pid
    except (OSError, ValueError):
        pass
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            try:
                if os.stat(f"/proc/{entry}").st_uid == os.getuid() and ours(cmdline(entry)):
                    return int(entry)
            except OSError:
                continue
    return None


def llama_pid():
    return find(LLAMA_PID, is_llama)


def running():
    """(model path, arguments after the port) of the running llama-server, or None."""
    cmd = cmdline(llama_pid() or 0)
    if "-m" not in cmd[:-1]:
        return None
    return cmd[cmd.index("-m") + 1], cmd[cmd.index("--port") + 2:] if "--port" in cmd else []


def end(pid, seconds=10):
    """Ask a process to stop and wait until it is really gone; one that hangs on the way out
    (seen with llama-server in the middle of a long answer) is killed."""
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(seconds * 5):
            time.sleep(0.2)
            # Our own child stays in the list as a zombie until it is collected.
            try:
                os.waitpid(pid, os.WNOHANG)
            except OSError:
                pass
            os.kill(pid, 0)
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.5)
    except OSError:
        pass


def start(cmd, logfile, pidfile, env=None, cwd=None):
    """Run a program on its own, so it outlives whoever started it. Returns the process."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(logfile, "w") as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True, env=env, cwd=cwd)
    with open(pidfile, "w") as f:
        f.write(str(proc.pid))
    return proc


def forget(pidfile):
    try:
        os.remove(pidfile)
    except OSError:
        pass


# --- llama-server ------------------------------------------------------------

def stop(pid_of, pidfile, what, say):
    """Stop every process pid_of finds (there can be a second one that something else started)."""
    pid = pid_of()
    if pid is None:
        say(f"{what} wasn't running.")
    for _ in range(5):
        if pid is None:
            break
        end(pid)
        say(f"{what} (pid {pid}) stopped.")
        pid = pid_of()
    forget(pidfile)


def llama_stop(say=print):
    stop(llama_pid, LLAMA_PID, "llama-server", say)


def health():
    """"ready", "loading" or None (nothing answers)."""
    try:
        urllib.request.urlopen(S.llama_url() + "/health", timeout=2).read()
        return "ready"
    except urllib.error.HTTPError:
        return "loading"
    except (OSError, ValueError):
        return None


def llama_start(launcher, say=print):
    """Load a launcher's model (stopping whatever was loaded) and wait until it answers.
    Returns 0 if ready."""
    if launcher.error:
        say(f"{launcher.name}: {launcher.error}")
        return 1
    if not os.path.isfile(launcher.model):
        say(f"Model file not found: {launcher.model}")
        return 1
    if not (S.llama_server and os.access(S.llama_server, os.X_OK)):
        say(f"llama-server not found ({S.llama_server or 'not set'}) — see the Settings tab.")
        return 1
    llama_stop(say)
    tools.stop_for_model(say)
    proc = start(launchers.command(launcher.model, launcher.args), LLAMA_LOG, LLAMA_PID)
    say(f"Starting {launcher.friendly} (pid {proc.pid}) on port {S.llama_port} — log: {LLAMA_LOG}")
    say("Loading the model...")
    wait = int(os.environ.get("AI_LLAMA_WAIT", "600"))
    for i in range(1, wait + 1):
        if proc.poll() is not None:
            say(f"llama-server gave up while loading. Last lines of {LLAMA_LOG}:")
            with open(LLAMA_LOG, errors="replace") as f:
                say("".join(f.readlines()[-6:]).rstrip())
            forget(LLAMA_PID)
            return 1
        if health() == "ready":
            say(f"{launcher.friendly} is ready after {i}s: {S.llama_url()}")
            return 0
        if i % 10 == 0:
            say(f"  still loading... {i}s")
        time.sleep(1)
    say(f"Not ready after {wait}s — check {LLAMA_LOG}")
    return 1


# --- everything ----------------------------------------------------------------

def stop_all(say=print):
    llama_stop(say)
    tools.stop_marked(say)


def status(say=print):
    pid = llama_pid()
    if pid is None:
        say("llama-server: not running")
    else:
        server = running()
        name = os.path.basename(server[0]) if server else "?"
        match = next((l.name for l in launchers.find_launchers() if server and not l.error
                      and l.model == server[0] and launchers.run_args(l.args) == server[1]), None)
        say(f"llama-server: {health() or 'starting'} (pid {pid}), {S.llama_url()} — {name}"
            + (f" [{match}]" if match else ""))
    for tool in tools.find_tools():
        if not tool.error and tool.state() in ("running", "starting"):
            say(f"{tool.title}: {tool.state()}" + (f", {tool.address}" if tool.address else ""))
