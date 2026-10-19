# Writing: long texts (a novel, a fic) as projects of chapters, one Markdown file each, in the
# AIcove folder:
#   writing/<id>/project.txt          title:, author:, git:, then one "chapter: <file> | <status>" line
#                                     per chapter, in reading order
#   writing/<id>/chapters/c001.md     the text; its first "# " line is the chapter's title, then one
#                                     line per paragraph (an empty line is extra space you put there)
#   writing/<id>/.trash/              deleted chapters
#   writing/<id>/.git/                with "git: on": a commit when a chapter was worked on
# Only text the user wrote or accepted goes into a chapter file. Import takes pasted text, an
# HTML file (an AO3 download, a saved page) or a PDF (through pdftotext), turns it into
# Markdown and splits it into chapters at their headings.

import html
import html.parser
import os
import re
import subprocess
import time

from .personas import SAFE_ID
from .settings import COVE_DIR

WRITING_DIR = os.path.join(COVE_DIR, "writing")
PROJECT_FILE = "project.txt"
STATUSES = ["draft", "done"]
CHAPTER_FILE = re.compile(r"c\d{3,}\.md")
# A line that starts a chapter: "Chapter 12", "Chapter 12: The Letter", "Kapitel 3", "Prologue",
# "Part Two", "12.", "XII", with or without Markdown #.
# Roman numerals only in capitals, so a line "I" or "c" in the text isn't one.
CHAPTER_LINE = re.compile(
    r"#{0,6}\s*(?:(?i:(?:chapter|chap\.|ch\.|kapitel|part|teil|book|buch)\s+(?!(?:end\s+)?(?:summary|notes)\b)[\w.-]+\b(?:\s*[:.|\-–—]\s*.*)?)"
    r"|(?i:(?:prolog(?:ue)?|epilog(?:ue)?|interlude|zwischenspiel|afterword|nachwort)\b(?:\s*[:.|\-–—]\s*.*)?)"
    r"|\d{1,3}\.?|[IVXLC]{2,7}\.?|[IVX]\.)")
IGNORED = ".trash/\n*.tmp\nsuggestions/\ntalk/\nreview/\n"
SPLITS = ["Chapter headings", "Markdown headings (# lines)", "Lines that start with…", "Don't split"]


def folder(pid):
    return os.path.join(WRITING_DIR, pid)


def words(text):
    return len(re.findall(r"\w[\w'’-]*", text))


def title_of(text, fallback=""):
    """The chapter's title: its first "# " line."""
    for line in text.split("\n"):
        if line.strip():
            m = re.match(r"#{1,6}\s+(.*)", line.strip())
            return m.group(1).strip() if m else fallback
    return fallback


def body_of(text):
    """The chapter without its title line."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.strip():
            return "\n".join(lines[i + 1:]) if re.match(r"#{1,6}\s", line.strip()) else text
    return text


class Project:
    def __init__(self, pid, title=""):
        self.id = pid
        self.title = title or pid
        self.author = ""
        self.persona = ""          # who the Writing panel talks with
        self.memory_model = ""     # the launcher that reads chapters for the story memory ("": the persona's)
        # "lines": one line per paragraph. Projects from before had an empty line between
        # paragraphs; to_lines() takes them over once.
        self.paragraphs = "lines"
        self.git = True
        # [[file, status]] in reading order
        self.chapters = []

    @property
    def path(self):
        return os.path.join(folder(self.id), PROJECT_FILE)

    @classmethod
    def parse(cls, pid, text):
        p = cls(pid)
        for line in text.replace("\r\n", "\n").split("\n"):
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "title":
                p.title = value or pid
            elif key == "author":
                p.author = value
            elif key == "persona":
                p.persona = value if SAFE_ID.fullmatch(value) else ""
            elif key == "paragraphs":
                p.paragraphs = value
            elif key == "memory model":
                p.memory_model = value if SAFE_ID.fullmatch(value) else ""
            elif key == "git":
                p.git = value != "off"
            elif key == "chapter":
                file, _, status = (v.strip() for v in value.partition("|"))
                if CHAPTER_FILE.fullmatch(file) and file not in (c[0] for c in p.chapters):
                    p.chapters.append([file, status if status in STATUSES else "draft"])
        return p

    @classmethod
    def read(cls, pid):
        with open(os.path.join(folder(pid), PROJECT_FILE), encoding="utf-8") as f:
            text = f.read()
        p = cls.parse(pid, text)
        if not re.search(r"^paragraphs:", text, re.M):
            p.paragraphs = "blank"
        # Chapter files put there by hand join at the end.
        listed = {c[0] for c in p.chapters}
        for name in sorted(os.listdir(p.chapter_dir)) if os.path.isdir(p.chapter_dir) else []:
            if CHAPTER_FILE.fullmatch(name) and name not in listed:
                p.chapters.append([name, "draft"])
        return p

    def text(self):
        lines = [f"title: {self.title}"] + ([f"author: {self.author}"] if self.author else []) \
            + ([f"persona: {self.persona}"] if self.persona else []) \
            + ([f"memory model: {self.memory_model}"] if self.memory_model else []) \
            + [f"paragraphs: {self.paragraphs}", f"git: {'on' if self.git else 'off'}", ""]
        lines += [f"chapter: {file} | {status}" for file, status in self.chapters]
        return "\n".join(lines) + "\n"

    def write(self):
        os.makedirs(self.chapter_dir, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(self.text())
        os.replace(tmp, self.path)

    # --- chapters ----------------------------------------------------------------

    @property
    def chapter_dir(self):
        return os.path.join(folder(self.id), "chapters")

    def chapter(self, file):
        try:
            with open(os.path.join(self.chapter_dir, file), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def save_chapter(self, file, text):
        os.makedirs(self.chapter_dir, exist_ok=True)
        path = os.path.join(self.chapter_dir, file)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)

    def free_file(self):
        taken = {c[0] for c in self.chapters}
        if os.path.isdir(self.chapter_dir):
            taken |= set(os.listdir(self.chapter_dir))
        n = 1
        while f"c{n:03d}.md" in taken:
            n += 1
        return f"c{n:03d}.md"

    def add_chapter(self, title, body="", after=None):
        """A new chapter after the one in file after (None: at the end). Returns its file."""
        file = self.free_file()
        self.save_chapter(file, f"# {title}\n{one_line_each(body.strip())}\n")
        at = next((i + 1 for i, c in enumerate(self.chapters) if c[0] == after), len(self.chapters))
        self.chapters.insert(at, [file, "draft"])
        self.write()
        return file

    def delete_chapter(self, file):
        trash = os.path.join(folder(self.id), ".trash")
        os.makedirs(trash, exist_ok=True)
        path = os.path.join(self.chapter_dir, file)
        if os.path.exists(path):
            os.replace(path, os.path.join(trash, f"{file[:-3]}-{time.strftime('%Y%m%d-%H%M%S')}.md"))
        self.chapters = [c for c in self.chapters if c[0] != file]
        self.write()
        if os.path.exists(suggestions_path(self, file)):
            os.remove(suggestions_path(self, file))

    def move_chapter(self, file, step):
        i = next(i for i, c in enumerate(self.chapters) if c[0] == file)
        j = i + step
        if 0 <= j < len(self.chapters):
            self.chapters[i], self.chapters[j] = self.chapters[j], self.chapters[i]
            self.write()

    def set_status(self, file, status):
        for c in self.chapters:
            if c[0] == file:
                c[1] = status
        self.write()

    # --- versions ----------------------------------------------------------------

    def run_git(self, *args):
        try:
            p = subprocess.run(["git", "-c", "user.name=AIcove", "-c", "user.email=aicove@localhost", *args],
                               cwd=folder(self.id), capture_output=True, text=True, timeout=60,
                               stdin=subprocess.DEVNULL)
            return p.returncode, (p.stdout + p.stderr).strip()
        except (OSError, subprocess.SubprocessError) as e:
            return 1, str(e)

    def commit(self, message):
        """With git on: one commit of whatever changed. Returns an error text or ""."""
        if not self.git:
            return ""
        if not os.path.isdir(os.path.join(folder(self.id), ".git")):
            code, out = self.run_git("init", "-q")
            if code:
                return f"git init: {out}"
            with open(os.path.join(folder(self.id), ".gitignore"), "w", encoding="utf-8") as f:
                f.write(IGNORED)
        ignore = os.path.join(folder(self.id), ".gitignore")
        try:
            with open(ignore, encoding="utf-8") as f:
                had = f.read()
        except OSError:
            had = ""
        if any(line not in had.split("\n") for line in IGNORED.split()):
            # Suggestions and talks are work in progress, not versions of the text.
            with open(ignore, "w", encoding="utf-8") as f:
                f.write(IGNORED)
            self.run_git("rm", "-r", "-q", "--cached", "--ignore-unmatch", "suggestions", "talk")
        self.run_git("add", "-A")
        if self.run_git("diff", "--cached", "--quiet")[0] != 0:
            code, out = self.run_git("commit", "-q", "-m", message)
            if code:
                return f"git commit: {out}"
        return ""


def one_line_each(text):
    """Text with an empty line between paragraphs as one line per paragraph."""
    return collapse(text)[0]


def collapse(text):
    """(text with one newline less in every run of them and no spaces at line ends, a function
    that tells where an index of the old text is in the new one)."""
    keep = [True] * len(text)
    for m in re.finditer(r"[ \t]+(?=\n)", text):
        for i in range(m.start(), m.end()):
            keep[i] = False
    for m in re.finditer(r"\n(?:[ \t]*\n)+", text):
        keep[m.start()] = False
    gone, before = 0, []
    for k in keep:
        before.append(gone)
        gone += 0 if k else 1
    before.append(gone)
    new = "".join(c for c, k in zip(text, keep) if k)
    return new, (lambda i: i - before[max(0, min(i, len(text)))])


def to_lines(project):
    """Take a project from before over: one line per paragraph instead of an empty line
    between them, its waiting changes, review notes and story memory moved along. Once."""
    import json
    index = read_index(project)
    for file, _ in project.chapters:
        old = project.chapter(file)
        new, where = collapse(old)
        if new != old:
            project.save_chapter(file, new)
        items = read_suggestions(project, file)
        for i in items:
            i["at"], i["old"], i["new"] = where(i["at"]), one_line_each(i["old"]), one_line_each(i["new"])
        write_suggestions(project, file, items)
        notes = read_notes(project, file)
        for n in notes:
            n["at"] = where(n["at"])
            for key in ("quote", "original"):
                if key in n:
                    n[key] = one_line_each(n[key])
        write_notes(project, file, notes)
        entry = index["notes"].get(file)
        if entry and entry.get("hash") == chapter_hash(old):
            entry["hash"] = chapter_hash(new)
    if index["notes"]:
        write_index(project, index)
    project.paragraphs = "lines"
    project.write()
    project.commit("One line per paragraph")


def load_all():
    found = {}
    names = sorted(os.listdir(WRITING_DIR)) if os.path.isdir(WRITING_DIR) else []
    for n in names:
        if SAFE_ID.fullmatch(n) and os.path.isfile(os.path.join(folder(n), PROJECT_FILE)):
            try:
                found[n] = Project.read(n)
            except OSError:
                continue
    return found


def new_id(name):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-") or "project"
    pid, n = base, 2
    while os.path.exists(folder(pid)):
        pid, n = f"{base}-{n}", n + 1
    return pid


def trash(pid):
    """The whole project (chapters, versions and all) into writing/.trash."""
    dest = os.path.join(WRITING_DIR, ".trash")
    os.makedirs(dest, exist_ok=True)
    os.replace(folder(pid), os.path.join(dest, f"{pid}-{time.strftime('%Y%m%d-%H%M%S')}"))


def create(title, chapters=(), git=True):
    """A new project with the chapters [(title, body)]."""
    p = Project(new_id(title), title)
    p.git = git
    p.write()
    for t, body in chapters:
        p.add_chapter(t, body)
    p.commit("Started")
    return p


# --- import ------------------------------------------------------------------------

class ToMarkdown(html.parser.HTMLParser):
    """HTML to Markdown, as far as a story needs it: paragraphs, headings, italics, bold,
    scene breaks, quotes, lists. Everything else becomes its text."""
    BLOCKS = {"p", "div", "section", "article", "blockquote", "li", "ul", "ol", "tr", "table",
              "h1", "h2", "h3", "h4", "h5", "h6", "dd", "dt", "dl", "pre", "center", "header", "footer"}
    SKIP = {"script", "style", "head", "title", "noscript", "svg", "nav", "button", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []          # finished paragraphs
        self.line = []         # pieces of the paragraph being read
        self.skip = 0
        self.heading = 0
        self.quote = 0

    def flush(self):
        text = re.sub(r"[ \t\r\n\xa0]+", " ", "".join(self.line)).strip()
        self.line = []
        if not text.strip("* "):
            # Markup around nothing ("* *"), or a scene break written as stars.
            if re.fullmatch(r"\*(\s*\*){2,}", text) and not self.heading:
                self.out.append("* * *")
            return
        if re.fullmatch(r"[~#=_•·\-–—]{3,}|([~#=_•·\-–—]\s+){2,}[~#=_•·\-–—]", text):
            self.out.append("* * *")
            return
        if self.heading:
            text = "#" * self.heading + " " + text.strip("* ")
        elif self.quote:
            text = "> " + text
        self.out.append(text)

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if self.skip:
            return
        if tag in self.BLOCKS:
            self.flush()
            if tag[0] == "h" and tag[1:].isdigit():
                self.heading = int(tag[1])
            elif tag == "blockquote":
                self.quote += 1
            elif tag == "li":
                self.line.append("- ")
        elif tag == "br":
            self.flush()
        elif tag == "hr":
            self.flush()
            self.out.append("* * *")
        elif tag in ("em", "i", "cite"):
            self.line.append("*")
        elif tag in ("strong", "b"):
            self.line.append("**")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in self.BLOCKS:
            self.flush()
            if tag[0] == "h" and tag[1:].isdigit():
                self.heading = 0
            elif tag == "blockquote":
                self.quote = max(0, self.quote - 1)
        elif tag in ("em", "i", "cite"):
            self.line.append("*")
        elif tag in ("strong", "b"):
            self.line.append("**")

    def handle_data(self, data):
        if not self.skip:
            self.line.append(data)

    def markdown(self):
        self.flush()
        # Spaces inside the markers: "* word *" -> " *word* ".
        fixed = [re.sub(r"(\*{1,2})(\s+)([^*]+?)(\s+)\1", r"\2\1\3\1\4", p) for p in self.out]
        fixed = [p if p == "* * *" else re.sub(r"(\*) +([.,;:!?…])", r"\1\2", re.sub(r" {2,}", " ", p))
                 for p in fixed]
        return "\n".join(fixed) + "\n"


def html_to_markdown(text):
    p = ToMarkdown()
    p.feed(text)
    p.close()
    return p.markdown()


def reflow(text):
    """Text out of a PDF: lines put back into paragraphs, page numbers and hyphenation at line
    ends taken out."""
    lines = []
    for page in text.split("\f"):
        page_lines = page.split("\n")
        # A page number alone on the first or last line of a page.
        while page_lines and not page_lines[-1].strip():
            page_lines.pop()
        if page_lines and re.fullmatch(r"\s*(page\s*)?\d+(\s*(of|/)\s*\d+)?\s*", page_lines[-1], re.I):
            page_lines.pop()
        if page_lines and re.fullmatch(r"\s*\d+\s*", page_lines[0]):
            page_lines.pop(0)
        lines += page_lines
    full = [line for line in lines if line.strip()]
    if not full:
        return ""
    # Without empty lines between paragraphs, a line clearly shorter than most ends one.
    blanks = sum(1 for line in lines if not line.strip())
    width = sorted(len(line) for line in full)[len(full) * 3 // 4]
    by_length = blanks < len(full) / 20
    paragraphs, current = [], ""
    for line in lines:
        s = line.strip()
        if not s:
            if current:
                paragraphs.append(current)
            current = ""
            continue
        if CHAPTER_LINE.fullmatch(s) and len(s) < 80:
            if current:
                paragraphs.append(current)
            paragraphs.append(s)
            current = ""
            continue
        if current.endswith("-") and s[:1].islower():
            current = current[:-1] + s
        else:
            current = f"{current} {s}" if current else s
        if by_length and len(s) < width * 0.8:
            paragraphs.append(current)
            current = ""
    if current:
        paragraphs.append(current)
    return "\n".join(paragraphs) + "\n"


def read_file(path):
    """The text of a file to import, as Markdown. Raises OSError with a reason."""
    ending = os.path.splitext(path)[1].lower()
    if ending == ".pdf":
        try:
            out = subprocess.run(["pdftotext", "-enc", "UTF-8", path, "-"], capture_output=True,
                                 text=True, errors="replace", timeout=120)
        except FileNotFoundError:
            raise OSError("pdftotext is missing (it comes with poppler)")
        except subprocess.TimeoutExpired:
            raise OSError("pdftotext took too long")
        if out.returncode != 0:
            raise OSError(f"pdftotext: {out.stderr.strip()[:300]}")
        return reflow(out.stdout)
    with open(path, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    if ending in (".html", ".htm", ".xhtml") or re.match(r"\s*(<!doctype html|<html)", text, re.I):
        return html_to_markdown(text)
    return text.replace("\r\n", "\n")


def split(text, how=SPLITS[0], start=""):
    """Text cut into chapters: [(title, body, is a chapter)]. What comes before the first
    heading is a piece of its own that isn't a chapter (title page, summary, notes)."""
    lines = text.replace("\r\n", "\n").split("\n")

    def heading(line):
        s = line.strip()
        if not s or len(s) > 120:
            return False
        if how == SPLITS[0]:
            return bool(CHAPTER_LINE.fullmatch(s))
        if how == SPLITS[1]:
            return bool(re.match(r"#{1,3}\s+\S", s))
        if how == SPLITS[2]:
            return bool(start.strip()) and s.lstrip("# ").lower().startswith(start.strip().lower())
        return False

    pieces, title, body, chapter = [], "", [], False
    for line in lines:
        if heading(line):
            if title or "".join(body).strip():
                pieces.append((title, "\n".join(body).strip(), chapter))
            title, body, chapter = re.sub(r"^#+\s*", "", line.strip()).strip("* "), [], True
        else:
            body.append(line)
    if title or "".join(body).strip():
        pieces.append((title, "\n".join(body).strip(), chapter))
    if len(pieces) == 1 and not pieces[0][2]:
        pieces = [("Chapter 1", pieces[0][1], True)]
    return [(t or "Before the first chapter", b, c) for t, b, c in pieces]


# --- export ------------------------------------------------------------------------
# A whole project as one file: Markdown, plain text, HTML, EPUB or Word (.docx), all without
# extra programs. PDF is made by the window (Qt prints the HTML).

EXPORTS = [("Markdown", ".md"), ("Plain text", ".txt"), ("Web page (HTML)", ".html"), ("PDF", ".pdf"),
           ("E-book (EPUB)", ".epub"), ("Word (.docx)", ".docx")]
INLINE = re.compile(r"(\*\*[^*\n]+?\*\*|\*[^*\n]+?\*|_[^_\n]+?_)")


def export_chapters(project, only_done=False):
    """[(title, body as Markdown)] in reading order."""
    return [(title_of(text, f"Chapter {n}"), body_of(text).strip())
            for n, (file, status) in enumerate(project.chapters, 1)
            if not only_done or status == "done"
            for text in [project.chapter(file)]]


def as_markdown(body):
    """A chapter's lines as Markdown paragraphs (Markdown wants an empty line between them)."""
    return re.sub(r"\n", "\n\n", body.strip())


def to_markdown(title, chapters):
    return f"# {title}\n\n" + "\n\n".join(f"## {t}\n\n{as_markdown(b)}" for t, b in chapters) + "\n"


def to_plain(title, chapters):
    def plain(text):
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
        text = re.sub(r"^>\s?", "    ", text, flags=re.M)
        return INLINE.sub(lambda m: m.group(0).strip("*_"), text)
    return f"{title}\n\n\n" + "\n\n\n".join(f"{t}\n\n{plain(b)}" for t, b in chapters) + "\n"


def md_html(text):
    import markdown
    md = markdown.Markdown(extensions=["sane_lists"], output_format="xhtml")
    return md.convert(re.sub(r"^\* \* \*$", '<p class="break">* * *</p>', as_markdown(text), flags=re.M))


STYLE = ("body { font-family: Georgia, serif; line-height: 1.5; max-width: 40em; margin: 2em auto; "
         "padding: 0 1em; } h1, h2 { text-align: center; } h2 { margin-top: 3em; } "
         ".break { text-align: center; } blockquote { font-style: italic; }")


def for_posting(body):
    """A chapter's text as the HTML a site like AO3 takes: a <p> per paragraph, <em> and
    <strong>, scene breaks centred. No title (the site has its own field for it)."""
    out = md_html(body).replace('<p class="break">* * *</p>', '<p align="center">* * *</p>')
    return re.sub(r"\n{2,}", "\n", out).strip() + "\n"


def to_html(title, chapters, page_breaks=False):
    brk = ' style="page-break-before: always"' if page_breaks else ""
    parts = [f"<h2{brk}>{html.escape(t)}</h2>\n{md_html(b)}" for t, b in chapters]
    return (f'<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f"<style>{STYLE}</style></head>\n<body>\n<h1>{html.escape(title)}</h1>\n"
            + "\n".join(parts) + "\n</body></html>\n")


def write_epub(path, title, chapters, author=""):
    import uuid
    import zipfile
    book = f"urn:uuid:{uuid.uuid4()}"
    xhtml = ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml">'
             '<head><meta charset="utf-8"/><title>{t}</title><link rel="stylesheet" href="style.css"/></head>'
             '<body>{b}</body></html>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile '
                   'full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
                   zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/style.css", STYLE, zipfile.ZIP_DEFLATED)
        items, spine, nav = [], [], []
        for n, (t, b) in enumerate(chapters, 1):
            name = f"c{n:03d}.xhtml"
            z.writestr(f"OEBPS/{name}", xhtml.format(t=html.escape(t), b=f"<h2>{html.escape(t)}</h2>{md_html(b)}"),
                       zipfile.ZIP_DEFLATED)
            items.append(f'<item id="c{n}" href="{name}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{n}"/>')
            nav.append(f'<li><a href="{name}">{html.escape(t)}</a></li>')
        z.writestr("OEBPS/nav.xhtml", xhtml.format(t="Contents", b=f'<nav xmlns:epub="http://www.idpf.org/2007/ops" '
                   f'epub:type="toc"><h1>{html.escape(title)}</h1><ol>{"".join(nav)}</ol></nav>')
                   .replace("<html ", '<html xmlns:epub="http://www.idpf.org/2007/ops" '), zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", f'<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
                   f'version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   f'<dc:identifier id="id">{book}</dc:identifier><dc:title>{html.escape(title)}</dc:title>'
                   f'<dc:creator>{html.escape(author)}</dc:creator><dc:language>en</dc:language>'
                   f'<meta property="dcterms:modified">{time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}</meta>'
                   f'</metadata><manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
                   f'properties="nav"/><item id="css" href="style.css" media-type="text/css"/>{"".join(items)}'
                   f'</manifest><spine>{"".join(spine)}</spine></package>', zipfile.ZIP_DEFLATED)


def docx_runs(text):
    """Markdown inline text as Word runs: plain, *italic*, **bold**."""
    from xml.sax.saxutils import escape
    runs = []
    for part in INLINE.split(text):
        if not part:
            continue
        props = ""
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            part, props = part[2:-2], "<w:b/>"
        elif part[0] in "*_" and part[-1] == part[0] and len(part) > 2:
            part, props = part[1:-1], "<w:i/>"
        runs.append(f'<w:r>{f"<w:rPr>{props}</w:rPr>" if props else ""}'
                    f'<w:t xml:space="preserve">{escape(part)}</w:t></w:r>')
    return "".join(runs)


def write_docx(path, title, chapters):
    import zipfile

    def para(text, style="", center=False, page_break=False):
        ppr = (f'<w:pStyle w:val="{style}"/>' if style else "") + ('<w:jc w:val="center"/>' if center else "")
        ppr += '<w:pageBreakBefore/>' if page_break else ""
        return f'<w:p>{f"<w:pPr>{ppr}</w:pPr>" if ppr else ""}{docx_runs(text)}</w:p>'

    body = [para(title, "Title")]
    for t, b in chapters:
        body.append(para(t, "Heading1", page_break=True))
        for p in b.split("\n"):
            p = p.strip()
            if not p:
                continue
            if p == "* * *":
                body.append(para("* * *", center=True))
            elif p.startswith(">"):
                body.append(para(re.sub(r"^>\s?", "", p), "Quote"))
            else:
                m = re.match(r"(#{1,6})\s+(.*)", p)
                body.append(para(m.group(2), "Heading2") if m else para(p))
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {ns}>'
              '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Georgia" w:hAnsi="Georgia"/><w:sz w:val="24"/>'
              '</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="300" w:lineRule="auto"/>'
              '</w:pPr></w:pPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
              '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:pPr><w:jc w:val="center"/>'
              '</w:pPr><w:rPr><w:b/><w:sz w:val="48"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:jc w:val="center"/>'
              '<w:spacing w:before="480" w:after="240"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:pPr><w:outlineLvl w:val="1"/>'
              '</w:pPr><w:rPr><w:b/><w:sz w:val="28"/></w:rPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:pPr><w:ind w:left="720"/></w:pPr>'
              '<w:rPr><w:i/></w:rPr></w:style></w:styles>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types '
                   'xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" '
                   'ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" '
                   'ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/'
                   'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/'
                   'styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://'
                   'schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
                   'openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/_rels/document.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships '
                   'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://'
                   'schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("word/styles.xml", styles)
        z.writestr("word/document.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {ns}>'
                   f'<w:body>{"".join(body)}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" '
                   f'w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr></w:body></w:document>')


def export(path, kind, title, chapters, author=""):
    """Write one of EXPORTS but PDF. Raises OSError."""
    if kind == ".epub":
        write_epub(path, title, chapters, author)
    elif kind == ".docx":
        write_docx(path, title, chapters)
    else:
        text = {".md": to_markdown, ".txt": to_plain, ".html": to_html}[kind](title, chapters)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)


# --- talking to a model about the text ----------------------------------------------
# The Writing tab's panel: a persona reads the open chapter and talks about it. When it
# changes text it writes the new version between <rewrite> and </rewrite>; that is compared
# with the old one word by word and every difference becomes a suggestion, shown in the
# chapter in colour until it is accepted or declined:
#   writing/<id>/suggestions/c001.json   [{at, old, new, by, time}], at = where old starts
#                                        in the chapter file
#   writing/<id>/talk/<chat id>.json     the conversation (chat.py's format)
#   writing/templates/<name>.txt         name:, on: passage|chat, style:, empty line, the prompt
# Nothing the model writes reaches a chapter file before it is accepted.

TEMPLATE_DIR = os.path.join(WRITING_DIR, "templates")
REWRITE = re.compile(r"<rewrite>\s*(.*?)\s*(?:</rewrite>|$)", re.S)
# A passage of the chapter sent at once when a template goes over the whole chapter.
PART_WORDS = 600

CONTENT_RULES = """You are working on the user's own fiction, in the Writing tab of AIcove.
The text is the user's. It may contain dark themes, violence, explicit sex, abuse, drugs and crude or offensive language. That is deliberate. Keep all of it exactly as explicit and intense as written. Never soften, cut, censor, euphemise, moralise, add warnings or disclaimers, or refuse.
Keep the user's voice, tense, point of view, names and their spelling (British or American as they use it) unless they ask otherwise."""

RULES = CONTENT_RULES + """
When the user asks you to change, fix, polish, rewrite, expand, shorten or write text, put the complete new version of the passage between <rewrite> and </rewrite>, one block only. It replaces the selected passage; with nothing selected it is new text that goes in where the cursor is. Write it as plain text with *italics* and **bold** as in the chapter, each paragraph on a line of its own with no empty line between paragraphs. Put any comments outside the block, short. The user sees the changes in the text and accepts or declines each one.
When the user only asks a question or wants your opinion, answer without a <rewrite> block."""

# Said again at the end of a request: small models forget what only the system prompt says.
REMINDER = ("(Only if I asked you to change or write text: give the complete new version between "
            "<rewrite> and </rewrite>, comments outside it. If I asked a question or for your opinion, just "
            "answer, without a <rewrite> block.)")

PASSAGE_RULES = """Answer with the new version of the passage between <rewrite> and </rewrite> and nothing else: no comments, no explanation, no quotes around it. If nothing needs changing, give the passage back unchanged."""

# The templates a new install starts with: one of each kind, short, to be changed or replaced
# by your own (they are text files in the templates folder).
TEMPLATES = {
    "English polish": ("passage", "Factual",
        "Fix the grammar, spelling and punctuation of this passage. Keep the author's voice and every bit of "
        "the content. Change as little as possible."),
    "Review": ("review", "",
        "Review this text as an editor: what could be better, and why. Leave alone what works."),
    "Feedback": ("chat", "",
        "Read the chapter and tell me what works and what doesn't."),
}


# Defaults as they were once written into the templates folder; a file still exactly like one
# of these is brought up to date (or removed, for a template that is gone).
OLD_TEMPLATES = {
    "Line edit": [("passage", "",
        "Line-edit this passage: tighten flabby sentences, sharpen verbs, fix clumsy rhythm, repetition and "
        "unclear bits. Keep my voice, the content and its intensity, and the length roughly the same.")],
    "English polish": [("passage", "Factual",
        "Fix the English of this passage: grammar, spelling, punctuation, word order, wrong prepositions and "
        "phrasing that sounds non-native. Keep my voice, my word choices where they work, the tense, the "
        "point of view and every bit of the content, dark, violent, sexual or crude parts included, exactly as "
        "intense. Change as little as possible: no new sentences, no cuts, no restyling.")],
}


def template_text(name, on, style, prompt):
    return f"name: {name}\non: {on}\nstyle: {style}\n\n{prompt}\n"


class Template:
    def __init__(self, name, on="chat", style="", prompt=""):
        self.name, self.on, self.style, self.prompt = name, on, style, prompt

    @property
    def changes(self):
        return self.on == "passage"

    @property
    def reviews(self):
        return self.on == "review"


def template_path(name):
    return os.path.join(TEMPLATE_DIR, re.sub(r"[^\w .-]+", "", name).strip() + ".txt")


def templates():
    """The templates. Each default is written into the folder once (a new one in a later
    version too); one you deleted stays deleted, one you changed stays as you made it."""
    os.makedirs(TEMPLATE_DIR, exist_ok=True)
    made_path = os.path.join(TEMPLATE_DIR, ".made")
    made = set(read_page(made_path).split("\n")) - {""}
    if not os.path.exists(made_path):
        made = {n[:-4] for n in os.listdir(TEMPLATE_DIR) if n.endswith(".txt")}
    for name, versions in OLD_TEMPLATES.items():
        path = template_path(name)
        if read_page(path) in [template_text(name, *v) for v in versions]:
            if name in TEMPLATES:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(template_text(name, *TEMPLATES[name]))
            else:
                os.remove(path)
    for name, (on, style, prompt) in TEMPLATES.items():
        if name not in made:
            if not os.path.exists(template_path(name)):
                with open(template_path(name), "w", encoding="utf-8") as f:
                    f.write(template_text(name, on, style, prompt))
            made.add(name)
    with open(made_path, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(made)) + "\n")
    found = []
    for n in sorted(os.listdir(TEMPLATE_DIR)):
        if not n.endswith(".txt"):
            continue
        try:
            with open(os.path.join(TEMPLATE_DIR, n), encoding="utf-8") as f:
                head, _, prompt = f.read().replace("\r\n", "\n").partition("\n\n")
        except OSError:
            continue
        t = Template(n[:-4], prompt=prompt.strip())
        for line in head.split("\n"):
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "name" and value:
                t.name = value
            elif key == "on":
                t.on = value if value in ("passage", "review") else "chat"
            elif key == "style":
                t.style = value
        if t.prompt:
            found.append(t)
    order = list(TEMPLATES)
    return sorted(found, key=lambda t: (order.index(t.name) if t.name in order else len(order), t.name.lower()))


def rewrite_in(answer):
    """The text of the <rewrite> block in an answer, or None."""
    m = REWRITE.search(answer)
    if not m:
        return None
    text = re.sub(r"^```\w*\n|\n```$", "", m.group(1).strip()).strip()
    return text


def without_rewrite(answer, note):
    """An answer as shown in the panel: the block replaced by a note."""
    return REWRITE.sub(f"*{note}*", answer, count=1).strip() if REWRITE.search(answer) else answer


TOKEN = re.compile(r"\s+|\w+|[^\w\s]")


def same_typography(old, new):
    """new with the quotes, apostrophes and ellipses written the way old writes them: models
    turn “curly” quotes into "straight" ones, which would be a change on every line."""
    def curly(text, straight, opening, closing):
        out = []
        for i, c in enumerate(text):
            if c == straight:
                before = text[i - 1] if i else " "
                c = opening if before.isspace() or before in "([{—–-“‘" else closing
            out.append(c)
        return "".join(out)
    if ("“" in old or "”" in old) and '"' not in old:
        new = curly(new, '"', "“", "”")
    if ("’" in old or "‘" in old) and "'" not in old:
        new = curly(new, "'", "‘" if "‘" in old else "’", "’")
    if "…" in old and "..." not in old:
        new = new.replace("...", "…")
    return new


def differences(old, new, at, by=""):
    """old (which starts at position at in the chapter) against new, word by word: one
    suggestion per difference, nearby ones put together so they read as a phrase."""
    import difflib
    new = same_typography(old, new)
    if "\n\n" not in old.strip():
        new = re.sub(r"\n\s*\n", "\n", new)
    a, b = TOKEN.findall(old), TOKEN.findall(new)
    ops = [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()]
    # Changes with only a word or two (or a space) between them become one.
    merged = []
    for op in ops:
        if op[0] != "equal" and merged and merged[-1][0] != "equal":
            prev = merged.pop()
            merged.append(("replace", prev[1], op[2], prev[3], op[4]))
        elif op[0] != "equal" and len(merged) >= 2 and merged[-1][0] == "equal" \
                and merged[-1][2] - merged[-1][1] <= 3 and merged[-2][0] != "equal" \
                and not any("\n" in t for t in a[merged[-1][1]:merged[-1][2]]):
            merged.pop()
            prev = merged.pop()
            merged.append(("replace", prev[1], op[2], prev[3], op[4]))
        else:
            merged.append(op)
    found, now = [], time.time()
    starts = [0]
    for t in a:
        starts.append(starts[-1] + len(t))
    for tag, i1, i2, j1, j2 in merged:
        if tag == "equal":
            continue
        found.append({"at": at + starts[i1], "old": "".join(a[i1:i2]), "new": "".join(b[j1:j2]),
                      "by": by, "time": now})
    return found


def parts(text, start=0, end=None):
    """The chapter text from start to end cut into passages of about PART_WORDS words, at
    paragraph ends: [(where it starts, the passage)]. The title line is left out."""
    end = len(text) if end is None else end
    title = re.match(r"\s*#{1,6}\s[^\n]*\n", text)
    start = max(start, title.end() if title else 0)
    found, begin, count = [], None, 0
    for m in re.finditer(r"[^\n]+", text[start:end]):
        if begin is None:
            begin = start + m.start()
        count += words(m.group(0))
        if count >= PART_WORDS:
            found.append((begin, text[begin:start + m.end()]))
            begin, count = None, 0
    if begin is not None:
        stop = start + len(text[start:end].rstrip())
        if text[begin:stop].strip():
            found.append((begin, text[begin:stop]))
    return found


def matching_passage(text, new):
    """Where in text the passage is that new is a version of: (start, end), or None. For a
    rewrite that came without a selection: a reworked paragraph replaces that paragraph instead
    of going in a second time."""
    import difflib
    paras = [(m.start(), m.end()) for m in re.finditer(r"[^\n]+", text) if m.group(0).strip()]
    k = max(1, len([p for p in new.strip().split("\n") if p.strip()]))
    best, where = 0.0, None
    for size in {max(1, k - 1), k, k + 1}:
        for i in range(0, max(0, len(paras) - size) + 1):
            start, end = paras[i][0], paras[i + size - 1][1]
            m = difflib.SequenceMatcher(None, text[start:end], new, autojunk=False)
            if m.real_quick_ratio() <= best or m.quick_ratio() <= best:
                continue
            r = m.ratio()
            if r > best:
                best, where = r, (start, end)
    return where if best >= 0.6 else None


def suggestions_path(project, file):
    return os.path.join(folder(project.id), "suggestions", file[:-3] + ".json")


def read_suggestions(project, file):
    import json
    try:
        with open(suggestions_path(project, file), encoding="utf-8") as f:
            data = json.load(f)
        return [s for s in data if isinstance(s, dict) and isinstance(s.get("at"), int)
                and isinstance(s.get("old"), str) and isinstance(s.get("new"), str)]
    except (OSError, ValueError):
        return []


def write_suggestions(project, file, items):
    import json
    path = suggestions_path(project, file)
    if not items:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def locate(text, item, near=None):
    """Where the suggestion's old text is in the chapter now: at its place, else the nearest
    place it occurs. None when it is not there any more."""
    at, old = item["at"], item["old"]
    if 0 <= at <= len(text) and text[at:at + len(old)] == old:
        return at
    if not old:
        return None
    places = [m.start() for m in re.finditer(re.escape(old), text)]
    if not places:
        return None
    best = min(places, key=lambda p: abs(p - (near if near is not None else at)))
    return best if len(old) >= 12 or abs(best - at) < 400 else None


def project_chat_dir(project):
    return os.path.join(folder(project.id), "talk")


# --- the story memory (bible) -------------------------------------------------------
# What a model needs to know about the whole story without reading all of it each time:
#   bible/chapters/c001.md   the archivist's note on a chapter: summary, characters, facts,
#                            threads, time
#   bible/cast.md            the characters, made from all the notes
#   bible/threads.md         open and closed threads, made from all the notes
#   bible/style.md           yours only: what the writing is going for (tone, readers, pace…)
#   bible/plans.md           yours only: where the story is going, what must not be forgotten
#   bible/.model/            what the model last wrote, to see what you changed
#   bible/index.json         per chapter note: the hash of the chapter it was made from, and
#                            whether you locked it; per made page the sections you locked
# A note or section you edit is locked: building again never overwrites it. The archivist has
# no persona and records what happens plainly, nothing left out or softened.

import hashlib

BIBLE_PAGES = [("style.md", "Style (yours)"), ("cast.md", "Cast"), ("threads.md", "Threads"),
               ("plans.md", "Plans (yours)")]
# Pages only you write; the models read them.
USER_PAGES = ("style.md", "plans.md")


def style_of(project):
    """The author's style page, worded for a prompt, or ""."""
    style = read_page(os.path.join(bible_dir(project), "style.md")).strip()
    return ("What the author is going for: the style, tone and readers this story is written for. Judge and "
            "write by this, not by your own taste:\n<style>\n" + style + "\n</style>") if style else ""
MADE_PAGES = ["cast.md", "threads.md"]

ARCHIVIST = """You are the archivist of a novel. You keep its story bible: plain records of what the text says, for a writer and an editor to check continuity against.
Record only what is in the text. No interpretation, no praise or criticism, no guessing about what comes later. The novel may contain violence, abuse, explicit sex, drugs and crude language: record such events plainly and accurately as what happens, without softening, euphemism, judgement or warnings.
Use the names as the text uses them; put nicknames and titles in brackets after a name the first time."""

NOTE_TASK = """Write the bible entry for this chapter, in exactly this form (Markdown, no other headings, no introduction):

### Summary
What happens, in order, 5 to 10 sentences.

### Characters
- Name: what they do, learn, decide or reveal here; changes in how they stand with others.

### Facts
- Details that must stay consistent later: places, objects, injuries, ages, dates, rules of magic or the world, promises, deals, secrets and who knows them.

### Threads
- opened: a question, conflict or promise the chapter sets up
- continued: one that goes on
- closed: one that is resolved

### Time
When it happens and how much time passes, if the text says so; else "not stated"."""

CAST_TASK = """From the chapter entries below, write the cast list of the story so far. One section per character who matters, the most important first:

## Name (other names)
Who they are; how they stand with the others; what they know and keep secret; how they have changed; the chapter they were last seen in.

Only what the entries say. Markdown, no introduction."""

THREADS_TASK = """From the chapter entries below, list the story's threads: the questions, conflicts, promises and setups. Two sections:

## Open
- the thread (chapter it started; where it stands now)

## Closed
- the thread (chapter it started, chapter it closed)

Only what the entries say. Markdown, no introduction."""


CAST_UPDATE = """Update the cast list with what this chapter adds. Write ONLY the entries of characters who appear in the chapter or are changed by it, each complete (what was known before plus what is new), in this form:

## Name (other names)
Who they are; how they stand with the others; what they know and keep secret; how they have changed; the chapter they were last seen in.

For a character already in the list use exactly the same "## " heading line. Leave out everyone the chapter does not touch. Only what the text says. Markdown, no introduction."""

THREADS_UPDATE = """Update the list of the story's threads (questions, conflicts, promises, setups) with this chapter: add the ones it opens, move the ones it resolves to Closed, update where the others stand. Write the complete list, in this form:

## Open
- the thread (chapter it started; where it stands now)

## Closed
- the thread (chapter it started, chapter it closed)

Only what the text says. Markdown, no introduction."""


def bible_dir(project):
    return os.path.join(folder(project.id), "bible")


def note_path(project, file):
    return os.path.join(bible_dir(project), "chapters", file)


def chapter_hash(text):
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:16]


def read_index(project):
    import json
    try:
        with open(os.path.join(bible_dir(project), "index.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data.setdefault("notes", {})
    data.setdefault("locked", {})
    return data


def write_index(project, data):
    import json
    os.makedirs(bible_dir(project), exist_ok=True)
    path = os.path.join(bible_dir(project), "index.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def read_page(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def write_page(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
    os.replace(path + ".tmp", path)


def model_copy(project, name):
    """Where what the model last wrote for a page or note is kept (name: cast.md, chapters/c001.md)."""
    return os.path.join(bible_dir(project), ".model", name)


def note_state(project, file, index=None):
    """"none" (no note yet), "ok", "outdated" (the chapter changed since), "locked"."""
    index = index or read_index(project)
    entry = index["notes"].get(file)
    if entry is None or not os.path.exists(note_path(project, file)):
        return "none"
    if entry.get("locked"):
        return "locked"
    return "ok" if entry.get("hash") == chapter_hash(project.chapter(file)) else "outdated"


def sections(text):
    """A page as [(heading line, its text)], the part before the first "## " with heading ""."""
    found, head, body = [], "", []
    for line in text.split("\n"):
        if line.startswith("## "):
            found.append((head, "\n".join(body).strip()))
            head, body = line.strip(), []
        else:
            body.append(line)
    found.append((head, "\n".join(body).strip()))
    return [(h, b) for h, b in found if h or b]


def key_of(head):
    """A section's name without what is in brackets, to recognise it again."""
    return re.sub(r"\s*\(.*?\)", "", head[3:]).strip().lower()


def save_by_user(project, name, text):
    """A bible page or note as you saved it. What differs from the model's version is locked:
    a whole chapter note, or the sections of a made page."""
    path = note_path(project, name[9:]) if name.startswith("chapters/") else os.path.join(bible_dir(project), name)
    write_page(path, text)
    index = read_index(project)
    model = read_page(model_copy(project, name))
    if name.startswith("chapters/"):
        file = name[9:]
        entry = index["notes"].setdefault(file, {"hash": ""})
        entry["locked"] = text.strip() != model.strip()
    elif name in MADE_PAGES:
        theirs = {key_of(h): b for h, b in sections(model) if h}
        index["locked"][name] = sorted(key_of(h) for h, b in sections(text)
                                      if h and theirs.get(key_of(h), None) != b)
    write_index(project, index)


def merge_sections(page, update, locked):
    """page with the "## " sections of update put in: a section with the same name is replaced,
    a new one added at the end. Locked names (yours) stay as they are."""
    locked = set(locked)
    have = sections(page)
    keys = [key_of(h) if h else None for h, _ in have]
    for h, b in sections(update):
        if not h:
            continue
        k = key_of(h)
        if k in locked:
            continue
        if k in keys:
            have[keys.index(k)] = (h, b)
        else:
            have.append((h, b))
            keys.append(k)
    return "\n\n".join((f"{h}\n{b}" if h else b).strip() for h, b in have if h or b)


def merge_locked(project, name, made, index):
    """A page the model just made, with your locked sections in place of its versions."""
    locked = set(index["locked"].get(name, []))
    if not locked:
        return made
    mine = {key_of(h): (h, b) for h, b in sections(read_page(os.path.join(bible_dir(project), name))) if h}
    out, done = [], set()
    for h, b in sections(made):
        k = key_of(h) if h else None
        if k in locked and k in mine:
            h, b = mine[k]
            done.add(k)
        out.append((h, b))
    out += [mine[k] for k in sorted(locked) if k in mine and k not in done]
    return "\n\n".join((f"{h}\n{b}" if h else b).strip() for h, b in out)


def notes_text(project, upto=None, short=False, before=None):
    """The chapter notes in reading order, as one text. short: only the summaries. before: only
    the chapters before that one."""
    parts = []
    for n, (file, _) in enumerate(project.chapters, 1):
        if file == before:
            break
        note = read_page(note_path(project, file)).strip()
        if not note:
            continue
        if short:
            m = re.search(r"### Summary\s*\n(.*?)(?=\n### |\Z)", note, re.S)
            note = m.group(1).strip() if m else note[:600]
        title = title_of(project.chapter(file), f"Chapter {n}")
        if not re.match(r"(chapter|kapitel|prolog|epilog|part|teil)\b", title, re.I):
            title = f"Chapter {n}: {title}"
        mark = " (the chapter open now)" if file == upto else ""
        parts.append(f"## {title}{mark}\n{note}")
    return "\n\n".join(parts)


def for_prompt(project, open_file, room):
    """The story memory for a request about open_file, in about room characters: plans, cast,
    threads and the chapter notes; with too little room the notes shrink to their summaries,
    then to the chapters nearest the open one."""
    style = style_of(project)
    plans = read_page(os.path.join(bible_dir(project), "plans.md")).strip()
    cast = read_page(os.path.join(bible_dir(project), "cast.md")).strip()
    threads = read_page(os.path.join(bible_dir(project), "threads.md")).strip()
    head = []
    if plans:
        head.append(f"<plans>\nThe author's plans (what is meant to happen; not yet in the text):\n{plans}\n</plans>")
    if cast:
        head.append(f"<cast>\n{cast}\n</cast>")
    if threads:
        head.append(f"<threads>\n{threads}\n</threads>")
    fixed = "\n\n".join(head)
    left = room - len(fixed) - len(style)
    notes = notes_text(project, open_file)
    if len(notes) > left:
        notes = notes_text(project, open_file, short=True)
    if len(notes) > left > 0:
        # Keep the chapters around the open one.
        parts = notes.split("\n\n## ")
        here = next((i for i, p in enumerate(parts) if "(the chapter open now)" in p.split("\n")[0]), len(parts) - 1)
        keep, size, lo, hi = {here}, len(parts[here]), here - 1, here + 1
        while lo >= 0 or hi < len(parts):
            for i in (lo, hi):
                if 0 <= i < len(parts) and size + len(parts[i]) < left:
                    keep.add(i)
                    size += len(parts[i])
            lo, hi = lo - 1, hi + 1
        notes = "\n\n## ".join(parts[i] for i in sorted(keep))
    if not fixed and not notes.strip():
        return style
    body = fixed + (f"\n\n<chapter_notes>\n{notes}\n</chapter_notes>" if notes.strip() and left > 0 else "")
    return "\n\n".join(x for x in (style, "The story bible (what the archivist recorded about the whole story, and "
                                    f"the author's plans):\n<bible>\n{body}\n</bible>") if x)


# --- review: notes on the text, never changes ----------------------------------------
# A note points at a passage and says what may be wrong there. The checker makes notes by
# counting (slop phrases from a list you keep, gestures, long runs of dialogue, repetition);
# they are patterns to look at, not errors. A review by a model makes notes by judging; a note
# may carry a suggested alternative, which only becomes a change in the text when you ask.
# Notes are {id, at, quote, original, kind, note, suggestion, source, by, time, status} (status:
# open, done, dismissed, outdated). They last while AIcove is open; nothing of them is stored.
#   writing/slop.txt                the phrases the checker looks for, one per line
# A note stays on its passage while you edit; when the passage changes too much it is outdated
# instead of pointing at the wrong words.

SLOP_FILE = os.path.join(WRITING_DIR, "slop.txt")
KINDS = ["slop", "cliché", "repetition", "continuity", "error", "dialogue", "pacing", "underdeveloped", "other"]
# Below this likeness to what it was about, a note's passage counts as changed too much.
SAME_ENOUGH = 0.6

SLOP = """# Phrases the checker marks. One per line, any case. A * stands for one word ("raised * eyebrow").
# A match is only a warning: a phrase can be right where it is. Lines starting with # are comments.

# faces and bodies doing the talking
raised an eyebrow
raised * eyebrow
arched an eyebrow
arched * eyebrow
lifted an eyebrow
quirked an eyebrow
eyebrows shot up
smirked
smirk
the corner of * mouth
corners of * mouth
lips curled
lips twitched
a ghost of a smile
ghost of a smile
let out a breath
released a breath
a breath * didn't know * was holding
breath she didn't know she was holding
breath he didn't know he was holding
jaw clenched
jaw tightened
clenched * jaw
eyes darkened
something flickered
flicker of
a beat passed
a beat later
heart hammered
pulse quickened
swallowed hard
rolled * eyes
shrugged
nodded slowly
tilted * head
chuckled darkly
huffed a laugh
scoffed
# words models love
a testament to
testament to
tapestry
delve
in the grand scheme
couldn't help but
could not help but
something shifted
something in * shifted
the weight of
heavy with unspoken
unspoken words
palpable
electric
the air crackled
the air was thick with
hung in the air
a mixture of
a mix of * and
barely above a whisper
voice barely a whisper
whispered softly
murmured softly
low and dangerous
dangerously low
for what felt like
in that moment
it was as if
little did * know
needless to say
time seemed to slow
the world fell away
every fiber of
every fibre of
padded
sauntered
# tags that do the acting
purred
growled
hissed
drawled
breathed
"""

GESTURES = re.compile(r"\b(smirk\w*|sigh\w*|eyebrows?|shrug\w*|nod(?:s|ded|ding)?|grin(?:s|ned|ning)?|"
                      r"chuckl\w*|scoff\w*|glanc\w*|frown\w*|wink\w*|roll(?:s|ed)? (?:her|his|their) eyes)\b", re.I)
GESTURE_NAMES = ["smirk", "sigh", "eyebrow", "shrug", "nod", "grin", "chuckle", "scoff", "glance", "frown", "wink",
                 "roll of the eyes"]
STOP = set("""a about above after again against all am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having he
her here hers herself him himself his how i if in into is it its itself just me more most my myself no nor not
now of off on once only or other our ours out over own same she should so some such than that the their theirs
them themselves then there these they this those through to too under until up very was we were what when where
which while who whom why will with would you your yours yourself said says say like one back even still
""".split())


def slop_phrases():
    """The list, made from SLOP the first time."""
    if not os.path.exists(SLOP_FILE):
        os.makedirs(WRITING_DIR, exist_ok=True)
        with open(SLOP_FILE, "w", encoding="utf-8") as f:
            f.write(SLOP)
    found = []
    for line in read_page(SLOP_FILE).split("\n"):
        line = line.strip()
        if line and not line.startswith("#"):
            words = [re.escape(w) if w != "*" else r"[\w’']+" for w in line.split()]
            found.append((line, re.compile(r"(?<![\w])" + r"\s+".join(words) + r"(?![\w])", re.I)))
    return found


def is_dialogue(paragraph):
    """Whether a paragraph is (mostly) speech."""
    p = paragraph.strip()
    quoted = sum(len(m) for m in re.findall(r"[“\"][^”\"]*[”\"]", p))
    return bool(p) and (p[0] in "“\"" or quoted > len(p) * 0.6)


def check(text):
    """The notes the checker makes on a chapter's text: patterns to look at, not errors."""
    notes, now = [], time.time()

    def add(at, end, kind, note):
        notes.append({"at": at, "quote": text[at:end], "kind": kind, "note": note, "source": "check",
                      "by": "Checker", "time": now})

    body_start = 0
    title = re.match(r"\s*#{1,6}\s[^\n]*\n", text)
    if title:
        body_start = title.end()
    # Slop phrases.
    for line, pattern in slop_phrases():
        for m in pattern.finditer(text, body_start):
            add(m.start(), m.end(), "slop", f"On the slop list (\"{line}\"). Fine if it earns its place here.")
    # Gestures: many in a short stretch.
    hits = [(m.start(), m.end(), m.group(1).lower()) for m in GESTURES.finditer(text, body_start)]
    i = 0
    while i < len(hits):
        window = [h for h in hits[i:] if h[0] - hits[i][0] < 6000]       # about 1000 words
        if len(window) >= 7:
            counts = {}
            for _, _, w in window:
                w = next((base for base in GESTURE_NAMES if w.startswith(base[:4])), w)
                counts[w] = counts.get(w, 0) + 1
            listed = ", ".join(f"{w} ×{c}" for w, c in sorted(counts.items(), key=lambda x: -x[1]))
            add(window[0][0], window[-1][1], "pattern",
                f"{len(window)} gestures in about {words(text[window[0][0]:window[-1][1]]):,} words ({listed}). "
                "Many faces and shrugs close together can read as filler; some scenes want them.")
            i += len(window)
        else:
            i += 1
    # Long runs of dialogue with little around it.
    paras = [(m.start(), m.end(), m.group(0)) for m in re.finditer(r"[^\n]+", text[body_start:]) if m.group(0).strip()]
    paras = [(a + body_start, b + body_start, p) for a, b, p in paras]
    run = []
    for para in paras + [(len(text), len(text), "")]:
        if para[2] and is_dialogue(para[2]):
            run.append(para)
            continue
        if len(run) >= 8:
            speech = sum(len(q) for _, _, p in run for q in re.findall(r"[“\"][^”\"]*[”\"]", p))
            total = sum(len(p) for _, _, p in run)
            outside = 1 - speech / max(total, 1)
            if outside < 0.2:
                add(run[0][0], run[-1][1], "pattern",
                    f"{len(run)} paragraphs of dialogue in a row, {outside:.0%} of it outside the quotes. Not "
                    "a problem in itself: look at whether the reader can still see where they are and what "
                    "they do.")
        run = []
    # The same word again and again in a short stretch (names left out: a word that is written
    # with a capital in the middle of a sentence somewhere).
    names = {m.group(1).lower() for m in re.finditer(r"(?<=[a-z,;] )([A-Z][a-z’']+)", text)}
    seen = {}
    for m in re.finditer(r"[A-Za-z’'-]{4,}", text[body_start:]):
        w = m.group(0).lower().strip("’'-")
        if w in STOP or w in names:
            continue
        at = m.start() + body_start
        places = [p for p in seen.get(w, []) if at - p < 240] + [at]
        seen[w] = places
        if len(places) == 3:
            add(places[0], at + len(m.group(0)), "repetition",
                f"\"{w}\" three times in about {words(text[places[0]:at]) + 1} words.")
            seen[w] = []
    # Sentences starting the same way, three or more in a row.
    for para_at, _, para in paras:
        starts = [(m.start(), m.group(1)) for m in re.finditer(r"(?:^|(?<=[.!?…]\s))[“\"]?([A-Z][a-z’']*)", para)]
        k = 0
        while k < len(starts):
            j = k
            while j + 1 < len(starts) and starts[j + 1][1] == starts[k][1]:
                j += 1
            if j - k >= 2 and starts[k][1].lower() not in ("i",):
                add(para_at + starts[k][0], para_at + starts[j][0] + len(starts[j][1]), "repetition",
                    f"{j - k + 1} sentences in a row start with \"{starts[k][1]}\".")
            k = j + 1
    notes.sort(key=lambda n: n["at"])
    return notes


REVIEW_FORMAT = """Write each note like this, as many as are needed, and nothing around them:
<note>
<quote>the exact words of the chapter the note is about, copied character for character: a phrase, a sentence or two</quote>
<kind>one of: slop, cliché, repetition, continuity, error, dialogue, pacing, underdeveloped, other</kind>
<why>what is wrong and why it matters, one to three sentences</why>
<suggestion>only if a concrete alternative clearly helps: the replacement for the quoted words, minimal; else leave this line out</suggestion>
</note>
Go through the chapter scene by scene before you decide. If after that it really needs no notes, write <none/> and then, in three or four sentences, what the chapter does well and why nothing needs changing."""

NOTE = re.compile(r"<note>(.*?)(?:</note>|(?=<note>)|$)", re.S)


def parse_review(answer):
    """The notes in a model's review: [{quote, kind, note, suggestion}]."""
    found = []
    for m in NOTE.finditer(answer):
        part = m.group(1)

        def field(name):
            f = re.search(rf"<{name}>\s*(.*?)\s*(?:</{name}>|(?=<\w+>)|$)", part, re.S)
            return f.group(1).strip() if f else ""
        quote, why = field("quote"), field("why")
        if not quote or not why:
            continue
        kind = field("kind").lower().strip(" .")
        kind = next((k for k in KINDS if kind.startswith(k[:5])), "other")
        found.append({"quote": quote, "kind": kind, "note": why, "suggestion": field("suggestion")})
    return found


def normalised(text):
    """text with quotes, dashes and spaces made plain, and where each of its characters was."""
    table = {"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "…": "..."}
    out, where = [], []
    for i, c in enumerate(text):
        c = table.get(c, c)
        if c.isspace():
            if out and out[-1] == " ":
                continue
            c = " "
        for ch in c:
            out.append(ch.lower())
            where.append(i)
    return "".join(out), where


FUZZY_QUOTE = 600      # characters: longer quotes are only found exactly
FUZZY_NEAR = 5000      # characters around where a quote was that are searched for a likeness


def find_quote(text, quote, near=0):
    """Where a quoted passage is in text: (start, end), or None. Exact first, then with quotes,
    dashes and spaces evened out, then the most alike stretch of sentences."""
    import difflib
    quote = quote.strip()
    if not quote:
        return None
    for q in dict.fromkeys([quote, quote.strip("“”\"«»")]):
        places = [m.start() for m in re.finditer(re.escape(q), text)] if q else []
        if places:
            at = min(places, key=lambda p: abs(p - near))
            return at, at + len(q)
    plain, where = normalised(text)
    q, _ = normalised(quote)
    q = q.strip()
    places = [m.start() for m in re.finditer(re.escape(q), plain)] if q else []
    if places:
        at = min(places, key=lambda p: abs(where[p] - near))
        return where[at], where[at + len(q) - 1] + 1
    # The most alike stretch: only for a short quote, and only near where it was. (Comparing a
    # long one at every place of a chapter takes minutes; it is outdated instead.)
    n = max(1, len(re.findall(r"[.!?…]+", quote)))
    if len(quote) > FUZZY_QUOTE or n > 6:
        return None
    sentences = [(m.start(), m.end()) for m in re.finditer(r"[^.!?…\n]+[.!?…]*[”\"’']?", text)
                 if m.group(0).strip() and abs(m.start() - near) < FUZZY_NEAR]
    best, span = 0.0, None
    for size in {n, n + 1}:
        for i in range(0, max(0, len(sentences) - size) + 1):
            a, b = sentences[i][0], sentences[i + size - 1][1]
            m = difflib.SequenceMatcher(None, text[a:b].strip().lower(), quote.lower(), autojunk=False)
            if m.real_quick_ratio() <= best or m.quick_ratio() <= best:
                continue
            r = m.ratio()
            if r > best:
                best, span = r, (a + len(text[a:b]) - len(text[a:b].lstrip()), b)
    return span if best >= 0.75 else None


def alike(a, b):
    """How alike two passages are, 0 to 1. Long ones are compared word by word (letter by
    letter takes too long)."""
    import difflib
    if not a and not b:
        return 1.0
    if len(a) + len(b) > 1000:
        a, b = a.split(), b.split()
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def review_path(project, file):
    return os.path.join(folder(project.id), "review", file[:-3] + ".json")


def read_notes(project, file):
    import json
    try:
        with open(review_path(project, file), encoding="utf-8") as f:
            data = json.load(f)
        return [n for n in data if isinstance(n, dict) and isinstance(n.get("at"), int) and isinstance(n.get("quote"), str)]
    except (OSError, ValueError):
        return []


def write_notes(project, file, notes):
    import json
    path = review_path(project, file)
    if not notes:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def place_note(text, note):
    """Where a stored note's passage is in text now: (start, end), or None when it changed too
    much (the note is outdated then)."""
    at, quote = note["at"], note["quote"]
    if text[at:at + len(quote)] == quote and quote:
        return at, at + len(quote)
    span = find_quote(text, quote, at)
    if span is None:
        return None
    return span if alike(text[span[0]:span[1]], note.get("original", quote)) >= SAME_ENOUGH else None


def note_id():
    import uuid
    return uuid.uuid4().hex[:10]
