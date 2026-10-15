# The Passwords tab: the entries of the password store (see passwords.py) with search, copy
# and edit. GnuPG runs on the side, so its own window can ask for the master password while
# this one stays alive. A password goes into the form and the clipboard, never anywhere else.

import getpass
import os

from PyQt6.QtCore import QMimeData, QProcess, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QFontDatabase
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QStackedWidget,
    QVBoxLayout, QWidget,
)

from . import passwords

# How long a copied password stays in the clipboard.
CLIPBOARD_SECONDS = 45


class PasswordsTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.entry = None        # name of the entry in the form; None = a new one or nothing
        self.baseline = None     # the form as it was filled; None = no form to edit
        self.switching = False
        self.after = None        # what to do with the answer of the GnuPG run in progress
        self.copy_when_open = False
        self.copied = None
        self.names = None
        self.proc = QProcess(self)
        self.proc.finished.connect(self.gpg_done)
        self.proc.errorOccurred.connect(self.gpg_failed)
        self.clip_timer = QTimer(self)
        self.clip_timer.setSingleShot(True)
        self.clip_timer.timeout.connect(self.clear_clipboard)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.setup_page())
        self.stack.addWidget(self.main_page())
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.stack)
        self.refresh()

    # --- pages ---------------------------------------------------------------

    def setup_page(self):
        page = QWidget()
        box = QVBoxLayout(page)
        self.setup_note = QLabel()
        self.setup_note.setWordWrap(True)
        self.setup_note.setTextFormat(Qt.TextFormat.RichText)
        box.addWidget(self.setup_note)
        new = QPushButton("Start a new store here…")
        new.clicked.connect(self.start_new)
        fetch = QPushButton("Get my store from another machine…")
        fetch.clicked.connect(self.fetch_store)
        row = QHBoxLayout()
        row.addWidget(new)
        row.addWidget(fetch)
        row.addStretch(1)
        box.addLayout(row)
        box.addStretch(1)
        return page

    def main_page(self):
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.fill_list)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.on_select)
        self.list.itemDoubleClicked.connect(self.copy_on_double_click)
        self.list.setToolTip("Double-click copies the password")
        new_btn = QPushButton("New…")
        new_btn.clicked.connect(self.new_entry)
        left = QWidget()
        lbox = QVBoxLayout(left)
        lbox.setContentsMargins(0, 0, 0, 0)
        lbox.addWidget(self.search)
        lbox.addWidget(self.list, 1)
        lbox.addWidget(new_btn)

        self.form = QWidget()
        grid = QGridLayout(self.form)
        grid.setColumnStretch(1, 1)
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. myserver/mariadb root — a / makes a group")
        self.user = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.url = QLineEdit()
        self.notes = QPlainTextEdit()
        for w in (self.name, self.user, self.password, self.url):
            w.textChanged.connect(self.changed)
        self.notes.textChanged.connect(self.changed)
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: self.password.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        self.show_box = show
        generate = QPushButton("Generate")
        generate.setToolTip("A new random password of 20 characters")
        generate.clicked.connect(self.generate)
        copy_pw = QPushButton("Copy")
        copy_pw.clicked.connect(lambda: self.copy(self.password.text(), "Password"))
        copy_user = QPushButton("Copy")
        copy_user.clicked.connect(lambda: self.copy(self.user.text(), "User name", secret=False))
        open_url = QPushButton("Open")
        open_url.clicked.connect(self.open_url)
        grid.addWidget(QLabel("Name"), 0, 0)
        grid.addWidget(self.name, 0, 1, 1, 4)
        grid.addWidget(QLabel("User"), 1, 0)
        grid.addWidget(self.user, 1, 1, 1, 3)
        grid.addWidget(copy_user, 1, 4)
        grid.addWidget(QLabel("Password"), 2, 0)
        grid.addWidget(self.password, 2, 1)
        grid.addWidget(show, 2, 2)
        grid.addWidget(generate, 2, 3)
        grid.addWidget(copy_pw, 2, 4)
        grid.addWidget(QLabel("Address"), 3, 0)
        grid.addWidget(self.url, 3, 1, 1, 3)
        grid.addWidget(open_url, 3, 4)
        grid.addWidget(QLabel("Notes"), 4, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.notes, 4, 1, 1, 4)
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.save)
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.clicked.connect(self.revert)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self.delete)
        buttons = QHBoxLayout()
        buttons.addWidget(self.delete_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.revert_btn)
        buttons.addWidget(self.save_btn)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        right = QWidget()
        rbox = QVBoxLayout(right)
        rbox.addWidget(self.hint)
        rbox.addWidget(self.form, 1)
        rbox.addLayout(buttons)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 870])

        self.status = QLabel()
        self.status.setWordWrap(True)
        lock = QPushButton("Lock")
        lock.setToolTip("Forget the master password now and empty the form and the clipboard")
        lock.clicked.connect(self.lock)
        self.sync_btn = QPushButton("Sync now")
        self.sync_btn.clicked.connect(lambda: self.sync())
        more = QPushButton("More")
        menu = QMenu(more)
        menu.addAction("Set sync address…", self.set_sync_address)
        menu.addAction("Sync: where both changed, keep this machine's", lambda: self.sync("mine"))
        menu.addAction("Sync: where both changed, take the other machine's", lambda: self.sync("theirs"))
        menu.addSeparator()
        menu.addAction("Export key (for another machine)…", self.export_key)
        menu.addAction("Import key…", self.import_key)
        more.setMenu(menu)
        bar = QHBoxLayout()
        bar.addWidget(self.status, 1)
        bar.addWidget(lock)
        bar.addWidget(self.sync_btn)
        bar.addWidget(more)

        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(split, 1)
        box.addLayout(bar)
        self.empty_form("Pick an entry, or New… for a new one.")
        return page

    # --- keeping the view in step --------------------------------------------

    def refresh(self):
        """Look at the store again: which page, which entries, the line at the bottom."""
        if not passwords.gpg_program():
            self.setup_note.setText("GnuPG (the <b>gpg</b> program) is not installed on this machine. "
                                    "The passwords are encrypted with it.")
            self.stack.setCurrentIndex(0)
            return
        if not passwords.ready():
            self.setup_note.setText(
                f"<b>No password store on this machine yet</b> (looked in {passwords.store()}).<br><br>"
                "Passwords are kept one file per entry, encrypted with GnuPG. You choose one master "
                "password; GnuPG asks for it in its own window, AIcove never sees it.<br><br>"
                "A store that already exists on another machine is fetched with the second button; "
                "its key comes over once as a file (there: More → Export key, here: More → Import key).")
            self.stack.setCurrentIndex(0)
            return
        self.stack.setCurrentIndex(1)
        names = passwords.entries()
        if names != self.names:
            self.names = names
            self.fill_list()
        key = passwords.key_name()
        if key is None:
            text = ("<span style='color:#c0392b'>The key of this store is not on this machine</span> — "
                    "More → Import key…")
        else:
            text = f"Key: {key.replace('&', '&amp;').replace('<', '&lt;')}"
        url = passwords.remote()
        if url:
            waiting = passwords.unsent()
            text += f" · synced with {url}" + (f" — {waiting} change{'s' if waiting != 1 else ''} not sent yet"
                                               if waiting else "")
        else:
            text += " · not synced with another machine"
        self.status.setText(text)
        self.sync_btn.setEnabled(bool(url))

    def fill_list(self, *_):
        words = self.search.text().lower().split()
        self.switching = True
        self.list.clear()
        for name in self.names or []:
            if all(w in name.lower() for w in words):
                self.list.addItem(name)
        match = self.list.findItems(self.entry or "", Qt.MatchFlag.MatchExactly) if self.entry else []
        if match:
            self.list.setCurrentItem(match[0])
        self.switching = False

    def empty_form(self, hint=""):
        self.entry, self.baseline = None, None
        self.switching = True
        for w in (self.name, self.user, self.password, self.url):
            w.clear()
        self.notes.clear()
        self.show_box.setChecked(False)
        self.switching = False
        self.form.setEnabled(False)
        self.hint.setText(hint)
        self.changed()

    def values(self):
        return {"name": self.name.text().strip(), "user": self.user.text().strip(),
                "password": self.password.text(), "url": self.url.text().strip(),
                "notes": self.notes.toPlainText().strip("\n")}

    def dirty(self):
        return self.baseline is not None and self.values() != self.baseline

    def changed(self, *_):
        if self.switching:
            return
        editing = self.baseline is not None
        self.save_btn.setEnabled(editing and self.dirty())
        self.revert_btn.setEnabled(editing and self.dirty())
        self.delete_btn.setEnabled(editing and self.entry is not None)

    def resolve(self):
        """Ask what to do with an unsaved entry. False = stay where we are."""
        if not self.dirty():
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved changes")
        box.setText(f"{self.values()['name'] or 'The new entry'} has unsaved changes.")
        box.setStandardButtons(QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                               | QMessageBox.StandardButton.Cancel)
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Save:
            # Saving finishes on the side; stay here until it has.
            self.save()
            return False
        return answer == QMessageBox.StandardButton.Discard

    # --- GnuPG on the side ---------------------------------------------------

    def busy(self):
        return self.proc.state() != QProcess.ProcessState.NotRunning

    def gpg(self, args, after, data=None):
        """Run gpg; after(exit code, its output as bytes, what it complained about) when it is through."""
        if self.busy():
            return False
        self.after = after
        self.stack.setEnabled(False)
        self.proc.start(passwords.gpg_program(), args)
        if data is not None:
            self.proc.write(data)
        self.proc.closeWriteChannel()
        return True

    def gpg_done(self, *_):
        after, self.after = self.after, None
        out = bytes(self.proc.readAllStandardOutput())
        err = bytes(self.proc.readAllStandardError()).decode(errors="replace").strip()
        ok = self.proc.exitStatus() == QProcess.ExitStatus.NormalExit
        self.stack.setEnabled(True)
        if after is not None:
            after(self.proc.exitCode() if ok else 1, out, err)

    def gpg_failed(self, *_):
        if self.after is not None and not self.busy():
            after, self.after = self.after, None
            self.stack.setEnabled(True)
            after(1, b"", "gpg could not be started")

    def complaint(self, err):
        """The line of GnuPG's complaints worth showing."""
        lines = [l for l in err.split("\n") if l.strip()]
        return lines[-1] if lines else "no reason given"

    # --- entries -------------------------------------------------------------

    def on_select(self, current, _previous):
        if self.switching or current is None:
            return
        name = current.text()
        if name == self.entry:
            return
        if not self.resolve():
            self.fill_list()
            return
        self.open(name)

    def open(self, name):
        self.empty_form(f"Opening {name}…")

        def opened(code, out, err):
            if code != 0:
                self.copy_when_open = False
                self.empty_form(f"Can't open {name}: {self.complaint(err)}")
                self.switching = True
                self.list.setCurrentItem(None)
                self.switching = False
                return
            entry = passwords.parse_entry(out.decode(errors="replace").rstrip("\n"))
            self.switching = True
            self.name.setText(name)
            self.user.setText(entry["user"])
            self.password.setText(entry["password"])
            self.url.setText(entry["url"])
            self.notes.setPlainText(entry["notes"])
            self.switching = False
            self.entry, self.baseline = name, self.values()
            self.form.setEnabled(True)
            self.hint.setText("")
            self.changed()
            if self.copy_when_open:
                self.copy_when_open = False
                self.copy(entry["password"], "Password")

        self.gpg(passwords.decrypt_args(name), opened)

    def copy_on_double_click(self, item):
        if self.entry == item.text() and not self.busy():
            self.copy(self.baseline["password"], "Password")
        else:
            self.copy_when_open = True

    def new_entry(self):
        if self.busy() or not self.resolve():
            return
        self.empty_form()
        self.switching = True
        self.list.setCurrentItem(None)
        self.switching = False
        self.baseline = self.values()
        self.form.setEnabled(True)
        self.hint.setText("New entry: give it a name and a password, then Save.")
        self.name.setFocus()
        self.changed()

    def generate(self):
        self.password.setText(passwords.generate())
        self.show_box.setChecked(True)

    def revert(self):
        if self.entry is None:
            self.empty_form("Pick an entry, or New… for a new one.")
        else:
            name, self.entry = self.entry, None
            self.open(name)

    def warn(self, text):
        QMessageBox.warning(self, "Passwords", text)

    def save(self):
        if self.baseline is None or self.busy():
            return
        v = self.values()
        name, old = v["name"], self.entry
        if not passwords.NAME.fullmatch(name):
            return self.warn("The name can have letters, digits, spaces and . _ @ + -, and / for groups.")
        if not v["password"]:
            return self.warn("The password is empty.")
        if "\n" in v["password"]:
            return self.warn("The password can't have a line break in it.")
        dest = passwords.path_of(name)
        if name != old and os.path.exists(dest):
            return self.warn(f"There is already an entry called {name}.")
        try:
            os.makedirs(os.path.dirname(dest), mode=0o700, exist_ok=True)
        except OSError as e:
            return self.warn(str(e))
        tmp = dest + ".new"

        def saved(code, _out, err):
            if code != 0:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return self.warn(f"Not saved: {self.complaint(err)}")
            os.replace(tmp, dest)
            if old and old != name:
                try:
                    os.remove(passwords.path_of(old))
                except OSError:
                    pass
            passwords.commit(f"Rename {old} to {name}" if old and old != name
                             else f"Change {name}" if old else f"Add {name}")
            self.window.say(f"Password entry {name} saved.")
            self.entry, self.baseline = name, v
            self.names = None
            self.hint.setText("")
            self.refresh()
            self.changed()

        self.gpg(passwords.encrypt_args(tmp), saved,
                 passwords.format_entry(v["password"], v["user"], v["url"], v["notes"]).encode())

    def delete(self):
        if self.entry is None or self.busy():
            return
        name = self.entry
        if QMessageBox.question(self, "Delete entry", f"Delete the entry {name}?\n"
                                "Its old versions stay in the store's history.") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            os.remove(passwords.path_of(name))
        except OSError as e:
            return self.warn(str(e))
        passwords.commit(f"Remove {name}")
        self.window.say(f"Password entry {name} deleted.")
        self.empty_form("Pick an entry, or New… for a new one.")
        self.refresh()

    def open_url(self):
        url = self.url.text().strip()
        if url:
            QDesktopServices.openUrl(QUrl(url if "://" in url else "https://" + url))

    # --- clipboard -----------------------------------------------------------

    def copy(self, text, what, secret=True):
        if not text:
            return
        data = QMimeData()
        data.setText(text)
        if secret:
            # Clipboard managers that know this hint keep it out of their history.
            data.setData("x-kde-passwordManagerHint", b"secret")
            self.copied = text
            self.clip_timer.start(CLIPBOARD_SECONDS * 1000)
        QApplication.clipboard().setMimeData(data)
        name = self.entry or self.values()["name"]
        self.window.say(f"{what} of {name} copied"
                        + (f" — the clipboard is emptied in {CLIPBOARD_SECONDS} seconds." if secret else "."))

    def clear_clipboard(self):
        """Empty the clipboard if our password is still what is in it."""
        self.clip_timer.stop()
        if self.copied is not None and QApplication.clipboard().text() == self.copied:
            QApplication.clipboard().clear()
        self.copied = None

    def lock(self):
        if self.busy():
            return
        passwords.lock()
        self.clear_clipboard()
        self.switching = True
        self.list.setCurrentItem(None)
        self.switching = False
        self.empty_form("Locked: the next entry you open asks for the master password again.")

    def leaving(self):
        """The window closes: False = an unsaved entry keeps it open."""
        if not self.resolve():
            return False
        self.clear_clipboard()
        return True

    # --- setting up, keys, sync ----------------------------------------------

    def start_new(self):
        if self.busy():
            return
        keys = passwords.secret_keys()
        fresh = "A new key (recommended)"
        if keys:
            choice, ok = QInputDialog.getItem(self, "New password store", "Encrypt the passwords with:",
                                              [fresh] + [f"{name}  [{fpr[-16:]}]" for fpr, name in keys], 0, False)
            if not ok:
                return
            if choice != fresh:
                fpr = next(fpr for fpr, name in keys if choice.endswith(f"[{fpr[-16:]}]"))
                return self.store_made(fpr)
        label, ok = QInputDialog.getText(self, "New password store", "A name for the new key:",
                                         text=f"{getpass.getuser()} passwords")
        if not ok or not label.strip():
            return
        QMessageBox.information(
            self, "Master password",
            "GnuPG now asks you to choose the master password, in a window of its own (twice).\n\n"
            "AIcove doesn't see it and nobody can reset it: without it the passwords can't be "
            "read by anyone, you included.")

        def made(code, out, err):
            fpr = passwords.created_key(out.decode(errors="replace"))
            if code != 0 or not fpr:
                return self.warn(f"No key was made: {self.complaint(err)}")
            self.store_made(fpr)

        self.gpg(passwords.new_key_args(label.strip()), made)

    def store_made(self, fpr):
        try:
            passwords.init_store(fpr)
        except OSError as e:
            return self.warn(str(e))
        self.window.say(f"Password store set up in {passwords.store()}")
        self.names = None
        self.refresh()

    def fetch_store(self):
        url, ok = QInputDialog.getText(self, "Get my store", "Where it is synced with (a git address):",
                                       text="myserver:passwords.git")
        if not ok or not url.strip():
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            ok, said = passwords.clone(url.strip())
        finally:
            QApplication.restoreOverrideCursor()
        if not ok:
            return self.warn(f"Couldn't get the store from {url.strip()}:\n{said}")
        self.window.say(f"Password store fetched from {url.strip()} into {passwords.store()}")
        self.names = None
        self.refresh()
        if passwords.key_name() is None:
            QMessageBox.information(
                self, "The key", "The store is here, but its key is not on this machine yet.\n\n"
                "On the machine that has it: More → Export key. Bring that file over, then here: "
                "More → Import key.")

    def set_sync_address(self):
        url, ok = QInputDialog.getText(self, "Sync address",
                                       "The git address the store is synced with (empty = none):",
                                       text=passwords.remote() or "myserver:passwords.git")
        if not ok:
            return
        code, said = passwords.set_remote(url.strip())
        if code != 0:
            return self.warn(said)
        self.window.say(f"Passwords are synced with {url.strip()} — press Sync now." if url.strip()
                        else "Passwords are no longer synced.")
        self.refresh()

    def sync(self, prefer=None):
        if self.resolve():
            self.window.spawn_cove("passwords", "sync", *([prefer] if prefer else []))

    def export_key(self):
        if self.busy():
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export key", os.path.expanduser("~/aicove-passwords-key.asc"))
        if not path:
            return

        def exported(code, out, err):
            if code != 0 or not out:
                return self.warn(f"The key wasn't exported: {self.complaint(err)}")
            try:
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
                    f.write(out)
            except OSError as e:
                return self.warn(str(e))
            self.window.say(f"Key written to {path}")
            QMessageBox.information(
                self, "Export key", f"The key is in {path}.\n\nBring it to the other machine, use "
                "More → Import key there, then delete the file on both. It is locked with your "
                "master password, but don't leave it lying around.")

        self.gpg(passwords.export_key_args(), exported)

    def import_key(self):
        if self.busy():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import key", os.path.expanduser("~"))
        if not path:
            return

        def imported(code, _out, err):
            if code != 0:
                return self.warn(f"The key wasn't imported: {self.complaint(err)}")
            self.window.say(f"Key imported from {path} — delete that file now.")
            self.refresh()

        self.gpg(passwords.import_key_args(path), imported)
