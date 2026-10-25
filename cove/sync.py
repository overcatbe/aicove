# Keeping the AIcove folder in step with the other machines: the folder (personas, writing,
# agents) and each story under writing/ that has a git repo with an "origin". Nothing happens
# for a folder without one, so a fresh install never syncs anything.
#
# The AIcove folder: what changed here is committed, the other machines' commits are put
# under it (rebase), and the result is pushed. A story: only its commits are sent and fetched;
# its versions are the Writing tab's to make, so nothing is committed here, and a story with
# unsaved changes is not pulled into.
#
# A conflict (the same lines changed here and elsewhere) is never merged by guessing: the
# rebase is undone, this machine keeps what it has, and the report says what to look at.

import fcntl
import os
import socket
import subprocess

from .settings import COVE_DIR, STATE_DIR


def git(path, *args, timeout=60):
    """(exit code, what git said)."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    # Never wait for a question nobody sees.
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=10")
    try:
        p = subprocess.run(["git", "-c", "user.name=AIcove", "-c", f"user.email=aicove@{socket.gethostname()}",
                            *args], cwd=path, env=env, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def synced(path):
    """Whether path is a git repo with an origin to sync with."""
    return os.path.isdir(os.path.join(path, ".git")) and git(path, "remote", "get-url", "origin")[0] == 0


def repos():
    """[(path, commit here?)]: the AIcove folder, then its stories."""
    found = [(COVE_DIR, True)] if synced(COVE_DIR) else []
    writing = os.path.join(COVE_DIR, "writing")
    try:
        names = sorted(os.listdir(writing))
    except OSError:
        names = []
    found += [(os.path.join(writing, n), False) for n in names if synced(os.path.join(writing, n))]
    return found


def pending():
    """What git status says about the AIcove folder ("" = nothing to commit), to notice when it
    has stopped changing."""
    if not synced(COVE_DIR):
        return ""
    return git(COVE_DIR, "status", "--porcelain")[1]


def label(path):
    return "AIcove folder" if path == COVE_DIR else f"story {os.path.basename(path)}"


def commit_all(path):
    git(path, "add", "-A")
    if git(path, "diff", "--cached", "--quiet")[0] == 0:
        return
    names = git(path, "diff", "--cached", "--name-only")[1].split("\n")
    shown = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    git(path, "commit", "-q", "-m", f"AIcove on {socket.gethostname()}: {shown}")


def count(path, spec):
    code, out = git(path, "rev-list", "--count", spec)
    return int(out) if code == 0 and out.isdigit() else 0


def sync_repo(path, commit):
    """One repo: (lines for the log, files the other machines changed here)."""
    name = label(path)
    if commit:
        commit_all(path)
    code, branch = git(path, "symbolic-ref", "-q", "--short", "HEAD")
    if code != 0 or not branch:
        return [f"{name}: not on a branch, left alone."], []
    code, out = git(path, "fetch", "-q", "origin", timeout=120)
    if code != 0:
        return [f"{name}: can't reach {git(path, 'remote', 'get-url', 'origin')[1]}: {out.splitlines()[-1] if out else ''}"], []
    upstream = f"origin/{branch}"
    lines, changed = [], []
    born = git(path, "rev-parse", "-q", "--verify", "HEAD")[0] == 0
    if not born:
        # A fresh clone of a repo that was empty then: take what is there now.
        if git(path, "rev-parse", "-q", "--verify", upstream)[0] == 0:
            code, out = git(path, "checkout", "-q", "-B", branch, upstream)
            return ([f"{name}: fetched from the other machines."] if code == 0
                    else [f"{name}: can't take what the other machines have: {out}"]), []
        return [], []
    if git(path, "rev-parse", "-q", "--verify", upstream)[0] == 0:
        behind = count(path, f"HEAD..{upstream}")
        if behind and git(path, "status", "--porcelain")[1]:
            # Only a story gets here (the folder was just committed): don't pull into unsaved work.
            return [f"{name}: {behind} new from the other machines, fetched once its changes are saved."], []
        if behind:
            before = git(path, "rev-parse", "HEAD")[1]
            code, out = git(path, "rebase", "-q", upstream)
            if code != 0:
                both = git(path, "diff", "--name-only", "--diff-filter=U")[1].split()
                git(path, "rebase", "--abort")
                return [f"{name}: changed here and on another machine, nothing was merged"
                        + (f" ({', '.join(both)})" if both else "") + ". This machine keeps its version for now; "
                        f"to settle it: cd {path} && git pull --rebase"], []
            changed = [f for f in git(path, "diff", "--name-only", before, "HEAD")[1].split("\n") if f]
            lines.append(f"{name}: {behind} change{'s' if behind != 1 else ''} from the other machines.")
    ahead = count(path, f"{upstream}..HEAD") if git(path, "rev-parse", "-q", "--verify", upstream)[0] == 0 \
        else count(path, "HEAD")
    if ahead:
        code, out = git(path, "push", "-q", "origin", f"HEAD:{branch}", timeout=120)
        lines.append(f"{name}: sent {ahead} change{'s' if ahead != 1 else ''}." if code == 0
                     else f"{name}: can't send: {out.splitlines()[-1] if out else ''}")
    return lines, [os.path.join(path, f) for f in changed]


def sync():
    """All repos, one machine-wide sync at a time: (lines for the log, files changed here)."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, "sync.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        lines, changed = [], []
        for path, commit in repos():
            more, files = sync_repo(path, commit)
            lines += more
            changed += files
        return lines, changed


def main(argv):
    if argv:
        print("usage: aicove sync")
        return 2
    if not repos():
        print(f"Nothing to sync: {COVE_DIR} is not a git repo with an origin.")
        return 1
    lines, _ = sync()
    print("\n".join(lines) or "In step with the other machines.")
    return 1 if any("can't" in l or "nothing was merged" in l for l in lines) else 0
