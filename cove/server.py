# Starting and stopping llama-server (one model at a time). The other things of the machine
# (voice tools, web servers, ...) are in tools.py. Pid file and log are in ~/.cache/aicove.
# A llama-server on our port that something else started is found too, so it is never
# started twice.
#
# A launcher of another machine (host: in its file) is started there with that machine's own
# aicove, over ssh; an ssh tunnel then puts its llama-server on our port. The tunnel takes the
# place of llama-server here: same pid file, and stopping it stops the model over there too.

import json
import os
import shlex
import signal
import subprocess
import time
import urllib.error
import urllib.request

from . import launchers, tools
from .settings import S, STATE_DIR

LLAMA_PID = os.path.join(STATE_DIR, "llama-server.pid")
LLAMA_LOG = os.path.join(STATE_DIR, "llama-server.log")
# The launcher (ours) whose model the tunnel brings over.
REMOTE_NOW = os.path.join(STATE_DIR, "remote-now.txt")
# ssh runs commands without the login's PATH; where install.sh puts the aicove command.
REMOTE_PATH = 'PATH="$HOME/bin:$HOME/.local/bin:$PATH" '


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


def tunnel_spec():
    return f"127.0.0.1:{S.llama_port}:127.0.0.1:"


def is_tunnel(cmd):
    """Our ssh tunnel to another machine's llama-server: ssh -N ... -L <our port>:... host"""
    return bool(cmd) and os.path.basename(cmd[0]) == "ssh" and "-N" in cmd \
        and any(c.startswith(tunnel_spec()) for c in cmd)


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
    return find(LLAMA_PID, lambda cmd: is_llama(cmd) or is_tunnel(cmd))


def remote_host():
    """The machine whose model is on our port now, or None (a model of ours, or none)."""
    cmd = cmdline(llama_pid() or 0)
    return cmd[-1] if is_tunnel(cmd) else None


def running():
    """(model path, arguments after the port) of the running llama-server, or None.
    For another machine's model: (remote:<host>/<launcher>, its arguments here)."""
    cmd = cmdline(llama_pid() or 0)
    if is_tunnel(cmd):
        try:
            with open(REMOTE_NOW) as f:
                launcher = launchers.get(f.read().strip())
        except OSError:
            launcher = None
        if launcher is None or launcher.host != cmd[-1]:
            return f"{launchers.REMOTE}{cmd[-1]}/?", []
        return launcher.model, launchers.run_args(launcher.args)
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
    host = remote_host()
    if host:
        say(f"Stopping the model on {host}...")
        remote(host, ["stop"], say)
        stop(llama_pid, LLAMA_PID, f"The tunnel to {host}", say)
        forget(REMOTE_NOW)
        return
    stop(llama_pid, LLAMA_PID, "llama-server", say)


# --- another machine's model -------------------------------------------------------

def ssh(host, args):
    """The command that runs aicove with these arguments on host."""
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host,
            REMOTE_PATH + shlex.join(["aicove"] + args)]


def remote(host, args, say, prefix=None):
    """Run aicove on host, its output shown line by line. Returns its exit code."""
    prefix = f"  {host}: " if prefix is None else prefix
    try:
        proc = subprocess.Popen(ssh(host, args), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, errors="replace")
    except OSError as e:
        say(f"Can't run ssh: {e}")
        return 1
    for line in proc.stdout:
        say(prefix + line.rstrip())
    return proc.wait()


def ask(host):
    """Ask host about its launchers (and keep the answer). Returns it, or raises OSError."""
    try:
        out = subprocess.run(ssh(host, ["info"]), stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise OSError(f"no answer from {host} ({e})")
    if out.returncode != 0:
        last = (out.stderr or out.stdout).strip().split("\n")[-1]
        raise OSError(f"{host}: {last or f'ssh ended with {out.returncode}'}")
    try:
        # The JSON is the last line: anything aicove says before it (moving folders in) isn't.
        info = json.loads(out.stdout.strip().split("\n")[-1])
        int(info["port"]), dict(info["launchers"])
    except (ValueError, KeyError, TypeError):
        raise OSError(f"{host} doesn't answer like AIcove does (too old? git pull there)")
    launchers.save_remote_info(host, info)
    return info


def remote_start(launcher, say):
    host, name = launcher.host, launcher.remote_name
    say(f"Asking {host} about {name}...")
    try:
        info = ask(host)
    except OSError as e:
        say(str(e))
        return 1
    there = info["launchers"].get(name)
    added, removed = launchers.follow(host, info)
    if added or removed:
        say(f"Launchers of {host}: " + ", ".join([f"new {n}" for n in added] + [f"gone {n}" for n in removed]))
    if there is None:
        say(f"{host} has no launcher called {name} (it has: {', '.join(sorted(info['launchers'])) or 'none'}).")
        return 1
    if there.get("error") or not there.get("present"):
        say(f"{name} on {host}: {there.get('error') or 'model file not found there'}")
        return 1
    if info.get("host") not in ("127.0.0.1", "localhost", "::1"):
        say(f"Note: llama-server on {host} listens on {info.get('host')}, so other machines on its "
            "network reach it too. 127.0.0.1 on its Settings tab is enough for the tunnel.")
    llama_stop(say)
    say(f"Starting {name} on {host}:")
    if remote(host, ["start", name], say) != 0:
        say(f"{host} couldn't start it.")
        return 1
    proc = start(["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
                  "-o", "ServerAliveInterval=30", "-L", tunnel_spec() + str(info["port"]), host],
                 LLAMA_LOG, LLAMA_PID)
    with open(REMOTE_NOW, "w") as f:
        f.write(launcher.name)
    for i in range(30):
        if proc.poll() is not None:
            say(f"The tunnel to {host} closed at once:")
            with open(LLAMA_LOG, errors="replace") as f:
                say("".join(f.readlines()[-4:]).rstrip())
            forget(LLAMA_PID)
            forget(REMOTE_NOW)
            say(f"(The model still runs on {host}: aicove stop there, or start it again here.)")
            return 1
        if health() == "ready":
            say(f"{there.get('friendly') or name} is ready on {host}, here at {S.llama_url()} (tunnel pid {proc.pid})")
            return 0
        time.sleep(1)
    say(f"The tunnel is open but nothing answers through it — check {LLAMA_LOG}")
    return 1


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
    if launcher.host:
        return remote_start(launcher, say)
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
        host = remote_host()
        say((f"model on {host} (tunnel pid {pid})" if host else f"llama-server (pid {pid})")
            + f": {health() or 'starting'}, {S.llama_url()} — {name}" + (f" [{match}]" if match else ""))
    for tool in tools.find_tools():
        if not tool.error and tool.state() in ("running", "starting"):
            say(f"{tool.title}: {tool.state()}" + (f", {tool.address}" if tool.address else ""))
