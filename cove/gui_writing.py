# The Writing tab: projects of chapters (writing.py), a chapter list with word counts and
# draft/done, and an editor for one chapter. Text is saved by itself a moment after typing
# stops; with git on, a commit is made when you leave a chapter, the tab or the window, or
# press Ctrl+S. Import… takes pasted text, an HTML file or a PDF and splits it into chapters.

import os

from PyQt6.QtCore import QMarginsF, QTimer, Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QFont, QKeySequence, QPageLayout, QPageSize, QPdfWriter, QShortcut, QTextDocument
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QRadioButton, QSplitter,
    QVBoxLayout, QWidget,
)

from . import writing

ROLE = Qt.ItemDataRole.UserRole
SAVE_AFTER = 1500      # ms without typing before the chapter is written to its file


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

        self.projects = QComboBox()
        self.projects.setMinimumWidth(260)
        self.projects.currentIndexChanged.connect(self.pick_project)
        new_btn = QPushButton("New project…")
        new_btn.clicked.connect(self.new_project)
        import_btn = QPushButton("Import…")
        import_btn.setToolTip("Pasted text, an HTML file or a PDF, split into chapters")
        import_btn.clicked.connect(self.import_text)
        rename_btn = QPushButton("Rename…")
        rename_btn.clicked.connect(self.rename_project)
        export_btn = QPushButton("Export…")
        export_btn.setToolTip("The whole project as one file: Markdown, text, HTML, PDF, EPUB or Word")
        export_btn.clicked.connect(self.export)
        delete_btn = QPushButton("Delete project…")
        delete_btn.clicked.connect(self.delete_project)
        folder_btn = QPushButton("Open folder")
        folder_btn.clicked.connect(self.open_folder)
        self.git_box = QCheckBox("Keep versions (git)")
        self.git_box.setToolTip("A commit in the project's own folder each time you leave a chapter you "
                                "changed, so every earlier version can be got back")
        self.git_box.toggled.connect(self.set_git)
        top = QHBoxLayout()
        top.addWidget(QLabel("Project:"))
        top.addWidget(self.projects)
        for b in (new_btn, import_btn, export_btn, rename_btn, delete_btn, folder_btn):
            top.addWidget(b)
        top.addWidget(self.git_box)
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
        left = QWidget()
        lbox = QVBoxLayout(left)
        lbox.setContentsMargins(0, 0, 0, 0)
        lbox.addWidget(self.list, 1)
        lbox.addLayout(row1)
        lbox.addLayout(row2)

        self.editor = TextBox()
        font = QFont(self.editor.font())
        font.setPointSizeF(font.pointSizeF() * 1.2)
        self.editor.setFont(font)
        self.editor.setPlaceholderText("# Chapter title\n\nThe text. Paste from a web page and the italics come along.")
        self.editor.textChanged.connect(self.typed)
        self.status = QLabel()
        right = QWidget()
        rbox = QVBoxLayout(right)
        rbox.setContentsMargins(0, 0, 0, 0)
        rbox.addWidget(self.editor, 1)
        rbox.addWidget(self.status)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([280, 900])
        box = QVBoxLayout(self)
        box.addLayout(top)
        box.addWidget(split, 1)

        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(SAVE_AFTER)
        self.save_timer.timeout.connect(self.save)
        QShortcut(QKeySequence.StandardKey.Save, self).activated.connect(lambda: self.leave("Saved"))
        self.fill_projects()

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
        except OSError:
            self.project = None
        self.git_box.blockSignals(True)
        self.git_box.setChecked(bool(self.project and self.project.git))
        self.git_box.setEnabled(self.project is not None)
        self.git_box.blockSignals(False)
        if not same:
            self.file = None
        self.fill_list()

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
        for n, (file, status) in enumerate(self.project.chapters if self.project else [], 1):
            text = self.editor.toPlainText() if file == self.file else self.project.chapter(file)
            count = writing.words(writing.body_of(text))
            self.total += count
            item = QListWidgetItem()
            item.setData(ROLE, file)
            self.label(item, n, text, status, count)
            self.list.addItem(item)
            if file == keep:
                pick = item
        self.list.blockSignals(False)
        self.list.setCurrentItem(pick or self.list.item(0))
        if self.list.count() == 0:
            self.show_chapter(None)

    def label(self, item, n, text, status, count):
        mark = "✓ " if status == "done" else ""
        item.setText(f"{n}. {mark}{writing.title_of(text, 'untitled')}\n    {count:,} words · {status}")

    def pick_chapter(self, item, *_):
        self.leave()
        self.show_chapter(item.data(ROLE) if item else None)

    def show_chapter(self, file):
        if file is not None and file == self.file and self.editor.toPlainText() == self.saved_text:
            # The same chapter again (the list was refilled): keep the place in it.
            self.done_btn.setText("Back to draft" if self.status_of(file) == "done" else "Done")
            self.show_status()
            return
        self.file = file
        self.saved_text = self.project.chapter(file) if file else ""
        self.loading = True
        self.editor.setPlainText(self.saved_text)
        self.loading = False
        self.editor.setReadOnly(file is None)
        self.done_btn.setText("Back to draft" if self.status_of(file) == "done" else "Done")
        self.show_status()

    def status_of(self, file):
        return next((s for f, s in self.project.chapters if f == file), "draft") if self.project else "draft"

    def show_status(self, note=""):
        if self.file is None:
            self.status.setText("No project yet: New project… or Import…" if self.project is None else "")
            return
        count = writing.words(writing.body_of(self.editor.toPlainText()))
        unsaved = self.editor.toPlainText() != self.saved_text
        self.status.setText(f"This chapter {count:,} words · all chapters {self.total:,} words · "
                            + (note or ("not saved yet" if unsaved else "saved")))

    def typed(self):
        if self.loading or self.file is None:
            return
        self.save_timer.start()
        item = self.list.currentItem()
        if item is not None:
            text = self.editor.toPlainText()
            self.label(item, self.list.row(item) + 1, text, self.status_of(self.file),
                       writing.words(writing.body_of(text)))
        self.show_status()

    def save(self):
        """The editor's text into its file (no commit)."""
        self.save_timer.stop()
        if self.project is None or self.file is None:
            return
        text = self.editor.toPlainText()
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

    def delete_chapter(self):
        if self.file is None:
            return
        title = writing.title_of(self.editor.toPlainText(), self.file)
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
