# The Writing tab: projects of chapters (writing.py), a chapter list with word counts and
# draft/done, an editor for one chapter, and on the right a persona to talk with about it.
# Text is saved by itself a moment after typing stops; with git on, a commit is made when you
# leave a chapter, the tab or the window, or press Ctrl+S. Import… takes pasted text, an HTML
# file or a PDF and splits it into chapters. What a model changes comes into the editor as
# suggestions, old text struck through in red and new text in green, until you accept or
# decline them.

import bisect
import json
import os
import re
import shutil
import time

from PyQt6.QtCore import QMarginsF, QMimeData, QThread, QTimer, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QAction, QColor, QDesktopServices, QKeySequence, QPageLayout, QPageSize, QPdfWriter, QShortcut, QTextCharFormat,
    QTextCursor, QTextDocument, QTextFormat,
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QSizePolicy,
    QSplitter, QTabWidget, QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

from . import chat, facts, gui_state, launchers, personas, summary, writing
from .gui_chat import Face, to_html
from .settings import STATE_DIR

ROLE = Qt.ItemDataRole.UserRole
SAVE_EVERY = 60000     # ms: changed text is written to its file once a minute (and on Ctrl+S,
                       # leaving the chapter, the tab or the window)
COUNT_AFTER = 1000     # ms without typing before word counts and labels catch up


def loose(*labels):
    """Labels whose text changes (and can get long) wrap, and never make the window wider."""
    for label in labels:
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Ignored, label.sizePolicy().verticalPolicy())
        label.setMinimumWidth(1)


class TextBox(QPlainTextEdit):
    """Plain text; what is pasted from a web page or a word processor comes in as Markdown
    (paragraphs, italics, bold, scene breaks) instead of losing its italics."""

    def createMimeDataFromSelection(self):
        data = super().createMimeDataFromSelection()
        # Copied from here stays plain text, so pasting it back changes nothing.
        data.removeFormat("text/html")
        return data

    def insertFromMimeData(self, source):
        if source.hasHtml():
            text = writing.html_to_markdown(source.html()).strip("\n")
            if text:
                self.insertPlainText(text)
                return
        super().insertFromMimeData(source)


def qlen(text):
    """Length of a text as Qt counts it (UTF-16: an emoji is two)."""
    return len(text) + sum(1 for c in text if ord(c) > 0xFFFF)


def p2q(text, p):
    return qlen(text[:p])


def q2p(text, q):
    if not any(ord(c) > 0xFFFF for c in text):
        return min(q, len(text))
    count = 0
    for i, c in enumerate(text):
        if count >= q:
            return i
        count += 2 if ord(c) > 0xFFFF else 1
    return len(text)


class Suggestion:
    """A change a model proposes, living in the editor: the old text (struck through, red)
    followed by the new (green). Four cursors mark the two pieces; Qt keeps them in place while
    the text around them changes. Text typed at their edges stays outside them."""

    def __init__(self, doc, start, old_len, new_len, by="", when=0.0):
        def mark(pos, keep):
            c = QTextCursor(doc)
            c.setPosition(pos)
            c.setKeepPositionOnInsert(keep)
            return c
        self.a = mark(start, False)
        self.b = mark(start + old_len, True)
        self.b2 = mark(start + old_len, False)
        self.c = mark(start + old_len + new_len, True)
        self.by, self.when = by, when

    def old(self):
        return self.a.position(), max(self.a.position(), self.b.position())

    def new(self):
        return self.b2.position(), max(self.b2.position(), self.c.position())

    def span(self):
        return self.a.position(), self.new()[1]


class ChapterEditor(TextBox):
    """The chapter's text with the pending suggestions in it. file_text() is the text as it goes
    into the file: without the suggested new pieces (the old ones are still the text)."""
    items_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.items = []
        self.notes = []
        self.current_note = None
        self.under_cursor = None
        self.cursorPositionChanged.connect(self.moved)

    # --- positions: Qt counts UTF-16 units, Python characters -----------------------
    # Worked out once per state of the text (and of the list of changes), not per question:
    # a chapter with a hundred changes waiting asks a great many.

    def snap(self):
        key = (self.document().revision(), len(self.items), id(self.items[-1]) if self.items else 0)
        if getattr(self, "_snap", None) is None or self._snap[0] != key:
            text = self.document().toPlainText()
            wide = any(ord(c) > 0xFFFF for c in text)
            conv = (lambda q: q2p(text, q)) if wide else (lambda q: min(q, len(text)))
            ranges = sorted((conv(s.new()[0]), conv(s.new()[1])) for s in self.items)
            ranges = [(a, max(a, b)) for a, b in ranges]
            starts = [a for a, _ in ranges]
            before = [0]                       # removed before range i
            for a, b in ranges:
                before.append(before[-1] + b - a)
            fstarts = [a - before[i] for i, (a, _) in enumerate(ranges)]
            self._snap = (key, text, wide, conv, ranges, starts, before, fstarts)
        return self._snap

    def doc_text(self):
        return self.snap()[1]

    def ranges(self, text=None):
        """The new pieces as Python index ranges of the document text, in order."""
        return self.snap()[4]

    def file_text(self):
        _, text, _, _, ranges, _, _, _ = self.snap()
        if not ranges:
            return text
        parts, last = [], 0
        for a, b in ranges:
            parts.append(text[last:a])
            last = b
        parts.append(text[last:])
        return "".join(parts)

    def to_file(self, pos):
        """A document position as an index in file_text()."""
        _, text, _, conv, ranges, starts, before, _ = self.snap()
        p = conv(pos)
        i = bisect.bisect_left(starts, p)
        removed = before[i]
        if i > 0 and ranges[i - 1][1] > p:
            # inside the piece before: only the part up to p is gone
            removed -= ranges[i - 1][1] - p
        return p - removed

    def to_doc(self, at):
        """An index in file_text() as a document position, after the new pieces before it."""
        _, text, wide, _, _, _, before, fstarts = self.snap()
        p = at + before[bisect.bisect_right(fstarts, at)]
        return p2q(text, p) if wide else p

    def piece(self, r):
        _, text, _, conv, _, _, _, _ = self.snap()
        return text[conv(r[0]):conv(r[1])]

    # --- loading and saving ------------------------------------------------------

    def load(self, text, found):
        """The file's text with its suggestions, put together in one go. Returns how many no
        longer fit the text."""
        self.items = []
        self.decided = 0           # changes accepted or declined one by one since the chapter opened
        self._snap = None
        placed, last_end, dropped = [], -1, 0
        for item in sorted(found, key=lambda i: i["at"]):
            at = writing.locate(text, item)
            if at is None or (not item["old"] and not item["new"]) or at < last_end \
                    or (at == last_end and placed and placed[-1][1]["old"] and not item["old"]):
                dropped += 1
                continue
            placed.append((at, item))
            last_end = at + len(item["old"])
        # The document: the text with each change's new piece right after its old one.
        parts, spans, last, length = [], [], 0, 0
        for at, item in placed:
            parts.append(text[last:at])
            length += qlen(text[last:at])
            old_len, new_len = qlen(item["old"]), qlen(item["new"])
            spans.append((length, old_len, new_len, item))
            parts.append(item["old"] + item["new"])
            length += old_len + new_len
            last = at + len(item["old"])
        parts.append(text[last:])
        self.setPlainText("".join(parts))
        doc = self.document()
        self.items = [Suggestion(doc, start, old_len, new_len, item.get("by", ""), item.get("time", 0.0))
                      for start, old_len, new_len, item in spans]
        doc.clearUndoRedoStacks()
        self.paint()
        self.items_changed.emit()
        return dropped

    def add(self, found):
        """New suggestions from a model. Returns (put in, couldn't place)."""
        added = dropped = 0
        for item in sorted(found, key=lambda i: i["at"]):
            if self.add_one(item, self.file_text()):
                added += 1
            else:
                dropped += 1
        self.paint()
        self.items_changed.emit()
        return added, dropped

    def add_one(self, item, ft):
        at = writing.locate(ft, item)
        if at is None or (not item["old"] and not item["new"]):
            return False
        end = at + len(item["old"])
        for s in self.items:
            o1, o2 = self.to_file(s.old()[0]), self.to_file(s.old()[1])
            n = self.to_file(s.new()[0])
            if at < n < end:
                return False
            if (at < o2 and o1 < end) or (at == end and o1 < at < o2):
                return False
        start = self.to_doc(at)
        old_len = qlen(item["old"])
        c = QTextCursor(self.document())
        c.setPosition(start + old_len)
        c.insertText(item["new"])
        self.items.append(Suggestion(self.document(), start, old_len, qlen(item["new"]),
                                     item.get("by", ""), item.get("time", 0.0)))
        return True

    def saved_items(self):
        return [{"at": self.to_file(s.old()[0]), "old": self.piece(s.old()), "new": self.piece(s.new()),
                 "by": s.by, "time": s.when} for s in sorted(self.items, key=lambda s: s.a.position())]

    # --- deciding ----------------------------------------------------------------

    def cut(self, start, end, text=""):
        c = QTextCursor(self.document())
        c.setPosition(start)
        c.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        c.insertText(text)

    def accept(self, s, new_text=None):
        pos = s.a.position()
        c = QTextCursor(self.document())
        c.beginEditBlock()
        if new_text is not None:
            self.cut(*s.new(), new_text)
        self.cut(*s.old())
        c.endEditBlock()
        self.decided_one(s, pos)

    def decline(self, s):
        pos = s.a.position()
        self.cut(*s.new())
        self.decided_one(s, pos)

    def decided_one(self, s, pos):
        """One change is done with: on to the next one (after where it was, else the first)."""
        self.items.remove(s)
        self.decided = getattr(self, "decided", 0) + 1
        order = sorted(self.items, key=lambda s: s.a.position())
        if order:
            target = next((t for t in order if t.a.position() >= pos), order[0])
            c = self.textCursor()
            c.setPosition(target.a.position())
            self.setTextCursor(c)
            self.centerCursor()
        self.paint()
        self.items_changed.emit()

    def every(self, accept):
        c = QTextCursor(self.document())
        c.beginEditBlock()
        for s in sorted(self.items, key=lambda s: s.a.position(), reverse=True):
            if accept:
                self.cut(*s.old())
            else:
                self.cut(*s.new())
        c.endEditBlock()
        self.items = []
        self.paint()
        self.items_changed.emit()

    def at(self, pos):
        return next((s for s in self.items if s.span()[0] <= pos <= s.span()[1]), None)

    def current(self):
        return self.at(self.textCursor().position())

    def go(self, step):
        if not self.items:
            return
        pos = self.textCursor().position()
        order = sorted(self.items, key=lambda s: s.a.position())
        now = self.current()
        if step > 0:
            target = next((s for s in order if s.a.position() > pos and s is not now), order[0])
        else:
            target = next((s for s in reversed(order) if s.span()[1] < (now.a.position() if now else pos)), order[-1])
        c = self.textCursor()
        c.setPosition(target.a.position())
        self.setTextCursor(c)
        self.centerCursor()

    def describe(self, s):
        return self.piece(s.old()), self.piece(s.new())

    # --- review notes: highlights that never change the text -----------------------

    def mark(self, start, end):
        a, b = QTextCursor(self.document()), QTextCursor(self.document())
        a.setPosition(start)
        b.setPosition(end)
        b.setKeepPositionOnInsert(True)
        return [a, b]

    def load_notes(self, notes, text):
        """Notes as stored: open ones are put on their passage, or outdated when it changed
        too much. Returns how many went outdated."""
        self.notes, gone = [], 0
        for n in notes:
            n = dict(n)
            n["marks"] = None
            if n.get("status", "open") == "open" and n.get("placed", True):
                span = writing.place_note(text, n)
                if span is None:
                    n["status"] = "outdated"
                    gone += 1
                else:
                    n["marks"] = self.mark(self.to_doc(span[0]), self.to_doc(span[1]))
            self.notes.append(n)
        self.paint()
        return gone

    def add_notes(self, found):
        """New notes (from the checker or a review) put on their passages."""
        ft = self.file_text()
        for n in found:
            n = {"id": writing.note_id(), "status": "open", **n}
            span = writing.find_quote(ft, n["quote"], n.get("at", 0))
            n["placed"] = span is not None
            n["marks"] = None
            if span is not None:
                n["at"], n["quote"] = span[0], ft[span[0]:span[1]]
                n["marks"] = self.mark(self.to_doc(span[0]), self.to_doc(span[1]))
            n.setdefault("original", n["quote"])
            self.notes.append(n)
        self.notes.sort(key=lambda n: (n["marks"][0].position() if n["marks"] else -1))
        self.paint()

    def note_span(self, n):
        """(start, end) of a note's passage in the document, or None."""
        if not n.get("marks"):
            return None
        a, b = n["marks"][0].position(), n["marks"][1].position()
        return (a, b) if b > a else None

    def refresh_notes(self):
        """Notes follow their passages; one whose passage changed too much is outdated. Returns
        whether any went outdated."""
        changed = False
        ft = None
        for n in self.notes:
            if n.get("status") != "open" or not n.get("marks"):
                continue
            span = self.note_span(n)
            if ft is None:
                ft = self.file_text()
            now = ft[self.to_file(span[0]):self.to_file(span[1])] if span else ""
            if now != n["quote"]:
                if not now or writing.alike(now, n.get("original", n["quote"])) < writing.SAME_ENOUGH:
                    n["status"], n["marks"] = "outdated", None
                    changed = True
                else:
                    n["quote"] = now
            if n.get("marks"):
                n["at"] = self.to_file(n["marks"][0].position())
        return changed

    def saved_notes(self):
        self.refresh_notes()
        return [{k: v for k, v in n.items() if k != "marks"} for n in self.notes]

    def note_at(self, pos):
        for n in self.notes:
            span = self.note_span(n) if n.get("status") == "open" else None
            if span and span[0] <= pos <= span[1]:
                return n
        return None

    def show_note(self, n):
        """Go to a note's passage and select it."""
        span = self.note_span(n)
        if span is None:
            return False
        self.current_note = n
        c = self.textCursor()
        c.setPosition(span[0])
        c.setPosition(span[1], QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(c)
        self.centerCursor()
        self.paint()
        return True

    def note_marks(self, dark):
        amber = QColor("#e8b04a" if dark else "#b7791f")
        marks = []
        for n in self.notes:
            span = self.note_span(n) if n.get("status") == "open" else None
            if span is None:
                continue
            now = n is getattr(self, "current_note", None)
            # A note on a long stretch (a run of dialogue) is only drawn while it is the one you look at.
            if span[1] - span[0] > 400 and not now:
                continue
            sel = QTextEdit.ExtraSelection()
            c = QTextCursor(self.document())
            c.setPosition(span[0])
            c.setPosition(span[1], QTextCursor.MoveMode.KeepAnchor)
            sel.cursor = c
            sel.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            sel.format.setUnderlineColor(amber)
            back = QColor(amber)
            back.setAlpha(55 if now else 18)
            sel.format.setBackground(back)
            marks.append(sel)
        return marks

    # --- looks -------------------------------------------------------------------

    def paint(self):
        dark = self.palette().base().color().lightness() < 128
        red = QColor("#ff6b6b" if dark else "#c0262d")
        green = QColor("#5fd17a" if dark else "#17803a")
        now = self.current()
        marks = self.note_marks(dark)
        for s in self.items:
            for (start, end), colour, strike in ((s.old(), red, True), (s.new(), green, False)):
                if start >= end:
                    continue
                sel = QTextEdit.ExtraSelection()
                c = QTextCursor(self.document())
                c.setPosition(start)
                c.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                back = QColor(colour)
                back.setAlpha(70 if s is now else 28)
                if not self.piece((start, end)).strip():
                    # Only spaces or line breaks: nothing to see, so the whole line is marked.
                    c.clearSelection()
                    sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                    back.setAlpha(90 if s is now else 45)
                else:
                    sel.format.setForeground(colour)
                    sel.format.setFontStrikeOut(strike)
                sel.cursor = c
                sel.format.setBackground(back)
                marks.append(sel)
        self.setExtraSelections(marks)

    def moved(self):
        """The cursor moved: redraw only when it went into or out of a change or note (the
        marks themselves follow the text by themselves)."""
        if not self.items and not self.notes:
            return
        pos = self.textCursor().position()
        now = (self.at(pos), self.note_at(pos))
        if now != self.under_cursor:
            self.under_cursor = now
            self.paint()

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        s = self.at(self.cursorForPosition(event.pos()).position())
        if s is not None:
            first = menu.actions()[0] if menu.actions() else None
            for label, act in (("✓ Accept this change", lambda: self.accept(s)),
                               ("✗ Decline this change", lambda: self.decline(s)),
                               ("✎ Edit, then accept…", lambda: self.edit(s))):
                a = QAction(label, menu)
                a.triggered.connect(act)
                menu.insertAction(first, a)
            menu.insertSeparator(first)
        menu.exec(event.globalPos())

    def edit(self, s):
        old, new = self.describe(s)
        text, ok = QInputDialog.getMultiLineText(self, "Edit the change", f"Instead of:\n{old[:300]}\n\nWrite:", new)
        if ok:
            self.accept(s, text)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1 if event.angleDelta().y() > 0 else -1)
            return
        super().wheelEvent(event)

    def zoom(self, step):
        font = self.font()
        size = min(40.0, max(7.0, font.pointSizeF() + step))
        font.setPointSizeF(size)
        self.setFont(font)
        remember("font", size)


def remembered(key, default=None):
    return gui_state.remembered(key, default, "writing.json")


def remember(key, value):
    gui_state.remember(key, value, "writing.json")


class Ask(QThread):
    """One request in the background. In the talk: one answer, streamed. For a template that
    goes over passages: one request per passage, each answer's changes handed back as found."""
    part = pyqtSignal(str, str)
    found = pyqtSignal(str, list, int, int)     # chapter file, suggestions, passage n, of
    failed = pyqtSignal(str)

    def __init__(self, persona, messages=None, style="", file="", passages=(), prompt="", by="", think=False):
        super().__init__()
        self.persona, self.messages, self.style = persona, messages, style
        self.think = think
        self.file, self.passages, self.prompt, self.by = file, list(passages), prompt, by
        self.style_guide = ""      # the author's style page, for passage jobs that restyle
        self.stopping = False
        self.answer = ""

    def stop(self):
        self.stopping = True

    def run(self):
        try:
            if self.passages:
                self.over_passages()
            else:
                def collect(kind, text):
                    if kind == "text":
                        self.answer += text
                    self.part.emit(kind, text)
                body = chat.request_for(self.persona, self.messages, self.controls())
                chat.stream(body, collect, lambda: self.stopping)
        except OSError as e:
            self.failed.emit(str(e))

    def controls(self):
        # Thinking makes a small model take minutes over a paragraph; only when asked for.
        return {"style": self.style, "thinking": "on" if self.think else "off"}

    def over_passages(self):
        system = "\n\n".join(p for p in (self.persona.system.strip(), writing.RULES, self.style_guide,
                                          writing.PASSAGE_RULES) if p)
        for n, (at, passage) in enumerate(self.passages, 1):
            if self.stopping:
                return
            self.part.emit("status", f"{self.by}: passage {n} of {len(self.passages)}…")
            got = {"text": ""}

            def collect(kind, text):
                if kind == "text":
                    got["text"] += text
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": f"{self.prompt}\n\nThe passage:\n\n{passage}\n\n"
                                                    "(Answer with the new version between <rewrite> and "
                                                    "</rewrite>, nothing else.)"}]
            body = chat.request_for(self.persona, messages, self.controls())
            chat.stream(body, collect, lambda: self.stopping)
            if self.stopping:
                return
            new = writing.rewrite_in(got["text"])
            if new is None:
                # A model that forgot the tags: its answer, if it looks like the passage again.
                plain = got["text"].strip()
                new = plain if 0.6 < len(plain) / max(len(passage.strip()), 1) < 1.6 else None
            items = writing.differences(passage.strip(), new, at + (len(passage) - len(passage.lstrip())),
                                        self.by) if new else []
            self.found.emit(self.file, items, n, len(self.passages))


class TalkPanel(QWidget):
    """The right side of the Writing tab: a persona, its model, templates and the talk."""

    def __init__(self, tab):
        super().__init__()
        self.tab = tab
        self.window = tab.window
        self.worker = None
        self.waiting = None        # what to do once the model is up
        self.live = ""
        self.talk = []             # messages of the talk: role, content (as sent), shown, time
        self.talk_file = None      # where it is kept: one talk per chapter
        self.reload_later = False  # the chapter changed while an answer was coming

        self.who = QComboBox()
        self.who.currentIndexChanged.connect(self.pick_persona)
        self.state = QLabel()
        self.load_btn = QPushButton("Load")
        self.load_btn.setToolTip("Start this persona's model")
        self.load_btn.clicked.connect(lambda: self.load_model())
        # The persona's picture; its ring pulses while the model thinks, writes or loads.
        self.face = Face(self.working, size=64)
        row = QHBoxLayout()
        row.addWidget(self.face)
        row.addWidget(self.who, 1)
        row.addWidget(self.load_btn)
        self.template = QComboBox()
        self.template.setToolTip("Prompts for the usual jobs; the ones that change text work on the selection, "
                                 "or with nothing selected on the whole chapter")
        run_btn = QPushButton("Run")
        run_btn.clicked.connect(self.run_template)
        edit_btn = QPushButton("…")
        edit_btn.setToolTip("Open the folder of the templates (one text file each) to change or add some")
        edit_btn.setFixedWidth(30)
        edit_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(writing.TEMPLATE_DIR)))
        trow = QHBoxLayout()
        trow.addWidget(self.template, 1)
        trow.addWidget(run_btn)
        trow.addWidget(edit_btn)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.target = QLabel()
        self.target.setEnabled(False)
        loose(self.state, self.target)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Ask about the chapter or the selected text, or say what to change "
                                      "(Enter sends, Shift+Enter is a new line)")
        self.input.installEventFilter(self)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(lambda: self.send())
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.hide()
        self.think = QCheckBox("Think")
        self.think.setToolTip("Let a thinking model think before it answers: sometimes better, much slower. "
                              "Templates that change text never think.")
        self.think.setChecked(bool(remembered("think", False)))
        self.think.toggled.connect(lambda on: remember("think", on))
        self.put_btn = QPushButton("Put the last answer into the text")
        self.put_btn.setToolTip("For an answer that is a new version of the passage but came without "
                                "<rewrite> marks: it goes in as changes, like a marked one")
        self.put_btn.clicked.connect(self.put_last)
        self.put_btn.hide()
        new_btn = QPushButton("New talk")
        new_btn.setToolTip("Start over; the talk so far is kept in the project's talk folder")
        new_btn.clicked.connect(self.new_talk)
        brow = QHBoxLayout()
        brow.addWidget(new_btn)
        brow.addWidget(self.think)
        brow.addStretch(1)
        brow.addWidget(self.stop_btn)
        brow.addWidget(self.send_btn)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addLayout(row)
        box.addWidget(self.state)
        box.addLayout(trow)
        # The talk over the box you write in, with a divider to make that bigger.
        above, below = QWidget(), QWidget()
        abox = QVBoxLayout(above)
        abox.setContentsMargins(0, 0, 0, 0)
        abox.addWidget(self.view, 1)
        abox.addWidget(self.put_btn)
        abox.addWidget(self.target)
        bbox = QVBoxLayout(below)
        bbox.setContentsMargins(0, 0, 0, 0)
        bbox.addWidget(self.input, 1)
        bbox.addLayout(brow)
        box.addWidget(gui_state.resizable(above, below, "writing_input", 130), 1)

        self.window.proc.finished.connect(self.model_started)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.show_state)
        self.timer.start(2000)
        self.paint_timer = QTimer(self)
        self.paint_timer.setInterval(250)
        self.paint_timer.timeout.connect(self.paint)
        self.fill()

    # --- who and which model -----------------------------------------------------

    def showEvent(self, event):
        super().showEvent(event)
        # Personas made or deleted on the Personas tab meanwhile.
        keep = self.who.currentData()
        self.fill()
        i = self.who.findData(keep)
        if i < 0:
            self.project_changed()
        else:
            self.who.blockSignals(True)
            self.who.setCurrentIndex(i)
            self.who.blockSignals(False)
        QTimer.singleShot(0, self.show_state)

    def fill(self):
        keep_template = self.template.currentData()
        self.who.blockSignals(True)
        self.who.clear()
        for p in sorted(personas.load_all().values(), key=lambda p: p.name.lower()):
            self.who.addItem(p.name, p.id)
        self.who.blockSignals(False)
        self.template.clear()
        for t in writing.templates():
            self.template.addItem(t.name + ("  ✎" if t.changes else "  (notes)" if t.reviews else ""), t.name)
        self.template.setCurrentIndex(max(self.template.findData(keep_template), 0))

    def project_changed(self):
        """Another project: its persona and its talk."""
        p = self.tab.project
        want = (p.persona if p else "") or remembered("persona") or ""
        i = self.who.findData(want)
        self.who.blockSignals(True)
        self.who.setCurrentIndex(i if i >= 0 else 0)
        self.who.blockSignals(False)
        self.face.show_persona(self.persona())
        self.load_talk()
        self.show_state()

    def persona(self):
        pid = self.who.currentData()
        try:
            return personas.Persona.read(pid) if pid else None
        except OSError:
            return None

    def working(self):
        """What the model does for the talk right now, for the ring around the face."""
        if self.worker is not None:
            return "writing" if self.live else "thinking"
        if self.waiting is not None or self.window.busy():
            return "loading"
        return None

    def pick_persona(self, *_):
        self.face.show_persona(self.persona())
        p = self.tab.project
        if p is not None and self.who.currentData():
            p.persona = self.who.currentData()
            p.write()
            remember("persona", p.persona)
        self.show_state()

    def show_state(self):
        if not self.isVisible() and self.worker is None:
            return
        persona = self.persona()
        loaded = chat.loaded_launcher()
        if persona is None:
            text, can_load = "No persona: make one on the Personas tab", False
        elif self.window.busy():
            text, can_load = "Loading the model…", False
        elif loaded is not None and chat.runs_on(persona, loaded):
            text = ("Ready" if self.window.health.state == "ready" else "Loading…") + f": {loaded.label()}"
            can_load = False
        else:
            launcher = chat.launcher_for(persona)
            text = (f"Not loaded: {launcher.label()}" if launcher else "None of its models is on this machine") \
                + (f" (now loaded: {loaded.label()})" if loaded else "")
            can_load = launcher is not None
        self.state.setText(text)
        self.load_btn.setEnabled(can_load)
        self.show_target()

    def ready(self, name=None):
        """Whether the model is up: the launcher name, or one the persona runs on."""
        persona = self.persona()
        loaded = chat.loaded_launcher()
        right = loaded is not None and (loaded.name == name if name else
                                        persona is not None and chat.runs_on(persona, loaded))
        return right and not self.window.busy() and self.window.health.state == "ready"

    def load_model(self, then=None, name=None):
        persona = self.persona()
        launcher = launchers.get(name) if name else chat.launcher_for(persona) if persona else None
        if launcher is None or self.window.busy():
            if name and launcher is None:
                self.window.say(f"Writing: there is no launcher {name} on this machine.")
            return
        self.waiting = (then, name) if then else None
        self.window.say(f"--- {launcher.name} for {persona.name if persona and not name else 'the story memory'} "
                        "(Writing) ---")
        self.window.run_cove("start", launcher.name)
        self.show_state()

    def model_started(self, *_):
        self.show_state()
        waiting, self.waiting = self.waiting, None
        if waiting is not None:
            self.wait_for(*waiting, time.time() + 30)

    def wait_for(self, then, name, until):
        """The start command is done; AIcove's own check of the model may need a moment more."""
        if self.ready(name):
            then()
            return
        loaded, persona = chat.loaded_launcher(), self.persona()
        right = loaded is not None and (loaded.name == name if name else
                                        persona is not None and chat.runs_on(persona, loaded))
        if right and time.time() < until:
            QTimer.singleShot(500, lambda: self.wait_for(then, name, until))
        else:
            self.window.say("The model didn't come up; try again with Load.")

    def when_ready(self, action, name=None):
        """Do action now if the model is up (the launcher name, or one of the persona's), else
        load it first and do it then."""
        loaded = chat.loaded_launcher()
        if self.persona() is None:
            QMessageBox.information(self, "Writing", "There is no persona yet: make one on the Personas tab.")
        elif self.ready(name):
            action()
        elif self.window.busy():
            self.window.say("A model is being started or stopped; try again when it is done.")
        elif loaded is not None and (loaded.name == name if name else chat.runs_on(self.persona(), loaded)):
            # Loaded, but its check isn't through yet.
            self.wait_for(action, name, time.time() + 60)
        else:
            self.load_model(then=action, name=name)

    # --- what the model works on -------------------------------------------------

    def selection(self):
        """(start, end) of the selection in the chapter file, or None."""
        ed = self.tab.editor
        c = ed.textCursor()
        if not c.hasSelection() or self.tab.file is None:
            return None
        start, end = ed.to_file(c.selectionStart()), ed.to_file(c.selectionEnd())
        return (start, end) if end > start else None

    def show_target(self):
        if self.tab.file is None:
            self.target.setText("")
            return
        sel = self.selection()
        if sel:
            text = self.tab.editor.file_text()[sel[0]:sel[1]]
            self.target.setText(f"Working on the selection: {writing.words(text):,} words")
        else:
            self.target.setText("Nothing selected: questions are about the whole chapter; changes go in at "
                                "the cursor (templates with ✎ go over the whole chapter)")

    # --- the talk ----------------------------------------------------------------

    def talk_path(self):
        """The open chapter's talk. The one talk a project had before goes to the chapter it was
        last about."""
        p, file = self.tab.project, self.tab.file
        if not p or not file:
            return None
        folder = writing.project_chat_dir(p)
        old = os.path.join(folder, "current.json")
        if os.path.exists(old):
            try:
                with open(old, encoding="utf-8") as f:
                    messages = json.load(f).get("messages", [])
            except (OSError, ValueError):
                messages = []
            about = next((m["target"]["file"] for m in reversed(messages) if m.get("target", {}).get("file")),
                         p.chapters[0][0] if p.chapters else file)
            to = os.path.join(folder, about[:-3] + ".json")
            os.replace(old, to if not os.path.exists(to) else
                       os.path.join(folder, f"{about[:-3]}-{time.strftime('%Y%m%d-%H%M%S')}.json"))
        return os.path.join(folder, file[:-3] + ".json")

    def chapter_changed(self):
        """Another chapter open: its talk (once a coming answer is in)."""
        if self.worker is not None:
            self.reload_later = True
        elif self.talk_path() != self.talk_file:
            self.load_talk()

    def load_talk(self):
        self.talk = []
        path = self.talk_file = self.talk_path()
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    self.talk = json.load(f).get("messages", [])
            except (OSError, ValueError):
                self.talk = []
        self.live = ""
        # A chapter with text and no talk yet: the first prompt ready in the box, Enter sends it.
        first = writing.first_prompt()
        typed = self.input.toPlainText().strip()
        if not typed or typed == first:
            body = writing.body_of(self.tab.project.chapter(self.tab.file)) if path else ""
            self.input.setPlainText(first if first and not self.talk and body.strip() else "")
            self.input.moveCursor(QTextCursor.MoveOperation.End)
        self.paint()

    def save_talk(self):
        path = self.talk_file
        if not path:
            return
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"messages": self.talk}, f, ensure_ascii=False, indent=1)
        os.replace(path + ".tmp", path)

    def new_talk(self):
        if self.worker is not None:
            return
        path = self.talk_file
        if path and os.path.exists(path):
            os.remove(path)
        self.load_talk()

    def paint(self):
        dark = self.palette().window().color().lightness() < 128
        persona = self.persona()
        name = persona.name if persona else "Model"
        parts = []
        for m in self.talk:
            if m["role"] == "user":
                parts.append(f"**You:** {m.get('shown') or m['content']}")
            else:
                parts.append(f"**{m.get('name') or name}:** {m.get('shown') or m['content']}")
        if self.worker is not None:
            parts.append(f"**{name}:** {writing.without_rewrite(self.live, '(writing the changes…)') or '…'}")
        if not parts:
            parts.append(f"*Talk with {name} about this chapter. Select text first to work on just that.*")
        self.view.setHtml(to_html("\n\n---\n\n".join(parts), dark))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == event.Type.KeyPress \
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.send()
            return True
        return super().eventFilter(obj, event)

    def send(self, text=None, style="", label=""):
        if self.worker is not None or self.tab.file is None:
            return
        text = (text if text is not None else self.input.toPlainText()).strip()
        if not text:
            return
        self.tab.save()
        ft = self.tab.editor.file_text()
        sel = self.selection()
        cursor = self.tab.editor.to_file(self.tab.editor.textCursor().position())
        target = {"file": self.tab.file}
        if sel:
            target.update(at=sel[0], text=ft[sel[0]:sel[1]])
            content = f"{text}\n\nThe selected passage:\n<selected>\n{target['text']}\n</selected>"
            shown = f"{label + ': ' if label else ''}{text}\n\n*(on the selection, {writing.words(target['text']):,} words)*"
        else:
            target["cursor"] = cursor
            before = ft[max(0, cursor - 200):cursor]
            content = f"{text}\n\n(Nothing is selected. The cursor is right after: «…{before}»)"
            shown = f"{label + ': ' if label else ''}{text}"
        self.talk.append({"role": "user", "content": content, "shown": shown, "time": time.time(),
                          "target": target, "style": style})
        self.save_talk()
        self.input.clear()
        self.paint()
        self.when_ready(self.answer)

    def system(self, rules=writing.RULES):
        persona = self.persona()
        p, ft = self.tab.project, self.tab.editor.file_text()
        try:
            n_ctx = summary.context_size()
        except (OSError, ValueError, KeyError):
            n_ctx = 8192
        # The chapter takes at most 45% of the context (a long one is cut around where you are),
        # the story memory 30%.
        room = int(n_ctx * 0.45 * 3.2)
        bible = writing.for_prompt(p, self.tab.file, int(n_ctx * 0.3 * 3.2),
                                   self.talk[-1]["content"] if self.talk and self.talk[-1]["role"] == "user" else "")
        if len(ft) > room:
            target = self.talk[-1].get("target", {}) if self.talk else {}
            where = target.get("at", target.get("cursor", self.tab.editor.to_file(self.tab.editor.textCursor().position())))
            start = max(0, min(where - room // 2, len(ft) - room))
            ft = ("[…]\n" if start else "") + ft[start:start + room] + ("\n[…]" if start + room < len(ft) else "")
        title = writing.title_of(self.tab.editor.file_text(), "this chapter")
        return "\n\n".join(x for x in (persona.system.strip(), facts.for_prompt(persona.id), rules, bible,
                                       f"The project is \"{p.title}\". The chapter open now is \"{title}\"; "
                                       f"this is its text as it stands:\n<chapter>\n{ft}\n</chapter>") if x)

    def answer(self):
        if not self.talk or self.talk[-1]["role"] != "user":
            return
        persona = self.persona()
        system = self.system()
        # The latest messages of the talk, at most 12, as many as fit next to the chapter and
        # the story memory with room left for the answer (about 3.2 characters a token).
        try:
            n_ctx = summary.context_size()
        except (OSError, ValueError, KeyError):
            n_ctx = 8192
        left = int(n_ctx * 0.85 * 3.2) - len(system) - len(writing.REMINDER)
        recent = []
        for m in reversed(self.talk[-12:]):
            if recent and len(m["content"]) > left:
                break
            recent.insert(0, m)
            left -= len(m["content"])
        if recent[0]["role"] == "assistant" and len(recent) > 1:
            recent = recent[1:]
        messages = [{"role": "system", "content": system}]
        messages += [{"role": m["role"], "content": m["content"]} for m in recent]
        messages[-1] = {"role": "user", "content": messages[-1]["content"] + "\n\n" + writing.REMINDER}
        self.live = ""
        self.put_btn.hide()
        self.worker = Ask(persona, messages, self.talk[-1].get("style", ""), think=self.think.isChecked())
        self.worker.part.connect(self.on_part)
        self.worker.failed.connect(lambda e: self.window.say(f"Writing: no answer: {e}"))
        self.worker.finished.connect(self.answered)
        self.busy(True)
        self.worker.start()

    def on_part(self, kind, text):
        if kind == "text":
            self.live += text
        elif kind == "status":
            self.target.setText(text)

    def busy(self, on):
        self.send_btn.setVisible(not on)
        self.stop_btn.setVisible(on)
        if on:
            self.paint_timer.start()
        else:
            self.paint_timer.stop()

    def stop(self):
        if self.worker is not None:
            self.worker.stop()

    def answered(self):
        try:
            self.take_answer()
        finally:
            if self.reload_later:
                self.reload_later = False
                self.chapter_changed()

    def take_answer(self):
        worker, self.worker = self.worker, None
        self.busy(False)
        if worker.passages:
            note = f"→ {self.counted[0]} change{'s' if self.counted[0] != 1 else ''} put into the text"
            if self.counted[1]:
                note += f" ({self.counted[1]} couldn't be placed: the text changed meanwhile)"
            if worker.stopping:
                note += " (stopped)"
            self.talk.append({"role": "assistant", "content": note, "shown": note, "time": time.time(),
                              "name": self.persona().name if self.persona() else ""})
            self.save_talk()
            self.show_target()
            self.paint()
            return
        text = worker.answer.strip()
        persona = self.persona()
        if text:
            shown = text
            new = writing.rewrite_in(text)
            if new is not None:
                added, dropped = self.place(self.talk[-1]["target"], new, persona.name if persona else "")
                shown = writing.without_rewrite(text, self.placed(added, dropped))
            self.talk.append({"role": "assistant", "content": text, "shown": shown, "time": time.time(),
                              "name": persona.name if persona else ""})
            self.save_talk()
            self.put_btn.setVisible(new is None)
        self.live = ""
        self.paint()

    def put_last(self):
        """The last answer, which came without <rewrite> marks, as the new version."""
        self.put_btn.hide()
        last = next((i for i in range(len(self.talk) - 1, 0, -1) if self.talk[i]["role"] == "assistant"), None)
        if last is None or self.talk[last - 1]["role"] != "user" or "target" not in self.talk[last - 1]:
            return
        answer = self.talk[last]
        persona = self.persona()
        added, dropped = self.place(self.talk[last - 1]["target"], answer["content"].strip(),
                                    persona.name if persona else "")
        answer["shown"] = answer["content"] + f"\n\n*{self.placed(added, dropped)}*"
        self.save_talk()
        self.paint()

    def place(self, target, new, by):
        """A rewrite from the talk into the text as suggestions: against the selected passage,
        or as new text at the cursor."""
        self.inserted = False
        if "text" in target:
            items = writing.differences(target["text"], new, target["at"], by)
        else:
            ft = self.tab.editor.file_text() if target["file"] == self.tab.file \
                else self.tab.project.chapter(target["file"])
            same = writing.matching_passage(ft, new)
            if same is not None:
                return self.tab.suggest(target["file"], writing.differences(ft[same[0]:same[1]], new.strip(),
                                                                            same[0], by))
            at = min(target.get("cursor", len(ft)), len(ft))
            head = "" if at == 0 or ft[:at].endswith("\n") else "\n"
            tail = "" if at >= len(ft) or ft[at:].startswith("\n") else "\n"
            new = re.sub(r"\n\s*\n", "\n", new.strip())
            items = [{"at": at, "old": "", "new": head + new + tail, "by": by, "time": time.time()}]
            self.inserted = True
        return self.tab.suggest(target["file"], items)

    def placed(self, added, dropped):
        """The note under an answer whose changes went into the text."""
        note = f"→ {added} change{'s' if added != 1 else ''} put into the text"
        if dropped:
            note += f" ({dropped} couldn't be placed: the text changed meanwhile)"
        if self.inserted and added:
            note += ". Nothing was selected, so it went in at the cursor as new text; to have a passage " \
                    "replaced, select it first"
        return note

    def run_template(self):
        name = self.template.currentData()
        t = next((t for t in writing.templates() if t.name == name), None)
        if t is None or self.worker is not None or self.tab.file is None:
            return
        if t.reviews:
            self.tab.side.setCurrentWidget(self.tab.review)
            self.tab.review.review(t)
            return
        if not t.changes:
            self.send(t.prompt, t.style, t.name)
            return
        self.tab.save()
        ft = self.tab.editor.file_text()
        sel = self.selection()
        passages = writing.parts(ft, *sel) if sel else writing.parts(ft)
        if not passages:
            return
        if not sel and QMessageBox.question(
                self, t.name, f"Nothing is selected: go over the whole chapter ({len(passages)} passages, "
                f"{writing.words(ft):,} words)? The changes come in as suggestions, passage by passage."
                ) != QMessageBox.StandardButton.Yes:
            return
        persona = self.persona()
        self.talk.append({"role": "user", "content": t.prompt, "shown": f"{t.name}: "
                          + (f"the selection ({writing.words(ft[sel[0]:sel[1]]):,} words)" if sel
                             else f"the whole chapter, {len(passages)} passages"), "time": time.time(),
                          "target": {"file": self.tab.file}, "template": t.name})
        self.save_talk()
        self.paint()

        def go():
            self.worker = Ask(self.persona(), style=t.style, file=self.tab.file, passages=passages,
                              prompt=t.prompt, by=f"{persona.name} · {t.name}")
            # A mechanical job (Factual, like English polish) fixes and doesn't restyle: no style page.
            if t.style != "Factual":
                self.worker.style_guide = writing.style_of(self.tab.project)
            self.worker.part.connect(self.on_part)
            self.worker.found.connect(self.on_found)
            self.worker.failed.connect(lambda e: self.window.say(f"Writing: no answer: {e}"))
            self.worker.finished.connect(self.answered)
            self.counted = [0, 0]
            self.busy(True)
            self.worker.start()
        self.when_ready(go)

    def on_found(self, file, items, n, total):
        added, dropped = self.tab.suggest(file, items)
        self.counted[0] += added
        self.counted[1] += dropped


class Remember(QThread):
    """Builds the story memory in the background: a note for each chapter in files (those
    without one or whose chapter changed, never locked ones), then the cast and the threads
    from all notes."""
    progress = pyqtSignal(str)
    noted = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, project, persona, files, pages=True, again=False):
        super().__init__()
        self.project, self.persona, self.files, self.pages = project, persona, list(files), pages
        self.again = again         # read them even when their note is up to date
        self.stopping = False
        self.made = 0

    def stop(self):
        self.stopping = True

    def ask(self, task):
        got = {"text": ""}

        def collect(kind, text):
            if kind == "text":
                got["text"] += text
        messages = [{"role": "system", "content": writing.ARCHIVIST}, {"role": "user", "content": task}]
        body = chat.request_for(self.persona, messages, {"style": "Factual", "thinking": "off"})
        chat.stream(body, collect, lambda: self.stopping)
        # Some models wrap the whole answer in a code block.
        return re.sub(r"^```\w*\n|\n```$", "", got["text"].strip()).strip()

    def run(self):
        try:
            try:
                self.room = int(summary.context_size() * 0.7 * 3.2)
            except (OSError, ValueError, KeyError):
                self.room = int(8192 * 0.7 * 3.2)
            for file in self.files:
                if self.stopping:
                    return
                self.note(file)
            if self.pages == "always" and not self.stopping:
                self.make_pages()
        except OSError as e:
            self.failed.emit(str(e))

    def note(self, file):
        p = self.project
        index = writing.read_index(p)
        state = writing.note_state(p, file, index)
        if state == "locked" or (state == "ok" and not self.again):
            return
        text = p.chapter(file)
        if not writing.words(writing.body_of(text)):
            return
        n = next(i for i, (f, _) in enumerate(p.chapters, 1) if f == file)
        title = writing.title_of(text, f"Chapter {n}")
        self.done_count = getattr(self, "done_count", 0) + 1
        self.progress.emit(f"Reading chapter {n} ({self.done_count} of {len(self.files)} to read): {title}…")
        names = [h[3:] for h, _ in writing.sections(writing.read_page(os.path.join(writing.bible_dir(p), "cast.md"))) if h]
        before = writing.notes_text(p, before=file, short=True)
        if len(before) > self.room // 3:
            before = "[…]\n" + before[-(self.room // 3):]
        intro = (f"The novel is \"{p.title}\".\n"
                 + (f"Characters known so far (use these names): {', '.join(names)}.\n" if names else "")
                 + (f"The story so far, chapter by chapter:\n<so_far>\n{before}\n</so_far>\n" if before else ""))
        # A chapter too long for the model's context is read in pieces.
        body = writing.body_of(text).strip()
        pieces = [body] if len(body) < self.room - 2000 else self.split(body)
        notes = []
        for k, piece in enumerate(pieces, 1):
            if self.stopping:
                return
            which = f" (part {k} of {len(pieces)})" if len(pieces) > 1 else ""
            notes.append(self.ask(f"{intro}\nChapter {n}: {title}{which}\n<chapter>\n{piece}\n</chapter>\n\n"
                                  f"{writing.NOTE_TASK}"))
        if self.stopping or not any(notes):
            return
        note = notes[0] if len(notes) == 1 else \
            "\n\n".join(f"#### Part {k}\n\n{t}" for k, t in enumerate(notes, 1))
        writing.write_page(writing.note_path(p, file), note)
        writing.write_page(writing.model_copy(p, f"chapters/{file}"), note)
        index = writing.read_index(p)
        index["notes"][file] = {"hash": writing.chapter_hash(text), "locked": False, "time": time.time()}
        writing.write_index(p, index)
        self.made += 1
        if self.pages:
            self.update_pages(note, n, title)
        self.noted.emit(file)

    def update_pages(self, note, n, title):
        """Cast and threads brought up to date with one new chapter note: the cast only for the
        characters in it, the threads as a whole (a short list)."""
        p = self.project
        index = writing.read_index(p)
        cast_path = os.path.join(writing.bible_dir(p), "cast.md")
        cast = writing.read_page(cast_path)
        if self.stopping:
            return
        self.progress.emit(f"Chapter {n}: updating the cast…")
        got = self.ask(f"{writing.CAST_UPDATE}\n\nThe cast list so far:\n<cast>\n{cast.strip() or '(empty)'}\n</cast>\n\n"
                       f"The entry for chapter {n} ({title}):\n<entry>\n{note}\n</entry>")
        if got and not self.stopping:
            merged = writing.merge_sections(cast, got, index["locked"].get("cast.md", []))
            writing.write_page(cast_path, merged)
            writing.write_page(writing.model_copy(p, "cast.md"),
                               writing.merge_sections(writing.read_page(writing.model_copy(p, "cast.md")), got, []))
        threads_path = os.path.join(writing.bible_dir(p), "threads.md")
        threads = writing.read_page(threads_path)
        if self.stopping:
            return
        self.progress.emit(f"Chapter {n}: updating the threads…")
        got = self.ask(f"{writing.THREADS_UPDATE}\n\nThe threads so far:\n<threads>\n{threads.strip() or '(none yet)'}\n"
                       f"</threads>\n\nThe entry for chapter {n} ({title}):\n<entry>\n{note}\n</entry>")
        if got and "## " in got and not self.stopping:
            writing.write_page(writing.model_copy(p, "threads.md"), got)
            writing.write_page(threads_path, writing.merge_locked(p, "threads.md", got, index))

    def split(self, body):
        size = max(4000, self.room - 3000)
        pieces, current = [], ""
        for para in body.split("\n"):
            if current and len(current) + len(para) > size:
                pieces.append(current)
                current = ""
            current = f"{current}\n{para}" if current else para
        return pieces + ([current] if current else [])

    def make_pages(self):
        p = self.project
        entries = writing.notes_text(p).split("\n\n## ")
        entries = [e if i == 0 else "## " + e for i, e in enumerate(entries) if e.strip()]
        if not entries:
            return
        index = writing.read_index(p)
        for name, task, what in (("cast.md", writing.CAST_TASK, "cast list"),
                                 ("threads.md", writing.THREADS_TASK, "threads")):
            # In batches that fit: the page so far, then the next chapter entries.
            page, batch, batches = "", [], []
            for e in entries:
                if batch and sum(map(len, batch)) + len(e) > self.room // 2:
                    batches.append(batch)
                    batch = []
                batch.append(e)
            batches.append(batch)
            for k, batch in enumerate(batches, 1):
                if self.stopping:
                    return
                self.progress.emit(f"Writing the {what}" + (f" ({k} of {len(batches)})" if len(batches) > 1 else "") + "…")
                notes = "\n\n".join(batch)
                if page:
                    prompt = (f"{task}\n\nThe {what} so far:\n<so_far>\n{page}\n</so_far>\n\nMore chapter entries; "
                              f"write the complete updated {what}:\n<entries>\n{notes}\n</entries>")
                else:
                    prompt = f"{task}\n\n<entries>\n{notes}\n</entries>"
                page = self.ask(prompt) or page
            if page and not self.stopping:
                writing.write_page(writing.model_copy(p, name), page)
                writing.write_page(os.path.join(writing.bible_dir(p), name), writing.merge_locked(p, name, page, index))


class CastDialog(QDialog):
    """Who is in a chapter: ticked entries of the cast page go to the models with it."""

    def __init__(self, tab, labels, chosen, found):
        super().__init__(tab)
        self.tab = tab
        self.setWindowTitle("Cast of this chapter")
        info = QLabel("Who is in this chapter. The models get only these people's cast entries (and of "
                      "anyone you name in your message)." + ("" if chosen is not None else
                      " Nothing picked yet: ticked are the ones whose name comes up in the chapter."))
        info.setWordWrap(True)
        self.list = QListWidget()
        for label in labels:
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if label in (chosen if chosen is not None else found)
                               else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        auto = buttons.addButton("By names", QDialogButtonBox.ButtonRole.ResetRole)
        auto.setToolTip("No pick: the people whose name comes up in the chapter, worked out each time")
        auto.clicked.connect(lambda: self.done_with(None))
        buttons.accepted.connect(lambda: self.done_with([self.list.item(i).text() for i in range(self.list.count())
                                                         if self.list.item(i).checkState() == Qt.CheckState.Checked]))
        buttons.rejected.connect(self.reject)
        box = QVBoxLayout(self)
        box.addWidget(info)
        box.addWidget(self.list, 1)
        box.addWidget(buttons)
        self.resize(360, 480)

    def done_with(self, chosen):
        p, file = self.tab.project, self.tab.file
        if chosen is None:
            p.cast.pop(file, None)
        else:
            p.cast[file] = chosen
        p.write()
        self.accept()


class MemoryDialog(QDialog):
    """The story memory of a project: build it, read it, correct it."""

    def __init__(self, tab):
        super().__init__(tab)
        self.tab = tab
        self.project = tab.project
        self.setWindowTitle(f"Story memory · {self.project.title}")
        self.resize(1000, 720)
        self.current = None
        intro = QLabel("What a model knows about the whole story: a note on every chapter, the cast and the "
                       "threads made from them, and your plans. It goes along with everything you ask in the "
                       "Writing tab. Correct whatever is wrong: what you change is locked and never "
                       "overwritten when it is built again.")
        intro.setWordWrap(True)
        self.build_btn = QPushButton("Build / update")
        self.build_btn.setToolTip("Read the chapters that have no note yet or changed since, then make the cast "
                                  "and threads again (with the model of the persona in the Writing panel)")
        self.build_btn.clicked.connect(lambda: tab.remember())
        self.pages_btn = QPushButton("Make cast and threads again")
        self.pages_btn.clicked.connect(lambda: tab.remember(files=[], pages="always"))
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(tab.stop_remembering)
        self.progress = QLabel()
        self.reader = QComboBox()
        self.reader.setToolTip("The model that reads the chapters. A small fast one does the job: a 9B model "
                               "takes about a minute a chapter, a 27B one about three.")
        persona = tab.talk.persona()
        self.reader.addItem(f"{persona.name}'s model" if persona else "The persona's model", "")
        for l in sorted((l for l in launchers.find_launchers() if not l.error and l.present and not l.host),
                        key=lambda l: l.name):
            self.reader.addItem(l.label(), l.name)
        self.reader.setCurrentIndex(max(self.reader.findData(self.project.memory_model), 0))
        self.reader.currentIndexChanged.connect(self.pick_reader)
        top = QHBoxLayout()
        top.addWidget(QLabel("Read with:"))
        top.addWidget(self.reader)
        for b in (self.build_btn, self.pages_btn, self.stop_btn):
            top.addWidget(b)
        top.addWidget(self.progress, 1)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.pick)
        self.text = QPlainTextEdit()
        self.text.textChanged.connect(lambda: self.save_btn.setEnabled(True))
        self.state = QLabel()
        self.state.setWordWrap(True)
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.save)
        self.unlock_btn = QPushButton("Unlock")
        self.unlock_btn.setToolTip("Let the model write this again the next time it builds")
        self.unlock_btn.clicked.connect(self.unlock)
        self.read_btn = QPushButton("Read this chapter")
        self.read_btn.setToolTip("Only this chapter: its note, then cast and threads")
        self.read_btn.clicked.connect(lambda: self.current and tab.remember_this(self.current[9:]))
        buttons = QHBoxLayout()
        buttons.addWidget(self.state, 1)
        buttons.addWidget(self.read_btn)
        buttons.addWidget(self.unlock_btn)
        buttons.addWidget(self.save_btn)
        right = QWidget()
        rbox = QVBoxLayout(right)
        rbox.setContentsMargins(0, 0, 0, 0)
        rbox.addWidget(self.text, 1)
        rbox.addLayout(buttons)
        split = QSplitter()
        split.addWidget(self.list)
        split.addWidget(right)
        split.setSizes([300, 700])
        box = QVBoxLayout(self)
        box.addWidget(intro)
        box.addLayout(top)
        box.addWidget(split, 1)
        self.fill()
        self.running(tab.remembering is not None)

    def pick_reader(self, *_):
        self.project.memory_model = self.reader.currentData() or ""
        self.project.write()
        if self.tab.project is not None and self.tab.project.id == self.project.id:
            self.tab.project.memory_model = self.project.memory_model

    def running(self, on, text=""):
        self.build_btn.setEnabled(not on)
        self.pages_btn.setEnabled(not on)
        self.stop_btn.setEnabled(on)
        self.progress.setText(text if on else "")

    def fill(self):
        keep = self.current
        index = writing.read_index(self.project)
        self.list.blockSignals(True)
        self.list.clear()
        for name, label in writing.BIBLE_PAGES:
            locked = index["locked"].get(name)
            item = QListWidgetItem(label + (f"  · {len(locked)} locked" if locked else ""))
            item.setData(ROLE, name)
            self.list.addItem(item)
        words = {"none": "not read yet", "ok": "", "outdated": "chapter changed since", "locked": "locked"}
        for n, (file, _) in enumerate(self.project.chapters, 1):
            state = writing.note_state(self.project, file, index)
            title = writing.title_of(self.project.chapter(file), f"Chapter {n}")
            item = QListWidgetItem(f"{n}. {title}" + (f"  · {words[state]}" if words[state] else ""))
            item.setData(ROLE, f"chapters/{file}")
            self.list.addItem(item)
        self.list.blockSignals(False)
        for i in range(self.list.count()):
            if self.list.item(i).data(ROLE) == keep:
                self.list.setCurrentRow(i)
                return
        self.list.setCurrentRow(0)

    def relabel(self):
        """The chapter lines again, while a build runs, without touching what is open."""
        index = writing.read_index(self.project)
        words = {"none": "not read yet", "ok": "", "outdated": "chapter changed since", "locked": "locked"}
        for i in range(self.list.count()):
            item = self.list.item(i)
            name = item.data(ROLE)
            if not name.startswith("chapters/"):
                continue
            file = name[9:]
            n = next((k for k, (f, _) in enumerate(self.project.chapters, 1) if f == file), 0)
            state = writing.note_state(self.project, file, index)
            title = writing.title_of(self.project.chapter(file), f"Chapter {n}")
            item.setText(f"{n}. {title}" + (f"  · {words[state]}" if words[state] else ""))
        if self.current and not self.save_btn.isEnabled() and self.current not in writing.USER_PAGES:
            # What is shown, if you haven't changed it, follows the build.
            self.text.blockSignals(True)
            self.text.setPlainText(writing.read_page(self.path(self.current)))
            self.text.blockSignals(False)
            self.show_state()

    def path(self, name):
        return writing.note_path(self.project, name[9:]) if name.startswith("chapters/") \
            else os.path.join(writing.bible_dir(self.project), name)

    def pick(self, item, *_):
        if self.current and self.save_btn.isEnabled() and QMessageBox.question(
                self, "Story memory", "Save your changes first?") == QMessageBox.StandardButton.Yes:
            self.save()
        self.current = item.data(ROLE) if item else None
        if self.current is None:
            return
        self.text.blockSignals(True)
        self.text.setPlainText(writing.read_page(self.path(self.current)))
        self.text.blockSignals(False)
        self.save_btn.setEnabled(False)
        self.show_state()

    def show_state(self):
        name, index = self.current, writing.read_index(self.project)
        if name == "style.md":
            self.text.setPlaceholderText(
                "What the writing is going for. Only you write here; it goes along with the talk, Review and "
                "rewrites (not English polish), so the model judges by your aim, not its own taste. For example:\n\n"
                "Genre and tone:\nFor whom:\nPace:\nDialogue and description:\nVoice:\nAvoid:\nFeels like:")
            self.state.setText("Yours: models read this, they never write it.")
            self.unlock_btn.hide()
            self.read_btn.hide()
        elif name == "plans.md":
            self.text.setPlaceholderText("Where the story is going, what must happen, what must not be forgotten. "
                                         "Only you write here; the models read it.")
            self.state.setText("Yours: models read this, they never write it.")
            self.unlock_btn.hide()
            self.read_btn.hide()
        elif name.startswith("chapters/"):
            self.text.setPlaceholderText("No note yet: Read this chapter, or Build / update.")
            state = writing.note_state(self.project, name[9:], index)
            self.read_btn.show()
            self.read_btn.setText("Read this chapter" if state == "none" else "Read this chapter again")
            self.state.setText({"none": "Not read yet.", "ok": "Made by the model from the chapter as it is now.",
                                "outdated": "The chapter changed since this note was made: Build / update reads it again.",
                                "locked": "Locked: you changed it, so building never overwrites it."}[state])
            self.unlock_btn.setVisible(state == "locked")
        else:
            self.text.setPlaceholderText("Made from the chapter notes when the memory is built.")
            self.read_btn.hide()
            locked = index["locked"].get(name, [])
            self.state.setText("Made by the model from the chapter notes."
                               + (f" Locked (yours): {', '.join(locked)}." if locked else
                                  " Change a section and it is locked."))
            self.unlock_btn.setVisible(bool(locked))

    def save(self):
        if self.current is None:
            return
        if self.current in writing.USER_PAGES:
            writing.write_page(self.path(self.current), self.text.toPlainText())
        else:
            writing.save_by_user(self.project, self.current, self.text.toPlainText())
        self.save_btn.setEnabled(False)
        self.fill()
        self.show_state()

    def unlock(self):
        index = writing.read_index(self.project)
        if self.current.startswith("chapters/"):
            index["notes"].get(self.current[9:], {})["locked"] = False
            index["notes"].get(self.current[9:], {})["hash"] = ""
        else:
            index["locked"].pop(self.current, None)
        writing.write_index(self.project, index)
        self.fill()
        self.show_state()

    def done(self, r):
        if self.save_btn.isEnabled() and self.current and QMessageBox.question(
                self, "Story memory", "Save your changes?") == QMessageBox.StandardButton.Yes:
            self.save()
        self.tab.memory_dialog = None
        super().done(r)


class ReviewPanel(QWidget):
    """The Review side of the right panel: the checker's and the reviewer's notes on the open
    chapter. Notes never change the text; a suggestion in a note goes into the text as changes
    only with Try it."""
    SHOW = [("Open", lambda n: n.get("status", "open") == "open"),
            ("Outdated", lambda n: n.get("status") == "outdated"),
            ("Done or dismissed", lambda n: n.get("status") in ("done", "dismissed")),
            ("All", lambda n: True)]

    def __init__(self, tab):
        super().__init__()
        self.tab = tab
        self.worker = None
        check_btn = QPushButton("Check")
        check_btn.setToolTip("Count patterns, no model: phrases from your slop list, many gestures close "
                             "together, long runs of dialogue, words and sentence openings repeated. Warnings "
                             "to look at, not errors.")
        check_btn.clicked.connect(self.check)
        self.review_btn = QPushButton("Review")
        self.review_btn.setToolTip("The persona reads the chapter (or the selection) as an editor and writes notes: "
                                   "what may be wrong and why. It never changes the text.")
        self.review_btn.clicked.connect(lambda: self.review())
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(lambda: self.worker and self.worker.stop())
        self.stop_btn.hide()
        slop_btn = QPushButton("Slop list…")
        slop_btn.setToolTip("The phrases Check looks for, one per line: open the file to change them")
        slop_btn.clicked.connect(self.open_slop)
        row = QHBoxLayout()
        for b in (check_btn, self.review_btn, self.stop_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(slop_btn)
        self.show_box = QComboBox()
        for name, _ in self.SHOW:
            self.show_box.addItem(name)
        self.kind_box = QComboBox()
        self.kind_box.addItem("All kinds", None)
        for k in ["slop", "pattern"] + [k for k in writing.KINDS if k != "slop"]:
            self.kind_box.addItem(k, k)
        for b in (self.show_box, self.kind_box):
            b.currentIndexChanged.connect(self.fill)
        frow = QHBoxLayout()
        frow.addWidget(self.show_box)
        frow.addWidget(self.kind_box)
        frow.addStretch(1)
        self.status = QLabel()
        self.status.setEnabled(False)
        loose(self.status)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.currentItemChanged.connect(self.pick)
        self.view = QTextBrowser()
        self.try_btn = QPushButton("Try it")
        self.try_btn.setToolTip("Put the note's suggestion into the text as changes (red and green), to accept or decline")
        self.try_btn.clicked.connect(self.try_it)
        ask_btn = QPushButton("Ask…")
        ask_btn.setToolTip("Talk about this note: the passage is selected and the note goes into the talk's box")
        ask_btn.clicked.connect(self.ask)
        self.done_btn = QPushButton("Done")
        self.done_btn.clicked.connect(lambda: self.set_status("done"))
        self.dismiss_btn = QPushButton("Dismiss")
        self.dismiss_btn.setToolTip("Not a problem here")
        self.dismiss_btn.clicked.connect(lambda: self.set_status("dismissed"))
        self.reopen_btn = QPushButton("Reopen")
        self.reopen_btn.clicked.connect(lambda: self.set_status("open"))
        brow = QHBoxLayout()
        for b in (self.try_btn, ask_btn):
            brow.addWidget(b)
        brow.addStretch(1)
        for b in (self.done_btn, self.dismiss_btn, self.reopen_btn):
            brow.addWidget(b)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.list)
        split.addWidget(self.view)
        split.setSizes([360, 220])
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addLayout(row)
        box.addLayout(frow)
        box.addWidget(self.status)
        box.addWidget(split, 1)
        box.addLayout(brow)
        self.fill()

    def notes(self):
        return self.tab.editor.notes

    def fill(self, *_, keep=None):
        item = self.list.currentItem()
        keep = keep or (item.data(ROLE) if item else None)
        wanted = self.SHOW[self.show_box.currentIndex()][1]
        kind = self.kind_box.currentData()
        self.list.blockSignals(True)
        self.list.clear()
        pick = None
        for n in self.notes():
            if not wanted(n) or (kind and n.get("kind") != kind):
                continue
            quote = " ".join(n["quote"].split())
            quote = quote if len(quote) <= 70 else quote[:67] + "…"
            where = "" if n.get("placed", True) else " · passage not found"
            status = "" if n.get("status", "open") == "open" else f" · {n['status']}"
            item = QListWidgetItem(f"{n.get('kind', 'other')}{status}{where}: “{quote}”\n{n['note'][:140]}")
            item.setData(ROLE, n["id"])
            if n.get("status", "open") != "open":
                item.setForeground(self.palette().placeholderText())
            self.list.addItem(item)
            if n["id"] == keep:
                pick = item
        self.list.blockSignals(False)
        if pick is not None:
            self.list.setCurrentItem(pick)
        else:
            self.view.clear()
        opened = sum(1 for n in self.notes() if n.get("status", "open") == "open")
        self.status.setText(f"{opened} open note{'s' if opened != 1 else ''} in this chapter." if self.tab.file
                            else "")
        self.buttons()

    def current(self):
        item = self.list.currentItem()
        nid = item.data(ROLE) if item else None
        return next((n for n in self.notes() if n["id"] == nid), None)

    def pick(self, *_):
        n = self.current()
        self.buttons()
        if n is None:
            return
        dark = self.palette().window().color().lightness() < 128
        text = f"**{n.get('kind', 'other')}** · {n.get('by', '')} · {n.get('status', 'open')}\n\n> {n['quote']}\n\n{n['note']}"
        if n.get("suggestion"):
            text += f"\n\n**Suggestion:** {n['suggestion']}"
        if n.get("status") == "outdated":
            text += "\n\n*The passage changed too much since: the note is no longer shown in the text.*"
        elif not n.get("placed", True):
            text += "\n\n*The quoted words were not found in the chapter.*"
        self.view.setHtml(to_html(text, dark))
        self.tab.editor.show_note(n)

    def buttons(self):
        n = self.current()
        open_ = n is not None and n.get("status", "open") == "open"
        self.try_btn.setEnabled(open_ and bool(n.get("suggestion")) and self.tab.editor.note_span(n) is not None)
        self.done_btn.setEnabled(open_)
        self.dismiss_btn.setEnabled(open_)
        self.reopen_btn.setEnabled(n is not None and n.get("status") in ("done", "dismissed"))

    def set_status(self, status):
        n = self.current()
        if n is None:
            return
        if status == "open":
            # Back on its passage, if that is still there.
            span = writing.place_note(self.tab.editor.file_text(), n)
            if span is None:
                status = "outdated"
            else:
                n["marks"] = self.tab.editor.mark(self.tab.editor.to_doc(span[0]), self.tab.editor.to_doc(span[1]))
        n["status"] = status
        self.changed()
        self.fill(keep=n["id"])

    def changed(self):
        self.tab.editor.paint()
        self.tab.notes_dirty = True
        self.tab.save_soon()
        self.tab.relabel()

    def check(self):
        if self.tab.file is None:
            return
        found = writing.check(self.tab.editor.file_text())
        # Notes of the last check that are still open go; what you dismissed stays dismissed.
        old = self.notes()
        gone = {(n.get("kind"), n.get("original", n["quote"])) for n in old
                if n.get("source") == "check" and n.get("status") in ("dismissed", "done")}
        self.tab.editor.notes = [n for n in old if not (n.get("source") == "check" and n.get("status", "open") == "open")]
        fresh = [n for n in found if (n["kind"], n["quote"]) not in gone]
        self.tab.editor.add_notes(fresh)
        self.changed()
        self.fill()
        self.tab.window.say(f"Writing: the checker found {len(fresh)} thing{'s' if len(fresh) != 1 else ''} to look at"
                            + (f" ({len(found) - len(fresh)} more you had already dismissed)." if len(found) > len(fresh) else "."))

    def open_slop(self):
        writing.slop_phrases()
        QDesktopServices.openUrl(QUrl.fromLocalFile(writing.SLOP_FILE))

    def review(self, template=None):
        talk = self.tab.talk
        if self.tab.file is None or self.worker is not None or talk.worker is not None:
            return
        t = template or next((t for t in writing.templates() if t.reviews), None)
        if t is None:
            QMessageBox.information(self, "Review", "There is no review template (on: review) in the templates folder.")
            return
        self.tab.save()
        ft = self.tab.editor.file_text()
        sel = talk.selection()
        found = writing.check(ft[sel[0]:sel[1]] if sel else ft)
        hint = ""
        if found:
            lines = [f"- {n['kind']}: “{' '.join(n['quote'].split())[:120]}” ({n['note'].split('.')[0]})" for n in found[:25]]
            hint = ("\n\nA mechanical checker marked these patterns. They are not errors in themselves: judge "
                    "whether each is a problem here, and only write a note for those that are.\n" + "\n".join(lines))
        part = (f"\n\nReview only this passage of the chapter (the rest is there for context):\n<selected>\n"
                f"{ft[sel[0]:sel[1]]}\n</selected>") if sel else ""
        persona = talk.persona()
        if persona is None:
            return
        self.reviewing = (self.tab.file, bool(sel), persona.name, t.name)

        def go():
            messages = [{"role": "system", "content": talk.system(rules=writing.CONTENT_RULES)},
                        {"role": "user", "content": f"{t.prompt}{part}{hint}\n\n{writing.REVIEW_FORMAT}"}]
            self.worker = Ask(talk.persona(), messages, t.style, think=talk.think.isChecked())
            self.worker.part.connect(lambda k, x: None)
            self.worker.failed.connect(lambda e: self.tab.window.say(f"Writing: no review: {e}"))
            self.worker.finished.connect(self.reviewed)
            self.review_btn.hide()
            self.stop_btn.show()
            self.status.setText(f"{persona.name} is reading {'the selection' if sel else 'the chapter'}…")
            self.started = time.time()
            self.worker.start()
        talk.when_ready(go)

    def reviewed(self):
        worker, self.worker = self.worker, None
        self.review_btn.show()
        self.stop_btn.hide()
        file, partial, who, name = self.reviewing
        found = writing.parse_review(worker.answer)
        if worker.stopping:
            self.tab.window.say("Writing: review stopped.")
        elif file != self.tab.file:
            self.tab.window.say("Writing: the review was for another chapter; open it and review again.")
        else:
            if not partial:
                # A new review of the whole chapter takes the place of the open notes of the last one.
                self.tab.editor.notes = [n for n in self.notes()
                                         if not (n.get("source") == "review" and n.get("status", "open") == "open")]
            for n in found:
                n.update(source="review", by=f"{who} · {name}", time=time.time())
            self.tab.editor.add_notes(found)
            self.changed()
            lost = sum(1 for n in found if not n.get("placed", True))
            none = "<none" in worker.answer and not found
            if none:
                # Its reasons for having no notes, where you can read them.
                why = worker.answer.split("<none/>", 1)[-1].strip() or "(no reasons given)"
                self.tab.talk.talk.append({"role": "assistant", "content": worker.answer, "time": time.time(),
                                           "shown": f"**Review: no notes.** {why}", "name": who})
                self.tab.talk.save_talk()
                self.tab.talk.paint()
            self.tab.window.say(f"Writing: {who} wrote {len(found)} note{'s' if len(found) != 1 else ''}"
                                + (" (no notes; why is in the Talk tab)" if none else "")
                                + (f", {lost} on words not found in the chapter" if lost else "")
                                + f" in {time.time() - self.started:.0f} s.")
            if not found and not none and worker.answer.strip():
                # Not in the note format: keep what it said in the talk, so nothing is lost.
                self.tab.talk.talk.append({"role": "assistant", "content": worker.answer, "shown": worker.answer,
                                           "time": time.time(), "name": who})
                self.tab.talk.save_talk()
                self.tab.talk.paint()
                self.tab.window.say("Writing: the review came back without notes in the expected form; it is in the Talk tab.")
        self.fill()

    def try_it(self):
        n = self.current()
        span = self.tab.editor.note_span(n) if n else None
        if span is None or not n.get("suggestion"):
            return
        at = self.tab.editor.to_file(span[0])
        passage = self.tab.editor.file_text()[at:self.tab.editor.to_file(span[1])]
        n["status"] = "done"
        n["marks"] = None
        items = writing.differences(passage, n["suggestion"], at, n.get("by", ""))
        added, dropped = self.tab.suggest(self.tab.file, items)
        self.changed()
        self.fill(keep=n["id"])
        self.tab.window.say(f"Writing: {added} change{'s' if added != 1 else ''} in the text to accept or decline.")

    def ask(self):
        n = self.current()
        if n is None:
            return
        self.tab.editor.show_note(n)
        self.tab.side.setCurrentWidget(self.tab.talk)
        self.tab.talk.input.setPlainText(f"About this passage — {n['note']} ")
        self.tab.talk.input.setFocus()


class ImportDialog(QDialog):
    def __init__(self, parent, project):
        super().__init__(parent)
        self.setWindowTitle("Import")
        self.resize(980, 720)
        self.project = project
        self.pieces = []
        self.text = TextBox()
        self.text.setPlaceholderText("Paste the text here (from a web page the italics come along), "
                                     "or take a file: HTML (an AO3 download, a saved page), PDF, "
                                     "Markdown or plain text.")
        self.text.textChanged.connect(lambda: self.later.start())
        file_btn = QPushButton("From file…")
        file_btn.clicked.connect(self.from_file)
        self.how = QComboBox()
        self.how.addItems(writing.SPLITS)
        self.how.currentIndexChanged.connect(self.resplit)
        self.start = QLineEdit()
        self.start.setPlaceholderText("e.g. Chapter")
        self.start.textChanged.connect(lambda: self.later.start())
        top = QHBoxLayout()
        top.addWidget(file_btn)
        top.addSpacing(20)
        top.addWidget(QLabel("Split at:"))
        top.addWidget(self.how)
        top.addWidget(self.start)
        top.addStretch(1)
        self.found = QListWidget()
        self.found.itemChanged.connect(self.count)
        self.summary = QLabel()
        left = QWidget()
        lbox = QVBoxLayout(left)
        lbox.setContentsMargins(0, 0, 0, 0)
        lbox.addWidget(self.text)
        right = QWidget()
        rbox = QVBoxLayout(right)
        rbox.setContentsMargins(0, 0, 0, 0)
        rbox.addWidget(QLabel("Chapters found (untick what shouldn't be one):"))
        rbox.addWidget(self.found, 1)
        rbox.addWidget(self.summary)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setSizes([620, 340])
        self.new_project = QRadioButton("New project:")
        self.name = QLineEdit()
        self.add_here = QRadioButton(f"Add to \"{project.title}\" after its last chapter" if project
                                     else "Add to the current project")
        self.add_here.setEnabled(project is not None)
        (self.add_here if project is not None else self.new_project).setChecked(True)
        into = QHBoxLayout()
        into.addWidget(self.new_project)
        into.addWidget(self.name, 1)
        into.addSpacing(20)
        into.addWidget(self.add_here)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.check)
        buttons.rejected.connect(self.reject)
        box = QVBoxLayout(self)
        box.addLayout(top)
        box.addWidget(split, 1)
        box.addLayout(into)
        box.addWidget(buttons)
        self.later = QTimer(self)
        self.later.setSingleShot(True)
        self.later.setInterval(400)
        self.later.timeout.connect(self.resplit)
        self.resplit()

    def from_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import from", os.path.expanduser("~"),
                                              "Stories (*.html *.htm *.xhtml *.pdf *.md *.txt);;All files (*)")
        if not path:
            return
        try:
            text = writing.read_file(path)
        except OSError as e:
            QMessageBox.warning(self, "Import", f"Can't read {path}:\n{e}")
            return
        self.text.setPlainText(text)
        if not self.name.text().strip():
            self.name.setText(os.path.splitext(os.path.basename(path))[0].replace("_", " "))
        if path.lower().endswith(".pdf"):
            self.summary.setText("From a PDF: check the paragraphs; HTML keeps them (and the italics) better.")

    def resplit(self):
        how = self.how.currentText()
        self.start.setVisible(how == writing.SPLITS[2])
        self.pieces = writing.split(self.text.toPlainText(), how, self.start.text())
        self.found.blockSignals(True)
        self.found.clear()
        if self.text.toPlainText().strip():
            for title, body, chapter in self.pieces:
                item = QListWidgetItem(f"{title}  ·  {writing.words(body):,} words")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if chapter else Qt.CheckState.Unchecked)
                self.found.addItem(item)
        self.found.blockSignals(False)
        self.count()

    def count(self, *_):
        picked = self.chosen()
        self.summary.setText(f"{len(picked)} chapters, {sum(writing.words(b) for _, b in picked):,} words")

    def chosen(self):
        return [(t, b) for i, (t, b, _) in enumerate(self.pieces)
                if i < self.found.count() and self.found.item(i).checkState() == Qt.CheckState.Checked]

    def check(self):
        if not self.chosen():
            QMessageBox.information(self, "Import", "Nothing to import: no chapter is ticked.")
        elif self.new_project.isChecked() and not self.name.text().strip():
            QMessageBox.information(self, "Import", "The new project needs a name.")
        else:
            self.accept()


class ExportDialog(QDialog):
    def __init__(self, parent, project):
        super().__init__(parent)
        self.setWindowTitle(f"Export \"{project.title}\"")
        self.kind = QComboBox()
        for name, ending in writing.EXPORTS:
            self.kind.addItem(f"{name}  ({ending})", ending)
        self.only_done = QCheckBox("Only chapters marked done")
        self.author = QLineEdit(project.author)
        self.author.setPlaceholderText("for the title page and the e-book")
        form = QVBoxLayout(self)
        for label, w in (("Format:", self.kind), ("Author:", self.author)):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addWidget(w, 1)
            form.addLayout(row)
        form.addWidget(self.only_done)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export…")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addWidget(buttons)


def write_pdf(path, title, chapters):
    doc = QTextDocument()
    doc.setDefaultStyleSheet(writing.STYLE)
    doc.setHtml(writing.to_html(title, chapters, page_breaks=True))
    pdf = QPdfWriter(path)
    pdf.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    pdf.setPageMargins(QMarginsF(20, 20, 20, 20), QPageLayout.Unit.Millimeter)
    pdf.setTitle(title)
    doc.print(pdf)


class WritingTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.project = None
        self.file = None           # the chapter in the editor
        self.saved_text = ""       # its text as in the file
        self.uncommitted = False
        self.loading = False
        self.items_dirty = False
        self.notes_dirty = False
        self.session_notes = {}    # (project, chapter) -> review notes, while the window is open
        self.remembering = None    # the story memory being built
        self.memory_dialog = None

        self.projects = QComboBox()
        self.projects.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.projects.setMinimumContentsLength(24)
        self.projects.currentIndexChanged.connect(self.pick_project)
        # What is done now and then goes into a menu, so the row stays short.
        menu = QMenu(self)
        for label, tip, act in (("New project…", "", self.new_project),
                                ("Import…", "Pasted text, an HTML file or a PDF, split into chapters", self.import_text),
                                ("Export…", "The whole project as one file: Markdown, text, HTML, PDF, EPUB or Word",
                                 self.export),
                                ("Rename…", "", self.rename_project),
                                ("Delete project…", "", self.delete_project)):
            a = menu.addAction(label)
            a.setToolTip(tip)
            a.triggered.connect(act)
        menu.addSeparator()
        self.git_box = menu.addAction("Keep versions (git)")
        self.git_box.setCheckable(True)
        self.git_box.setToolTip("A commit in the project's own folder each time you leave a chapter you "
                                "changed, so every earlier version can be got back")
        self.git_box.toggled.connect(self.set_git)
        menu.setToolTipsVisible(True)
        project_btn = QPushButton("Project")
        project_btn.setMenu(menu)
        post_btn = QPushButton("Copy for AO3")
        post_btn.setToolTip("The open chapter (without its title) on the clipboard as HTML paragraphs with "
                            "italics: paste it into AO3's HTML box, or its rich text box")
        post_btn.clicked.connect(self.copy_for_posting)
        memory_btn = QPushButton("Story memory…")
        memory_btn.setToolTip("What the models know about the whole story: chapter notes, cast, threads, your "
                              "style and plans. Build it, read it, correct it.")
        memory_btn.clicked.connect(self.show_memory)
        folder_btn = QPushButton("Folder")
        folder_btn.setToolTip("Open the project's folder in the file manager")
        folder_btn.clicked.connect(self.open_folder)
        top = QHBoxLayout()
        top.addWidget(QLabel("Project:"))
        top.addWidget(self.projects)
        for b in (project_btn, folder_btn, memory_btn, post_btn):
            top.addWidget(b)
        top.addStretch(1)

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.pick_chapter)
        add_btn = QPushButton("New chapter")
        add_btn.setToolTip("After the selected one")
        add_btn.clicked.connect(self.new_chapter)
        up_btn = QPushButton("▲")
        up_btn.setToolTip("Move up")
        up_btn.clicked.connect(lambda: self.move(-1))
        down_btn = QPushButton("▼")
        down_btn.setToolTip("Move down")
        down_btn.clicked.connect(lambda: self.move(1))
        self.done_btn = QPushButton("Done")
        self.done_btn.setToolTip("Mark the chapter done (finished for now) or back to draft")
        self.done_btn.clicked.connect(self.toggle_done)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self.delete_chapter)
        row1 = QHBoxLayout()
        row1.addWidget(add_btn, 1)
        row1.addWidget(up_btn)
        row1.addWidget(down_btn)
        row2 = QHBoxLayout()
        row2.addWidget(self.done_btn, 1)
        row2.addWidget(del_btn, 1)
        remember_btn = QPushButton("Remember")
        remember_btn.setToolTip("Read this chapter into the story memory (its note, then cast and threads), "
                                "without marking it done")
        remember_btn.clicked.connect(self.remember_this)
        cast_btn = QPushButton("Cast…")
        cast_btn.setToolTip("Who is in this chapter: the models get only their entries from the cast page")
        cast_btn.clicked.connect(self.pick_cast)
        left = QWidget()
        lbox = QVBoxLayout(left)
        lbox.setContentsMargins(0, 0, 0, 0)
        lbox.addWidget(self.list, 1)
        lbox.addLayout(row1)
        lbox.addLayout(row2)
        row3 = QHBoxLayout()
        row3.addWidget(remember_btn, 1)
        row3.addWidget(cast_btn, 1)
        lbox.addLayout(row3)

        self.editor = ChapterEditor()
        size = remembered("font")
        if isinstance(size, (int, float)):
            font = self.editor.font()
            font.setPointSizeF(float(size))
            self.editor.setFont(font)
        self.editor.setPlaceholderText("# Chapter title\n\nThe text. Paste from a web page and the italics come along.")
        self.editor.setToolTip("Ctrl + mouse wheel (or Ctrl+= / Ctrl+-) makes the text bigger or smaller")
        self.editor.textChanged.connect(self.typed)
        self.editor.items_changed.connect(self.items_changed)
        self.editor.cursorPositionChanged.connect(lambda: self.editor.items and self.show_bar())
        QShortcut(QKeySequence.StandardKey.ZoomIn, self.editor).activated.connect(lambda: self.editor.zoom(1))
        QShortcut(QKeySequence("Ctrl+="), self.editor).activated.connect(lambda: self.editor.zoom(1))
        QShortcut(QKeySequence.StandardKey.ZoomOut, self.editor).activated.connect(lambda: self.editor.zoom(-1))
        self.status = QLabel()

        # The bar of the suggestions, only there while there are some.
        self.bar = QWidget()
        bar = QHBoxLayout(self.bar)
        bar.setContentsMargins(0, 0, 0, 0)
        self.bar_label = QLabel()
        bar.addWidget(self.bar_label, 1)
        for label, tip, act in (("◀", "The change before", lambda: self.editor.go(-1)),
                                ("▶", "The next change", lambda: self.editor.go(1)),
                                ("✓ Accept", "Take this change into the text", lambda: self.on_current(self.editor.accept)),
                                ("✗ Decline", "Throw this change away", lambda: self.on_current(self.editor.decline)),
                                ("✎ Edit…", "Change it, then take it", lambda: self.on_current(self.editor.edit)),
                                ("Accept all", "Take every change in this chapter", lambda: self.all_items(True)),
                                ("Decline all", "Throw every change in this chapter away", lambda: self.all_items(False))):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(act)
            bar.addWidget(b)
        self.bar.hide()
        loose(self.bar_label, self.status)

        middle = QWidget()
        mbox = QVBoxLayout(middle)
        mbox.setContentsMargins(0, 0, 0, 0)
        mbox.addWidget(self.bar)
        mbox.addWidget(self.editor, 1)
        mbox.addWidget(self.status)
        self.talk = TalkPanel(self)
        self.editor.selectionChanged.connect(self.talk.show_target)

        self.review = ReviewPanel(self)
        self.side = QTabWidget()
        self.side.addTab(self.talk, "Talk")
        self.side.addTab(self.review, "Review")

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(middle)
        split.addWidget(self.side)
        split.setStretchFactor(1, 1)
        split.setSizes([240, 760, 380])
        box = QVBoxLayout(self)
        box.addLayout(top)
        box.addWidget(split, 1)

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(SAVE_EVERY)
        self.count_timer = QTimer(self)
        self.count_timer.setSingleShot(True)
        self.count_timer.setInterval(COUNT_AFTER)
        self.count_timer.timeout.connect(self.counted)
        self.save_timer.timeout.connect(self.save)
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(lambda: self.leave("Saved"))
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.fill_projects()
        self.talk.project_changed()

    def shutdown(self):
        """The window closes: background work stops (the story memory goes on where it stopped
        the next time it is built)."""
        for worker in (self.remembering, self.talk.worker, self.review.worker):
            if worker is not None:
                worker.stop()
                worker.wait(5000)

    # --- projects ----------------------------------------------------------------

    def fill_projects(self, select=None):
        keep = select or (self.project.id if self.project else None)
        found = sorted(writing.load_all().values(), key=lambda p: p.title.lower())
        self.projects.blockSignals(True)
        self.projects.clear()
        for p in found:
            self.projects.addItem(p.title, p.id)
        self.projects.setCurrentIndex(max(self.projects.findData(keep), 0))
        self.projects.blockSignals(False)
        self.pick_project()

    def pick_project(self, *_):
        self.leave()
        pid = self.projects.currentData()
        same = self.project is not None and self.project.id == pid
        try:
            self.project = writing.Project.read(pid) if pid else None
            if self.project is not None:
                # Earlier versions kept review notes on disk; they are for one session now.
                shutil.rmtree(os.path.join(writing.folder(self.project.id), "review"), ignore_errors=True)
            if self.project is not None and self.project.paragraphs != "lines":
                writing.to_lines(self.project)
                self.window.say(f"Writing: \"{self.project.title}\" now has one line per paragraph "
                                "(the empty lines between them are gone; git has the version before).")
        except OSError:
            self.project = None
        self.git_box.blockSignals(True)
        self.git_box.setChecked(bool(self.project and self.project.git))
        self.git_box.setEnabled(self.project is not None)
        self.git_box.blockSignals(False)
        if not same:
            self.file = None
        self.fill_list()
        if hasattr(self, "talk") and not same:
            self.talk.project_changed()

    def new_project(self):
        name, ok = QInputDialog.getText(self, "New project", "Title:")
        if ok and name.strip():
            p = writing.create(name.strip(), [("Chapter 1", "")])
            self.fill_projects(select=p.id)

    def rename_project(self):
        if self.project is None:
            return
        name, ok = QInputDialog.getText(self, "Rename", "Title:", text=self.project.title)
        if ok and name.strip():
            self.project.title = name.strip()
            self.project.write()
            self.fill_projects()

    def export(self):
        if self.project is None:
            return
        self.leave()
        d = ExportDialog(self, self.project)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        kind, author = d.kind.currentData(), d.author.text().strip()
        if author != self.project.author:
            self.project.author = author
            self.project.write()
        chapters = writing.export_chapters(self.project, d.only_done.isChecked())
        if not chapters:
            QMessageBox.information(self, "Export", "No chapter to export (none is marked done).")
            return
        start = os.path.join(os.path.expanduser("~/Documents"), self.project.title + kind)
        path, _ = QFileDialog.getSaveFileName(self, "Export to", start, f"*{kind}")
        if not path:
            return
        if not path.lower().endswith(kind):
            path += kind
        try:
            if kind == ".pdf":
                write_pdf(path, self.project.title, chapters)
            else:
                writing.export(path, kind, self.project.title, chapters, author)
        except OSError as e:
            QMessageBox.warning(self, "Export", f"Could not write {path}:\n{e}")
            return
        self.window.say(f"Exported {len(chapters)} chapters to {path}")

    def copy_for_posting(self):
        if self.file is None:
            return
        self.save()
        html = writing.for_posting(writing.body_of(self.editor.file_text()))
        data = QMimeData()
        data.setHtml(html)
        # Pasted as plain text (AO3's HTML box) it is the HTML itself.
        data.setText(html)
        QApplication.clipboard().setMimeData(data)
        pending = len(self.editor.items)
        self.show_status("copied for posting" + (f" (without the {pending} changes still waiting)" if pending else ""))

    def delete_project(self):
        if self.project is None:
            return
        if QMessageBox.question(self, "Delete project", f"Delete the project \"{self.project.title}\" with all "
                                f"its {len(self.project.chapters)} chapters?\n(It goes to "
                                "~/AIcove/writing/.trash, from where it can be got back.)"
                                ) != QMessageBox.StandardButton.Yes:
            return
        self.leave()
        try:
            writing.trash(self.project.id)
        except OSError as e:
            QMessageBox.warning(self, "Delete project", f"It could not be moved:\n{e}")
            return
        self.window.say(f"Project \"{self.project.title}\" moved to the trash.")
        self.project, self.file = None, None
        self.fill_projects()

    def open_folder(self):
        if self.project is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(writing.folder(self.project.id)))

    def set_git(self, on):
        if self.project is not None:
            self.project.git = on
            self.project.write()
            if on:
                self.say_error(self.project.commit("Versions on"))

    def import_text(self):
        self.leave()
        d = ImportDialog(self, self.project)
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        chapters = d.chosen()
        if d.new_project.isChecked():
            p = writing.create(d.name.text().strip(), chapters)
            self.fill_projects(select=p.id)
        else:
            # Into a project that has nothing written yet (the empty "Chapter 1" of New
            # project…): the imported chapters take the place of the empty ones.
            files = [f for f, _ in self.project.chapters]
            if all(not writing.words(writing.body_of(self.project.chapter(f))) for f in files):
                self.file = None
                for f in files:
                    self.project.delete_chapter(f)
            for title, body in chapters:
                self.project.add_chapter(title, body)
            self.say_error(self.project.commit(f"Imported {len(chapters)} chapters"))
            self.fill_list()
        self.window.say(f"Imported {len(chapters)} chapters, "
                        f"{sum(writing.words(b) for _, b in chapters):,} words.")

    # --- chapters ----------------------------------------------------------------

    def fill_list(self, select=None):
        keep = select or self.file
        self.list.blockSignals(True)
        self.list.clear()
        self.total = 0
        pick = None
        index = writing.read_index(self.project) if self.project else None
        for n, (file, status) in enumerate(self.project.chapters if self.project else [], 1):
            text = self.editor.file_text() if file == self.file else self.project.chapter(file)
            count = writing.words(writing.body_of(text))
            self.total += count
            item = QListWidgetItem()
            item.setData(ROLE, file)
            pending = len(self.editor.items) if file == self.file \
                else len(writing.read_suggestions(self.project, file))
            notes = sum(1 for x in (self.editor.notes if file == self.file
                                    else self.session_notes.get((self.project.id, file), []))
                        if x.get("status", "open") == "open")
            self.label(item, n, text, status, count, pending, self.memory_mark(file, index)
                       + (f" · {notes} note{'s' if notes != 1 else ''}" if notes else ""))
            self.list.addItem(item)
            if file == keep:
                pick = item
        self.list.blockSignals(False)
        self.list.setCurrentItem(pick or self.list.item(0))
        if self.list.count() == 0:
            self.show_chapter(None)

    def label(self, item, n, text, status, count, pending=0, memory=""):
        mark = "✓ " if status == "done" else ""
        changes = f" · {pending} change{'s' if pending != 1 else ''}" if pending else ""
        item.setText(f"{n}. {mark}{writing.title_of(text, 'untitled')}\n    {count:,} words · {status}{changes}{memory}")

    def memory_mark(self, file, index):
        """" · not in memory" / " · memory outdated", once the project has a story memory."""
        if not index or not index["notes"]:
            return ""
        state = writing.note_state(self.project, file, index)
        if state == "none" and not writing.words(writing.body_of(self.project.chapter(file))):
            return ""
        return {"none": " · not in memory", "outdated": " · memory outdated"}.get(state, "")

    def pick_chapter(self, item, *_):
        self.leave()
        self.show_chapter(item.data(ROLE) if item else None)

    def show_chapter(self, file):
        if file is not None and file == self.file and self.editor.file_text() == self.saved_text:
            # The same chapter again (the list was refilled): keep the place in it.
            self.done_btn.setText("Back to draft" if self.status_of(file) == "done" else "Done")
            self.show_status()
            return
        self.file = file
        self.saved_text = self.project.chapter(file) if file else ""
        self.loading = True
        dropped = self.editor.load(self.saved_text, writing.read_suggestions(self.project, file) if file else [])
        gone = self.editor.load_notes(self.session_notes.get((self.project.id, file), []) if file else [],
                                      self.saved_text)
        if gone:
            self.notes_dirty = True
        self.loading = False
        if hasattr(self, "review"):
            self.review.fill()
        self.relabel()
        if dropped:
            self.window.say(f"Writing: {dropped} suggestion{'s' if dropped != 1 else ''} no longer fit the text "
                            "(it was changed outside AIcove) and were dropped.")
            self.items_dirty = True
            self.save()
        self.show_bar()
        self.editor.setReadOnly(file is None)
        self.done_btn.setText("Back to draft" if self.status_of(file) == "done" else "Done")
        self.show_status()
        if hasattr(self, "talk"):
            self.talk.chapter_changed()

    def status_of(self, file):
        return next((s for f, s in self.project.chapters if f == file), "draft") if self.project else "draft"

    def show_status(self, note=""):
        if self.file is None:
            self.status.setText("No project yet: New project… or Import…" if self.project is None else "")
            return
        count = writing.words(writing.body_of(self.editor.file_text()))
        unsaved = self.editor.file_text() != self.saved_text
        self.status.setText(f"This chapter {count:,} words · all chapters {self.total:,} words · "
                            + (note or ("not saved yet: within a minute, or Ctrl+S" if unsaved else "saved")))

    def typed(self):
        """Every keystroke: as little as possible. The save comes within a minute, the counts
        when you pause."""
        if self.loading or self.file is None:
            return
        self.save_soon()
        self.count_timer.start()

    def save_soon(self):
        if not self.save_timer.isActive():
            self.save_timer.start()

    def counted(self):
        self.relabel()
        self.show_status()

    def relabel(self):
        item = self.list.currentItem()
        if item is not None and self.file is not None:
            text = self.editor.file_text()
            index = writing.read_index(self.project)
            mark = self.memory_mark(self.file, index)
            if index["notes"].get(self.file) and not index["notes"][self.file].get("locked") \
                    and index["notes"][self.file].get("hash") != writing.chapter_hash(text):
                mark = " · memory outdated"
            notes = sum(1 for x in self.editor.notes if x.get("status", "open") == "open")
            mark += f" · {notes} note{'s' if notes != 1 else ''}" if notes else ""
            self.label(item, self.list.row(item) + 1, text, self.status_of(self.file),
                       writing.words(writing.body_of(text)), len(self.editor.items), mark)

    def save(self):
        """The editor's text into its file (no commit)."""
        self.save_timer.stop()
        if self.project is None or self.file is None:
            return
        text = self.editor.file_text()
        if self.editor.items or self.items_dirty:
            # Where the suggestions are moves with every change of the text.
            try:
                writing.write_suggestions(self.project, self.file, self.editor.saved_items())
                self.items_dirty = False
            except OSError as e:
                self.window.say(f"Writing: the suggestions could not be saved: {e}")
        if self.editor.notes or self.notes_dirty:
            went = self.editor.refresh_notes()
            # Notes are for this session only: kept while AIcove runs, never written to disk.
            self.session_notes[(self.project.id, self.file)] = self.editor.saved_notes()
            self.notes_dirty = False
            if went:
                self.review.fill()
                self.relabel()
        if text != self.saved_text:
            try:
                self.project.save_chapter(self.file, text)
            except OSError as e:
                QMessageBox.warning(self, "Writing", f"The chapter could not be saved:\n{e}")
                return
            self.total += writing.words(writing.body_of(text)) - writing.words(writing.body_of(self.saved_text))
            self.saved_text = text
            self.uncommitted = True
        self.show_status()

    def leave(self, note=None):
        """Save, and with git on make the commit for what was done. Called when leaving a
        chapter, the project, the tab or the window, and by Ctrl+S."""
        self.save()
        if self.project is not None and self.uncommitted:
            title = writing.title_of(self.saved_text, self.file or "")
            self.say_error(self.project.commit(f"{title}"))
            self.uncommitted = False
        if note:
            self.show_status(note)
        return True

    def say_error(self, error):
        if error:
            self.window.say(f"Writing: {error}")

    def selected(self):
        item = self.list.currentItem()
        return item.data(ROLE) if item else None

    def new_chapter(self):
        if self.project is None:
            return
        self.leave()
        title, ok = QInputDialog.getText(self, "New chapter", "Title:",
                                         text=f"Chapter {len(self.project.chapters) + 1}")
        if ok:
            file = self.project.add_chapter(title.strip() or "Untitled", after=self.selected())
            self.fill_list(select=file)
            self.editor.setFocus()

    def move(self, step):
        if self.file is not None:
            self.save()
            self.project.move_chapter(self.file, step)
            self.fill_list()

    def toggle_done(self):
        if self.file is not None:
            self.save()
            self.project.set_status(self.file, "draft" if self.status_of(self.file) == "done" else "done")
            self.fill_list()
            self.done_btn.setText("Back to draft" if self.status_of(self.file) == "done" else "Done")
            if self.status_of(self.file) == "done" \
                    and writing.note_state(self.project, self.file) in ("none", "outdated"):
                # The model only works when asked: a hint, not a run.
                self.show_status("done · its story memory is out of date: Remember reads it again")

    def delete_chapter(self):
        if self.file is None:
            return
        title = writing.title_of(self.editor.file_text(), self.file)
        if QMessageBox.question(self, "Delete", f"Delete \"{title}\"?\n(It goes to the project's .trash "
                                "folder.)") != QMessageBox.StandardButton.Yes:
            return
        self.save()
        row = self.list.currentRow()
        self.project.delete_chapter(self.file)
        self.say_error(self.project.commit(f"Deleted {title}"))
        self.file = None
        self.uncommitted = False
        self.fill_list()
        self.list.setCurrentRow(min(row, self.list.count() - 1))

    # --- suggestions ---------------------------------------------------------------

    def items_changed(self):
        self.items_dirty = True
        self.save_soon()
        self.relabel()
        self.show_bar()

    def show_bar(self):
        n = len(self.editor.items)
        self.bar.setVisible(n > 0)
        if n:
            now = self.editor.current()
            where = ""
            if now is not None:
                order = sorted(self.editor.items, key=lambda s: s.a.position())
                old, new = self.editor.describe(now)
                shown = lambda t: ("¶" if "\n" in t and not t.strip() else "space" if t and not t.strip()
                                   else f"“{' '.join(t.split())[:30]}”" if t else "nothing")
                where = f" · this is {order.index(now) + 1}: {shown(old)} → {shown(new)}"
                if now.by:
                    where += f", by {now.by}"
            self.bar_label.setText(f"{n} change{'s' if n != 1 else ''} waiting{where}. Right-click one "
                                   "or put the cursor in it.")

    def on_current(self, act):
        s = self.editor.current()
        if s is None:
            self.editor.go(1)
            s = self.editor.current()
        if s is not None:
            act(s)

    def all_items(self, accept):
        n = len(self.editor.items)
        # Asked only before any change was decided by hand: after that you are going through
        # them, and "all" means the rest.
        if n and (getattr(self.editor, "decided", 0) or QMessageBox.question(
                self, "Writing", f"{'Accept' if accept else 'Decline'} all {n} changes in this chapter?")
                == QMessageBox.StandardButton.Yes):
            self.editor.every(accept)

    def suggest(self, file, items):
        """Suggestions from a model for a chapter: into the editor if it is open, else into its
        file. Returns (put in, couldn't place)."""
        if not items or self.project is None:
            return 0, 0
        if file == self.file:
            return self.editor.add(items)
        text = self.project.chapter(file)
        kept = writing.read_suggestions(self.project, file)
        placed = [i for i in items if writing.locate(text, i) is not None]
        writing.write_suggestions(self.project, file, kept + placed)
        self.fill_list()
        return len(placed), len(items) - len(placed)

    # --- the story memory ----------------------------------------------------------

    def show_memory(self):
        if self.project is None:
            return
        self.leave()
        if self.memory_dialog is None:
            self.memory_dialog = MemoryDialog(self)
        self.memory_dialog.show()
        self.memory_dialog.raise_()

    def remember(self, files=None, pages=True, again=False):
        """Build the story memory: notes for files (default: every chapter without one or
        whose chapter changed), then cast and threads. again: read files even if up to date."""
        if self.project is None:
            return
        if self.remembering is not None:
            self.window.say("Writing: the story memory is being built already; Stop it first (Story memory…).")
            return
        self.save()
        project = self.project
        if files is None:
            index = writing.read_index(project)
            files = [f for f, _ in project.chapters if writing.note_state(project, f, index) in ("none", "outdated")]
            if not files and pages is True:
                pages = "always"

        def go():
            persona = self.talk.persona()
            self.remembering = Remember(project, persona, files, pages, again)
            self.remembering.progress.connect(self.remember_progress)
            self.remembering.noted.connect(lambda f: self.noted(project))
            self.remembering.failed.connect(lambda e: self.window.say(f"Writing: story memory: {e}"))
            self.remembering.finished.connect(self.remembered)
            if self.memory_dialog is not None:
                self.memory_dialog.running(True, "Starting…")
            self.window.say(f"Writing: building the story memory of \"{project.title}\" "
                            f"({len(files)} chapter{'s' if len(files) != 1 else ''} to read)…")
            self.remembering.start()
        self.talk.when_ready(go, project.memory_model or None)

    def noted(self, project):
        """A chapter note is made: the lists show it at once."""
        if self.project is project:
            self.fill_list()
        if self.memory_dialog is not None:
            self.memory_dialog.relabel()

    def pick_cast(self):
        if self.project is None or self.file is None:
            return
        self.save()
        cast = writing.read_page(os.path.join(writing.bible_dir(self.project), "cast.md"))
        labels = writing.cast_labels(cast)
        if not labels:
            QMessageBox.information(self, "Cast", "The cast page has no entries yet (\"## Name\" sections). "
                                    "Build the story memory or write it in Story memory….")
            return
        CastDialog(self, labels, self.project.cast.get(self.file),
                   writing.cast_found(cast, self.project.chapter(self.file))).exec()

    def remember_this(self, file=None):
        """The story memory for one chapter."""
        file = file or self.file
        if self.project is None or file is None:
            return
        self.save()
        state = writing.note_state(self.project, file)
        if state == "locked":
            QMessageBox.information(self, "Story memory", "You changed this chapter's note, so it is locked. "
                                    "Unlock it in Story memory… to have it read again.")
            return
        if state == "ok" and QMessageBox.question(
                self, "Story memory", "This chapter's note is up to date. Read the chapter again anyway?"
                ) != QMessageBox.StandardButton.Yes:
            return
        self.remember(files=[file], again=True)

    def remember_progress(self, text):
        self.status.setText(f"Story memory: {text}")
        if self.memory_dialog is not None:
            self.memory_dialog.running(True, text)

    def stop_remembering(self):
        if self.remembering is not None:
            self.remembering.stop()

    def remembered(self):
        worker, self.remembering = self.remembering, None
        what = f"{worker.made} chapter note{'s' if worker.made != 1 else ''} made"
        self.window.say(f"Writing: story memory {'stopped' if worker.stopping else 'done'}: {what}.")
        if self.memory_dialog is not None:
            self.memory_dialog.running(False)
            self.memory_dialog.fill()
        self.fill_list()
        self.show_status("story memory " + ("stopped" if worker.stopping else "up to date"))
