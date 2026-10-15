# A persona's knowledge: the files in personas/<id>/knowledge/ (text, Markdown, PDF, EPUB, ...),
# its own material, used whether or not it has file access. Small enough, all of it goes into
# the system prompt. Too big for that (books), the persona gets a list of the files and two
# tools that only reach into this folder: search_knowledge and read_knowledge. PDF, EPUB and
# Word files are turned into text once; the text is kept in knowledge/.text/ and made again
# when the file changes.

import html
import json
import os
import re
import subprocess
import zipfile

from . import personas

FOLDER = "knowledge"
CACHE = ".text"
PLAIN = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".ini", ".log",
         ".py", ".sh", ".c", ".h", ".js", ".css", ".tex", ".rst", ".org", ".srt"}
CONVERTED = {".pdf", ".epub", ".docx", ".odt", ".html", ".htm"}
# All of it in the prompt while it takes at most this share of the context.
SHARE = 0.25
# A rough count: tokens per character of ordinary text.
PER_CHAR = 1 / 3.5
MAX_OUTPUT = 12_000
HEADER = ("[Your knowledge: material the user gave you to work from. Use it, and say so when "
          "you take something from it.]")
TOOLS_HEADER = ("[Your knowledge: material the user gave you to work from. It is too long to "
                "show here; look things up with search_knowledge and read_knowledge whenever a "
                "question touches it, and say so when you take something from it. The files:]")

_cache = {}


def folder(pid):
    return os.path.join(personas.folder(pid), FOLDER)


def strip_tags(markup):
    markup = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>|</tr>", "\n", markup)
    text = html.unescape(re.sub(r"<[^>]+>", "", markup))
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def convert(path):
    """The text of a PDF, EPUB, Word/LibreOffice or HTML file, or None if it can't be had."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            out = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", path, "-"],
                                 capture_output=True, timeout=300)
            return out.stdout.decode("utf-8", errors="replace") if out.returncode == 0 else None
        if ext in (".html", ".htm"):
            with open(path, encoding="utf-8", errors="replace") as f:
                return strip_tags(f.read())
        with zipfile.ZipFile(path) as z:
            if ext == ".docx":
                return strip_tags(z.read("word/document.xml").decode("utf-8", errors="replace")
                                  .replace("</w:p>", "</p>"))
            if ext == ".odt":
                return strip_tags(z.read("content.xml").decode("utf-8", errors="replace")
                                  .replace("</text:p>", "</p>").replace("</text:h>", "</p>"))
            # EPUB: the chapters in the order of the archive, which is the book's order in practice.
            parts = [z.read(n).decode("utf-8", errors="replace") for n in z.namelist()
                     if n.lower().endswith((".xhtml", ".html", ".htm")) and "nav" not in n.lower()]
            return "\n\n".join(strip_tags(p) for p in parts)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, subprocess.SubprocessError):
        return None


def text_of(path):
    """The text of one knowledge file (converted ones from the cache), or None."""
    ext = os.path.splitext(path)[1].lower()
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None
    if (path, stamp) in _cache:
        return _cache[(path, stamp)]
    text = None
    if ext in PLAIN:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            text = None
    elif ext in CONVERTED:
        cached = os.path.join(os.path.dirname(path), CACHE, os.path.basename(path) + ".txt")
        try:
            if os.path.getmtime(cached) >= stamp:
                with open(cached, encoding="utf-8") as f:
                    text = f.read()
        except OSError:
            pass
        if text is None:
            text = convert(path)
            if text is not None:
                try:
                    os.makedirs(os.path.dirname(cached), exist_ok=True)
                    with open(cached, "w", encoding="utf-8") as f:
                        f.write(text)
                except OSError:
                    pass
    _cache[(path, stamp)] = text
    return text


def files(pid):
    """[(name relative to the folder, full path)] of the usable knowledge files, sorted."""
    base = folder(pid)
    found = []
    for root, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(names):
            if name.startswith(".") or os.path.splitext(name)[1].lower() not in PLAIN | CONVERTED:
                continue
            full = os.path.join(root, name)
            found.append((os.path.relpath(full, base), full))
    return found


def texts(pid):
    """[(name, text)] of the knowledge files that could be read."""
    return [(name, t) for name, full in files(pid) if (t := text_of(full)) and t.strip()]


def tokens(pid):
    return int(sum(len(t) for _, t in texts(pid)) * PER_CHAR)


def for_prompt(pid, n_ctx, tools_possible):
    """(text for the system prompt, whether the knowledge tools are needed)."""
    found = texts(pid)
    if not found:
        return "", False
    size = sum(len(t) for _, t in found) * PER_CHAR
    if size <= SHARE * n_ctx or not tools_possible:
        if size > SHARE * n_ctx:
            # Without tools only what fits can be shown; the rest is cut off.
            room = int(SHARE * n_ctx / PER_CHAR)
            out, used = [], 0
            for name, t in found:
                if used >= room:
                    break
                out.append((name, t[:room - used]))
                used += len(out[-1][1])
            found = out
        return HEADER + "\n\n" + "\n\n".join(f"=== {name} ===\n{t.strip()}" for name, t in found), False
    listing = "\n".join(f"- {name} ({len(t.splitlines())} lines){heads(t)}" for name, t in found)
    return f"{TOOLS_HEADER}\n{listing}", True


def heads(text):
    """The first few headings or chapter lines of a text, for the list of files."""
    found = [line.strip("# ").strip() for line in text.splitlines()
             if re.match(r"\s*(#{1,3}\s+\S|(chapter|kapitel|book|part|teil)\b)", line, re.I)][:8]
    return (": " + "; ".join(h[:60] for h in found)) if found else ""


# --- the tools ---------------------------------------------------------------------------------

def tool(name, description, **params):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object",
                       "properties": {k: {"type": t, "description": d} for k, (t, d, _) in params.items()},
                       "required": [k for k, (_, _, need) in params.items() if need]}}}


TOOLS = [
    tool("search_knowledge", "Search your knowledge (the material the user gave you) for passages with these "
         "words. Returns the best passages with file name and line number; read more around one with "
         "read_knowledge.",
         words=("string", "What to look for: a few telling words or a name. Upper/lower case doesn't matter.", True)),
    tool("read_knowledge", "Read a part of one of your knowledge files, from a line on.",
         file=("string", "The file name as in the list of your knowledge.", True),
         start_line=("integer", "The first line to read (1 = the beginning).", False),
         max_lines=("integer", "How many lines at most (default 150).", False)),
]
NAMES = {t["function"]["name"] for t in TOOLS}


def search(pid, words):
    terms = [w for w in re.findall(r"\w+", words.lower()) if len(w) > 1]
    if not terms:
        return "Give a few words to look for."
    hits = []
    for name, text in texts(pid):
        lines = text.splitlines()
        # Passages of 12 lines, overlapping by half.
        for start in range(0, max(len(lines), 1), 6):
            chunk = "\n".join(lines[start:start + 12])
            low = chunk.lower()
            score = sum(min(low.count(t), 3) for t in terms) + 2 * sum(1 for t in terms if t in low)
            if all(t in low for t in terms):
                score += 5
            if score > 2:
                hits.append((score, name, start + 1, chunk))
    if not hits:
        return f"Nothing in your knowledge matches \"{words}\". Try other words."
    hits.sort(key=lambda h: -h[0])
    out, used, seen = [], 0, set()
    for score, name, line, chunk in hits:
        if (name, line // 12) in seen:
            continue
        seen.add((name, line // 12))
        piece = f"--- {name}, from line {line} ---\n{chunk.strip()}"
        if used + len(piece) > MAX_OUTPUT or len(out) >= 8:
            break
        out.append(piece)
        used += len(piece)
    return f"{len(out)} best passages for \"{words}\":\n\n" + "\n\n".join(out)


def read(pid, file, start_line=1, max_lines=150):
    found = dict(texts(pid))
    name = file if file in found else next((n for n in found if os.path.basename(n) == os.path.basename(file)), None)
    if name is None:
        return f"There is no knowledge file called {file}. Yours: {', '.join(found) or 'none'}."
    lines = found[name].splitlines()
    start = max(1, int(start_line or 1))
    count = max(1, min(int(max_lines or 150), 600))
    part = "\n".join(lines[start - 1:start - 1 + count])[:MAX_OUTPUT]
    end = min(start - 1 + count, len(lines))
    more = f" More: start_line={end + 1}." if end < len(lines) else ""
    return f"{name}: lines {start}-{end} of {len(lines)}.{more}\n{part}"


def run(name, arguments, persona):
    try:
        a = json.loads(arguments or "{}") if isinstance(arguments, str) else dict(arguments or {})
    except ValueError:
        return "The arguments were not valid JSON. Call the tool again with proper arguments."
    try:
        if name == "search_knowledge":
            return search(persona.id, str(a["words"]))
        return read(persona.id, str(a["file"]), a.get("start_line", 1), a.get("max_lines", 150))
    except KeyError as e:
        return f"Missing argument {e} for {name}."
    except (TypeError, ValueError) as e:
        return f"Wrong argument for {name}: {e}."


def describe(name, arguments):
    try:
        a = json.loads(arguments or "{}")
    except ValueError:
        a = {}
    if name == "search_knowledge":
        return f"Searched its knowledge for \"{a.get('words', '')}\""
    return f"Read {a.get('file', '')} in its knowledge" + (f" from line {a['start_line']}" if a.get("start_line") else "")
