# aicove fetch <job.json> — fetches the model files of one "Add model" job from the window.
# Runs detached from the window, so closing AIcove doesn't stop it. Every file is written as
# <dest>.part and renamed when complete; running the same job again continues where it stopped.
# Next to the job file: <job>.pid while it runs, <job>.log with what happened.
# The job file is removed when everything is in place, so a job file that is still there
# without a running pid means "stopped, can be resumed".
#
# Job file: {"files": [{"url": "https://...", "dest": "/models/x.gguf", "size": 123},
#                      {"src": "/some/local/file.gguf", "dest": "...", "size": 123}]}

import json
import os
import signal
import subprocess
import sys
import time

RETRIES = 500        # a dropped connection is retried this often...
RETRY_WAIT = 30      # ...this many seconds apart (about 4 hours of trying)


def say(text):
    print(time.strftime("%Y-%m-%d %H:%M:%S ") + text, flush=True)


def size_of(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def copy(src, part):
    """Local file -> part, continuing after what is already there."""
    with open(src, "rb") as fin, open(part, "ab") as fout:
        fin.seek(fout.tell())
        while True:
            chunk = fin.read(8 << 20)
            if not chunk:
                break
            fout.write(chunk)


def download(url, part):
    """One curl run; returns its exit code. Gives up on a connection that stalls for a minute."""
    return subprocess.call(["curl", "-fL", "-sS", "-C", "-", "--connect-timeout", "30",
                            "--speed-limit", "1024", "--speed-time", "60", "-o", part, url])


def fetch(item):
    dest, size = item["dest"], item.get("size") or 0
    part = dest + ".part"
    if os.path.exists(dest):
        say(f"{dest} is already there.")
        return True
    for attempt in range(1, RETRIES + 1):
        if size and size_of(part) == size:
            break
        if size and size_of(part) > size:
            say(f"{part} is bigger than the file should be — remove it and start again.")
            return False
        if "src" in item:
            say(f"Copying {item['src']} ({size_of(part) / 1e9:.1f} GB already here)")
            try:
                copy(item["src"], part)
            except OSError as e:
                say(f"Copy failed: {e}")
                return False
            break
        say(f"Downloading {item['url']} ({size_of(part) / 1e9:.1f} GB already here, try {attempt})")
        rc = download(item["url"], part)
        if rc == 0:
            break
        if rc == 22:
            say("The server refused (file gone, or it needs a login). Not trying again.")
            return False
        say(f"Interrupted (curl exit {rc}), trying again in {RETRY_WAIT}s")
        time.sleep(RETRY_WAIT)
    if size and size_of(part) != size:
        say(f"Stopped: {part} has {size_of(part)} of {size} bytes.")
        return False
    os.rename(part, dest)
    say(f"Done: {dest}")
    return True


def is_fetch(cmdline):
    """Whether a process's command line (bytes from /proc) is one of us."""
    return b"\0fetch\0" in cmdline and b"aicove" in cmdline


def main(argv):
    if len(argv) != 1:
        sys.exit("usage: aicove fetch <job.json>")
    job = os.path.abspath(argv[0])
    base = job[:-len(".json")] if job.endswith(".json") else job
    with open(job, encoding="utf-8") as f:
        files = json.load(f)["files"]
    pidfile = base + ".pid"
    try:
        with open(pidfile) as f:
            other = int(f.read().strip())
            with open(f"/proc/{other}/cmdline", "rb") as c:
                # AIcove writes our own pid there when it starts us.
                if other != os.getpid() and is_fetch(c.read()):
                    sys.exit("This job is already running.")
    except (OSError, ValueError):
        pass
    with open(pidfile, "w") as f:
        f.write(str(os.getpid()))

    def stopped(*_):
        say("Stopped by hand. Start it again to continue.")
        sys.exit(1)

    signal.signal(signal.SIGTERM, stopped)
    ok = False
    try:
        ok = all(fetch(item) for item in files)
        if ok:
            os.remove(job)
            say("All files in place.")
    finally:
        os.remove(pidfile)
    sys.exit(0 if ok else 1)

