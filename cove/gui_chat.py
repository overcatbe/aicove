# The Chat tab: pick a persona, talk to it. Its chats are files in its folder (chat.py);
# the model it runs on is started here when another one is loaded.

import html
import math
import json
import os
import re
import threading
import time

import markdown
from PyQt6.QtCore import QSize, QThread, QTimer, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFontDatabase, QColor, QCursor, QFont, QIcon, QLinearGradient, QImage, QImageReader, QKeySequence, QPainter, QPainterPath, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QFileDialog, QMenu, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from . import bestof, chat, facts, knowledge, launchers, personas, pictures, profiles, server, settings, summary, voice

try:
    from pygments.formatters import HtmlFormatter  # noqa: F401  (codehilite needs it)
    CODE = True
except ImportError:
    CODE = False

ROLE = Qt.ItemDataRole.UserRole


PICTURE_WIDTH = 420   # pictures in the chat, in pixels; a click shows them whole
YOUR_PICTURE = 320    # your pictures in the chat: the longer side, in pixels
SEND_SIDE = 1536      # a picture you send is made at most this big (more costs the model time)
FACE_SIZE = 40        # persona pictures in the persona list, in pixels
CHAT_FACE_SIZE = 110  # the persona picture at the top right of the chat, in pixels


def short(n):
    """12345 -> 12.3k, for the context meter."""
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


# Personas without a picture get a circle with their first letter, its colour running from that
# of the name's first letter (top left) to that of its last (bottom right). A letter's colour is
# its place on the colour wheel: A red, through orange, green and blue, to Z.


def letter_colour(c):
    """The colour of a letter or digit, as #rrggbb."""
    c = c.lower()
    if "a" <= c <= "z":
        place = (ord(c) - ord("a")) / 26
    elif c.isdigit():
        place = int(c) / 10
    else:
        place = (ord(c) % 26) / 26
    return QColor.fromHsvF(place, 0.65, 0.80).name()


def face(persona):
    """The file of a persona's round face: its picture, or its letter circle."""
    picture = personas.picture_path(persona.id)
    return round_face(picture) if picture else letter_face(persona.name or persona.id)


def letter_face(name):
    letters = [c for c in name if c.isalnum()] or ["?"]
    letter = letters[0].upper()
    start, end = letter_colour(letters[0]), letter_colour(letters[-1])
    out = os.path.join(settings.STATE_DIR, "faces", f"letter3-{start[1:]}-{end[1:]}-{ord(letter)}.png")
    if not os.path.exists(out):
        side = 256
        image = QPixmap(side, side)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        gradient = QLinearGradient(0, 0, side, side)
        gradient.setColorAt(0, QColor(start))
        gradient.setColorAt(1, QColor(end))
        painter.setBrush(gradient)
        painter.drawEllipse(0, 0, side, side)
        # An outline font: the desktop's may be a bitmap one (Terminus) that can't get this big.
        font = QFont()
        font.setFamilies(["DejaVu Sans", "Noto Sans", "Liberation Sans", "Sans Serif"])
        font.setStyleHint(QFont.StyleHint.SansSerif)
        font.setStyleStrategy(QFont.StyleStrategy.ForceOutline)
        font.setPixelSize(int(side * 0.5))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("white"))
        painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, letter)
        painter.end()
        os.makedirs(os.path.dirname(out), exist_ok=True)
        image.save(out, "PNG")
    return out


def round_face(picture):
    """A round copy of a persona picture, as a PNG in the state folder (the chat view can't round
    pictures itself). Made again when the picture changes."""
    stamp = os.stat(picture).st_mtime_ns
    name = os.path.basename(os.path.dirname(picture)) + f"-{stamp}.png"
    out = os.path.join(settings.STATE_DIR, "faces", name)
    if not os.path.exists(out):
        source = QPixmap(picture)
        side = min(source.width(), source.height())
        source = source.copy((source.width() - side) // 2, (source.height() - side) // 2, side, side)
        face = QPixmap(side, side)
        face.fill(Qt.GlobalColor.transparent)
        painter = QPainter(face)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addEllipse(0, 0, side, side)
        painter.setClipPath(path)
        painter.drawPixmap(0, 0, source)
        painter.end()
        os.makedirs(os.path.dirname(out), exist_ok=True)
        face.save(out, "PNG")
    return out


def to_html(text, dark):
    """Markdown of a message as HTML for Qt's text view. HTML the model writes is shown as
    text, not run."""
    extensions = ["fenced_code", "tables", "sane_lists", "nl2br"]
    configs = {}
    if CODE:
        extensions.append("codehilite")
        configs["codehilite"] = {"noclasses": True, "pygments_style": "monokai" if dark else "default",
                                 "guess_lang": False}
    md = markdown.Markdown(extensions=extensions, extension_configs=configs)
    md.preprocessors.deregister("html_block")
    md.inlinePatterns.deregister("html")
    # Pictures in Markdown can't be shown (nothing is fetched); models invent such links for
    # pictures they made, which are shown under the tool call anyway.
    parts = re.split(r"(```.*?```|`[^`\n]*`)", text, flags=re.S)
    return md.convert("".join(p if i % 2 else re.sub(r"!\[[^\]]*\]\([^)]*\)", "", p)
                              for i, p in enumerate(parts)))


class Face(QWidget):
    """The persona's picture at the top right, with a ring that pulses while the model works:
    orange while it thinks or uses tools, the accent colour while it writes, slow grey while a
    model loads. state() says which ("thinking", "writing", "loading" or None)."""
    RING = 5

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.pixmap = None
        self.now = None
        self.phase = 0.0
        self.setFixedSize(CHAT_FACE_SIZE, CHAT_FACE_SIZE)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.step)
        self.timer.start(40)

    def setPixmap(self, pixmap):
        self.pixmap = pixmap
        self.update()

    def step(self):
        now = self.state() if self.isVisible() else None
        if now is None and self.now is None:
            return
        self.phase += 0.04 if now == "loading" else 0.1
        self.now = now
        self.update()

    def paintEvent(self, _):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        inner = self.rect().adjusted(self.RING + 1, self.RING + 1, -self.RING - 1, -self.RING - 1)
        if self.pixmap is not None:
            painter.drawPixmap(inner, self.pixmap)
        if self.now is None:
            return
        colour = QColor({"thinking": "#e08a1e", "loading": "#8a8a8a"}.get(
            self.now, self.palette().highlight().color().name()))
        glow = 0.5 + 0.5 * math.sin(self.phase)
        colour.setAlphaF(0.25 + 0.75 * glow)
        pen = painter.pen()
        pen.setColor(colour)
        pen.setWidthF(1.5 + (self.RING - 1.5) * glow)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        half = pen.widthF() / 2
        painter.drawEllipse(self.rect().toRectF().adjusted(half + 0.5, half + 0.5, -half - 0.5, -half - 0.5))


class Reply(QThread):
    """Gets one answer in the background; the pieces come as signals. A write the model wants
    is asked in the window (asking), and this thread waits for the answer."""
    part = pyqtSignal(str, str)
    failed = pyqtSignal(str)
    asking = pyqtSignal(str, str)

    def __init__(self, persona, conversation):
        super().__init__()
        self.persona, self.conversation = persona, conversation
        self.stopping = False
        self.answered = threading.Event()
        self.answer = False

    def ask(self, title, message):
        self.answered.clear()
        self.answer = False
        self.asking.emit(title, message)
        self.answered.wait()
        return self.answer and not self.stopping

    def stop(self):
        self.stopping = True
        self.answered.set()

    def run(self):
        try:
            chat.reply(self.persona, self.conversation, self.part.emit, lambda: self.stopping, self.ask)
        except OSError as e:
            self.failed.emit(str(e))


class Measure(QThread):
    """How much of the model's context the next message takes: the persona's prompt, facts,
    knowledge and tools, the summary and the chat (as summary.py counts it)."""
    done = pyqtSignal(object, int, int)

    def __init__(self, persona, conversation, n_ctx, parent):
        # A parent keeps it alive until it has really ended: dropped while its thread still runs,
        # Qt ends the whole program.
        super().__init__(parent)
        self.persona, self.n_ctx = persona, n_ctx
        # A copy: the chat may change while this runs.
        c = conversation or chat.Chat(persona.id, "")
        self.conversation = chat.Chat(c.persona, c.id, c.title, c.created, list(c.messages), c.summary, c.controls)

    def run(self):
        try:
            system, tools = chat.setup(self.persona, self.conversation)
            used, n_ctx = summary.usage(self.conversation, system + json.dumps(tools), self.n_ctx)
        except (OSError, ValueError, KeyError):
            used, n_ctx = -1, 0
        self.done.emit(self.conversation.id, used, n_ctx)


class Summarise(QThread):
    """Summarises the older messages now, when asked by hand (the button by the meter)."""
    failed = pyqtSignal(str)
    said = pyqtSignal(str)

    def __init__(self, persona, conversation, parent):
        super().__init__(parent)
        self.persona, self.conversation = persona, conversation
        self.changed = False

    def stop(self):
        pass

    def run(self):
        try:
            system, tools = chat.setup(self.persona, self.conversation)
            self.changed = summary.keep_up(self.conversation, system + json.dumps(tools), self.said.emit, now=True)
        except (OSError, ValueError, KeyError) as e:
            self.failed.emit(str(e))


class WriteDialog(QDialog):
    """A persona wants to write a file: what changes, and Write it / Don't."""

    def __init__(self, parent, title, message):
        super().__init__(parent)
        self.setWindowTitle(title)
        head, _, rest = message.partition("\n\n")
        label = QLabel(head)
        label.setWordWrap(True)
        text = QPlainTextEdit(rest)
        text.setReadOnly(True)
        text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        buttons = QDialogButtonBox()
        yes = buttons.addButton("Write it", QDialogButtonBox.ButtonRole.AcceptRole)
        no = buttons.addButton("Don't", QDialogButtonBox.ButtonRole.RejectRole)
        no.setDefault(True)
        yes.setAutoDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box = QVBoxLayout(self)
        box.addWidget(label)
        box.addWidget(text, 1)
        box.addWidget(buttons)
        self.resize(720, 460)


class EditDialog(QDialog):
    def __init__(self, parent, title, text, again):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.again = False
        self.text = QPlainTextEdit(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        if again:
            b = buttons.addButton("Save and answer again", QDialogButtonBox.ButtonRole.AcceptRole)
            b.setToolTip("Everything after this message goes, and the persona answers it anew.")
            b.clicked.connect(lambda: setattr(self, "again", True))
        box = QVBoxLayout(self)
        box.addWidget(self.text)
        box.addWidget(buttons)
        self.resize(640, 380)


class Speaking(QThread):
    """Makes the audio of one answer (or finds it made before)."""
    done = pyqtSignal(str, str)

    def __init__(self, pid, profile, text):
        super().__init__()
        self.args = (pid, profile, text)

    def run(self):
        try:
            self.done.emit(voice.speak(*self.args, say=lambda t: None), "")
        except Exception as e:  # noqa: BLE001 (anything left uncaught in a thread ends the program)
            self.done.emit("", str(e) or type(e).__name__)


class Proposing(QThread):
    """Asks the model which facts of a chat are worth keeping."""
    done = pyqtSignal(list, str)

    def __init__(self, persona, conversation):
        super().__init__()
        self.persona, self.conversation = persona, conversation

    def run(self):
        try:
            self.done.emit(facts.propose(self.persona, self.conversation), "")
        except Exception as e:  # noqa: BLE001 (anything left uncaught in a thread ends the program)
            self.done.emit([], str(e) or type(e).__name__)


class FactsDialog(QDialog):
    """A persona's facts, one tab per section file, to edit here or in the text editor."""

    def __init__(self, parent, persona):
        super().__init__(parent)
        self.persona = persona
        self.setWindowTitle(f"What {persona.name} remembers")
        hint = QLabel(f"These go into every chat with {persona.name}. One fact per line. Each section is "
                      f"a file of its own in {os.path.join(personas.folder(persona.id), facts.FOLDER)}.")
        hint.setWordWrap(True)
        self.tabs = QTabWidget()
        self.texts = {}
        known = facts.read(persona.id)
        for section in facts.SECTIONS:
            page = QWidget()
            pbox = QVBoxLayout(page)
            note = QLabel(facts.HINTS[section].capitalize() + ".")
            note.setEnabled(False)
            text = QPlainTextEdit("\n".join(known[section]))
            text.setPlaceholderText("Nothing yet.")
            outside = QPushButton("Open in the text editor")
            outside.setToolTip("Saves what is here, then opens the file in your editor")
            outside.clicked.connect(lambda _, s=section: self.open_outside(s))
            row = QHBoxLayout()
            row.addWidget(note, 1)
            row.addWidget(outside)
            pbox.addLayout(row)
            pbox.addWidget(text, 1)
            self.texts[section] = text
            self.tabs.addTab(page, f"{section} ({len(known[section])})")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        show = buttons.addButton("Show files", QDialogButtonBox.ButtonRole.ActionRole)
        show.setToolTip("Saves what is here, then opens the folder of the four files in the file manager")
        show.clicked.connect(self.show_files)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box = QVBoxLayout(self)
        box.addWidget(hint)
        box.addWidget(self.tabs, 1)
        box.addWidget(buttons)
        self.resize(760, 560)

    def save(self):
        for section, text in self.texts.items():
            facts.write(self.persona.id, section, facts.lines_of(text.toPlainText()))

    def open_outside(self, section):
        """Save, then hand the file to the desktop's editor. This window closes, so it can't
        write over what is changed there."""
        try:
            self.save()
            p = facts.path(self.persona.id, section)
            if not os.path.exists(p):
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "w").close()
        except OSError as e:
            QMessageBox.critical(self, "Facts", str(e))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(p))
        self.reject()

    def show_files(self):
        """Save, make the four files (empty ones too, so they can be found), open their folder.
        This window closes, like for the text editor."""
        try:
            self.save()
            for section in facts.SECTIONS:
                p = facts.path(self.persona.id, section)
                if not os.path.exists(p):
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    open(p, "w").close()
        except OSError as e:
            QMessageBox.critical(self, "Facts", str(e))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(p)))
        self.reject()


class ProposalDialog(QDialog):
    """Facts the model found in a chat: tick, change or drop each, then keep the ticked ones."""

    def __init__(self, parent, persona, found):
        super().__init__(parent)
        self.setWindowTitle(f"What should {persona.name} remember?")
        hint = QLabel("Ticked ones are kept. Change the wording or the section if you like.")
        hint.setWordWrap(True)
        grid = QGridLayout()
        self.rows = []
        for r, (section, fact) in enumerate(found):
            tick = QCheckBox()
            tick.setChecked(True)
            where = QComboBox()
            where.addItems(facts.SECTIONS)
            where.setCurrentText(section)
            line = QLineEdit(fact)
            line.setCursorPosition(0)
            grid.addWidget(tick, r, 0)
            grid.addWidget(where, r, 1)
            grid.addWidget(line, r, 2)
            self.rows.append((tick, where, line))
        grid.setColumnStretch(2, 1)
        inner = QWidget()
        inner.setLayout(grid)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        buttons = QDialogButtonBox()
        buttons.addButton("Keep the ticked ones", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box = QVBoxLayout(self)
        box.addWidget(hint)
        box.addWidget(scroll, 1)
        box.addWidget(buttons)
        self.resize(860, 120 + 40 * min(len(found), 10))

    def chosen(self):
        return [(where.currentText(), line.text().strip()) for tick, where, line in self.rows
                if tick.isChecked() and line.text().strip()]


class ChatTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.persona = None
        self.chat = None
        self.worker = None
        self.waiting = None          # a message that waits for the model to load
        self.open_thinking = set()   # messages whose thinking (or tool result) is shown
        self.live = None             # {"thinking", "text"} of the answer coming in
        self.live_thinking = False   # the thinking of the answer coming in is shown (stays so for the next)
        self.dirty = False
        self.problem = None          # why the last answer didn't come, shown under the chat

        self.people = QListWidget()
        self.people.setIconSize(QSize(FACE_SIZE, FACE_SIZE))
        self.people.currentItemChanged.connect(self.on_persona)
        self.chats = QListWidget()
        self.chats.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chats.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.chats.currentItemChanged.connect(self.on_chat)
        new_btn = QPushButton("New chat")
        new_btn.clicked.connect(self.new_chat)
        rename_btn = QPushButton("Rename…")
        rename_btn.clicked.connect(self.rename_chat)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self.delete_chat)
        row = QHBoxLayout()
        row.addWidget(rename_btn)
        row.addWidget(delete_btn)
        left = QWidget()
        lbox = QVBoxLayout(left)
        lbox.setContentsMargins(0, 0, 0, 0)
        lbox.addWidget(QLabel("Personas"))
        lbox.addWidget(self.people, 2)
        lbox.addWidget(QLabel("Chats"))
        lbox.addWidget(self.chats, 3)
        lbox.addWidget(new_btn)
        lbox.addLayout(row)

        self.title = QLabel()
        font = self.title.font()
        font.setPointSizeF(font.pointSizeF() * 1.25)
        font.setBold(True)
        self.title.setFont(font)
        self.model = QComboBox()
        self.model.currentIndexChanged.connect(lambda _: (self.show_thinking_control(), self.show_state()))
        self.model.setToolTip("The model for this chat: the persona's own on top, then all the others. "
                              "Picking one doesn't change the persona.")
        self.load_btn = QPushButton("Load")
        self.load_btn.setToolTip("Load this model now (the one loaded at the moment is stopped)")
        self.load_btn.clicked.connect(lambda: self.load_model())
        self.state = QLabel()
        self.state.setEnabled(False)
        self.facts_btn = QPushButton("Facts…")
        self.facts_btn.setToolTip("What this persona remembers from earlier chats; it goes into every chat")
        self.facts_btn.clicked.connect(self.edit_facts)
        self.remember_btn = QPushButton("Remember…")
        self.remember_btn.setToolTip("Let the model pick what from this chat is worth remembering; "
                                     "you keep, change or drop each one")
        self.remember_btn.clicked.connect(self.remember)
        self.proposing = None
        self.speaking = None    # (message number, thread) while an answer's audio is being made
        self.playing = None     # message number whose audio plays
        self.player = None
        self.best_btn = QPushButton("Best of…")
        self.best_btn.setToolTip("The lines of this persona you kept: ★ keep under an answer, or select a bit "
                                 "and use the right mouse button")
        self.best_btn.clicked.connect(self.edit_best)
        self.knowledge_btn = QPushButton("Knowledge…")
        self.knowledge_btn.clicked.connect(self.open_knowledge)
        # The persona's picture, once, at the top right (not with every answer).
        self.face = Face(self.working)
        top = QHBoxLayout()
        top.addWidget(self.title)
        top.addStretch(1)
        top.addWidget(self.state)
        top.addWidget(self.model)
        top.addWidget(self.load_btn)
        under = QHBoxLayout()
        under.addWidget(self.facts_btn)
        under.addWidget(self.remember_btn)
        under.addWidget(self.knowledge_btn)
        under.addWidget(self.best_btn)
        under.addStretch(1)
        rows = QVBoxLayout()
        rows.addStretch(1)
        rows.addLayout(top)
        rows.addLayout(under)
        rows.addStretch(1)
        head = QHBoxLayout()
        head.addLayout(rows, 1)
        head.addWidget(self.face)

        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self.on_link)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.view_menu)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("Write here. Enter sends, Shift+Enter makes a new line.")
        self.input.setFixedHeight(96)
        self.input.installEventFilter(self)
        self.input.viewport().installEventFilter(self)
        self.attached = None   # (QImage, name) of a picture going with the next message
        self.attach_label = QLabel()
        self.attach_label.setTextFormat(Qt.TextFormat.RichText)
        self.attach_label.linkActivated.connect(lambda _: self.attach(None))
        self.attach_label.hide()
        self.picture_btn = QPushButton("Picture…")
        self.picture_btn.setToolTip("Send a picture with your message, for a model that can see "
                                    "(one with an eyes file). Dropping or pasting a picture into the box works too.")
        self.picture_btn.clicked.connect(self.pick_attachment)
        self.send_btn = QPushButton("Send")
        self.send_btn.clicked.connect(self.send)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.hide()
        buttons = QVBoxLayout()
        buttons.addWidget(self.send_btn)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(self.picture_btn)
        buttons.addStretch(1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.input, 1)
        bottom.addLayout(buttons)

        # The controls: per chat, over the persona's own settings.
        self.controls = {}
        self.new_controls = {}
        knobs = QHBoxLayout()
        for key, label, options, tip in (
                ("style", "Style", [("as set", "")] + [(n, n) for n in chat.STYLES],
                 "How free the writing is: Factual = focused, Creative and Story = looser and more varied. "
                 "\"as set\" = the persona's (or its launcher's) own settings."),
                ("thinking", "Thinking", [("as set", ""), ("on", "on"), ("off", "off")],
                 "Whether a thinking model thinks before it answers. Off is quicker."),
                ("length", "Length", [("as set", "")] + [(f"{n.lower()} (≤{v} tokens)", n) for n, v in chat.LENGTHS.items()],
                 "The longest an answer may get."),
                ("web", "Web", [("off", ""), ("on", "on")],
                 "On: the persona may search the web and read pages for this chat."),
                ("pictures", "Pictures", [("off", ""), ("on", "on")],
                 "On: the persona may make pictures in this chat (the image server starts when needed).")):
            combo = QComboBox()
            for text, value in options:
                combo.addItem(text, value)
            combo.setToolTip(tip)
            combo.currentIndexChanged.connect(lambda _, k=key: self.on_control(k))
            name = QLabel(label + ":")
            name.setToolTip(tip)
            knobs.addWidget(name)
            knobs.addWidget(combo)
            knobs.addSpacing(12)
            self.controls[key] = combo
        self.keep_btn = QPushButton()
        self.keep_btn.setToolTip("Make this chat's Style, Thinking and Length the persona's settings on this "
                                 "model (here, on this machine); the menus go back to \"as set\"")
        self.keep_btn.clicked.connect(self.keep_for_model)
        self.keep_btn.hide()
        knobs.addWidget(self.keep_btn)
        knobs.addStretch(1)
        self.meter = QProgressBar()
        self.meter.setFixedWidth(230)
        self.meter.setMaximumHeight(18)
        self.meter.hide()
        knobs.addWidget(self.meter)
        self.summarise_btn = QPushButton("Summarise")
        self.summarise_btn.setToolTip("Summarise the older messages now (\"So far\" above the chat), before "
                                      "the context is full. The newer ones stay as they are.")
        self.summarise_btn.clicked.connect(self.summarise_now)
        self.summarise_btn.hide()
        knobs.addWidget(self.summarise_btn)
        self.measuring = None
        self.measured_for = None
        self.contexts = {}   # launcher name -> its context size, once asked

        right = QWidget()
        rbox = QVBoxLayout(right)
        rbox.setContentsMargins(0, 0, 0, 0)
        rbox.addLayout(head)
        rbox.addWidget(self.view, 1)
        rbox.addLayout(knobs)
        rbox.addWidget(self.attach_label)
        rbox.addLayout(bottom)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([250, 900])
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(split)
        QShortcut(QKeySequence("Ctrl+N"), self, self.new_chat)

        # Repaint at most this often while an answer comes in.
        self.paint_timer = QTimer(self)
        self.paint_timer.setInterval(120)
        self.paint_timer.timeout.connect(self.paint_if_dirty)
        self.state_timer = QTimer(self)
        self.state_timer.timeout.connect(self.show_state)
        self.state_timer.start(2000)
        window.proc.finished.connect(self.model_started)
        self.reload()

    # --- personas and chats ----------------------------------------------------------

    def reload(self):
        """Fill the persona list again (personas may have been changed on the Personas tab), and
        look at the knowledge again (files may have been added)."""
        keep = self.persona.id if self.persona else None
        loaded = chat.loaded_launcher()
        self.people.blockSignals(True)
        self.people.clear()
        pick = None
        for p in sorted(personas.load_all().values(), key=lambda p: p.name.lower()):
            item = QListWidgetItem(p.name)
            item.setData(ROLE, p.id)
            item.setIcon(QIcon(face(p)))
            self.people.addItem(item)
            if p.id == keep or (keep is None and pick is None and loaded and loaded.name in p.models):
                pick = item
        self.people.blockSignals(False)
        if self.people.count():
            self.people.setCurrentItem(pick or self.people.item(0))

    def on_persona(self, item, _=None):
        if item is None or self.worker:
            return
        pid = item.data(ROLE)
        if self.persona and self.persona.id == pid:
            self.persona = personas.Persona.read(pid)
            self.show_face()
            self.fill_models(keep=True)
            self.show_knowledge()
            return
        self.persona = personas.Persona.read(pid)
        self.title.setText(self.persona.name)
        self.show_face()
        self.fill_models()
        self.show_knowledge()
        self.fill_chats()

    def working(self):
        """What the model does for this chat right now, for the ring around the face."""
        if self.worker is not None:
            live = self.live or {}
            return "writing" if live.get("text") else "thinking"
        if self.waiting is not None or self.window.busy():
            return "loading"
        return None

    def show_face(self):
        ratio = self.devicePixelRatioF()
        side = CHAT_FACE_SIZE - 2 * (Face.RING + 1)
        pixmap = QPixmap(face(self.persona)).scaled(
            int(side * ratio), int(side * ratio),
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        pixmap.setDevicePixelRatio(ratio)
        self.face.setPixmap(pixmap)

    def fill_models(self, keep=False):
        """Every launcher: the persona's own first (its models, then those it ran on here), then a line, then the others (this machine's,
        then those of other machines). Picking one only changes this chat's model, never the
        persona. keep: what was picked stays (the same persona's list filled again); else what is
        loaded; else the persona's first."""
        here = {l.name: l for l in launchers.find_launchers() if not l.error}
        own = [n for n in self.persona.models if n in here]
        # Then the ones it has run on here (profiles.py), the last used first.
        ran = profiles.read(self.persona.id)
        own += [n for n in sorted(ran, key=lambda n: ran[n]["used"], reverse=True) if n in here and n not in own]
        others = sorted((n for n in here if n not in own), key=lambda n: (bool(here[n].host), n.lower()))
        loaded = chat.loaded_launcher()
        picked = self.model.currentData() if keep else None
        self.model.blockSignals(True)
        self.model.clear()
        for name in own:
            self.model.addItem(here[name].label(), name)
        if own and others:
            self.model.insertSeparator(self.model.count())
        for name in others:
            self.model.addItem(here[name].label(), name)
        index = self.model.findData(picked) if picked else -1
        if index < 0:
            index = self.model.findData(loaded.name) if loaded else -1
        self.model.setCurrentIndex(index if index >= 0 else 0)
        self.model.blockSignals(False)
        self.show_thinking_control()
        self.show_state()

    def fill_chats(self, select=None):
        self.chats.blockSignals(True)
        self.chats.clear()
        pick = None
        for cid, title, mtime in chat.list_chats(self.persona.id):
            item = QListWidgetItem(title)
            item.setData(ROLE, cid)
            item.setToolTip(time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)))
            self.chats.addItem(item)
            if cid == select:
                pick = item
        self.chats.blockSignals(False)
        if pick is not None or (select is None and self.chats.count()):
            self.chats.setCurrentItem(pick or self.chats.item(0))
        else:
            self.chat = None
            self.paint()

    def on_chat(self, item, _=None):
        if self.player is not None:
            self.player.stop()
        if item is None or self.worker:
            return
        try:
            self.chat = chat.Chat.load(self.persona.id, item.data(ROLE))
        except (OSError, ValueError) as e:
            self.window.say(f"Can't read that chat: {e}")
            self.chat = None
        self.open_thinking.clear()
        self.problem = None
        self.show_controls()
        self.paint()
        self.input.setFocus()

    def new_chat(self):
        if self.worker or self.persona is None:
            return
        self.chats.clearSelection()
        self.chats.setCurrentItem(None)
        self.chat = None
        self.problem = None
        self.show_controls()
        self.paint()
        self.input.setFocus()

    def rename_chat(self):
        if self.chat is None or self.worker:
            return
        title, ok = QInputDialog.getText(self, "Rename chat", "Title:", text=self.chat.title)
        if ok and title.strip():
            self.chat.title = title.strip()
            self.chat.save()
            self.fill_chats(self.chat.id)

    def delete_chat(self):
        if self.chat is None or self.worker:
            return
        answer = QMessageBox.question(self, "Delete chat", f"Delete \"{self.chat.title}\"?\n\n"
                                      f"It is kept in {os.path.join(personas.TRASH_DIR, 'chats')}.")
        if answer == QMessageBox.StandardButton.Yes:
            self.chat.delete()
            self.chat = None
            self.fill_chats()

    # --- controls ---------------------------------------------------------------------

    def show_controls(self):
        """The menus as this chat has them; a new chat starts with "as set"."""
        values = self.chat.controls if self.chat else {}
        for key, combo in self.controls.items():
            combo.blockSignals(True)
            combo.setCurrentIndex(max(combo.findData(values.get(key, "")), 0))
            combo.blockSignals(False)
        self.new_controls = {}
        self.show_keep()

    def on_control(self, key):
        value = self.controls[key].currentData()
        self.show_keep()
        if self.chat is None:
            # Kept for the chat the next message starts.
            self.new_controls[key] = value
            return
        self.chat.controls[key] = value
        try:
            self.chat.save()
        except OSError as e:
            self.window.say(f"Can't save the chat: {e}")

    def show_thinking_control(self):
        """Thinking can only be switched for models whose template knows how; the web needs
        a model that can use tools."""
        launcher = launchers.get(self.model.currentData() or "")
        tools = launchers.can_use_tools(launcher.model) if launcher else None
        self.controls["web"].setEnabled(tools is not False)
        self.controls["web"].setToolTip("On: the persona may search the web and read pages for this chat."
                                        if tools is not False else "This model can't use tools, so no web search.")
        self.controls["pictures"].setEnabled(tools is not False)
        self.controls["pictures"].setToolTip(
            "On: the persona may make pictures in this chat (the image server starts when needed)."
            if tools is not False else "This model can't use tools, so no pictures.")
        self.picture_btn.setToolTip(
            "Send a picture with your message. Dropping or pasting a picture into the box works too."
            + ("" if launcher is None or launcher.sees else
               f"\n\n{launcher.friendly or launcher.name} can't see pictures (no 👁): AIcove offers "
               "to load a model that can when you send one."))
        can = launchers.can_think(launcher.model) if launcher else None
        combo = self.controls["thinking"]
        combo.setEnabled(can is not False)
        combo.setToolTip("Whether a thinking model thinks before it answers. Off is quicker." if can is not False
                         else "This model's thinking can't be switched on or off.")
        self.show_keep()

    KEPT = ("style", "thinking", "length")

    def show_keep(self):
        name = self.model.currentData() or ""
        self.keep_btn.setText(f"Keep for {name}")
        self.keep_btn.setVisible(bool(self.persona and name and any(self.controls[k].currentData() for k in self.KEPT)))

    def keep_for_model(self):
        """The chat's Style, Thinking and Length become the persona's settings on the picked model
        (profiles.py, this machine's file)."""
        name = self.model.currentData() or ""
        if not (self.persona and name):
            return
        wanted = dict(chat.STYLES.get(self.controls["style"].currentData(), {}))
        if self.controls["length"].currentData() in chat.LENGTHS:
            wanted["max_tokens"] = chat.LENGTHS[self.controls["length"].currentData()]
        if self.controls["thinking"].currentData() and self.controls["thinking"].isEnabled():
            wanted["thinking"] = self.controls["thinking"].currentData()
        wanted = {k: personas.sampler_value(k, v) for k, v in wanted.items()}
        merged = dict(profiles.read(self.persona.id).get(name, {}).get("samplers", {}))
        merged.update({k: v for k, v in wanted.items() if v is not None})
        try:
            profiles.save_samplers(self.persona.id, {name: merged})
        except OSError as e:
            self.window.say(f"Can't keep the settings: {e}")
            return
        self.window.profile_kept(self.persona.id, name, merged)
        self.window.say(f"{self.persona.name} on {name} now uses "
                        + ", ".join(f"{k} {v}" for k, v in sorted(wanted.items())))
        for key in self.KEPT:
            self.controls[key].setCurrentIndex(0)

    # --- the model --------------------------------------------------------------------

    def show_state(self):
        if self.persona is None:
            return
        loaded = chat.loaded_launcher()
        wanted = self.model.currentData()
        if self.window.busy():
            text = "Loading…"
        elif loaded is None:
            text = "No model loaded"
        elif loaded.name == wanted:
            text = ("Ready" if self.window.health.state == "ready" else "Loading…") \
                + (f" · on {loaded.host} 🖥" if loaded.host else "")
        else:
            text = f"Loaded: {loaded.label()}"
        self.state.setText(text)
        self.load_btn.setEnabled(not self.window.busy() and (loaded is None or loaded.name != wanted))
        self.measure()

    def measure(self, again=False):
        """Show how full the context is, if the model of this persona is loaded and ready. Measured
        again when the chat, its length or the model changed, or when asked (again=True)."""
        loaded = chat.loaded_launcher()
        if self.persona is None or loaded is None or loaded.name != self.model.currentData() \
                or self.window.busy() or self.window.health.state != "ready":
            self.meter.hide()
            self.summarise_btn.hide()
            self.measured_for = None
            return
        key = (self.persona.id, self.chat.id if self.chat else None,
               len(self.chat.messages) if self.chat else 0, loaded.name)
        if self.worker or self.measuring:
            if again:
                self.measured_for = None   # the next look (every 2 s) measures
            return
        if key == self.measured_for and not again:
            return
        self.measured_for = key
        self.measuring = Measure(self.persona, self.chat, self.contexts.get(loaded.name), self)
        self.measuring.finished.connect(self.measuring.deleteLater)
        self.measuring.done.connect(lambda cid, used, n_ctx, name=loaded.name: self.measured(cid, used, n_ctx, name))
        self.measuring.start()

    def measured(self, cid, used, n_ctx, name):
        self.measuring = None
        if used < 0 or (self.chat.id if self.chat else "") != cid:
            self.measured_for = None
            if used < 0:
                self.meter.hide()
                self.summarise_btn.hide()
            return
        self.contexts[name] = n_ctx
        share = used / max(n_ctx, 1)
        self.meter.setRange(0, n_ctx)
        self.meter.setValue(min(used, n_ctx))
        self.meter.setFormat(f"Context {short(used)} of {short(n_ctx)} ({share:.0%})")
        self.meter.setStyleSheet("QProgressBar::chunk { background: #d08a20; }" if share >= summary.TRIGGER else "")
        self.meter.setToolTip(
            f"The next message takes about {used:,} of the model's {n_ctx:,} tokens of context: the "
            f"persona's prompt, its facts and knowledge, and this chat. From {summary.TRIGGER:.0%} on "
            f"the oldest messages are summarised (\"So far\" above the chat).")
        self.meter.show()
        covered = summary.current(self.chat)[0] if self.chat else 0
        self.summarise_btn.setVisible(self.chat is not None and len(self.chat.messages) - covered >= 4)

    def summarise_now(self):
        if self.chat is None or self.worker or self.waiting:
            return
        self.problem = None
        self.live = {"thinking": "", "text": "", "status": "Summarising the older messages…"}
        self.summarise_btn.setEnabled(False)
        self.worker = Summarise(self.persona, self.chat, self)
        self.worker.said.connect(lambda text: self.on_part("status", text))
        self.worker.failed.connect(lambda error: QMessageBox.warning(self, "Summarise", f"No summary: {error}"))
        self.worker.finished.connect(self.summarised)
        self.paint()
        self.worker.start()

    def summarised(self):
        worker, self.worker = self.worker, None
        worker.deleteLater()
        self.live = None
        self.summarise_btn.setEnabled(True)
        if not worker.changed:
            self.window.say("Nothing to summarise yet: the chat is short.")
        self.paint()
        self.measure(again=True)

    def load_model(self, then=None):
        name = self.model.currentData()
        if not name or self.window.busy():
            return
        self.waiting = then
        self.window.say(f"--- {name} for {self.persona.name} ---")
        self.window.run_cove("start", name)
        self.show_state()

    def model_started(self, *_):
        self.show_state()
        message, self.waiting = self.waiting, None
        if message is None:
            return
        loaded = chat.loaded_launcher()
        if loaded is not None and loaded.name == self.model.currentData():
            self.answer()
        else:
            self.window.say("The model didn't come up; the message is kept in the chat. Send again to retry.")

    # --- talking ----------------------------------------------------------------------

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == event.Type.KeyPress \
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.send()
            return True
        # A picture pasted or dropped into the box goes with the message.
        if obj is self.input and event.type() == event.Type.KeyPress and event.matches(QKeySequence.StandardKey.Paste):
            if self.attach_from(QApplication.clipboard().mimeData()):
                return True
        if obj is self.input.viewport() and event.type() in (event.Type.DragEnter, event.Type.DragMove) \
                and self.picture_in(event.mimeData()) is not None:
            event.acceptProposedAction()
            return True
        if obj is self.input.viewport() and event.type() == event.Type.Drop and self.attach_from(event.mimeData()):
            event.acceptProposedAction()
            return True
        return super().eventFilter(obj, event)

    # --- pictures of yours ------------------------------------------------------------

    @staticmethod
    def picture_in(mime):
        """(QImage, name) of the picture in pasted or dropped data, or None."""
        if mime is None:
            return None
        for url in mime.urls():
            if url.isLocalFile():
                image = QImage(url.toLocalFile())
                if not image.isNull():
                    return image, os.path.basename(url.toLocalFile())
        if mime.hasImage():
            image = QImage(mime.imageData())
            if not image.isNull():
                return image, "pasted picture"
        return None

    def attach_from(self, mime):
        found = self.picture_in(mime)
        if found is not None:
            self.attach(found)
        return found is not None

    def pick_attachment(self):
        path, _ = QFileDialog.getOpenFileName(self, "Picture", os.path.expanduser("~"),
                                              "Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp);;All files (*)")
        if not path:
            return
        image = QImage(path)
        if image.isNull():
            QMessageBox.warning(self, "Picture", f"Can't read {path} as a picture.")
            return
        self.attach((image, os.path.basename(path)))

    def attach(self, found):
        """found = (QImage, name) to go with the next message, or None for none."""
        self.attached = found
        if found is None:
            self.attach_label.hide()
        else:
            self.attach_label.setText(f"📎 {html.escape(found[1])} ({found[0].width()}×{found[0].height()}) goes "
                                      f"with your next message · <a href='remove'>remove</a>")
            self.attach_label.show()
        self.input.setFocus()

    def save_attachment(self):
        """Save the attached picture into the persona's pictures folder; returns its file name."""
        image = self.attached[0]
        if max(image.width(), image.height()) > SEND_SIDE:
            image = image.scaled(SEND_SIDE, SEND_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                                 Qt.TransformationMode.SmoothTransformation)
        where = pictures.folder(self.persona.id)
        os.makedirs(where, exist_ok=True)
        ext = ".png" if image.hasAlphaChannel() else ".jpg"
        base, n = "you-" + time.strftime("%Y%m%d-%H%M%S"), 1
        name = base + ext
        while os.path.exists(os.path.join(where, name)):
            n += 1
            name = f"{base}-{n}{ext}"
        if not image.save(os.path.join(where, name), None, 90):
            raise OSError(f"can't save the picture in {where}")
        return name

    def eyes_for_picture(self):
        """Before a picture is sent: if the chosen model can't see, offer one that can. False =
        don't send now."""
        wanted = launchers.get(self.model.currentData() or "")
        if wanted is not None and wanted.sees:
            return True
        here = [l for l in launchers.find_launchers() if not l.error and l.sees and l.present]
        # Its own models first.
        here.sort(key=lambda l: (l.name not in self.persona.models, l.friendly.lower()))
        now = wanted.friendly if wanted else "This model"
        if not here:
            return QMessageBox.question(
                self, "Picture", f"{now} can't see pictures, and no model on this machine can (a launcher "
                "needs an eyes file, an mmproj). Send the message without the picture being seen?"
            ) == QMessageBox.StandardButton.Yes
        box = QMessageBox(self)
        box.setWindowTitle("Picture")
        box.setText(f"{now} can't see pictures. Load {here[0].friendly} for this chat?")
        box.setInformativeText("Load it = the message is sent once it is up (the model loaded now is stopped). "
                               "Send anyway = the model is told a picture is there, but can't see it.")
        load = box.addButton(f"Load {here[0].friendly}", QMessageBox.ButtonRole.AcceptRole)
        anyway = box.addButton("Send anyway", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(load)
        box.exec()
        if box.clickedButton() is anyway:
            return True
        if box.clickedButton() is not load:
            return False
        index = self.model.findData(here[0].name)
        if index < 0:
            self.model.addItem(here[0].label(), here[0].name)
            index = self.model.count() - 1
        self.model.setCurrentIndex(index)
        return True

    def send(self):
        if self.persona is None or self.worker or self.waiting:
            return
        self.problem = None
        text = self.input.toPlainText().strip()
        if not text and not self.attached \
                and not (self.chat and self.chat.messages and self.chat.messages[-1]["role"] == "user"):
            return
        if self.attached and not self.eyes_for_picture():
            return
        if self.chat is None:
            self.chat = chat.Chat.new(self.persona.id)
            self.chat.controls.update(self.new_controls)
        if text or self.attached:
            extra = {}
            if self.attached:
                try:
                    extra["picture"] = self.save_attachment()
                except OSError as e:
                    QMessageBox.warning(self, "Picture", str(e))
                    return
                self.attach(None)
            self.chat.add("user", text, **extra)
            self.chat.save()
            self.input.clear()
            self.fill_chats(self.chat.id)
        self.paint()
        loaded = chat.loaded_launcher()
        if loaded is None or loaded.name != self.model.currentData():
            if self.window.busy():
                self.window.say("A model is being started or stopped; send again when it is done.")
                return
            self.load_model(then=True)
            return
        self.answer()

    def answer(self):
        self.chat.new_version()
        self.live = {"thinking": "", "text": ""}
        self.worker = Reply(self.persona, self.chat)
        self.worker.part.connect(self.on_part)
        self.worker.asking.connect(self.on_ask)
        self.worker.failed.connect(self.on_failed)
        self.worker.finished.connect(self.answered)
        self.send_btn.hide()
        self.stop_btn.show()
        self.paint_timer.start()
        self.worker.start()

    def on_part(self, kind, text):
        if kind == "round":
            # The tools have run; their results are in the chat now, the next part of the answer starts.
            self.live = {"thinking": "", "text": ""}
        elif kind == "tool":
            self.live = {"thinking": "", "text": "", "tool": text}
        elif kind == "status":
            self.live["status"] = text
        elif kind == "note":
            self.window.say(text)
        else:
            self.live[kind] += text
        self.dirty = True

    def on_failed(self, error):
        self.problem = self.why_no_answer(error)
        self.window.say(f"No answer: {self.problem}")

    def why_no_answer(self, error):
        """What went wrong, in words: when llama-server died on the way, what its log says."""
        if server.running() is not None:
            return error
        try:
            with open(server.LLAMA_LOG, errors="replace") as f:
                tail = f.readlines()[-200:]
        except OSError:
            tail = []
        if any("n_ubatch" in line for line in tail):
            name = self.model.currentData() or "this model"
            return (f"llama-server crashed reading the picture: the picture has more tokens than the "
                    f"model takes in one batch. On the Launchers tab, give {name} a bigger batch "
                    f"(add -b 4096 -ub 4096 to its options), Save, and send again.")
        last = next((l.strip() for l in reversed(tail)
                     if any(w in l for w in ("GGML_ASSERT", "error", "failed", "out of memory"))), "")
        return ("llama-server stopped while answering (it crashed). "
                + (f"Its last error: {last}. " if last else "")
                + f"The whole log: {server.LLAMA_LOG}")

    def on_ask(self, title, message):
        worker = self.worker
        if worker is None:
            return
        self.paint()
        worker.answer = WriteDialog(self, title, message).exec() == QDialog.DialogCode.Accepted
        worker.answered.set()

    def stop(self):
        if self.worker:
            self.worker.stop()

    def answered(self):
        self.worker = None
        self.live = None
        if self.chat.messages and self.chat.messages[-1]["role"] == "user" and self.chat.messages[-1].get("answers"):
            self.chat.no_empty_version()
            self.chat.save()
        self.paint_timer.stop()
        self.stop_btn.hide()
        self.send_btn.show()
        self.fill_chats(self.chat.id)
        self.paint()
        self.input.setFocus()
        self.measure()

    def on_link(self, url):
        what, _, number = url.toString().partition(":")[2].partition("/")
        if url.scheme() != "cove":
            QDesktopServices.openUrl(url)
            return
        if what == "think" and number == "live":
            self.live_thinking = not self.live_thinking
            self.paint()
            return
        if self.chat is None or self.worker:
            return
        if what == "summary":
            self.on_summary(number)
            return
        if what == "keep" and number.isdigit() and int(number) < len(self.chat.messages):
            self.keep(self.chat.messages[int(number)]["content"])
            return
        if what == "rate":
            number, _, vote = number.partition("/")
            if number.isdigit() and int(number) < len(self.chat.messages):
                self.rate(int(number), vote)
            return
        if what == "version" and number.isdigit():
            if self.player is not None:
                self.player.stop()
            self.chat.show_version(int(number))
            self.open_thinking.clear()
            self.chat.save()
            self.paint()
            self.measure(again=True)
            return
        if not number.isdigit():
            return
        i = int(number)
        if i >= len(self.chat.messages):
            return
        message = self.chat.messages[i]
        if what == "listen":
            self.listen(i)
            return
        if what == "think":
            self.open_thinking ^= {i}
        elif what == "edit":
            dialog = EditDialog(self, "Edit message", message["content"], message["role"] == "user")
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            message["content"] = dialog.text.toPlainText().strip()
            if dialog.again:
                del self.chat.messages[i + 1:]
                message.pop("answers", None)
                message.pop("shown", None)
            self.chat.save()
            self.paint()
            self.measure(again=True)
            if dialog.again:
                self.send()
            return
        elif what == "againwith":
            launcher = self.pick_model(message.get("model", ""))
            if launcher is None:
                return
            self.model.setCurrentIndex(self.model.findData(launcher))
            self.chat.put_aside()
            self.open_thinking.clear()
            self.chat.save()
            self.paint()
            self.send()
            return
        elif what == "again":
            # A new answer to the same question; this one (with its tool steps) is kept as an
            # earlier version.
            self.chat.put_aside()
            self.open_thinking.clear()
            self.chat.save()
            self.paint()
            self.send()
            return
        elif what == "delete":
            answer = QMessageBox.question(self, "Delete message", "Delete this message?")
            if answer != QMessageBox.StandardButton.Yes:
                return
            # Tool results go with the call they answer.
            ids = {c["id"] for c in message.get("tool_calls", [])}
            self.chat.messages[:] = [m for n, m in enumerate(self.chat.messages)
                                     if n != i and not (m["role"] == "tool" and m.get("tool_call_id") in ids)]
            self.open_thinking.clear()
            self.chat.no_empty_version()
            self.chat.save()
        self.paint()
        self.measure(again=True)

    def rate(self, i, vote):
        """👍/👎 for an answer (the same again takes it back), counted for the persona on the
        answer's model (profiles.py)."""
        message = self.chat.messages[i]
        was = message.get("rating", "")
        now = "" if vote == was else vote if vote in profiles.VOTES else ""
        if now:
            message["rating"] = now
        else:
            message.pop("rating", None)
        self.chat.save()
        try:
            profiles.vote(self.persona.id, message.get("model", ""), was, now)
        except OSError as e:
            self.window.say(f"Can't count the vote: {e}")
        self.paint()

    def pick_model(self, now):
        """A menu of the chat's models at the mouse; the launcher's name, or None."""
        menu = QMenu(self)
        for k in range(self.model.count()):
            name = self.model.itemData(k)
            if not name:
                if self.model.itemText(k) == "" and not menu.isEmpty():
                    menu.addSeparator()
                continue
            action = menu.addAction(self.model.itemText(k))
            action.setData(name)
            if name == now:
                action.setEnabled(False)
                action.setText(action.text() + "  (this answer)")
        chosen = menu.exec(QCursor.pos())
        return chosen.data() if chosen is not None else None

    def on_summary(self, action):
        """The "So far" box: edit it, or drop it (the next message makes a new one if needed)."""
        covered, text = summary.current(self.chat)
        if not covered:
            return
        if action == "edit":
            dialog = EditDialog(self, "Edit the summary", text, False)
            if dialog.exec() == QDialog.DialogCode.Accepted and dialog.text.toPlainText().strip():
                summary.set_text(self.chat, dialog.text.toPlainText())
                self.chat.save()
        elif action == "drop":
            answer = QMessageBox.question(
                self, "Drop the summary",
                "Drop the summary? The persona sees the whole chat again; if it is too long for the "
                "model, the next message makes a new summary.")
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.chat.summary = {}
            self.chat.save()
        self.paint()
        self.measure(again=True)

    # --- knowledge ----------------------------------------------------------------------

    def show_knowledge(self):
        """The button says how much knowledge there is; its tooltip how it is used."""
        found = knowledge.texts(self.persona.id) if self.persona else []
        self.knowledge_btn.setText(f"Knowledge ({len(found)})…" if found else "Knowledge…")
        size = sum(len(t) for _, t in found) * knowledge.PER_CHAR
        if not found:
            how = "None yet. Put text, Markdown, PDF, EPUB or Word files into the folder this opens."
        else:
            how = (f"{len(found)} file(s), about {int(size):,} tokens. Up to a quarter of the model's context "
                   "goes into every chat as it is; more than that, and the persona looks things up in it.")
        self.knowledge_btn.setToolTip("Material this persona works from, used whether or not it has file "
                                      f"access. {how}")

    def open_knowledge(self):
        if self.persona is None:
            return
        where = knowledge.folder(self.persona.id)
        try:
            os.makedirs(where, exist_ok=True)
        except OSError as e:
            self.window.say(f"Can't make {where}: {e}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(where))

    # --- facts --------------------------------------------------------------------------

    # --- best of -----------------------------------------------------------------------

    def keep(self, text):
        if self.persona is None or not text.strip():
            return
        try:
            bestof.add(self.persona.id, text, self.chat.title if self.chat else "")
        except OSError as e:
            QMessageBox.critical(self, "Best of", str(e))
            return
        self.window.say(f"Kept in {self.persona.name}'s best of ({bestof.path(self.persona.id)}).")

    def view_menu(self, pos):
        menu = self.view.createStandardContextMenu(pos)
        picked = self.view.textCursor().selection().toPlainText().strip()
        if picked and self.persona is not None:
            menu.addSeparator()
            menu.addAction(f"★ Add to {self.persona.name}'s best of", lambda: self.keep(picked))
        menu.exec(self.view.mapToGlobal(pos))

    def edit_best(self):
        if self.persona is None:
            return
        dialog = EditDialog(self, f"{self.persona.name}'s best of", bestof.read(self.persona.id), False)
        dialog.text.setPlaceholderText("Nothing kept yet. ★ keep under an answer keeps it whole; to keep "
                                       "just a line, select it and use the right mouse button.")
        dialog.resize(760, 560)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            bestof.write(self.persona.id, dialog.text.toPlainText())
        except OSError as e:
            QMessageBox.critical(self, "Best of", str(e))

    def edit_facts(self):
        if self.persona is None:
            return
        dialog = FactsDialog(self, self.persona)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            dialog.save()
        except OSError as e:
            QMessageBox.critical(self, "Facts", str(e))
            return
        count = sum(map(len, facts.read(self.persona.id).values()))
        self.window.say(f"Saved: {self.persona.name} remembers {count} facts.")

    def remember(self):
        if self.persona is None or self.chat is None or not self.chat.messages or self.worker or self.proposing:
            return
        if chat.loaded_launcher() is None:
            self.window.say("Load a model first; it is the one that reads the chat.")
            return
        self.remember_btn.setEnabled(False)
        self.remember_btn.setText("Reading the chat…")
        self.proposing = Proposing(self.persona, self.chat)
        self.proposing.done.connect(self.proposed)
        # Let go of the thread only once it has ended: Qt ends the program when a running thread
        # is thrown away, and done comes while run() is still on its way out.
        self.proposing.finished.connect(self.proposing_ended)
        self.proposing.start()

    def proposing_ended(self):
        self.proposing = None
        self.remember_btn.setEnabled(True)
        self.remember_btn.setText("Remember…")

    def proposed(self, found, error):
        persona = self.proposing.persona
        self.remember_btn.setText("Remember…")
        if error:
            self.window.say(f"No facts: {error}")
            return
        if not found:
            QMessageBox.information(self, "Remember", f"Nothing new worth remembering for {persona.name} in this chat.")
            return
        dialog = ProposalDialog(self, persona, found)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.chosen():
            return
        try:
            facts.add(persona.id, dialog.chosen())
        except OSError as e:
            QMessageBox.critical(self, "Facts", str(e))
            return
        self.window.say(f"{persona.name} remembers {len(dialog.chosen())} more facts.")

    # --- reading answers aloud ------------------------------------------------------------

    def listen(self, i):
        if self.playing == i:
            self.player.stop()
            return
        if self.speaking:
            self.window.say("One moment: the voice is still busy with another answer.")
            return
        text = self.chat.messages[i]["content"]
        made = voice.said(self.persona.id, self.persona.voice, text)
        if made:
            self.play(i, made)
            return
        t = voice.tool()
        if t is not None and t.state() != "running":
            self.window.say(f"Starting {t.title}; the first answer takes longer while it loads its model.")
        thread = Speaking(self.persona.id, self.persona.voice, text)
        self.speaking = (i, thread)
        thread.done.connect(lambda path, error, i=i, chat_id=self.chat.id: self.spoken(i, chat_id, path, error))
        thread.finished.connect(self.speaking_ended)
        thread.start()
        self.paint()

    def spoken(self, i, chat_id, path, error):
        if error:
            self.window.say(f"Can't read it aloud: {error}")
        elif self.chat is not None and self.chat.id == chat_id:
            self.play(i, path)

    def speaking_ended(self):
        self.speaking = None
        self.paint()

    def play(self, i, path):
        if self.player is None:
            from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
            self.player = QMediaPlayer(self)
            self.player.setAudioOutput(QAudioOutput(self.player))
            self.player.playbackStateChanged.connect(self.play_state)
        self.player.stop()
        self.playing = i
        self.player.setSource(QUrl.fromLocalFile(path))
        self.player.play()
        self.paint()

    def play_state(self, state):
        from PyQt6.QtMultimedia import QMediaPlayer
        if state == QMediaPlayer.PlaybackState.StoppedState and self.playing is not None:
            self.playing = None
            self.paint()

    def turn_start(self, i):
        """Where the answer that message i belongs to starts: after the last message of yours."""
        start = i
        while start > 0 and self.chat.messages[start - 1]["role"] != "user":
            start -= 1
        return start

    # --- showing the chat -------------------------------------------------------------

    def paint_if_dirty(self):
        if self.dirty:
            self.paint()

    def paint(self):
        self.dirty = False
        bar = self.view.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 40
        keep = bar.value()
        self.view.setHtml(self.page())
        bar.setValue(bar.maximum() if at_end or self.worker else keep)

    @staticmethod
    def buttons(links, dim, ground):
        """The links under a message as a row of small buttons (cells with a ground: the view
        can't show real buttons); what isn't a single link (counts, the ◀ 1/2 ▶) stays plain."""
        if not links:
            return ""
        cells = []
        for link in links:
            if re.fullmatch(r"<a [^>]*>.*?</a>", link) and link.count("<a ") == 1:
                if "text-decoration" not in link:
                    link = link.replace("<a ", "<a style='text-decoration:none' ", 1)
                cells.append(f"<td bgcolor='{ground}' valign='middle' style='font-size:small'>&nbsp;{link}&nbsp;</td>")
            else:
                cells.append(f"<td valign='middle' style='color:{dim}; font-size:small'>{link}</td>")
        return f"<table cellspacing='4' cellpadding='3' style='margin-top:2px'><tr>{''.join(cells)}</tr></table>"

    def page(self):
        pal = self.palette()
        dark = pal.window().color().lightness() < 128
        dim = pal.placeholderText().color().name()
        mine = pal.alternateBase().color().name()
        # Buttons under messages: a step lighter (dark theme) or darker than the page.
        button = pal.base().color().lighter(190).name() if dark else pal.base().color().darker(112).name()
        if self.persona is None:
            return f"<p style='color:{dim}'>No personas yet — make one on the Personas tab.</p>"
        messages = self.chat.messages if self.chat else []
        if not messages and not self.live:
            return f"<p style='color:{dim}'>A new chat with {html.escape(self.persona.name)}.</p>"
        last = len(messages) - 1
        parts = []
        name = f"<b>{html.escape(self.persona.name)}</b>"
        covered, so_far = summary.current(self.chat) if self.chat else (0, "")
        if covered:
            edited = " (edited by you)" if self.chat.summary.get("edited") else ""
            parts.append(f"<table width='100%' cellpadding='8' style='background:{mine}'><tr><td>"
                         f"<b>So far</b> <span style='color:{dim}; font-size:small'>— what "
                         f"{html.escape(self.persona.name)} knows of the first {covered} messages{edited}"
                         f"</span><br>{to_html(so_far, dark)}"
                         f"<span style='color:{dim}; font-size:small'><a href='cove:summary/edit'>edit</a> · "
                         f"<a href='cove:summary/drop'>drop</a></span></td></tr></table>")
        for i, m in enumerate(messages):
            if covered and i == covered:
                parts.append(f"<p align='center' style='color:{dim}; font-size:small'>— above this line "
                             f"{html.escape(self.persona.name)} sees only the summary —</p>")
            # The persona's name only at the start of an answer, not again after each tool step.
            first = i == 0 or messages[i - 1]["role"] == "user"
            if m["role"] == "tool":
                shown = i in self.open_thinking
                line = (f"<a href='cove:think/{i}' style='color:{dim}'>{'▾' if shown else '▸'} "
                        f"{html.escape(m.get('summary') or m.get('name', 'tool'))}</a>")
                if shown:
                    line += (f"<table width='100%' cellpadding='6' style='background:{mine}; font-size:small'>"
                             f"<tr><td><pre style='white-space:pre-wrap'>{html.escape(m['content'][:6000])}"
                             f"{'…' if len(m['content']) > 6000 else ''}</pre></td></tr></table>")
                parts.append(f"<table width='100%' cellpadding='2'><tr><td>&nbsp;&nbsp;{line}</td></tr></table>")
                picture = os.path.join(pictures.folder(self.persona.id), m.get("picture") or "")
                if m.get("picture") and os.path.exists(picture):
                    # Shown whole and small; a click opens it in the image viewer.
                    url = QUrl.fromLocalFile(picture).toString()
                    parts.append(f"<p>&nbsp;&nbsp;<a href='{url}'><img src='{url}' width='{PICTURE_WIDTH}'></a></p>")
                continue
            if m["role"] == "user":
                head = "<b>You</b>"
                links = [f"<a href='cove:edit/{i}'>edit</a>", f"<a href='cove:delete/{i}'>delete</a>"]
                seen = ""
                picture = os.path.join(pictures.folder(self.persona.id), m.get("picture") or "")
                if m.get("picture") and os.path.exists(picture):
                    size = QImageReader(picture).size()
                    scale = min(1.0, YOUR_PICTURE / max(size.width(), size.height(), 1))
                    url = QUrl.fromLocalFile(picture).toString()
                    seen = (f"<p><a href='{url}'><img src='{url}' width='{int(size.width() * scale)}' "
                            f"height='{int(size.height() * scale)}'></a></p>")
                parts.append(f"<table width='100%' cellpadding='8' style='background:{mine}'><tr><td>"
                             f"{head}<br>{seen}{to_html(m['content'], dark)}"
                             f"{self.buttons(links, dim, button)}</td></tr></table>")
                continue
            if m["role"] != "assistant":
                continue
            model = f" <span style='color:{dim}; font-size:small'>on {html.escape(m['model'])}</span>" \
                if m.get("model") else ""
            body = ""
            if m.get("thinking"):
                words = len(m["thinking"].split())
                shown = i in self.open_thinking
                body += (f"<p style='margin-top:4px'><a href='cove:think/{i}' style='color:{dim}'>"
                         f"{'▾' if shown else '▸'} Thinking ({words} words)</a></p>")
                if shown:
                    body += (f"<table width='100%' cellpadding='6' style='background:{mine}; font-size:small'>"
                             f"<tr><td>{to_html(m['thinking'], dark)}</td></tr></table><br>")
            body += to_html(m["content"], dark) if m["content"] else ""
            if m.get("ended") == "length":
                body += f"<p style='color:{dim}'><i>(cut off: the answer reached its length limit)</i></p>"
            elif m.get("ended") in ("stopped", "broken off"):
                body += f"<p style='color:{dim}'><i>({m['ended']})</i></p>"
            elif m.get("ended") == "only thought":
                body += (f"<p style='color:{dim}'><i>(it only thought and gave no answer: answer again, "
                         f"maybe with Thinking off)</i></p>")
            # The steps of an answer that only call tools get no links; the answer as a whole is
            # redone with "answer again" or removed with its last part.
            links = []
            if not m.get("tool_calls"):
                links = [f"<a href='cove:edit/{i}'>edit</a>"] if m["content"] else []
                if m["content"] and self.persona.voice:
                    if self.speaking and self.speaking[0] == i:
                        links.insert(0, "<i>making the voice…</i>")
                    elif self.playing == i:
                        links.insert(0, f"<a href='cove:listen/{i}'>■ stop</a>")
                    else:
                        links.insert(0, f"<a href='cove:listen/{i}'>🔊 listen</a>")
                if m["content"]:
                    vote = m.get("rating", "")
                    # Bold doesn't show on an emoji: the chosen one is bigger.
                    links += [(f"<a href='cove:rate/{i}/{k}' style='text-decoration:none'>"
                               f"<span style='font-size:x-large'>{mark}</span></a>" if vote == k
                               else f"<a href='cove:rate/{i}/{k}'>{mark}</a>")
                              for k, mark in (("up", "👍"), ("down", "👎"))]
                if i == last:
                    links.append(f"<a href='cove:again/{i}'>answer again</a>")
                    links.append(f"<a href='cove:againwith/{i}'>again with…</a>")
                    version, versions = self.chat.versions()
                    if versions > 1:
                        back = f"<a href='cove:version/{version - 1}'>◀</a>" if version > 0 else "◀"
                        on = f"<a href='cove:version/{version + 1}'>▶</a>" if version < versions - 1 else "▶"
                        links.insert(0, f"{back} {version + 1}/{versions} {on}")
                links.append(f"<a href='cove:delete/{i}'>delete</a>")
                if m["content"]:
                    links.append(f"<a href='cove:keep/{i}'>★ keep</a>")
                if m.get("stats"):
                    links.append(f"{m['stats']['tokens']:,} tokens · {m['stats']['speed']} t/s")
            head = f"<p style='margin:0'>{name}{model}</p>" if first else ""
            links = self.buttons(links, dim, button)
            parts.append(f"<table width='100%' cellpadding='8'><tr><td>{head}{body}{links}</td></tr></table>")
        if self.live is not None:
            body = ""
            if self.live.get("status"):
                body += f"<p style='color:{dim}'><i>{html.escape(self.live['status'])}</i></p>"
            if self.live.get("tool"):
                body += f"<p style='color:{dim}'><i>{html.escape(self.live['tool'])} …</i></p>"
            if self.live["thinking"]:
                words = len(self.live["thinking"].split())
                busy = "Thinking… " if not self.live["text"] else "Thinking "
                body += (f"<p style='margin-top:4px'><a href='cove:think/live' style='color:{dim}'>{'▾' if self.live_thinking else '▸'} "
                         f"{busy}({words} words)</a></p>")
                if self.live_thinking:
                    body += (f"<table width='100%' cellpadding='6' style='background:{mine}; font-size:small'>"
                             f"<tr><td style='white-space:pre-wrap'>{html.escape(self.live['thinking'].strip())}"
                             f"</td></tr></table><br>")
            body += to_html(self.live["text"], dark) if self.live["text"] else ""
            if not body:
                body = f"<p style='color:{dim}'><i>…</i></p>"
            first = not messages or messages[-1]["role"] == "user"
            head = f"<p style='margin:0'>{name}</p>" if first else ""
            parts.append(f"<table width='100%' cellpadding='8'><tr><td>{head}{body}</td></tr></table>")
        if self.problem and self.live is None:
            parts.append(f"<table width='100%' cellpadding='8' style='background:{mine}'><tr><td>"
                         f"<b>No answer.</b> {html.escape(self.problem)}</td></tr></table>")
        return "".join(parts)
