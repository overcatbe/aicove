# Writing: long texts (a novel, a fic) as projects of chapters, one Markdown file each, in the
# AIcove folder:
#   writing/<id>/project.txt          title:, author:, git:, then one "chapter: <file> | <status>" line
#                                     per chapter, in reading order
#   writing/<id>/chapters/c001.md     the text; its first "# " line is the chapter's title
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
            p = cls.parse(pid, f.read())
        # Chapter files put there by hand join at the end.
        listed = {c[0] for c in p.chapters}
        for name in sorted(os.listdir(p.chapter_dir)) if os.path.isdir(p.chapter_dir) else []:
            if CHAPTER_FILE.fullmatch(name) and name not in listed:
                p.chapters.append([name, "draft"])
        return p

    def text(self):
        lines = [f"title: {self.title}"] + ([f"author: {self.author}"] if self.author else []) + [f"git: {'on' if self.git else 'off'}", ""]
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
        self.save_chapter(file, f"# {title}\n\n{body.strip()}\n")
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
                f.write(".trash/\n*.tmp\n")
        self.run_git("add", "-A")
        if self.run_git("diff", "--cached", "--quiet")[0] != 0:
            code, out = self.run_git("commit", "-q", "-m", message)
            if code:
                return f"git commit: {out}"
        return ""


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
        return "\n\n".join(fixed) + "\n"


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
    return "\n\n".join(paragraphs) + "\n"


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


def to_markdown(title, chapters):
    return f"# {title}\n\n" + "\n\n".join(f"## {t}\n\n{b}" for t, b in chapters) + "\n"


def to_plain(title, chapters):
    def plain(text):
        text = re.sub(r"^#{1,6}\s+", "", text, flags=re.M)
        text = re.sub(r"^>\s?", "    ", text, flags=re.M)
        return INLINE.sub(lambda m: m.group(0).strip("*_"), text)
    return f"{title}\n\n\n" + "\n\n\n".join(f"{t}\n\n{plain(b)}" for t, b in chapters) + "\n"


def md_html(text):
    import markdown
    md = markdown.Markdown(extensions=["sane_lists"], output_format="xhtml")
    return md.convert(re.sub(r"^\* \* \*$", '<p class="break">* * *</p>', text, flags=re.M))


STYLE = ("body { font-family: Georgia, serif; line-height: 1.5; max-width: 40em; margin: 2em auto; "
         "padding: 0 1em; } h1, h2 { text-align: center; } h2 { margin-top: 3em; } "
         ".break { text-align: center; } blockquote { font-style: italic; }")


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
        for p in re.split(r"\n\s*\n", b):
            p = p.strip()
            if not p:
                continue
            if p == "* * *":
                body.append(para("* * *", center=True))
            elif p.startswith(">"):
                body.append(para(re.sub(r"^>\s?", "", p, flags=re.M).replace("\n", " "), "Quote"))
            else:
                m = re.match(r"(#{1,6})\s+(.*)", p)
                body.append(para(m.group(2), "Heading2") if m else para(p.replace("\n", " ")))
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
