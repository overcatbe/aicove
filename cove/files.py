# File tools for the chat: a persona with "files: read" or "files: write" may look at the files
# of this machine and, after a yes from you for every single change, write text files. It
# cannot run commands. What it may touch is in the settings (Settings tab, "File access for
# the AIs"), read again at every call, so a change there counts at once. The model never gets
# to decide: every path is checked here after symbolic links are followed, the never-list
# below can't be switched off, and a write only happens after Yes in the dialog. Every call is
# logged to ~/.cache/aicove/access.log (paths only, never contents).

import datetime
import difflib
import fnmatch
import os
import re
import shutil
import stat
import time
import json
import tomllib

from . import personas
from .settings import CONFIG_FILE, STATE_DIR

GUIDE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "guide.md")
HOME = os.path.expanduser("~")

# "System files": what can be read when the setting is on. Never written.
SYSTEM_ROOTS = ["/etc", "/var/log", "/var/lib", "/proc", "/sys", "/usr", "/boot", "/opt",
                "/lib", "/lib64", "/bin", "/sbin"]
# Never read, never written, whatever the settings say.
NEVER_DIRS = [os.path.join(HOME, p) for p in (
    ".password-store", ".gnupg", ".ssh", ".mozilla", ".config/mozilla", ".config/chromium",
    ".config/google-chrome", ".config/BraveSoftware", ".local/share/keyrings", ".claude", ".cache",
    ".aws", ".kube", ".docker", ".config/gh")] + ["/etc/ssl/private", "/root"]
NEVER_NAMES = ["id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "ssh_host_*key", "*.pem", "*.key", "*.p12",
               "*.pfx", "*.kdbx", "*.gpg", "*.asc", ".netrc", ".pgpass", ".my.cnf", ".git-credentials",
               ".env", ".env.*", "shadow", "shadow-", "gshadow", "gshadow-", "sudoers", "*secret*",
               "*password*", "*credential*", "wpa_supplicant*.conf", "*_history", "webui.db"]
# Never written: places where a file is run later (that would be running commands by the back door).
NO_WRITE_DIRS = [os.path.join(HOME, p) for p in ("bin", ".local/bin")]

MAX_BYTES = 2_000_000      # of one file that is looked at
MAX_OUTPUT = 24_000        # characters handed back to the model in one answer
MAX_LINE = 1_000
NO_COMMANDS = ("You cannot run commands or change system files. When something has to be run or "
               "changed there, explain it and give the user the exact command to run.")


def real(path):
    return os.path.realpath(os.path.expanduser(path))


# The parts of a persona's folder that are its own to read and write; its persona file and
# chats are not.
OWN_PARTS = ("knowledge", "notes")


def own_folder(pid):
    """The folder of the persona, or None."""
    if not pid or not personas.SAFE_ID.fullmatch(pid):
        return None
    folder = personas.folder(pid)
    return real(folder) if os.path.isfile(os.path.join(folder, personas.PERSONA_FILE)) else None


def rules(pid=None):
    try:
        with open(CONFIG_FILE, "rb") as f:
            c = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        c = {}

    def folders(key):
        return [real(p) for p in c.get(key, []) if isinstance(p, str) and p.strip()]

    never = [p for p in c.get("access_never", []) if isinstance(p, str) and p.strip()]
    # A persona's knowledge and notes are always its to read and to write in.
    own = own_folder(pid)
    mine = [os.path.join(own, part) for part in OWN_PARTS] if own else []
    return {
        "own": own, "read": folders("access_read"), "write": folders("access_write") + mine,
        "system": c.get("access_system") is True,
        "never_dirs": NEVER_DIRS + [real(c.get("password_store") or "~/.password-store")]
                      + [real(p) for p in never if "/" in p or p.startswith("~")],
        "never_names": NEVER_NAMES + [p for p in never if "/" not in p and not p.startswith("~")],
        "notes": real(c["notes_file"]) if isinstance(c.get("notes_file"), str) and c["notes_file"].strip() else "",
    }


def inside(path, roots):
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def never(path, r):
    """Why this path is off limits for good, or None."""
    for d in r["never_dirs"]:
        if inside(path, [d]) or fnmatch.fnmatch(path, d) or fnmatch.fnmatch(path, d.rstrip("/") + "/*"):
            return "it is in a place that holds keys or passwords"
    for part in path.split("/"):
        if any(fnmatch.fnmatch(part.lower(), pattern.lower()) for pattern in r["never_names"]):
            return "its name says it holds keys or passwords"
    return None


def readable_places(r):
    places = r["write"] + r["read"] + (["system files (" + ", ".join(SYSTEM_ROOTS) + ")"] if r["system"] else [])
    return "; ".join(places) if places else "nothing — the user has not allowed any folder yet (AIcove, Settings tab)"


def why_not_read(path, r):
    reason = never(path, r)
    if reason:
        return f"Not allowed: {path} can never be read, {reason}. Don't look for another way; tell the user."
    if inside(path, r["write"] + r["read"]):
        return None
    if r["system"] and inside(path, SYSTEM_ROOTS):
        parts = path.split("/")
        if parts[1] == "proc" and len(parts) > 2 and (parts[2].isdigit() or parts[2] in ("self", "thread-self")):
            return "Not allowed: the files of single running programs in /proc are not readable."
        return None
    return f"Not allowed: {path} is outside what you may read. You may read: {readable_places(r)}."


def why_not_write(path, r):
    reason = never(path, r)
    if reason:
        return f"Not allowed: {path} can never be written, {reason}."
    root = next((w for w in r["write"] if inside(path, [w])), None)
    if root is None or path == root:
        places = "; ".join(r["write"]) or "nowhere — the user has not allowed a folder for writing (AIcove, Settings tab)"
        return f"Not allowed: you may only write inside: {places}. {NO_COMMANDS}"
    if inside(path, NO_WRITE_DIRS) or any(part.startswith(".") for part in path[len(root):].split("/") if part):
        return "Not allowed: hidden files and folders, and folders of programs, are not written. " + NO_COMMANDS
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return None
    if not stat.S_ISREG(mode):
        return f"Not allowed: {path} is not an ordinary file."
    if mode & 0o111:
        return f"Not allowed: {path} is a program (it can be run). Propose the change to the user instead."
    return None


def log(who, what, path, outcome):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, "access.log"), "a", encoding="utf-8") as f:
            f.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {who[:24]:24}  {what:12}  {outcome:10}  {path}\n")
    except OSError:
        pass


def read_text(path, end=False):
    """(text, note) of a file, or (None, why not). Of a very big file only the first part is
    taken, or with end=True the last part (logs)."""
    try:
        st = os.stat(path)
        if stat.S_ISDIR(st.st_mode):
            return None, f"{path} is a folder — use list_folder."
        if not stat.S_ISREG(st.st_mode):
            return None, f"{path} is not an ordinary file."
        with open(path, "rb") as f:
            if end and st.st_size > MAX_BYTES:
                f.seek(st.st_size - MAX_BYTES)
                data, cut = f.read(MAX_BYTES), "last"
                data = data[data.find(b"\n") + 1:]
            else:
                data = f.read(MAX_BYTES + 1)
                cut = "first" if len(data) > MAX_BYTES else ""
    except OSError as e:
        return None, f"Can't read {path}: {e.strerror or e}."
    if b"\0" in data[:8192]:
        return None, f"{path} is not a text file."
    note = f" (only the {cut} {MAX_BYTES // 1_000_000} MB were looked at)" if cut else ""
    return data[:MAX_BYTES].decode("utf-8", errors="replace"), note


def walk(folder, r, limit=20_000):
    """Files below a folder that may be read, hidden folders and other disks left out."""
    seen = 0
    for base, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".")
                         and why_not_read(real(os.path.join(base, d)), r) is None)
        for name in sorted(files):
            seen += 1
            if seen > limit:
                return
            yield os.path.join(base, name)


def do_read(path, start_line, max_lines, r):
    path = real(path)
    refusal = why_not_read(path, r)
    if refusal:
        return refusal, "refused"
    start = 1 if start_line is None else int(start_line)
    count = max(1, min(int(max_lines or 300), 2000))
    text, note = read_text(path, end=start < 1)
    if text is None:
        return note, "failed"
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    # start_line below 1 = the end of the file.
    start = max(1, len(lines) - count + 1) if start < 1 else start
    out, used, last = [], 0, start - 1
    for number in range(start, min(start + count - 1, len(lines)) + 1):
        line = lines[number - 1]
        if len(line) > MAX_LINE:
            line = line[:MAX_LINE] + " […line cut]"
        if used + len(line) > MAX_OUTPUT:
            break
        out.append(line)
        used += len(line) + 1
        last = number
    head = f"{path}: lines {start}-{last} of {len(lines)}{note}"
    if last < len(lines):
        head += f". More: call again with start_line={last + 1}"
    return head + "\n" + "\n".join(out), "ok"


def do_list(path, r):
    path = real(path)
    refusal = why_not_read(path, r)
    if refusal:
        return refusal, "refused"
    try:
        names = sorted(os.listdir(path), key=str.lower)
    except OSError as e:
        return f"Can't list {path}: {e.strerror or e}.", "failed"
    rows = []
    for name in names[:400]:
        full = os.path.join(path, name)
        try:
            st = os.stat(full)
            rows.append(f"{name}/" if stat.S_ISDIR(st.st_mode) else f"{name}  ({st.st_size} bytes)")
        except OSError:
            rows.append(name)
    more = f"\n… and {len(names) - 400} more" if len(names) > 400 else ""
    return f"{path}: {len(names)} entries\n" + "\n".join(rows) + more, "ok"


def do_find(folder, name, r):
    folder = real(folder)
    refusal = why_not_read(folder, r)
    if refusal:
        return refusal, "refused"
    pattern = name.lower() if any(c in name for c in "*?[") else f"*{name.lower()}*"
    found, end = [], time.time() + 15
    for path in walk(folder, r):
        if fnmatch.fnmatch(os.path.basename(path).lower(), pattern):
            found.append(path)
            if len(found) >= 200:
                break
        if time.time() > end:
            break
    return (f"{len(found)} file(s) named like {name} under {folder}"
            + (" (stopped at 200)" if len(found) >= 200 else "") + ":\n" + "\n".join(found)), "ok"


def do_search(folder, text, name, r):
    folder = real(folder)
    refusal = why_not_read(folder, r)
    if refusal:
        return refusal, "refused"
    if not text.strip():
        return "Give the text to look for.", "failed"
    pattern = (name or "*").lower()
    if not any(c in pattern for c in "*?["):
        pattern = f"*{pattern}*"
    needle, hits, files, end = text.lower(), [], 0, time.time() + 20
    for path in walk(folder, r):
        if time.time() > end or len(hits) >= 60 or files >= 4000:
            break
        if not fnmatch.fnmatch(os.path.basename(path).lower(), pattern) or never(real(path), r):
            continue
        try:
            if not stat.S_ISREG(os.stat(path).st_mode) or os.path.getsize(path) > MAX_BYTES:
                continue
        except OSError:
            continue
        content, _ = read_text(path)
        files += 1
        if content is None:
            continue
        for number, line in enumerate(content.split("\n"), 1):
            if needle in line.lower():
                hits.append(f"{path}:{number}: {line.strip()[:300]}")
                if len(hits) >= 60:
                    break
    return (f"{len(hits)} line(s) with \"{text}\" under {folder}"
            + (" (stopped at 60)" if len(hits) >= 60 else "") + f", {files} files looked at:\n" + "\n".join(hits)), "ok"


def guide_topics(r):
    """[(title, text)]: the topics of AIcove's guide, then the sections of the user's notes file."""
    found = []
    text, _ = read_text(GUIDE_FILE)
    for part in (text or "").split("\n## ")[1:]:
        title, _, body = part.partition("\n")
        found.append((title.strip(), body.strip()))
    notes = r["notes"]
    if notes and not never(notes, r):
        text, _ = read_text(notes)
        lines = (text or "").split("\n")
        # A section starts at a line that is underlined with --- or ===.
        heads = [i for i in range(len(lines) - 1) if lines[i].strip() and len(lines[i + 1].strip()) >= 3
                 and set(lines[i + 1].strip()) <= set("-=")]
        if text and (not heads or "\n".join(lines[:heads[0]]).strip()):
            found.append(("Notes", "\n".join(lines[:heads[0] if heads else None]).strip()))
        for i, end in zip(heads, heads[1:] + [len(lines)]):
            found.append(("Notes: " + lines[i].strip(), "\n".join(lines[i + 2:end]).strip()))
    return found


def do_own(r):
    own = r["own"]
    if not own:
        return "Only a persona has a folder of its own; this chat runs on the plain model.", "none"
    rows = []
    try:
        for part in OWN_PARTS:
            os.makedirs(os.path.join(own, part), exist_ok=True)
            for root, dirs, files in os.walk(os.path.join(own, part)):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for name in sorted(files):
                    full = os.path.join(root, name)
                    if not name.startswith(".") and not never(full, r):
                        rows.append(f"{full}  ({os.path.getsize(full)} bytes)")
    except OSError as e:
        return f"Can't look into {own}: {e.strerror or e}.", "failed"
    more = f"\n… and {len(rows) - 300} more; use list_folder" if len(rows) > 300 else ""
    return (f"Your own folder is {own}\n"
            f"- {own}/knowledge: what the user put there for you to work from\n"
            f"- {own}/notes: your notes from earlier chats. To remember something for later chats, "
            "write it to a file in there (write_file or edit_file, if you have them; the user approves it).\n\n"
            + (f"{len(rows)} file(s):\n" + "\n".join(rows[:300]) + more if rows else "It is still empty.")), "ok"


def do_guide(topic, part, r):
    topics = guide_topics(r)
    if not topics:
        return "There is no guide on this machine.", "missing"
    wanted = (topic or "").strip().lower()
    hits = [t for t in topics if t[0].lower() == wanted] or [t for t in topics if wanted and wanted in t[0].lower()]
    if len(hits) != 1:
        head = "Topics of the guide to this computer's setup" if not wanted else \
            (f"No topic is called \"{topic}\". The topics" if not hits else f"Several topics match \"{topic}\"")
        return head + ":\n" + "\n".join("- " + t[0] for t in (hits or topics)) + \
            "\nCall read_guide again with one of these as the topic.", "topics"
    title, text = hits[0]
    parts = max(1, -(-len(text) // MAX_OUTPUT))
    try:
        part = min(max(int(part), 1), parts)
    except (TypeError, ValueError):
        part = 1
    more = f" (part {part} of {parts}; call again with part={part + 1} for more)" if part < parts else \
        (f" (part {part} of {parts})" if parts > 1 else "")
    return f"{title}{more}\n\n" + text[(part - 1) * MAX_OUTPUT:part * MAX_OUTPUT], "ok"


def change_summary(path, old, new):
    """What the user is shown before saying yes."""
    if old is None:
        lines = new.split("\n")
        shown = "\n".join(lines[:25])
        return (f"NEW FILE: {path}\n{len(lines)} lines, {len(new.encode())} bytes\n\n{shown}"
                + ("\n…" if len(lines) > 25 else ""))
    diff = list(difflib.unified_diff(old.split("\n"), new.split("\n"), lineterm="", n=1))[2:]
    added = sum(1 for l in diff if l.startswith("+"))
    removed = sum(1 for l in diff if l.startswith("-"))
    shown = "\n".join(l[:200] for l in diff[:40])
    return (f"CHANGE: {path}\n{added} lines added, {removed} removed (the old version is kept)\n\n{shown}"
            + ("\n…" if len(diff) > 40 else ""))


def save(path, old, new):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = 0o644
    if old is not None:
        mode = stat.S_IMODE(os.stat(path).st_mode) & 0o666
        keep = os.path.join(STATE_DIR, "access-backups")
        os.makedirs(keep, exist_ok=True)
        shutil.copy2(path, os.path.join(keep, f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{os.path.basename(path)}"))
    tmp = path + ".aicove-new"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, path)


def confirmed(ask, title, message):
    """True only if the user said yes."""
    try:
        return ask is not None and ask(title, message) is True
    except Exception:
        return False


def change(path, new, persona, ask):
    """Put new text into a file after the user said yes."""
    r = rules(persona.id)
    path = real(path)
    refusal = why_not_write(path, r)
    if refusal:
        log(persona.name, "write", path, "refused")
        return refusal
    old = None
    if os.path.exists(path):
        old, note = read_text(path)
        if old is None:
            log(persona.name, "write", path, "failed")
            return note
    if old == new:
        return f"{path} already has exactly this text; nothing to do."
    if len(new.encode()) > MAX_BYTES:
        return f"Too big: a file written this way can have at most {MAX_BYTES // 1_000_000} MB."
    if not confirmed(ask, f"{persona.name} wants to write a file", change_summary(path, old, new)):
        log(persona.name, "write", path, "declined")
        return (f"The user did not approve writing {path}. Nothing was changed. "
                "Don't try again unless the user asks.")
    # What was approved is what is written: the file must not have changed while the question was open.
    now = read_text(path)[0] if os.path.exists(path) else None
    if now != old or why_not_write(real(path), rules(persona.id)):
        log(persona.name, "write", path, "failed")
        return f"{path} changed while the question was open. Nothing was written; read it again."
    try:
        save(path, old, new)
    except OSError as e:
        log(persona.name, "write", path, "failed")
        return f"Can't write {path}: {e.strerror or e}."
    log(persona.name, "write", path, "written")
    return f"Written: {path} ({len(new.split(chr(10)))} lines)." + (" The old version was kept." if old is not None else "")


# --- the tools as the model sees them ---------------------------------------------------

def tool(tool_name, description, **params):
    """One tool for llama-server's "tools" list. params: name -> (type, description, required)."""
    return {"type": "function", "function": {
        "name": tool_name, "description": description,
        "parameters": {"type": "object",
                       "properties": {k: {"type": t, "description": d} for k, (t, d, _) in params.items()},
                       "required": [k for k, (_, _, need) in params.items() if need]}}}


READ_TOOLS = [
    tool("list_folder", "List what is in a folder of this computer: names, sizes, and which entries are folders.",
         path=("string", "The folder, as a full path such as /etc or ~/Documents.", True)),
    tool("read_file", "Read a text file of this computer (also system files such as /etc/fstab or logs in "
         "/var/log). Long files come in parts: the answer says which lines it holds and how to get the next "
         "part. " + NO_COMMANDS,
         path=("string", "The file, as a full path.", True),
         start_line=("integer", "The first line to return (1 = from the top). 0 = the end of the file: the "
                                "last max_lines lines, which is what you want for logs.", False),
         max_lines=("integer", "How many lines to return at most (default 300).", False)),
    tool("find_files", "Find files by name below a folder, e.g. every file with \"fstab\" in its name, or *.conf.",
         folder=("string", "Where to look, as a full path. Everything below it is searched.", True),
         name=("string", "Part of the file name, or a pattern with * and ?.", True)),
    tool("search_text", "Find the lines that contain a text, in the text files below a folder (like grep -ri). "
         "Upper and lower case don't matter.",
         folder=("string", "Where to look, as a full path. Everything below it is searched.", True),
         text=("string", "The text to look for.", True),
         name=("string", "Only look in files whose name matches this, e.g. *.conf. Leave out for all files.", False)),
    tool("my_folder", "Your own folder on this computer, which only you have. Its knowledge folder holds files "
         "the user gave you to work from; its notes folder holds your notes from earlier chats. You remember "
         "nothing from earlier chats except what is written there, so look here when the user refers to "
         "earlier work, to your notes, or to documents they gave you. Returns the folder's path and the list "
         "of its files; read them with read_file or search them with search_text."),
    tool("read_guide", "Read the user's own guide to how this computer is set up: the AIcove app (launchers, "
         "personas, tools, passwords, keyboard, settings, what to do when something doesn't work) and the "
         "user's notes on what is installed here and which commands they use. Look here first when the user "
         "asks how something on this machine works, where something is, or what to do. Without a topic you "
         "get the list of topics.",
         topic=("string", "A topic from the list, e.g. Keyboard. Leave empty for the list of topics.", False),
         part=("integer", "For a long topic that comes in parts: which part.", False)),
]
WRITE_TOOLS = [
    tool("write_file", "Create a text file, or replace the whole text of one, in a folder the user allowed for "
         "writing. The user is shown the change and has to approve it first. For a small change to an "
         "existing file use edit_file instead.",
         path=("string", "The file, as a full path.", True),
         content=("string", "The complete new text of the file.", True)),
    tool("edit_file", "Change one place in an existing text file: old_text is replaced by new_text. old_text "
         "must be copied exactly from the file and occur in it exactly once, so include enough of the "
         "surrounding lines. The user is shown the change and has to approve it first.",
         path=("string", "The file, as a full path.", True),
         old_text=("string", "The exact text that is there now.", True),
         new_text=("string", "The text that goes in its place.", True)),
]
# What a persona's system prompt gets when it has tools.
HINT = ("You have tools for the files of this computer; use them when the user asks about files, "
        "about this machine or about your notes, instead of guessing. " + NO_COMMANDS)


def tools_for(persona):
    """The tools a persona gets from its "files:" line."""
    return {"read": READ_TOOLS, "write": READ_TOOLS + WRITE_TOOLS}.get(persona.files, [])


def edit(path, old_text, new_text, persona, ask):
    full = real(path)
    refusal = why_not_write(full, rules(persona.id))
    if refusal:
        log(persona.name, "edit", full, "refused")
        return refusal
    text, note = read_text(full)
    if text is None:
        return note
    count = text.count(old_text) if old_text else 0
    if count != 1:
        return (f"old_text was found {count} times in {full}; it has to be there exactly once. "
                "Read the file and copy the text exactly, with enough lines around it.")
    return change(full, text.replace(old_text, new_text, 1), persona, ask)


def run(name, arguments, persona, ask):
    """Carry out one tool call; returns the text the model gets back. ask(title, message) -> bool
    is how a write asks the user."""
    allowed = {t["function"]["name"] for t in tools_for(persona)}
    if name not in allowed:
        return f"There is no tool called {name} for you."
    try:
        a = json.loads(arguments or "{}") if isinstance(arguments, str) else dict(arguments or {})
        if not isinstance(a, dict):
            raise ValueError
    except ValueError:
        return "The arguments were not valid JSON. Call the tool again with proper arguments."
    r = rules(persona.id)
    try:
        if name == "list_folder":
            text, outcome = do_list(a["path"], r)
            log(persona.name, "list", real(a["path"]), outcome)
        elif name == "read_file":
            text, outcome = do_read(a["path"], a.get("start_line", 1), a.get("max_lines", 300), r)
            log(persona.name, "read", real(a["path"]), outcome)
        elif name == "find_files":
            text, outcome = do_find(a["folder"], a["name"], r)
            log(persona.name, "find", real(a["folder"]), outcome)
        elif name == "search_text":
            text, outcome = do_search(a["folder"], a["text"], a.get("name") or "*", r)
            log(persona.name, "search", real(a["folder"]), outcome)
        elif name == "my_folder":
            text, outcome = do_own(r)
            log(persona.name, "own folder", r["own"] or "-", outcome)
        elif name == "read_guide":
            text, outcome = do_guide(a.get("topic", ""), a.get("part", 1), r)
            log(persona.name, "guide", a.get("topic") or "(topics)", outcome)
        elif name == "write_file":
            text = change(a["path"], a["content"], persona, ask)
        else:
            text = edit(a["path"], a["old_text"], a["new_text"], persona, ask)
    except KeyError as e:
        return f"Missing argument {e} for {name}."
    except (TypeError, ValueError) as e:
        return f"Wrong argument for {name}: {e}."
    return text


def describe(name, arguments):
    """One line for the chat: what a tool call does."""
    try:
        a = json.loads(arguments or "{}")
    except ValueError:
        a = {}
    what = {"list_folder": "Looked into", "read_file": "Read", "find_files": "Looked for files in",
            "search_text": "Searched", "my_folder": "Looked into its own folder", "read_guide": "Read the guide",
            "write_file": "Write", "edit_file": "Change"}.get(name, name)
    target = a.get("path") or a.get("folder") or a.get("topic") or ""
    if name == "search_text" and a.get("text"):
        target += f" for \"{a['text']}\""
    if name == "find_files" and a.get("name"):
        target += f" ({a['name']})"
    return f"{what} {target}".strip()
