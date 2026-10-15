# The Documentation tab: the guide that comes with AIcove (cove/data/guide.md), one topic per
# "## " heading, and the notes file of this machine from the settings as the last topic.

import os

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices, QFontDatabase
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QSplitter, QTextBrowser, QVBoxLayout, QWidget,
)

from .settings import S

GUIDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "guide.md")
NOTES = "My notes"


def topics():
    """[(title, markdown text)] of the guide."""
    try:
        with open(GUIDE, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        return [("Guide", f"The guide can't be read: {e}")]
    found = []
    for part in text.split("\n## ")[1:]:
        title, _, body = part.partition("\n")
        found.append((title.strip(), f"## {title.strip()}\n{body}"))
    return found


class DocsTab(QWidget):
    def __init__(self):
        super().__init__()
        self.topics = topics()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.fill_list)
        self.list = QListWidget()
        self.list.currentTextChanged.connect(self.show_topic)
        left = QWidget()
        lbox = QVBoxLayout(left)
        lbox.setContentsMargins(0, 0, 0, 0)
        lbox.addWidget(self.search)
        lbox.addWidget(self.list, 1)

        self.page = QTextBrowser()
        self.page.setOpenExternalLinks(True)
        self.notes_bar = QWidget()
        bar = QHBoxLayout(self.notes_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        self.notes_label = QLabel()
        open_btn = QPushButton("Open the file")
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.notes_path())))
        bar.addWidget(self.notes_label, 1)
        bar.addWidget(open_btn)
        right = QWidget()
        rbox = QVBoxLayout(right)
        rbox.setContentsMargins(0, 0, 0, 0)
        rbox.addWidget(self.notes_bar)
        rbox.addWidget(self.page, 1)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([230, 900])
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(split)
        self.fill_list()

    def notes_path(self):
        return os.path.expanduser(S.notes_file) if S.notes_file else ""

    def notes_text(self):
        try:
            with open(self.notes_path(), encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def fill_list(self, *_):
        """The topics that have every search word in them; all of them without a search."""
        words = self.search.text().lower().split()
        current = self.list.currentItem().text() if self.list.currentItem() else None
        entries = list(self.topics)
        if self.notes_path():
            entries.append((NOTES, self.notes_text()))
        self.list.blockSignals(True)
        self.list.clear()
        for title, text in entries:
            if all(w in text.lower() or w in title.lower() for w in words):
                self.list.addItem(title)
        self.list.blockSignals(False)
        match = [i for i in range(self.list.count()) if self.list.item(i).text() == current]
        if self.list.count():
            self.list.setCurrentRow(match[0] if match else 0)
        self.show_topic(self.list.currentItem().text() if self.list.currentItem() else "")

    def show_topic(self, title):
        self.notes_bar.setVisible(title == NOTES)
        if title == NOTES:
            # The notes are plain text laid out in columns: shown as they are, in a fixed font.
            self.notes_label.setText(self.notes_path())
            self.page.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
            self.page.setPlainText(self.notes_text() or "The notes file can't be read.")
        else:
            self.page.setFont(self.font())
            text = next((t for name, t in self.topics if name == title), "")
            self.page.setMarkdown(text or "Nothing found for that search.")
        # Show where the first search word is.
        words = self.search.text().split()
        if words and title:
            self.page.find(words[0])

    def refresh(self):
        """The settings changed (the notes file may be another one now)."""
        self.fill_list()
