# The Agents tab: Reports (the stories the agents brought, to read, keep, dismiss or work on
# in a chat) and Agents (what each one does, when, on which models). Agents of this machine are
# files here (agents.py); those of another machine are asked for over ssh ("aicove agent export"
# there) when the window opens and on Update, and changed there with "aicove agent ..." too.

import json
import os
import re
import subprocess
import threading
import time

from PyQt6.QtCore import QObject, QTimer, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTabWidget,
    QTextBrowser, QVBoxLayout, QWidget,
)

from . import agents, chat, launchers, personas, server
from .gui_chat import to_html
from .settings import STATE_DIR

ROLE = Qt.ItemDataRole.UserRole
HERE = ""          # the place of this machine's agents; another machine's is its host name
SHOW = [("Not dismissed", lambda s: s != "dismissed"), ("New", lambda s: s == "new"),
        ("Kept", lambda s: s == "kept"), ("Used", lambda s: s == "used"),
        ("Dismissed", lambda s: s == "dismissed"), ("All", lambda s: True)]
NEW_TASK = ("Bring me the latest news from yesterday and today for my themes: many leads, spread over all "
            "themes, so I can pick what to write about. Original reporting over aggregators.")


def cache_path(host):
    return os.path.join(STATE_DIR, "remote", host + "-agents.json")


def read_cache(host):
    try:
        with open(cache_path(host), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def write_cache(host, data):
    os.makedirs(os.path.dirname(cache_path(host)), exist_ok=True)
    with open(cache_path(host), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def on_host(host, args, stdin=None, timeout=60):
    """Run aicove agent ... on another machine. Returns its output, or raises OSError."""
    try:
        out = subprocess.run(server.ssh(host, ["agent"] + args), input=stdin, capture_output=True,
                             text=True, errors="replace", timeout=timeout,
                             stdin=None if stdin is not None else subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise OSError(f"no answer from {host} ({e})")
    if out.returncode != 0:
        if "open the window" in out.stdout:
            raise OSError(f"AIcove on {host} has no agents yet (git pull there)")
        last = (out.stdout + out.stderr).strip().split("\n")[-1]
        raise OSError(f"{host}: {last or f'ssh ended with {out.returncode}'}"[:300])
    return out.stdout


class Entry:
    """One agent as the tab knows it, of here or of another machine."""

    def __init__(self, place, agent, state, stories, log, cron):
        self.place = place
        self.agent = agent
        self.state = state
        self.stories = stories
        self.log = log
        self.cron = cron

    @property
    def key(self):
        return (self.place, self.agent.id)

    def label(self):
        return self.agent.name + (f"  🖥 {self.place}" if self.place else "")


def local_entries():
    cron = agents.cron_installed()
    return [Entry(HERE, a, agents.state(a.id), agents.stories(a.id), agents.last_log(a.id), cron)
            for a in agents.load_all().values()]


def remote_entries(host):
    data = read_cache(host)
    found = []
    for e in data.get("agents", []):
        a = agents.Agent.parse(e["id"], e["text"])
        found.append(Entry(host, a, e.get("state", {}),
                           [agents.Story.parse(a.id, s["file"], s["text"]) for s in e.get("stories", [])],
                           e.get("log", ""), data.get("cron", False)))
    return found


class Fetch(QObject):
    """Asks the other machines for their agents and stories, in a plain thread like Follow."""
    done = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.asking = False

    def start(self):
        if not self.asking:
            self.asking = True
            threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        off = []
        for host in launchers.hosts():
            try:
                out = on_host(host, ["export"])
                write_cache(host, json.loads(out.strip().split("\n")[-1]))
            except (OSError, ValueError) as e:
                off.append(f"Agents of {host}: {e}")
        self.asking = False
        self.done.emit(off)


class WorkDialog(QDialog):
    def __init__(self, parent, story, default):
        super().__init__(parent)
        self.setWindowTitle("Work on it")
        box = QVBoxLayout(self)
        box.addWidget(QLabel(f"Start a new chat about\n\"{story.title}\"\nwith:"))
        self.who = QComboBox()
        for p in sorted(personas.load_all().values(), key=lambda p: p.name.lower()):
            self.who.addItem(p.name, p.id)
        self.who.setCurrentIndex(max(self.who.findData(default), 0))
        box.addWidget(self.who)
        self.web = QCheckBox("Web on in that chat (to read the sources)")
        self.web.setChecked(True)
        box.addWidget(self.web)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)


class AgentsTab(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.entries = []
        self.current = None       # the Entry shown on the Agents page
        self.baseline = None      # its file as saved, to see unsaved changes
        self.switching = False
        self.fetch = Fetch(self)
        self.fetch.done.connect(self.fetched)

        self.pages = QTabWidget()
        self.pages.addTab(self.reports_page(), "Reports")
        self.pages.addTab(self.agents_page(), "Agents")
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(self.pages)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(4000)
        self.reload()
        if launchers.hosts():
            self.fetch.start()

    # --- data --------------------------------------------------------------------

    def reload(self):
        """Read everything again (here from the files, other machines from what they last said)."""
        self.entries = local_entries()
        for host in launchers.hosts():
            self.entries += remote_entries(host)
        self.fill_reports()
        self.fill_agents()

    def entry(self, key):
        return next((e for e in self.entries if e.key == key), None)

    def update(self):
        if launchers.hosts():
            self.update_btn.setEnabled(False)
            self.window.say(f"Asking {', '.join(launchers.hosts())} for their agents...")
            self.fetch.start()
        else:
            self.reload()

    def fetched(self, off):
        for line in off:
            self.window.say(line)
        if not self.update_btn.isEnabled():
            self.window.say("Agents up to date." if not off else "The other agents are up to date.")
        self.update_btn.setEnabled(True)
        if not self.dirty():
            self.reload()

    def tick(self):
        """While an agent of here runs, its log and new stories show as they come."""
        if not self.isVisible():
            return
        busy = [e for e in self.entries if e.state.get("pid") or e.state.get("result") == "running"]
        if not busy:
            return
        if any(e.place for e in busy) and not self.fetch.asking:
            self.fetch.start()
        if any(not e.place for e in busy) and not self.dirty():
            self.reload()

    # --- Reports -----------------------------------------------------------------

    def reports_page(self):
        self.show_box = QComboBox()
        for name, _ in SHOW:
            self.show_box.addItem(name)
        self.theme_box = QComboBox()
        self.agent_box = QComboBox()
        for b in (self.show_box, self.theme_box, self.agent_box):
            b.currentIndexChanged.connect(self.fill_reports)
        filters = QHBoxLayout()
        for label, b in (("Show:", self.show_box), ("Theme:", self.theme_box), ("Agent:", self.agent_box)):
            filters.addWidget(QLabel(label))
            filters.addWidget(b)
        filters.addStretch(1)
        self.story_list = QListWidget()
        self.story_list.setWordWrap(True)
        self.story_list.currentItemChanged.connect(self.show_story)
        self.story_view = QTextBrowser()
        self.story_view.setOpenLinks(False)
        self.story_view.anchorClicked.connect(lambda url: QDesktopServices.openUrl(url))
        work_btn = QPushButton("Work on it…")
        work_btn.setToolTip("A new chat with a persona of your choice, the story and its sources in it to start from")
        work_btn.clicked.connect(self.work_on_it)
        self.mark_btns = {}
        buttons = QHBoxLayout()
        buttons.addWidget(work_btn)
        buttons.addStretch(1)
        for status, label, tip in (("kept", "Keep", "Worth keeping; more like this in the next hunts"),
                                   ("used", "Used", "You made something of it"),
                                   ("dismissed", "Dismiss", "Gone from the list (the file stays); less like "
                                                            "this in the next hunts"),
                                   ("new", "Mark new", "Back to new")):
            b = QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(lambda _, s=status: self.mark(s))
            buttons.addWidget(b)
            self.mark_btns[status] = b
        right = QWidget()
        rbox = QVBoxLayout(right)
        rbox.setContentsMargins(0, 0, 0, 0)
        rbox.addWidget(self.story_view, 1)
        rbox.addLayout(buttons)
        split = QSplitter()
        split.addWidget(self.story_list)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([380, 700])
        page = QWidget()
        box = QVBoxLayout(page)
        box.addLayout(filters)
        box.addWidget(split, 1)
        return page

    def all_stories(self):
        return sorted(((e, s) for e in self.entries for s in e.stories),
                      key=lambda es: (es[1].found, es[1].file), reverse=True)

    def refill(self, combo, values):
        keep = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("All", None)
        for label, value in values:
            combo.addItem(label, value)
        combo.setCurrentIndex(max(combo.findData(keep), 0))
        combo.blockSignals(False)

    def fill_reports(self):
        found = self.all_stories()
        self.refill(self.theme_box, [(t, t) for t in sorted({s.theme for _, s in found if s.theme})])
        self.refill(self.agent_box, [(e.label(), e.key) for e in self.entries])
        keep = self.story_list.currentItem().data(ROLE) if self.story_list.currentItem() else None
        wanted = SHOW[self.show_box.currentIndex()][1]
        theme, agent = self.theme_box.currentData(), self.agent_box.currentData()
        self.story_list.blockSignals(True)
        self.story_list.clear()
        pick = None
        for e, s in found:
            if not wanted(s.status) or (theme and s.theme != theme) or (agent and tuple(agent) != e.key):
                continue
            where = f" · 🖥 {e.place}" if e.place else ""
            status = "" if s.status == "new" else f" · {s.status}"
            teaser = s.teaser if len(s.teaser) <= 160 else s.teaser[:160].rsplit(" ", 1)[0] + "…"
            item = QListWidgetItem(f"{s.title}\n{teaser}\n{s.theme} · {s.found} · {e.agent.name}{where}{status}"
                                   if teaser else f"{s.title}\n{s.theme} · {s.found} · {e.agent.name}{where}{status}")
            item.setData(ROLE, (e.place, e.agent.id, s.file))
            item.setToolTip(s.teaser)
            if s.status == "new":
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
            self.story_list.addItem(item)
            if (e.place, e.agent.id, s.file) == keep:
                pick = item
        self.story_list.blockSignals(False)
        self.story_list.setCurrentItem(pick or self.story_list.item(0))
        if self.story_list.count() == 0:
            self.story_view.setHtml("<p>No reports here yet. Agents bring them: see the Agents page.</p>")

    def story(self):
        item = self.story_list.currentItem()
        if item is None:
            return None, None
        place, aid, file = item.data(ROLE)
        e = self.entry((place, aid))
        s = next((s for s in e.stories if s.file == file), None) if e else None
        return e, s

    def show_story(self, *_):
        e, s = self.story()
        if s is None:
            return
        dark = self.palette().window().color().lightness() < 128
        head = f"*{s.theme} · {s.found} · {e.agent.name}" + (f" on {e.place}" if e.place else "") + f" · {s.status}*\n\n"
        # A lead's body starts with its teaser already.
        teaser = "" if s.meta.get("kind") == "lead" else f"**{s.teaser}**\n\n"
        self.story_view.setHtml(to_html(f"# {s.title}\n\n" + head + teaser + s.body, dark))

    def mark(self, status):
        e, s = self.story()
        if s is None:
            return
        try:
            if e.place:
                on_host(e.place, ["mark", e.agent.id, s.file, status])
                data = read_cache(e.place)
                for a in data.get("agents", []):
                    for st in a.get("stories", []) if a["id"] == e.agent.id else []:
                        if st["file"] == s.file:
                            story = agents.Story.parse(a["id"], st["file"], st["text"])
                            story.meta["status"] = status
                            st["text"] = story.text()
                write_cache(e.place, data)
            else:
                agents.mark(e.agent.id, s.file, status)
        except (OSError, ValueError) as err:
            self.window.say(f"Can't mark it: {err}")
            return
        row = self.story_list.currentRow()
        self.reload()
        # Dismissed (and so gone from the list): the next one is shown.
        if self.story_list.currentItem() and self.story_list.currentItem().data(ROLE)[2] != s.file:
            self.story_list.setCurrentRow(min(row, self.story_list.count() - 1))

    def work_on_it(self):
        e, s = self.story()
        if s is None:
            return
        have = personas.load_all()
        default = e.agent.work_with(s.theme) or ("writing-buddy" if "writing-buddy" in have else e.agent.persona)
        dialog = WorkDialog(self, s, default)
        if not dialog.exec():
            return
        pid = dialog.who.currentData()
        tab = self.window.chat_tab
        if tab.worker:
            QMessageBox.information(self, "Work on it", "The chat is answering right now; try again when it is done.")
            return
        self.window.tabs.setCurrentWidget(tab)
        tab.reload()
        for i in range(tab.people.count()):
            if tab.people.item(i).data(ROLE) == pid:
                tab.people.setCurrentRow(i)
        tab.new_chat()
        if dialog.web.isChecked() and "web" in tab.controls and tab.controls["web"].isEnabled():
            tab.controls["web"].setCurrentIndex(max(tab.controls["web"].findData("on"), 0))
        tab.input.setPlainText(f"Let's work on this story ({e.agent.name} found it):\n\n{s.markdown()}")
        tab.input.setFocus()

    # --- Agents ----------------------------------------------------------------

    def agents_page(self):
        self.agent_list = QListWidget()
        self.agent_list.currentItemChanged.connect(self.on_agent)
        new_btn = QPushButton("New agent…")
        new_btn.clicked.connect(self.new_agent)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.clicked.connect(self.delete_agent)
        self.update_btn = QPushButton("Update from other machines")
        self.update_btn.setToolTip("Ask the other machines for their agents and new reports (also asked when "
                                   "the window opens)")
        self.update_btn.clicked.connect(self.update)
        left = QWidget()
        lbox = QVBoxLayout(left)
        lbox.setContentsMargins(0, 0, 0, 0)
        lbox.addWidget(QLabel("Agents"))
        lbox.addWidget(self.agent_list, 1)
        row = QHBoxLayout()
        row.addWidget(new_btn)
        row.addWidget(self.delete_btn)
        lbox.addLayout(row)
        lbox.addWidget(self.update_btn)

        self.name = QLineEdit()
        self.persona = QComboBox()
        self.models = QListWidget()
        self.models.setMaximumHeight(110)
        self.models.setToolTip("Ticked ones, in this order: the loaded model is used if it is one of them, "
                               "else the first one this machine has is started")
        self.schedule = QLineEdit()
        self.schedule.setPlaceholderText("manual · every 6h · daily 07:00, 19:00 · weekdays 07:00 · mon wed fri 08:30")
        self.schedule_says = QLabel()
        self.schedule_says.setEnabled(False)
        self.style = QComboBox()
        self.style.addItem("as the persona has it", "")
        for name in chat.STYLES:
            self.style.addItem(name, name)
        self.thinking = QComboBox()
        for label, value in (("as the persona has it", ""), ("on", "on"), ("off", "off")):
            self.thinking.addItem(label, value)
        self.limit_boxes = {}
        limits = QHBoxLayout()
        for key in agents.LIMITS:
            b = QSpinBox()
            b.setRange(1, 999)
            b.setToolTip(agents.LIMIT_WORDS[key])
            b.valueChanged.connect(self.changed)
            limits.addWidget(QLabel(key))
            limits.addWidget(b)
            self.limit_boxes[key] = b
        limits.addStretch(1)
        self.themes = QPlainTextEdit()
        self.themes.setPlaceholderText("One per line:  Name | what it means to you | persona to work on it with (optional)\n"
                                       "Tech | new tools, Linux, privacy, how tech companies behave\n"
                                       "Bullshit | absurd or hypocritical news, material for satire | balthazar-the-unsupervised")
        self.themes.setMaximumHeight(110)
        self.task = QPlainTextEdit()
        self.task.setPlaceholderText("What it should do, in words.")
        self.search_from = QComboBox()
        self.search_from.setToolTip("Where its searches and page reads go out from: the machine it runs on, or "
                                    "another machine of yours (ssh, with SearXNG there) - then sites see that "
                                    "machine's address, not yours")
        self.search_from.addItem("the machine it runs on", "")
        for host in agents.ssh_hosts():
            self.search_from.addItem(host, host)
        self.search_from.currentIndexChanged.connect(self.changed)
        for w in (self.name, self.schedule):
            w.textChanged.connect(self.changed)
        for w in (self.themes, self.task):
            w.textChanged.connect(self.changed)
        for w in (self.persona, self.style, self.thinking):
            w.currentIndexChanged.connect(self.changed)
        self.models.itemChanged.connect(self.changed)
        self.cron = QCheckBox("Run on schedule on this machine")
        self.cron.setToolTip("A line in the crontab that runs \"aicove agent due\" every 10 minutes; agents "
                             "set to manual never run from it")
        self.cron.clicked.connect(self.set_cron)

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Persona", self.persona)
        form.addRow("Models", self.models)
        sched = QVBoxLayout()
        sched.addWidget(self.schedule)
        sched.addWidget(self.schedule_says)
        form.addRow("Schedule", sched)
        form.addRow("", self.cron)
        form.addRow("Style", self.style)
        form.addRow("Thinking", self.thinking)
        form.addRow("Limits", limits)
        form.addRow("Search from", self.search_from)
        form.addRow("Themes", self.themes)
        form.addRow("Task", self.task)
        may = QLabel("May: search the web and read pages, save its reports in its own folder. Never commands, "
                     "never other files.")
        may.setEnabled(False)
        may.setWordWrap(True)
        form.addRow("", may)

        self.state_label = QLabel()
        self.state_label.setWordWrap(True)
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.save)
        self.run_btn = QPushButton("Run now")
        self.run_btn.clicked.connect(self.run_now)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_run)
        self.folder_btn = QPushButton("Open folder")
        self.folder_btn.clicked.connect(self.open_folder)
        buttons = QHBoxLayout()
        buttons.addWidget(self.state_label, 1)
        for b in (self.folder_btn, self.save_btn, self.run_btn, self.stop_btn):
            buttons.addWidget(b)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("What the last run did shows here.")
        self.log.setMaximumHeight(170)

        right = QWidget()
        rbox = QVBoxLayout(right)
        rbox.setContentsMargins(0, 0, 0, 0)
        rbox.addLayout(form, 1)
        rbox.addLayout(buttons)
        rbox.addWidget(QLabel("Last run:"))
        rbox.addWidget(self.log)
        self.form = right
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([230, 900])
        return split

    def fill_agents(self, select=None):
        select = select or (self.current.key if self.current else None)
        self.agent_list.blockSignals(True)
        self.agent_list.clear()
        pick = None
        for place in [HERE] + launchers.hosts():
            header = QListWidgetItem("This machine" if place == HERE else f"🖥 {place}")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            font = QFont(header.font())
            font.setBold(True)
            header.setFont(font)
            self.agent_list.addItem(header)
            for e in [e for e in self.entries if e.place == place]:
                st = e.state
                mark = " ▶" if st.get("pid") or st.get("result") == "running" else ""
                item = QListWidgetItem("   " + e.agent.name + mark)
                item.setData(ROLE, e.key)
                self.agent_list.addItem(item)
                if e.key == select:
                    pick = item
        self.agent_list.blockSignals(False)
        if pick is None:
            pick = next((self.agent_list.item(i) for i in range(self.agent_list.count())
                         if self.agent_list.item(i).data(ROLE)), None)
        if pick is not None:
            self.agent_list.setCurrentItem(pick)
        else:
            self.current = None
            self.show_agent()

    def on_agent(self, item, previous=None):
        if item is None or not item.data(ROLE):
            return
        key = tuple(item.data(ROLE))
        if self.current is not None and key == self.current.key:
            # The same agent read again: only what changes by itself (state, log) is shown new.
            self.current = self.entry(key)
            self.show_state()
            return
        if self.dirty():
            answer = QMessageBox.question(self, "Unsaved changes", f"{self.current.agent.name} has unsaved changes. Save them?",
                                          QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                          | QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel or \
                    (answer == QMessageBox.StandardButton.Save and not self.save()):
                self.agent_list.blockSignals(True)
                self.agent_list.setCurrentItem(previous)
                self.agent_list.blockSignals(False)
                return
        self.current = self.entry(key)
        self.show_agent()

    def personas_of(self, place):
        if place == HERE:
            return {p.id: p.name for p in personas.load_all().values()}
        return read_cache(place).get("personas", {})

    def launchers_of(self, place):
        if place == HERE:
            return [l.name for l in launchers.find_launchers() if not l.error]
        return read_cache(place).get("launchers", [])

    def show_agent(self):
        self.switching = True
        e = self.current
        self.form.setEnabled(e is not None)
        self.delete_btn.setEnabled(e is not None)
        a = e.agent if e else agents.Agent("-")
        place = e.place if e else HERE
        self.name.setText(a.name if e else "")
        self.persona.clear()
        known = self.personas_of(place)
        for pid, name in sorted(known.items(), key=lambda p: p[1].lower()):
            self.persona.addItem(name, pid)
        if a.persona and a.persona not in known:
            self.persona.addItem(f"{a.persona} (not there)", a.persona)
        self.persona.setCurrentIndex(max(self.persona.findData(a.persona), 0))
        self.models.clear()
        there = self.launchers_of(place)
        for name in a.models + [n for n in there if n not in a.models]:
            item = QListWidgetItem(name if name in there else f"{name} (not there)")
            item.setData(ROLE, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if name in a.models else Qt.CheckState.Unchecked)
            self.models.addItem(item)
        self.schedule.setText(a.schedule)
        self.style.setCurrentIndex(max(self.style.findData(a.style), 0))
        self.thinking.setCurrentIndex(max(self.thinking.findData(a.thinking), 0))
        for key, b in self.limit_boxes.items():
            b.setValue(a.limits[key])
        self.themes.setPlainText("\n".join(" | ".join(t if t[2] else t[:2] if t[1] else t[:1]) for t in a.themes))
        self.task.setPlainText(a.task)
        i = self.search_from.findData(a.search_from)
        if i < 0 and a.search_from:
            self.search_from.addItem(a.search_from, a.search_from)
            i = self.search_from.count() - 1
        self.search_from.setCurrentIndex(max(i, 0))
        self.folder_btn.setEnabled(e is not None and not e.place)
        self.switching = False
        self.baseline = self.agent_from_form().text() if e else None
        self.show_state()
        self.changed()

    def show_state(self):
        e = self.current
        if e is None:
            self.state_label.setText("")
            self.log.setPlainText("")
            self.run_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            return
        st = e.state
        runs = bool(st.get("pid")) or st.get("result") == "running"
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(st["started"])) if st.get("started") else "never"
        text = "Running now." if runs else f"Last run: {when}, {st.get('result', '-')}."
        if st.get("result") == "waiting":
            text += " (Waits until its model is free; Run now can stop the other one.)"
        self.state_label.setText(text + (f"  On {e.place}." if e.place else ""))
        self.cron.blockSignals(True)
        self.cron.setChecked(e.cron)
        self.cron.setText("Run on schedule on this machine" if not e.place else f"Run on schedule on {e.place}")
        self.cron.blockSignals(False)
        self.run_btn.setEnabled(not runs)
        self.stop_btn.setEnabled(runs)
        if self.log.toPlainText() != e.log:
            self.log.setPlainText(e.log)
            self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def agent_from_form(self):
        e = self.current
        a = agents.Agent(e.agent.id if e else "-")
        a.name = self.name.text().strip() or a.id
        a.persona = self.persona.currentData() or ""
        a.models = [self.models.item(i).data(ROLE) for i in range(self.models.count())
                    if self.models.item(i).checkState() == Qt.CheckState.Checked]
        a.schedule = self.schedule.text().strip() or "manual"
        a.style = self.style.currentData()
        a.thinking = self.thinking.currentData()
        a.limits = {k: b.value() for k, b in self.limit_boxes.items()}
        a.search_from = self.search_from.currentData() or ""
        for line in self.themes.toPlainText().split("\n"):
            parts = [p.strip() for p in line.split("|")]
            if parts[0]:
                a.themes.append((parts[0], parts[1] if len(parts) > 1 else "",
                                 parts[2] if len(parts) > 2 and agents.SAFE_ID.fullmatch(parts[2]) else ""))
        a.task = self.task.toPlainText().strip()
        return a

    def dirty(self):
        return self.current is not None and self.baseline is not None and not self.switching \
            and self.agent_from_form().text() != self.baseline

    def changed(self, *_):
        if self.switching:
            return
        self.schedule_says.setText(agents.describe_schedule(self.schedule.text()))
        self.save_btn.setEnabled(self.dirty())

    def save(self):
        e = self.current
        if e is None:
            return False
        a = self.agent_from_form()
        try:
            agents.parse_schedule(a.schedule)
        except ValueError as err:
            QMessageBox.warning(self, "Schedule", f"The schedule isn't understood: {err}")
            return False
        try:
            if e.place:
                on_host(e.place, ["write", a.id], stdin=a.text())
                data = read_cache(e.place)
                for x in data.get("agents", []):
                    if x["id"] == a.id:
                        x["text"] = a.text()
                write_cache(e.place, data)
            else:
                a.write()
        except OSError as err:
            QMessageBox.warning(self, "Save", f"Can't save {a.name}: {err}")
            return False
        self.window.say(f"{a.name} saved" + (f" on {e.place}." if e.place else "."))
        self.baseline = a.text()
        self.reload()
        return True

    def new_agent(self):
        places = [HERE] + launchers.hosts()
        place = HERE
        if len(places) > 1:
            label, ok = QInputDialog.getItem(self, "New agent", "Runs on:",
                                             ["This machine"] + [f"🖥 {h}" for h in places[1:]], 0, False)
            if not ok:
                return
            place = places[(["This machine"] + [f"🖥 {h}" for h in places[1:]]).index(label)]
        name, ok = QInputDialog.getText(self, "New agent", "Name:")
        if not ok or not name.strip():
            return
        taken = {e.agent.id for e in self.entries if e.place == place}
        aid = agents.new_id(name) if place == HERE else re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "agent"
        n, base = 2, aid
        while aid in taken:
            aid, n = f"{base}-{n}", n + 1
        known = self.personas_of(place)
        a = agents.Agent(aid, name.strip(), "story-hunter" if "story-hunter" in known else next(iter(known), ""), NEW_TASK)
        try:
            if place:
                on_host(place, ["write", aid], stdin=a.text())
                data = read_cache(place)
                data.setdefault("agents", []).append({"id": aid, "text": a.text(), "state": {}, "stories": [], "log": ""})
                write_cache(place, data)
            else:
                a.write()
        except OSError as err:
            QMessageBox.warning(self, "New agent", f"Can't make it: {err}")
            return
        self.reload()
        self.fill_agents((place, aid))

    def delete_agent(self):
        e = self.current
        if e is None:
            return
        if QMessageBox.question(self, "Delete agent", f"Delete {e.agent.name}" + (f" on {e.place}" if e.place else "")
                                + "? It goes to the agents' trash, with its reports.") != QMessageBox.StandardButton.Yes:
            return
        try:
            if e.place:
                on_host(e.place, ["delete", e.agent.id])
                data = read_cache(e.place)
                data["agents"] = [x for x in data.get("agents", []) if x["id"] != e.agent.id]
                write_cache(e.place, data)
            else:
                agents.delete(e.agent.id)
        except OSError as err:
            self.window.say(f"Can't delete it: {err}")
            return
        self.current = None
        self.reload()

    def run_now(self):
        e = self.current
        if e is None:
            return
        if self.dirty() and not self.save():
            return
        e = self.current
        take = False
        if not e.place:
            loaded = chat.loaded_launcher()
            if (loaded is not None or server.llama_pid()) and (loaded is None or loaded.name not in e.agent.models):
                what = loaded.name if loaded else "Another model"
                if QMessageBox.question(self, "Run now", f"{what} is loaded and isn't one of {e.agent.name}'s models. "
                                        f"Stop it and start {e.agent.models[0] if e.agent.models else 'its model'}?") \
                        != QMessageBox.StandardButton.Yes:
                    return
                take = True
        elif e.state.get("result") == "waiting":
            take = QMessageBox.question(self, "Run now", f"Last time another model was loaded on {e.place}. "
                                        "Stop it there if it still is?") == QMessageBox.StandardButton.Yes
        try:
            if e.place:
                on_host(e.place, ["start", e.agent.id] + (["--take-model"] if take else []))
            else:
                agents.detach(e.agent.id, take)
        except OSError as err:
            self.window.say(f"Can't start {e.agent.name}: {err}")
            return
        self.window.say(f"{e.agent.name} started" + (f" on {e.place}" if e.place else "")
                        + ". It goes on when the window closes; its reports show up under Reports.")
        e.state = dict(e.state, result="running")
        self.show_state()
        QTimer.singleShot(1500, self.update if e.place else self.reload)

    def stop_run(self):
        e = self.current
        if e is None:
            return
        try:
            on_host(e.place, ["stop", e.agent.id]) if e.place else agents.stop(e.agent.id)
        except OSError as err:
            self.window.say(f"Can't stop it: {err}")
            return
        QTimer.singleShot(1500, self.update if e.place else self.reload)

    def set_cron(self, on):
        e = self.current
        try:
            if e is not None and e.place:
                on_host(e.place, ["schedule", "on" if on else "off"])
                data = read_cache(e.place)
                data["cron"] = on
                write_cache(e.place, data)
            else:
                agents.set_cron(on)
        except (OSError, subprocess.SubprocessError) as err:
            QMessageBox.warning(self, "Schedule", f"Can't change the crontab: {err}")
        self.reload()

    def open_folder(self):
        if self.current is not None and not self.current.place:
            QDesktopServices.openUrl(QUrl.fromLocalFile(agents.folder(self.current.agent.id)))
