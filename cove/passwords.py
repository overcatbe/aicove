# Passwords: one file per entry, encrypted with GnuPG, in the folder from the settings
# (~/.password-store). It is the layout of the "pass" program, so the files stay usable
# without AIcove:
#
#   .gpg-id            the key everything is encrypted for
#   <name>.gpg         line 1 the password, then "user: ...", "url: ...", then free notes
#   .git/              every change is a commit; "Sync" pulls and pushes it
#
# AIcove never sees the master password: GnuPG asks for it in its own window and remembers
# it for a while. Nothing here writes a password to a log or to the output pane.
#
#   aicove passwords         the names of the entries
#   aicove passwords sync    bring the store in step with the place it is synced with
#   aicove passwords sync mine | theirs    the same, and where an entry was changed on both
#                            sides this machine's | the other version counts

import os
import re
import secrets
import shlex
import shutil
import string
import subprocess

from .settings import S

NAME = re.compile(r"[A-Za-z0-9@][A-Za-z0-9._@ +-]*(/[A-Za-z0-9@][A-Za-z0-9._@ +-]*)*")
USER_KEYS = ("user", "username", "login")
URL_KEYS = ("url", "address")
# Extra GnuPG options, for testing with a key that needs no window.
GPG_EXTRA = shlex.split(os.environ.get("AICOVE_GPG_OPTS", ""))
ALPHABET = string.ascii_letters + string.digits + "-_.!+="


def gpg_program():
    return shutil.which("gpg") or shutil.which("gpg2") or ""


def store():
    return os.path.expanduser(S.password_store or "~/.password-store")


def ready():
    return os.path.isfile(os.path.join(store(), ".gpg-id"))


def key_id():
    """The key the store is encrypted for, or ""."""
    try:
        with open(os.path.join(store(), ".gpg-id"), encoding="utf-8") as f:
            return f.read().split("\n")[0].strip()
    except OSError:
        return ""


def path_of(name):
    return os.path.join(store(), name + ".gpg")


def entries():
    found = []
    base = store()
    for folder, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.endswith(".gpg") and not f.startswith("."):
                found.append(os.path.relpath(os.path.join(folder, f), base)[:-len(".gpg")])
    return sorted(found, key=str.lower)


def format_entry(password, user="", url="", notes=""):
    lines = [password]
    if user:
        lines.append(f"user: {user}")
    if url:
        lines.append(f"url: {url}")
    if notes.strip():
        lines.append(notes.strip("\n"))
    return "\n".join(lines) + "\n"


def parse_entry(text):
    lines = text.split("\n")
    entry = {"password": lines[0], "user": "", "url": "", "notes": ""}
    rest = []
    for line in lines[1:]:
        key, sep, value = line.partition(":")
        key = key.strip().lower()
        if sep and key in USER_KEYS and not entry["user"]:
            entry["user"] = value.strip()
        elif sep and key in URL_KEYS and not entry["url"]:
            entry["url"] = value.strip()
        else:
            rest.append(line)
    entry["notes"] = "\n".join(rest).strip("\n")
    return entry


def generate(length=20):
    return "".join(secrets.choice(ALPHABET) for _ in range(length))


# --- GnuPG command lines (the window runs them, so its own window can ask) -------

def decrypt_args(name):
    return GPG_EXTRA + ["--quiet", "--batch", "--yes", "--decrypt", path_of(name)]


def encrypt_args(dest):
    """Encrypt what comes in on standard input into the file dest."""
    return GPG_EXTRA + ["--quiet", "--batch", "--yes", "--trust-model", "always", "--no-encrypt-to",
                        "--encrypt", "-r", key_id(), "-o", dest]


def new_key_args(label):
    return GPG_EXTRA + ["--batch", "--status-fd", "1", "--quick-generate-key", label, "default", "default", "never"]


def export_key_args():
    return GPG_EXTRA + ["--batch", "--armor", "--export-secret-keys", key_id()]


def import_key_args(path):
    return GPG_EXTRA + ["--batch", "--import", path]


def created_key(output):
    """The fingerprint from the output of new_key_args, or ""."""
    for line in output.split("\n"):
        parts = line.split()
        if parts[:2] == ["[GNUPG:]", "KEY_CREATED"] and len(parts) >= 4:
            return parts[3]
    return ""


def secret_keys():
    """[(fingerprint, name)] of the keys of this machine that can decrypt."""
    try:
        out = subprocess.run([gpg_program(), "--batch", "--list-secret-keys", "--with-colons"],
                             capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    keys, fpr = [], None
    for line in out.split("\n"):
        f = line.split(":")
        if f[0] == "sec":
            fpr = ""
        elif f[0] == "fpr" and fpr == "":
            fpr = f[9]
        elif f[0] == "uid" and fpr:
            keys.append((fpr, f[9]))
            fpr = None
    return keys


def key_name():
    """What the store's key calls itself if it is on this machine, else None."""
    key = key_id()
    return next((name for fpr, name in secret_keys() if key and fpr.endswith(key.upper())), None)


def lock():
    """Make GnuPG forget the master password."""
    try:
        subprocess.run(["gpgconf", "--reload", "gpg-agent"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


# --- git ---------------------------------------------------------------------

def git(*args, timeout=60):
    """Run git in the store: (exit code, what it said)."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    # Never wait for a question nobody sees.
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    try:
        p = subprocess.run(["git", "-c", "user.name=AIcove", "-c", "user.email=aicove@localhost", *args],
                           cwd=store(), env=env, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def commit(message):
    if not os.path.isdir(os.path.join(store(), ".git")):
        return
    git("add", "-A")
    if git("diff", "--cached", "--quiet")[0] != 0:
        git("commit", "-q", "-m", message)


def init_store(key):
    """Make the folder a store for that key."""
    os.makedirs(store(), mode=0o700, exist_ok=True)
    with open(os.path.join(store(), ".gpg-id"), "w", encoding="utf-8") as f:
        f.write(key + "\n")
    if not os.path.isdir(os.path.join(store(), ".git")):
        git("init", "-q")
        git("symbolic-ref", "HEAD", "refs/heads/master")
    commit("Password store set up by AIcove")


def clone(url):
    """Fetch a store from somewhere else into the (empty or missing) folder: (ok, what git said)."""
    dest = store()
    if os.path.isdir(dest) and os.listdir(dest):
        return False, f"{dest} is not empty."
    os.makedirs(os.path.dirname(dest.rstrip("/")) or "/", exist_ok=True)
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    try:
        p = subprocess.run(["git", "clone", "-q", url, dest], env=env, capture_output=True, text=True,
                           timeout=120, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    if p.returncode == 0:
        os.chmod(dest, 0o700)
        if not ready():
            return False, f"{url} was fetched, but it is not a password store (no .gpg-id in it)."
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def remote():
    code, out = git("remote", "get-url", "origin")
    return out if code == 0 else ""


def set_remote(url):
    if remote():
        return git("remote", "set-url", "origin", url) if url else git("remote", "remove", "origin")
    return git("remote", "add", "origin", url) if url else (0, "")


def unsent():
    """How many changes the synced place doesn't have yet, or None when nothing is synced."""
    if not remote():
        return None
    code, out = git("rev-list", "--count", "origin/master..HEAD")
    if code != 0:
        code, out = git("rev-list", "--count", "HEAD")
    return int(out) if code == 0 and out.isdigit() else None


def changed_between(a, b):
    """Entries changed in b since it parted from a."""
    code, out = git("diff", "--name-only", f"{a}...{b}")
    return {l[:-len(".gpg")] for l in out.split("\n") if l.endswith(".gpg")} if code == 0 else set()


def sync(say=print, prefer=None):
    """prefer: None, or "mine" / "theirs" for entries that were changed on both sides."""
    if not ready():
        say("No password store yet — set it up in the window, Passwords tab.")
        return 1
    commit("Changes made outside AIcove")
    url = remote()
    if not url:
        say("The store isn't synced with anything yet (Passwords tab: More → Set sync address).")
        return 1
    code, out = git("fetch", "-q", "origin", timeout=120)
    if code != 0:
        say(f"Can't reach {url}:\n{out}")
        return 1
    if git("rev-parse", "-q", "--verify", "origin/master")[0] == 0:
        both = sorted(changed_between("origin/master", "HEAD") & changed_between("HEAD", "origin/master"))
        if both and prefer is None:
            say("Changed on this machine and on another one since the last sync: " + ", ".join(both) + ".\n"
                "Nothing was merged. Decide which version counts (Passwords tab, More; or in a terminal):\n"
                "  aicove passwords sync mine      this machine's\n"
                "  aicove passwords sync theirs    the other one\n"
                "The version that loses stays in the store's history.")
            return 1
        code, out = git("merge", "-q", "--no-edit", "-X", "ours" if prefer != "theirs" else "theirs",
                        "-m", "Sync", "origin/master")
        if code != 0:
            git("merge", "--abort")
            say(f"Couldn't merge with {url}, nothing was changed:\n{out}")
            return 1
        if both:
            say(f"Kept {'this machine' if prefer == 'mine' else 'the other machine'}'s version of: "
                + ", ".join(both))
    code, out = git("push", "-q", "-u", "origin", "master", timeout=120)
    if code != 0:
        say(f"Can't send to {url}:\n{out}")
        return 1
    count = len(entries())
    say(f"Passwords are in step with {url} ({count} entr{'y' if count == 1 else 'ies'}).")
    return 0


def main(argv):
    if not argv:
        if not ready():
            print("No password store yet — set it up in the window, Passwords tab.")
            return 1
        for name in entries():
            print(name)
        return 0
    if argv[0] == "sync" and argv[1:] in ([], ["mine"], ["theirs"]):
        return sync(prefer=argv[1] if argv[1:] else None)
    print("usage: aicove passwords | aicove passwords sync [mine|theirs]")
    return 2
