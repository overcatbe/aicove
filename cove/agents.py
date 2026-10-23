# Agents: a persona with a standing task, run on its own (Run now, or on a schedule by cron
# calling "aicove agent due"), that comes back with reports. An agent lives on the machine that
# runs it, in the AIcove folder there:
#   agents/<id>/agent.txt      name:, models:, schedule:, style:, thinking:, limits:, files:, theme: lines,
#                              empty line, then the task in words (persona: from before agents
#                              had their own voice still works)
#   agents/<id>/voice.txt      how it talks, if it has a character; facts rules come first
#   agents/<id>/picture.*      its face; chats/, knowledge/, models/ like a persona's: in the chat
#                              an agent is "@<id>" (personas.folder) and knows its reports
#   agents/<id>/stories/*.md   one report per story: title, teaser, article, sources, facts to check
#   agents/<id>/seen.txt       links already reported, so they don't come again
#   agents/<id>/runs/*.txt     what each run did; state.json: when it last ran and how it went
# It may search the web and read pages, nothing else: no files, no commands. Its sources are
# only pages it really read in that run (small models make up addresses). Kept, used and
# dismissed stories go back into its next hunts as the user's taste.

import contextlib
import datetime
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.parse

from . import chat, facts, launchers, personas, server, web
from .settings import COVE_DIR, MAIN, S

AGENT_DIR = os.path.join(COVE_DIR, "agents")
AGENT_FILE = "agent.txt"
VOICE_FILE = "voice.txt"
# What an agent is told in the chat, after its voice.
CHAT = """You are {name}, an agent. On your own you do this job:

{task}

{themes}Now the user is talking with you about that work. Your reports (leads, stories, dossiers,
articles) are your knowledge: look there first and name the report. When web search is on, use it
to check a report, dig deeper or find more. Every fact you give comes from a report or a page you
read; say which. Never invent sources, numbers or quotes; say "I don't know" instead. A full run
(a hunt or a research) is started by the user on the Agents tab, not from here."""
SAFE_ID = personas.SAFE_ID
STATUSES = ["new", "kept", "used", "dismissed"]
LIMITS = {"searches": 15, "pages": 12, "steps": 40, "minutes": 20, "stories": 3, "sites": 1}
LIMIT_WORDS = {"searches": "web searches", "pages": "pages read", "steps": "tool calls in all",
               "minutes": "minutes", "stories": "stories (or leads) per run",
               "sites": "1: leads (a headline, why, one link, also straight from search results), for you to pick "
                        "from. 2 or more: written-up stories, each from articles of that many different sites"}
# A story's or lead's newest page may be this many days old at most (when the pages say when they
# came out). How many sites a story needs is the agent's "sites" limit; 1 means leads.
FRESH_DAYS = 3
# Front pages and section pages: a place to find articles, never a source.
FRONT = re.compile(r"/?(?:[a-z]{2}(?:[-_][a-z]{2})?/?)?(?:index\.\w+|home|news|latest|world|politics|science|"
                   r"tech(?:nology)?)?/?", re.IGNORECASE)
# One step's answer, thinking included, at most this many tokens: a thinking model can go on for
# 10000 words and the hunt's minutes run out before it saves anything.
STEP_TOKENS = 4096
# Of the pages read, the last few stay whole in what the model gets; older ones are cut short.
WHOLE_PAGES = 3
CUT_PAGE = 1500
# What "aicove agent due" is called by: every 10 minutes.
CRON_LINE = f"*/10 * * * * {sys.executable} {MAIN} agent due >/dev/null 2>&1"
CRON_MARK = " agent due"


# Hosts in ~/.ssh/config that are only for git, not machines of yours.
GIT_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")


def ssh_hosts():
    """The machines named in ~/.ssh/config (Host lines without wildcards), for "search from"."""
    found = []
    try:
        with open(os.path.expanduser("~/.ssh/config"), encoding="utf-8") as f:
            for line in f:
                key, _, value = line.strip().partition(" ")
                if key.lower() == "host":
                    found += [h for h in value.split() if launchers.NAME.fullmatch(h) and h not in GIT_HOSTS]
    except OSError:
        pass
    return list(dict.fromkeys(found))


def folder(aid):
    return os.path.join(AGENT_DIR, aid)


class Agent:
    def __init__(self, aid, name="", persona="", task=""):
        self.id = aid
        self.name = name or aid
        self.persona = persona
        self.models = []
        self.schedule = "manual"
        self.style = "Factual"
        self.thinking = ""
        self.limits = dict(LIMITS)
        # [(name, what it means, persona to work on it with or "")]
        self.themes = []
        # The ssh host its searches and page reads go out from, or "" for the machine it runs on
        self.search_from = ""
        self.task = task
        self.voice = ""
        # "read": in the chat it may read files (what the Settings tab allows); never write.
        self.files = ""

    @property
    def path(self):
        return os.path.join(folder(self.id), AGENT_FILE)

    @classmethod
    def parse(cls, aid, text):
        a = cls(aid)
        head, _, task = text.replace("\r\n", "\n").partition("\n\n")
        for line in head.split("\n"):
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "name":
                a.name = value or aid
            elif key == "persona" and SAFE_ID.fullmatch(value):
                a.persona = value
            elif key == "models":
                a.models = [m for m in dict.fromkeys(n.strip() for n in value.split(",")) if SAFE_ID.fullmatch(m)]
            elif key == "schedule":
                a.schedule = value or "manual"
            elif key == "style":
                a.style = value if value in chat.STYLES else ""
            elif key == "thinking":
                a.thinking = value if value in ("on", "off") else ""
            elif key == "limits":
                for part in value.split(","):
                    word, _, number = part.strip().partition(" ")
                    if word in LIMITS and number.strip().isdigit():
                        a.limits[word] = max(1, int(number))
            elif key == "files":
                a.files = "read" if value == "read" else ""
            elif key == "search from":
                a.search_from = value if launchers.NAME.fullmatch(value) else ""
            elif key == "theme":
                parts = [p.strip() for p in value.split("|")]
                if parts[0]:
                    a.themes.append((parts[0], parts[1] if len(parts) > 1 else "",
                                     parts[2] if len(parts) > 2 and SAFE_ID.fullmatch(parts[2]) else ""))
        a.task = task.strip()
        return a

    @classmethod
    def read(cls, aid):
        with open(os.path.join(folder(aid), AGENT_FILE), encoding="utf-8") as f:
            a = cls.parse(aid, f.read())
        try:
            with open(os.path.join(folder(aid), VOICE_FILE), encoding="utf-8") as f:
                a.voice = f.read().strip()
        except OSError:
            pass
        return a

    @property
    def pid(self):
        """Its name in the chat and in the per-model settings."""
        return personas.AGENT + self.id

    def own(self, system):
        """A persona made of the agent: its name, models and style, with this system prompt."""
        p = personas.Persona(self.pid, self.name, system)
        p.models = list(self.models)
        p.files = self.files
        for key, value in chat.STYLES.get(self.style, {}).items():
            if personas.sampler_value(key, value) is not None:
                p.samplers[key] = personas.sampler_value(key, value)
        if self.thinking:
            p.samplers["thinking"] = self.thinking
        return p

    def as_persona(self):
        """The agent as the chat has it: its voice, its job, its reports as knowledge."""
        themes = "".join(f"- {n}: {m}\n" if m else f"- {n}\n" for n, m, _ in self.themes)
        return self.own("\n\n".join(x for x in (self.voice, CHAT.format(
            name=self.name, task=self.task.strip() or "(no job written yet)",
            themes=f"Your themes:\n{themes}\n" if themes else "")) if x))

    def text(self):
        head = [f"name: {self.name}"] + ([f"persona: {self.persona}"] if self.persona else [])
        if self.models:
            head.append(f"models: {', '.join(self.models)}")
        head.append(f"schedule: {self.schedule or 'manual'}")
        if self.style:
            head.append(f"style: {self.style}")
        if self.thinking:
            head.append(f"thinking: {self.thinking}")
        head.append("limits: " + ", ".join(f"{k} {self.limits[k]}" for k in LIMITS))
        if self.search_from:
            head.append(f"search from: {self.search_from}")
        if self.files:
            head.append(f"files: {self.files}")
        for name, meaning, pid in self.themes:
            head.append(f"theme: {name} | {meaning}" + (f" | {pid}" if pid else ""))
        return "\n".join(head) + "\n\n" + self.task.strip() + "\n"

    def write(self):
        os.makedirs(folder(self.id), exist_ok=True)
        tmp = self.path + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(self.text())
        os.replace(tmp, self.path)
        voice = os.path.join(folder(self.id), VOICE_FILE)
        if self.voice.strip():
            with open(voice + ".new", "w", encoding="utf-8") as f:
                f.write(self.voice.strip() + "\n")
            os.replace(voice + ".new", voice)
        elif os.path.exists(voice):
            os.remove(voice)

    def work_with(self, theme):
        """The persona to work on a story of this theme with, or ""."""
        return next((pid for name, _, pid in self.themes if name == theme and pid), "")


def load_all():
    try:
        names = sorted(os.listdir(AGENT_DIR))
    except OSError:
        return {}
    found = {}
    for n in names:
        if SAFE_ID.fullmatch(n) and os.path.isfile(os.path.join(AGENT_DIR, n, AGENT_FILE)):
            try:
                found[n] = Agent.read(n)
            except OSError:
                continue
    return found


def new_id(name):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "agent"
    aid, n = base, 2
    while os.path.exists(folder(aid)):
        aid, n = f"{base}-{n}", n + 1
    return aid


def delete(aid):
    """Into the agents' trash, stories and all."""
    trash = os.path.join(AGENT_DIR, ".trash")
    os.makedirs(trash, exist_ok=True)
    dest = os.path.join(trash, f"{aid}-{time.strftime('%Y%m%d-%H%M%S')}")
    os.rename(folder(aid), dest)


# --- state: when it ran, whether it runs ------------------------------------------

def state_path(aid):
    return os.path.join(folder(aid), "state.json")


def state(aid):
    try:
        with open(state_path(aid), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    pid = data.get("pid")
    if pid and not alive(pid):
        # A run that died without saying so (the machine went down, it was killed).
        data.pop("pid", None)
        if data.get("result") == "running":
            data["result"] = "broken off"
    return data


def save_state(aid, **changes):
    data = state(aid)
    data.update(changes)
    for k in [k for k, v in data.items() if v is None]:
        del data[k]
    os.makedirs(folder(aid), exist_ok=True)
    tmp = state_path(aid) + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, state_path(aid))


def alive(pid):
    cmd = server.cmdline(pid)
    return any("agent" == c for c in cmd) and any(c.endswith("aicove") for c in cmd)


def running(aid):
    return bool(state(aid).get("pid"))


# --- stories ----------------------------------------------------------------------

def stories_dir(aid):
    return os.path.join(folder(aid), "stories")


class Story:
    def __init__(self, aid, file, meta, body):
        self.agent = aid
        self.file = file
        self.meta = meta
        self.body = body

    title = property(lambda self: self.meta.get("title", self.file))
    teaser = property(lambda self: self.meta.get("teaser", ""))
    theme = property(lambda self: self.meta.get("theme", ""))
    status = property(lambda self: self.meta.get("status", "new"))
    found = property(lambda self: self.meta.get("found", ""))

    @property
    def path(self):
        return os.path.join(stories_dir(self.agent), self.file)

    @classmethod
    def parse(cls, aid, file, text):
        meta, body = {}, text
        if text.startswith("---\n"):
            head, _, body = text[4:].partition("\n---\n")
            for line in head.split("\n"):
                key, _, value = line.partition(":")
                if key.strip():
                    meta[key.strip()] = value.strip()
        return cls(aid, file, meta, body.strip())

    def text(self):
        head = "".join(f"{k}: {' '.join(str(v).split())}\n" for k, v in self.meta.items())
        return f"---\n{head}---\n\n{self.body.strip()}\n"

    def save(self):
        os.makedirs(stories_dir(self.agent), exist_ok=True)
        tmp = self.path + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(self.text())
        os.replace(tmp, self.path)

    def markdown(self, earlier=True):
        """The whole report, to read or to hand to a chat; with the report it follows up, if any."""
        if self.meta.get("kind") == "lead":
            text = f"# {self.title}\n\n{self.body}\n"
        else:
            text = f"# {self.title}\n\n*{self.teaser}*\n\n{self.body}\n"
        before = self.earlier() if earlier else None
        if before is not None:
            text += f"\n---\n\nThe earlier report it follows up ({before.found[:10]}):\n\n" + before.markdown(False)
        return text

    def earlier(self):
        """The report this one follows up, or None."""
        file = self.meta.get("follows", "")
        if not file or "/" in file or not os.path.isfile(os.path.join(stories_dir(self.agent), file)):
            return None
        with open(os.path.join(stories_dir(self.agent), file), encoding="utf-8") as f:
            return Story.parse(self.agent, file, f.read())


def stories(aid):
    """The agent's stories, newest first."""
    found = []
    try:
        names = os.listdir(stories_dir(aid))
    except OSError:
        return []
    for name in names:
        if not name.endswith(".md"):
            continue
        try:
            with open(os.path.join(stories_dir(aid), name), encoding="utf-8") as f:
                found.append(Story.parse(aid, name, f.read()))
        except OSError:
            continue
    return sorted(found, key=lambda s: (s.found, s.file), reverse=True)


def mark(aid, file, status):
    if status not in STATUSES or "/" in file or not file.endswith(".md"):
        raise ValueError(f"can't mark {file} as {status}")
    with open(os.path.join(stories_dir(aid), file), encoding="utf-8") as f:
        story = Story.parse(aid, file, f.read())
    story.meta["status"] = status
    story.save()


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50].rstrip("-") or "story"


def plain(url):
    """An address in one spelling for comparing: no fragment, no trailing slash, host in lower case."""
    p = urllib.parse.urlsplit(url.strip())
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), p.query, ""))


def seen(aid):
    try:
        with open(os.path.join(folder(aid), "seen.txt"), encoding="utf-8") as f:
            return {line.split("\t")[0] for line in f if line.strip()}
    except OSError:
        return set()


def remember(aid, urls):
    with open(os.path.join(folder(aid), "seen.txt"), "a", encoding="utf-8") as f:
        for u in urls:
            f.write(f"{plain(u)}\t{time.strftime('%Y-%m-%d')}\n")


# --- the schedule ---------------------------------------------------------------
# "manual", "every 6h" (or 30m), "daily 07:00, 19:00", "weekdays 07:00", "weekends 10:00",
# "mon wed fri 07:00".

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_schedule(text):
    """("manual",) | ("every", seconds) | ("at", {weekday numbers}, [(hour, minute)]); raises ValueError."""
    t = " ".join(text.lower().replace(",", " ").split())
    if t in ("", "manual", "never"):
        return ("manual",)
    m = re.fullmatch(r"every (\d+) ?(h|m|hours?|minutes?)", t)
    if m:
        seconds = int(m.group(1)) * (3600 if m.group(2).startswith("h") else 60)
        if seconds < 600:
            raise ValueError("every 10 minutes is the least")
        return ("every", seconds)
    words = t.split()
    days, times = set(), []
    for w in words:
        if w == "daily":
            days |= set(range(7))
        elif w == "weekdays":
            days |= set(range(5))
        elif w == "weekends":
            days |= {5, 6}
        elif w[:3] in DAYS and w.isalpha():
            days.add(DAYS.index(w[:3]))
        elif re.fullmatch(r"\d{1,2}:\d\d", w) and int(w.split(":")[0]) < 24 and int(w.split(":")[1]) < 60:
            times.append(tuple(int(x) for x in w.split(":")))
        else:
            raise ValueError(f"don't know \"{w}\"")
    if not days or not times:
        raise ValueError("needs days (daily, weekdays, weekends, mon tue ...) and times (07:00)")
    return ("at", days, sorted(set(times)))


def last_slot(plan, now):
    """The last moment the schedule asked for a run, at or before now (a timestamp), or None."""
    if plan[0] != "at":
        return None
    day = datetime.datetime.fromtimestamp(now)
    for back in range(8):
        d = (day - datetime.timedelta(days=back)).date()
        if d.weekday() not in plan[1]:
            continue
        for h, m in reversed(plan[2]):
            t = datetime.datetime(d.year, d.month, d.day, h, m).timestamp()
            if t <= now:
                return t
    return None


def is_due(agent, now=None):
    """Whether the schedule wants a run now. A missed run (machine off) is caught up once."""
    now = now or time.time()
    try:
        plan = parse_schedule(agent.schedule)
    except ValueError:
        return False
    st = state(agent.id)
    if plan[0] == "manual" or st.get("pid"):
        return False
    last = st.get("started", 0)
    # Since when the schedule counts: a new agent doesn't run for times before it was there.
    since = max(last, st.get("scheduled_since", 0))
    if not since:
        save_state(agent.id, scheduled_since=now)
        return plan[0] == "every"
    if plan[0] == "every":
        return now - last >= plan[1]
    slot = last_slot(plan, now)
    return slot is not None and slot > since


def describe_schedule(text):
    try:
        plan = parse_schedule(text)
    except ValueError as e:
        return f"not understood: {e}"
    if plan[0] == "manual":
        return "only when you press Run now"
    if plan[0] == "every":
        s = plan[1]
        return f"every {s // 3600} h" if s % 3600 == 0 else f"every {s // 60} min"
    days = "every day" if plan[1] == set(range(7)) else ", ".join(DAYS[d] for d in sorted(plan[1]))
    return f"{days} at " + ", ".join(f"{h:02}:{m:02}" for h, m in plan[2])


def cron_installed():
    try:
        out = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return any(CRON_MARK in line and not line.lstrip().startswith("#") for line in out.stdout.split("\n"))


def set_cron(on):
    """Put the line that runs "aicove agent due" every 10 minutes into the user's crontab, or
    take it out. Other lines stay as they are."""
    out = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    lines = [l for l in out.stdout.split("\n") if l.strip()] if out.returncode == 0 else []
    lines = [l for l in lines if CRON_MARK not in l]
    if on:
        lines.append(CRON_LINE)
    done = subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n" if lines else "",
                          capture_output=True, text=True, timeout=10)
    if done.returncode != 0:
        raise OSError(done.stderr.strip() or "crontab didn't take it")


# --- a run --------------------------------------------------------------------

SAVE_TOOL = {"type": "function", "function": {
    "name": "save_story",
    "description": "Save one story report for the user. Only pages you read with read_web_page count as sources.",
    "parameters": {"type": "object", "required": ["title", "theme", "teaser", "article", "sources"], "properties": {
        "title": {"type": "string", "description": "A headline."},
        "theme": {"type": "string", "description": "Which of the themes it belongs to."},
        "teaser": {"type": "string", "description": "One or two sentences that make the user want to read it."},
        "article": {"type": "string", "description": "3 to 6 paragraphs in your own words, only what the sources say. "
                                                     "Paragraphs separated by an empty line."},
        "sources": {"type": "array", "items": {"type": "string"},
                    "description": "The addresses of the pages you read for it, the most important first."},
        "facts_to_check": {"type": "array", "items": {"type": "string"},
                           "description": "The claims the article stands on (numbers, names, dates, quotes), "
                                          "each ending with the number of its source in brackets, e.g. \"... [1]\"."},
    }}}}

# How far back a run sees what it reported, to name follow-ups.
FOLLOW_DAYS, FOLLOW_MAX = 21, 80

LEAD_TOOL = {"type": "function", "function": {
    "name": "save_lead",
    "description": "Save one lead for the user: a news item they may pick to write about. The address must be "
                   "a search result you got or a page you read in this hunt.",
    "parameters": {"type": "object", "required": ["title", "theme", "why", "url"], "properties": {
        "title": {"type": "string", "description": "What happened, as a short headline."},
        "theme": {"type": "string", "description": "Which of the themes it belongs to."},
        "why": {"type": "string", "description": "One or two sentences: what it is and why it is interesting, "
                                                 "only from what the search result or page says."},
        "url": {"type": "string", "description": "The article's address, exactly as in the result or page."},
        "follows": {"type": "string", "description": "Only if this news continues one of the earlier reports "
                                                     "listed (a reply, a consequence, the same people days "
                                                     "later): that report's title, exactly as listed. Else leave "
                                                     "it out."},
    }}}}

SOURCE_KINDS = ["primary", "research", "reference", "news", "book", "fringe", "other"]

SOURCE_TOOL = {"type": "function", "function": {
    "name": "note_source",
    "description": "Note a page you read as a source for the research: what it says about the topic.",
    "parameters": {"type": "object", "required": ["url", "title", "kind", "says"], "properties": {
        "url": {"type": "string", "description": "The page's address, exactly as you read it."},
        "title": {"type": "string", "description": "The page's title."},
        "kind": {"type": "string", "enum": SOURCE_KINDS,
                 "description": "primary: documents, archives, official files, eyewitnesses; research: papers, "
                                "historians, experts; reference: encyclopedias; news; book; fringe: conspiracy "
                                "sites, forums, claims without evidence; other."},
        "date": {"type": "string", "description": "When it came out, if the page says (YYYY or YYYY-MM-DD)."},
        "says": {"type": "string", "description": "2 to 6 sentences: what this page says about the topic, names, "
                                                  "dates, numbers, claims, in your words, only what it says."},
    }}}}

DOSSIER_TOOL = {"type": "function", "function": {
    "name": "finish_dossier",
    "description": "Write the dossier from your noted sources. This ends the research.",
    "parameters": {"type": "object", "required": ["title", "solid", "articles"], "properties": {
        "title": {"type": "string", "description": "A title for the dossier."},
        "solid": {"type": "string", "description": "What is solid: what good sources agree on, with source numbers "
                                                   "like [2]. Paragraphs separated by an empty line."},
        "disputed": {"type": "string", "description": "What is disputed or only claimed, and by whom, with source "
                                                      "numbers."},
        "questions": {"type": "string", "description": "Open questions nobody answers."},
        "articles": {"type": "array", "description": "3 to 5 proposed articles.", "items": {
            "type": "object", "required": ["title", "angle"], "properties": {
                "title": {"type": "string", "description": "A headline."},
                "angle": {"type": "string", "description": "The angle: what the article tells and why it grabs."},
                "hook": {"type": "string", "description": "The opening hook: the detail or scene to start with."},
                "sources": {"type": "array", "items": {"type": "integer"},
                            "description": "The numbers of the sources it stands on."}}}},
    }}}}

ARTICLE_TOOL = {"type": "function", "function": {
    "name": "save_article",
    "description": "Save one finished article.",
    "parameters": {"type": "object", "required": ["title", "teaser", "text"], "properties": {
        "title": {"type": "string", "description": "The headline."},
        "teaser": {"type": "string", "description": "One or two sentences that make people read it."},
        "text": {"type": "string", "description": "The article. Paragraphs separated by an empty line; "
                                                  "## subheadings if it helps."},
        "sources": {"type": "array", "items": {"type": "integer"},
                    "description": "The numbers of the dossier's sources it uses."},
    }}}}

RESEARCH = """[You work alone now, as an agent. Nobody reads along and nobody answers questions: decide yourself.]

Research this topic as deeply as you can: {topic}

What the user does with it (for the angle): {task}

How to work:
- Today is {today}. Old sources are welcome: history, archives, declassified files, research, books, \
news of any year; fringe sites and forums too, marked as fringe.
- Search from many angles (at most {searches} searches): names, places, dates, events, the people \
involved, words like "declassified", "archive", "expedition", "report", in English, in German and in \
other languages that fit the topic. Follow the names and leads you find on the pages.
- Read pages with read_web_page (at most {pages}). After each page that says something about the topic, \
note it with note_source. A search result alone is not a source; a page that says nothing about the topic \
is not noted.
- When you have enough, and before your searches, pages or time run out, write the dossier with \
finish_dossier: what is solid, what is disputed or only claimed (and by whom), open questions, and 3 to 5 \
proposed articles, each with a headline, the angle, the hook and the sources it stands on. Facts only from \
your sources, with their numbers like [3]; anything else only as a question. That ends the research."""

ARTICLES = """[You work alone now: nobody answers questions. Decide yourself.]

Write {count} article{s} from the research dossier below{wish}. Pick the strongest of the proposed \
articles, or a better angle the dossier carries. Each article: a headline, a teaser of one or two \
sentences, and the text, in your own voice and style. Facts, names, dates, numbers and quotes only from the \
dossier; what is disputed or only claimed is told as claimed, by whom; speculation only as a question. Save \
each with save_article, with the numbers of the sources it uses. When all are saved, answer with one short \
line, without a tool call.

{dossier}"""

HEAD = """[You work alone now, as an agent. Nobody reads along and nobody answers questions: decide yourself.]

Your task:
{task}

The themes, and what each means to the user:
{themes}

How to work:
- Today is {today}; yesterday was {yesterday}. The year is {year}. Search with web_search (at most \
{searches} searches) and read pages with read_web_page (at most {pages} pages). Search theme by theme, with \
the date in the search (like "{yesterday_short}") to get recent news.
"""

LEADS = HEAD + """- You bring leads, not articles: the user scans them and picks what to write about. Bring many, up \
to {stories}, spread over all themes: the latest news, what goes viral, what people fight about, the absurd. \
Different stories, not the same one twice.
- Save each with save_lead: a short headline, one or two sentences on what it is and why it is interesting, \
the article's address. A search result is enough: you need not open the page, but say only what the result \
or the page says, nothing invented. Open a page only when the result is too thin to tell what happened.
- The address must be an article from your search results or a page you read, copied exactly; never from \
memory. A front page or an index of articles is not a lead: find the article. Nothing older than {fresh} \
days: that is not news.
- Connect: when a new item continues one of the earlier reports listed below (a reply, a consequence, \
the same people a few days on), save it with follows set to that report's title. The user writes about \
such connections; a true one is worth more than three loose leads. Only when it really is the same story.
- Work fast: a search, then save the good leads from it, then the next search. When you are done, answer \
with one short line about the hunt, without a tool call."""

RULES = HEAD + """- Save every good story with save_story, at most {stories} this time. Only pages you really read with \
read_web_page count as sources. Never write an address from memory. A list of results, a front page or an \
index of articles is not a source: open the article itself.
- A story needs at least {sites} articles from different sites about the same thing: after the first one, \
search for the same story elsewhere and read it. A story whose newest article is more than {fresh} days old \
is not news: it is not saved.
- The article: 3 to 6 paragraphs in your own words, only what the sources say, nothing invented. If the \
sources disagree, say so. A thin source makes a short article: don't fill it up with general talk.
- Facts to check: the claims the article stands on, each with the number of its source, like "... [1]".
- When you are done, answer with one short line about the hunt, without a tool call."""

TASTE = """
Reported before, newest first (don't bring these again; a new item that continues one may follow it up):
{before}"""

LIKED = """
The user kept or used these, so more like them:
{liked}"""

DISLIKED = """
The user dismissed these, so less like them:
{disliked}"""


def system_prompt(agent, persona):
    themes = "\n".join(f"- {n}: {m}" if m else f"- {n}" for n, m, _ in agent.themes) or "- anything the task names"
    parts = [persona.system.strip() if persona else "", facts.for_prompt(persona.id) if persona else ""]
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    rules = (LEADS if agent.limits["sites"] <= 1 else RULES).format(task=agent.task.strip() or "Find stories for the themes.", themes=themes,
                         today=time.strftime("%A, %d %B %Y"), yesterday=yesterday.strftime("%A, %d %B %Y"),
                         yesterday_short=f"{yesterday.day} {yesterday:%B %Y}", year=datetime.date.today().year,
                         fresh=FRESH_DAYS, **agent.limits)
    known = stories(agent.id)
    # The last weeks, so that follow-ups can be seen (a run brings up to 30 leads).
    since = time.strftime("%Y-%m-%d", time.localtime(time.time() - FOLLOW_DAYS * 86400))
    before = [s for s in known if s.found[:10] >= since][:FOLLOW_MAX] or known[:30]
    if before:
        rules += TASTE.format(before="\n".join(f"- {s.title} ({s.found[:10]})" for s in before))
    liked = [s for s in known if s.status in ("kept", "used")][:15]
    if liked:
        rules += LIKED.format(liked="\n".join(f"- {s.title}" for s in liked))
    disliked = [s for s in known if s.status == "dismissed"][:15]
    if disliked:
        rules += DISLIKED.format(disliked="\n".join(f"- {s.title}" for s in disliked))
    parts.append(rules)
    return "\n\n".join(p for p in parts if p)


class Busy(Exception):
    """Another model is loaded (someone is chatting): the run waits for later."""


def model_for(agent, take, say):
    """(launcher, whether this run started it). Uses the loaded model when it is one of the
    agent's; starts one when nothing is loaded, or when take says another may be stopped."""
    loaded = chat.loaded_launcher()
    if loaded is not None and loaded.name in agent.models:
        return loaded, False
    if (loaded is not None or server.llama_pid()) and not take:
        what = loaded.name if loaded else "another model"
        raise Busy(f"{what} is loaded and isn't one of {agent.name}'s models")
    for name in agent.models:
        launcher = launchers.get(name)
        if launcher is None or launcher.error or not launcher.present:
            continue
        if server.llama_start(launcher, say) == 0:
            return launcher, True
        raise OSError(f"{name} didn't start")
    raise OSError(f"none of {agent.name}'s models ({', '.join(agent.models) or 'none set'}) is on this machine")


def tools_for(left, topic=None):
    if topic is not None:
        return [t for t, ok in ((web.TOOLS[0], left["searches"] > 0), (web.TOOLS[1], left["pages"] > 0),
                                (SOURCE_TOOL, True), (DOSSIER_TOOL, True)) if ok]
    tools = []
    if left["searches"] > 0:
        tools.append(web.TOOLS[0])
    if left["pages"] > 0:
        tools.append(web.TOOLS[1])
    if left["stories"] > 0:
        tools.append(LEAD_TOOL if left["sites"] <= 1 else SAVE_TOOL)
    return tools


def cut_old_pages(messages):
    """What the model gets: older pages shortened, so a long hunt fits the context."""
    pages = [i for i, m in enumerate(messages) if m.get("role") == "tool" and m.get("name") == "read_web_page"]
    searches = [i for i, m in enumerate(messages) if m.get("role") == "tool" and m.get("name") == "web_search"]
    out = []
    for i, m in enumerate(messages):
        m = {k: v for k, v in m.items() if k != "name"}
        if i in pages[:-WHOLE_PAGES] and len(m["content"]) > CUT_PAGE:
            m["content"] = m["content"][:CUT_PAGE] + "\n[... shortened: you read this page earlier]"
        elif i in searches[:-WHOLE_PAGES] and len(m["content"]) > CUT_PAGE:
            # Only the titles and addresses: what it may still save as a lead.
            blocks = m["content"].split("\n\n")
            m["content"] = "\n".join([blocks[0]] + ["\n".join(b.split("\n")[:2]) for b in blocks[1:]]) + \
                "\n[... shortened: you searched this earlier]"
        out.append(m)
    return out


def front_page(url):
    p = urllib.parse.urlsplit(url.strip())
    return not p.query and FRONT.fullmatch(p.path or "/") is not None


def site(url):
    host = urllib.parse.urlsplit(url.strip()).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def old_years(query):
    """The years a search asks for when all of them are before this one (models search with the
    year they were trained in), else []."""
    years = [int(y) for y in re.findall(r"\b(?:19|20)\d\d\b", query)]
    this = datetime.date.today().year
    return years if years and all(y < this for y in years) else []


def save_story(agent, a, fetched, saved, say, dates=None):
    """Carry out save_story. Returns what the model is told."""
    title = " ".join(str(a.get("title", "")).split())
    article = str(a.get("article", "")).strip()
    if not title or not article:
        return "Not saved: a story needs a title and an article."
    sources = a.get("sources") or []
    if isinstance(sources, str):
        sources = re.findall(r"https?://\S+", sources)
    read = {plain(u) for u in fetched}
    good, bad = [], []
    for u in sources:
        u = str(u).strip().strip("<>()[]")
        (good if plain(u) in read else bad).append(u)
    good = list(dict.fromkeys(good))
    if not good:
        return ("Not saved: none of these sources is a page you read with read_web_page in this hunt. "
                "Read the pages first, then save the story with their addresses.")
    fronts = [u for u in good if front_page(u)]
    good = [u for u in good if not front_page(u)]
    if not good:
        return ("Not saved: " + ", ".join(fronts) + " is a front page, not an article. Open the article itself "
                "with read_web_page and save the story with its address.")
    sites = agent.limits["sites"]
    if len({site(u) for u in good}) < sites:
        return (f"Not saved: a story needs articles from at least {sites} different sites, this has "
                f"{len({site(u) for u in good})} ({', '.join(sorted({site(u) for u in good}))}). Search for the same "
                "story on another site, read it, then save again with all the addresses.")
    days = {plain(u): d for u, d in (dates or {}).items()}
    known = [days[plain(u)] for u in good if plain(u) in days]
    if known and too_old(max(known)):
        return (f"Not saved: the newest of these articles came out on {max(known)}: that is not news any more. "
                "Find something from the last days.")
    bad += fronts
    before = seen(agent.id)
    if all(plain(u) in before for u in good):
        return "Not saved: this was reported before (same sources). Find another story."
    themes = [n for n, _, _ in agent.themes]
    theme = " ".join(str(a.get("theme", "")).split())
    theme = next((n for n in themes if n.lower() == theme.lower()), "") or theme or (themes[0] if themes else "")
    facts_ = [" ".join(str(f).split()) for f in (a.get("facts_to_check") or []) if str(f).strip()]
    body = article + "\n\n## Sources\n\n" + "\n".join(f"{n}. <{u}>" for n, u in enumerate(good, 1))
    if facts_:
        body += "\n\n## Facts to check\n\n" + "\n".join(f"- [ ] {f}" for f in facts_)
    stamp = time.strftime("%Y%m%d-%H%M")
    file = f"{stamp}-{slug(title)}.md"
    n = 2
    while os.path.exists(os.path.join(stories_dir(agent.id), file)):
        file, n = f"{stamp}-{slug(title)}-{n}.md", n + 1
    Story(agent.id, file, {"title": title, "theme": theme, "teaser": a.get("teaser", ""),
                           "status": "new", "found": time.strftime("%Y-%m-%d %H:%M"),
                           "agent": agent.name}, body).save()
    remember(agent.id, good)
    saved.append(file)
    say(f"  saved: {title} [{theme}] ({len(good)} sources" + (f", {len(bad)} dropped" if bad else "") + ")")
    note = f"Saved as \"{title}\"."
    if bad:
        note += " Dropped as sources (not read in this hunt, or a front page): " + ", ".join(bad)
    return note


def too_old(day):
    return bool(day) and day < str(datetime.date.today() - datetime.timedelta(days=FRESH_DAYS))


def save_lead(agent, a, fetched, results, dates, saved, say, mine=None):
    """Carry out save_lead. results: {plain address: (title, snippet)} of the searches and the
    pages' links in this hunt; mine: the addresses saved in this hunt (a digest page may carry
    several leads). Returns what the model is told."""
    title = " ".join(str(a.get("title", "")).split())
    why = " ".join(str(a.get("why", "")).split())
    url = str(a.get("url", "")).strip().strip("<>()[]")
    if not title or not why or not url:
        return "Not saved: a lead needs a title, why, and the address."
    read = {plain(u) for u in fetched}
    if plain(url) not in read and plain(url) not in results:
        return ("Not saved: that address was in none of your search results and you didn't read it. Copy the "
                "address exactly from a result.")
    if front_page(url):
        return ("Not saved: that is a front page, not an article. Save the article's own address: read the front "
                "page and you get the articles linked on it.")
    mine = mine if mine is not None else set()
    if plain(url) in seen(agent.id) and plain(url) not in mine:
        return "Not saved: the user had this one before. Find another."
    if any(x.title.lower() == title.lower() for x in stories(agent.id) if x.file in saved):
        return "Not saved: you saved that one already in this hunt."
    day = next((d for u, d in dates.items() if plain(u) == plain(url)), "") or web.published("", url)
    if too_old(day):
        return f"Not saved: it came out on {day}, that is not news any more."
    themes = [n for n, _, _ in agent.themes]
    theme = " ".join(str(a.get("theme", "")).split())
    theme = next((n for n in themes if n.lower() == theme.lower()), "") or theme or (themes[0] if themes else "")
    earlier = earlier_report(agent.id, a.get("follows", ""), saved)
    body = why + (f"\n\n**Follows up:** *{earlier.title}* ({earlier.found[:10]})" if earlier else "") \
        + f"\n\n## Source\n\n1. <{url}>" + (f" (published {day})" if day else "")
    if plain(url) in read:
        body += "\n\n*Read by the agent.*"
    else:
        rtitle, snippet = results[plain(url)]
        body += "\n\n*From a search result, not opened by the agent. The result said:* " + \
            f"**{rtitle}**" + (f" — {snippet}" if snippet else "")
    stamp = time.strftime("%Y%m%d-%H%M")
    file = f"{stamp}-{slug(title)}.md"
    n = 2
    while os.path.exists(os.path.join(stories_dir(agent.id), file)):
        file, n = f"{stamp}-{slug(title)}-{n}.md", n + 1
    meta = {"title": title, "theme": theme, "teaser": why, "status": "new",
            "found": time.strftime("%Y-%m-%d %H:%M"), "agent": agent.name, "kind": "lead"}
    if earlier:
        meta["follows"] = earlier.file
    Story(agent.id, file, meta, body).save()
    remember(agent.id, [url])
    mine.add(plain(url))
    saved.append(file)
    say(f"  lead: {title} [{theme}]" + ("" if plain(url) in read else " (from results)")
        + (f" ↳ follows {earlier.title}" if earlier else ""))
    return f"Saved lead \"{title}\"." + (f" It follows up \"{earlier.title}\"." if earlier else "")


def earlier_report(aid, title, saved=()):
    """The earlier report (not one of this run's) a lead says it follows, by its title, or None."""
    want = " ".join(str(title or "").split()).lower().strip(" \"'")
    if not want:
        return None
    known = [s for s in stories(aid) if s.file not in saved]
    return next((s for s in known if s.title.lower() == want), None) or \
        next((s for s in known if want in s.title.lower() or s.title.lower() in want), None)


def research_dir(aid):
    return os.path.join(folder(aid), "research")


def note_source(aid, cards_file, cards, a, fetched, dates, say):
    """Carry out note_source: the card goes into the list and its file at once (a run that
    breaks off keeps them)."""
    url = str(a.get("url", "")).strip().strip("<>()[]")
    if plain(url) not in {plain(u) for u in fetched}:
        return "Not noted: you didn't read that page in this research. Read it with read_web_page first."
    if any(plain(c["url"]) == plain(url) for c in cards):
        return "You noted that page already."
    says = str(a.get("says", "")).strip()
    if not says:
        return "Not noted: say what the page says about the topic."
    kind = str(a.get("kind", "other")).lower()
    date = str(a.get("date", "")).strip() or next((d for u, d in dates.items() if plain(u) == plain(url)), "")
    cards.append({"url": url, "title": " ".join(str(a.get("title", "")).split()) or url,
                  "kind": kind if kind in SOURCE_KINDS else "other", "date": date, "says": says})
    os.makedirs(research_dir(aid), exist_ok=True)
    with open(cards_file + ".new", "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=1)
    os.replace(cards_file + ".new", cards_file)
    say(f"  source [{len(cards)}]: {cards[-1]['title'][:90]} ({cards[-1]['kind']})")
    return f"Noted as source [{len(cards)}]."


def numbers(values, cards):
    out = []
    for v in values or []:
        try:
            n = int(str(v).strip("[] "))
        except ValueError:
            continue
        if 1 <= n <= len(cards) and n not in out:
            out.append(n)
    return out


def sources_md(cards):
    return "\n\n".join(f"{n}. **{c['title']}** · {site(c['url'])}" + (f" · {c['date']}" if c.get("date") else "")
                        + f" · {c['kind']}\n   <{c['url']}>\n   {' '.join(c['says'].split())}"
                        for n, c in enumerate(cards, 1))


def save_dossier(agent, topic, cards, cards_file, a, saved, say):
    """The dossier as a report (kind: dossier); its cards stay next to it for writing articles."""
    a = a or {}
    title = " ".join(str(a.get("title", "")).split()) or topic
    parts = []
    for head, key in (("What is solid", "solid"), ("Disputed or only claimed", "disputed"),
                      ("Open questions", "questions")):
        text = str(a.get(key, "")).strip()
        if text:
            parts.append(f"## {head}\n\n{text}")
    ideas = [x for x in (a.get("articles") or []) if isinstance(x, dict) and str(x.get("title", "")).strip()]
    if ideas:
        lines = []
        for n, x in enumerate(ideas, 1):
            line = f"{n}. **{' '.join(str(x['title']).split())}**: {' '.join(str(x.get('angle', '')).split())}"
            if str(x.get("hook", "")).strip():
                line += f"\n   Hook: {' '.join(str(x['hook']).split())}"
            nums = numbers(x.get("sources"), cards)
            if nums:
                line += "\n   Sources: " + ", ".join(f"[{k}]" for k in nums)
            lines.append(line)
        parts.append("## Proposed articles\n\n" + "\n\n".join(lines))
    if not a:
        parts.append("*The research ended before the dossier was written: these are its sources.*")
    parts.append("## Sources\n\n" + (sources_md(cards) or "None noted."))
    stamp = time.strftime("%Y%m%d-%H%M")
    file = f"{stamp}-{slug(title)}.md"
    n = 2
    while os.path.exists(os.path.join(stories_dir(agent.id), file)):
        file, n = f"{stamp}-{slug(title)}-{n}.md", n + 1
    Story(agent.id, file, {"title": title, "theme": "Research", "topic": topic,
                           "teaser": f"{topic}: {len(cards)} sources, {len(ideas)} article ideas.",
                           "status": "new", "found": time.strftime("%Y-%m-%d %H:%M"), "agent": agent.name,
                           "kind": "dossier", "cards": os.path.basename(cards_file)},
          "\n\n".join(parts)).save()
    saved.append(file)
    say(f"  dossier: {title} ({len(cards)} sources, {len(ideas)} article ideas)")
    return file


def write_articles(agent, persona, tell, saved, started, dossier_file, count, wish=""):
    """Articles from a dossier, in the persona's voice: each a report (kind: article)."""
    dossier = next((x for x in stories(agent.id) if x.file == dossier_file), None)
    if dossier is None:
        raise OSError(f"no dossier {dossier_file}")
    try:
        with open(os.path.join(research_dir(agent.id), dossier.meta.get("cards", "")), encoding="utf-8") as f:
            cards = json.load(f)
    except (OSError, ValueError):
        cards = []
    text = dossier.body
    if cards:
        # The full source cards, numbered as in the dossier.
        text = text.split("## Sources")[0].rstrip() + "\n\n## Sources\n\n" + sources_md(cards)
    topic = dossier.meta.get("topic", dossier.title)
    parts = [persona.system.strip(), facts.for_prompt(persona.id),
             ARTICLES.format(count=count, s="" if count == 1 else "s", wish=f" ({wish})" if wish else "", dossier=f"# {dossier.title}\n\n{text}")]
    messages = [{"role": "system", "content": "\n\n".join(p for p in parts if p)},
                {"role": "user", "content": f"Write the {'article' if count == 1 else f'{count} articles'} now."}]
    controls = {"style": "Creative", "thinking": agent.thinking}
    tries = 0
    while len(saved) < count and tries < count + 4 and time.time() - started < agent.limits["minutes"] * 60:
        tries += 1
        request = chat.request_for(persona, messages, controls, [ARTICLE_TOOL])
        said = {"text": "", "thinking": ""}
        ended, calls = chat.stream(request, lambda kind, part: said.__setitem__(kind, said[kind] + part))
        if not calls:
            messages.append({"role": "assistant", "content": said["text"]})
            messages.append({"role": "user", "content": f"Save the article{'s' if count > 1 else ''} with save_article: "
                             f"{count - len(saved)} still to go."})
            tell("  answered without saving" + (" (cut off)" if ended == "length" else ""))
            continue
        messages.append({"role": "assistant", "content": said["text"], "tool_calls": [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
            for c in calls]})
        for c in calls:
            try:
                a = json.loads(c["arguments"] or "{}")
                if not isinstance(a, dict):
                    raise ValueError
            except ValueError:
                a = {}
            title = " ".join(str(a.get("title", "")).split())
            text_ = str(a.get("text", "")).strip()
            if c["name"] != "save_article" or not title or not text_:
                result = "Not saved: an article needs a title, a teaser and the text."
            else:
                nums = numbers(a.get("sources"), cards)
                used = "\n\n## Sources\n\n" + "\n".join(f"{k}. <{cards[k - 1]['url']}>" for k in nums) if nums else ""
                stamp = time.strftime("%Y%m%d-%H%M")
                file = f"{stamp}-{slug(title)}.md"
                n = 2
                while os.path.exists(os.path.join(stories_dir(agent.id), file)):
                    file, n = f"{stamp}-{slug(title)}-{n}.md", n + 1
                Story(agent.id, file, {"title": title, "theme": "Article", "topic": topic,
                                       "teaser": " ".join(str(a.get("teaser", "")).split()), "status": "new",
                                       "found": time.strftime("%Y-%m-%d %H:%M"), "agent": agent.name,
                                       "kind": "article", "by": persona.name, "dossier": dossier_file},
                      text_ + used).save()
                saved.append(file)
                tell(f"  article: {title} ({len(text_.split())} words)")
                result = f"Saved. {count - len(saved)} still to go." if len(saved) < count else \
                    "All saved. Finish with one short line."
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
    return f"{len(saved)} article{'' if len(saved) == 1 else 's'} by {persona.name}"


def results_of(text):
    """{plain address: (title, snippet)} from what web_search gave the model."""
    found = {}
    for block in text.split("\n\n")[1:]:
        lines = block.split("\n")
        if len(lines) >= 2 and lines[1].startswith(("http://", "https://")):
            title = re.sub(r"^\d+\.\s*", "", lines[0])
            found[plain(lines[1])] = (title, " ".join(" ".join(lines[2:]).split())[:300])
    return found


def run(agent, say=print, take=False, topic=None, articles=None):
    """One hunt; with a topic a research; with articles ((dossier file, persona id, how many))
    articles from a dossier. Returns 0 when it ran (with or without stories), 1 when it couldn't,
    3 when another model is loaded (try later)."""
    if running(agent.id):
        say(f"{agent.name} is running already.")
        return 1
    started = time.time()
    os.makedirs(os.path.join(folder(agent.id), "runs"), exist_ok=True)
    log_path = os.path.join(folder(agent.id), "runs", time.strftime("%Y%m%d-%H%M%S") + ".txt")
    log = open(log_path, "w", encoding="utf-8")

    def tell(text):
        log.write(text + "\n")
        log.flush()
        say(text)

    before = state(agent.id)
    save_state(agent.id, pid=os.getpid(), started=started, result="running", log=os.path.basename(log_path),
               ended=None, stories=None)
    ours = False
    saved = []
    result = "broken off"
    try:
        pid = articles[1] if articles else agent.persona
        persona = personas.Persona.read(pid) if pid and \
            os.path.isfile(os.path.join(personas.folder(pid), personas.PERSONA_FILE)) else None
        if articles and persona is None:
            raise OSError(f"no persona {pid}")
        tell(f"{agent.name}, {time.strftime('%Y-%m-%d %H:%M')}" + (f", as {persona.name}" if persona else "")
             + (f": research on {topic}" if topic else "") + (f": articles from {articles[0]}" if articles else ""))
        try:
            launcher, ours = model_for(agent, take, tell)
        except Busy as e:
            tell(f"Not now: {e}. It tries again at the next check.")
            result = "waiting"
            return 3
        if launchers.can_use_tools(launcher.model) is False:
            raise OSError(f"{launcher.name} can't use tools (its chat template doesn't know them)")
        tell(f"Model: {launcher.name}")
        if articles:
            result = write_articles(agent, persona, tell, saved, started, articles[0], articles[2],
                                    articles[3] if len(articles) > 3 else "")
            return 0
        with (web.via(agent.search_from, tell) if agent.search_from else contextlib.nullcontext()):
            # Its own voice unless an old agent file names a persona to run as.
            result = hunt(agent, persona or agent.own(agent.voice), launcher, tell, saved, started, topic)
        return 0
    except (OSError, ValueError) as e:
        tell(f"Couldn't run: {e}")
        result = f"failed: {e}"
        return 1
    except KeyboardInterrupt:
        tell("Stopped.")
        result = "stopped"
        return 1
    finally:
        if ours:
            loaded = chat.loaded_launcher()
            if loaded is not None and loaded.name in agent.models:
                server.llama_stop(tell)
        tell(f"Done: {result}")
        log.close()
        if result == "waiting":
            # A run that didn't happen: the schedule asks again next time, and the old log stays.
            os.remove(log_path)
            save_state(agent.id, pid=None, started=before.get("started"), log=before.get("log"),
                       result="waiting", waiting_since=started)
        else:
            save_state(agent.id, pid=None, ended=time.time(), result=result, stories=len(saved),
                       waiting_since=None)
        tidy_runs(agent.id)


def research_prompt(agent, persona, topic):
    parts = [persona.system.strip() if persona else "", facts.for_prompt(persona.id) if persona else ""]
    parts.append(RESEARCH.format(topic=topic, task=agent.task.strip() or "articles", today=time.strftime("%A, %d %B %Y"),
                                 **agent.limits))
    return "\n\n".join(p for p in parts if p)


def hunt(agent, persona, launcher, tell, saved, started, topic=None):
    """A hunt for leads or stories, or with a topic a research that ends in a dossier."""
    if topic is None:
        return work(agent, persona, tell, saved, started)
    cards = []
    cards_file = os.path.join(research_dir(agent.id), time.strftime("%Y%m%d-%H%M%S") + f"-{slug(topic)}.json")
    done = []
    try:
        work(agent, persona, tell, saved, started, topic, cards, cards_file, done)
    finally:
        if not done and cards:
            save_dossier(agent, topic, cards, cards_file, None, saved, tell)
    return f"a dossier, {len(cards)} sources" if saved else "no sources found"


def work(agent, persona, tell, saved, started, topic=None, cards=None, cards_file=None, done=None):
    research = topic is not None
    left = dict(agent.limits)
    fetched = set()
    dates = {}
    pages = []      # the addresses read, in order: shown to it when a story is refused
    results = {}    # what its searches found, and the links on the pages it read, for leads
    mine = set()    # the addresses of the leads saved in this hunt
    messages = [{"role": "system", "content": research_prompt(agent, persona, topic) if research
                  else system_prompt(agent, persona)},
                {"role": "user", "content": "Start the research now." if research else "Start the hunt now."}]
    controls = {"style": agent.style, "thinking": agent.thinking}
    nudged = False
    cut = 0
    while True:
        out_of_time = time.time() - started > agent.limits["minutes"] * 60
        tools = [] if out_of_time or left["steps"] <= 0 else tools_for(left, topic)
        if research and not done and cards and not tools and left["steps"] > -2:
            # Out of time or steps: one more go, only to write the dossier.
            left["steps"] -= 1
            tools = [DOSSIER_TOOL]
            messages.append({"role": "user", "content": "Time is up. Write the dossier now with finish_dossier."})
        if research and done:
            tools = []
        body = chat.request_for(persona, cut_old_pages(messages), controls, tools)
        body["max_tokens"] = STEP_TOKENS
        text = {"text": "", "thinking": ""}
        ended, calls = chat.stream(body, lambda kind, part: text.__setitem__(kind, text[kind] + part))
        if text["thinking"].strip():
            tell("  (thought " + str(len(text["thinking"].split())) + " words)")
        if not calls and ended == "length" and tools and cut < 3:
            cut += 1
            left["steps"] -= 1
            tell("  cut off: it thought too long")
            if text["text"].strip():
                messages.append({"role": "assistant", "content": text["text"]})
            messages.append({"role": "user", "content": "You thought too long and were cut off. Decide now and make "
                             "your next tool call at once, with little thinking: save what you have found, or the next "
                             "search or page."})
            continue
        if not calls:
            line = " ".join(text["text"].split())
            if research and not done and cards and not nudged and tools:
                nudged = True
                messages.append({"role": "assistant", "content": text["text"]})
                messages.append({"role": "user", "content": "You haven't written the dossier yet. Search and read "
                                 "more if you need to, then write it with finish_dossier."})
                continue
            if research:
                tell(f"Its word: {line[:500]}" if line else f"It ended ({ended}).")
                return ""
            if not saved and not nudged and tools and left["stories"] > 0:
                nudged = True
                tell(f"  answered without a story: {line[:200]}")
                messages.append({"role": "assistant", "content": text["text"]})
                messages.append({"role": "user", "content": "You haven't saved a story yet. Search and read some "
                                 "more and save the best finds with " + ("save_lead" if left["sites"] <= 1 else "save_story")
                                 + ". If there really is nothing, say so in one line."})
                continue
            tell(f"Its word: {line[:500]}" if line else f"It ended ({ended}).")
            if left["sites"] <= 1:
                return f"{len(saved)} lead{'' if len(saved) == 1 else 's'}"
            return f"{len(saved)} stor{'y' if len(saved) == 1 else 'ies'}"
        messages.append({"role": "assistant", "content": text["text"], "tool_calls": [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
            for c in calls]})
        for c in calls:
            left["steps"] -= 1
            try:
                a = json.loads(c["arguments"] or "{}")
                if not isinstance(a, dict):
                    raise ValueError
            except ValueError:
                a = None
            if a is None:
                result = "The arguments were not valid JSON. Call the tool again with proper arguments."
            elif c["name"] == "web_search":
                query = str(a.get("query", ""))
                if left["searches"] <= 0:
                    result = "No searches left. Read what you found, or save your stories."
                elif not research and old_years(query):
                    tell(f"  sent back: {query} (old year)")
                    result = (f"Not searched: it is {datetime.date.today().year} now, not "
                              f"{', '.join(map(str, old_years(query)))}. Search again with the right date.")
                else:
                    left["searches"] -= 1
                    tell(f"  search: {a.get('query', '')}")
                    result = web.run("web_search", a)
                    results.update(results_of(result))
            elif c["name"] == "read_web_page":
                if left["pages"] <= 0:
                    result = "No more pages to read. Save your stories with what you have."
                else:
                    left["pages"] -= 1
                    tell(f"  read: {a.get('url', '')}")
                    try:
                        url = str(a.get("url", ""))
                        links = []
                        result = web.read_page(url, a.get("part", 1), fetched, dates,
                                               links if not research and left["sites"] <= 1 else None)
                        if links:
                            # A digest or front page: its articles may become leads.
                            results.update({plain(h): (t, "") for t, h in links})
                            result += "\n\nArticles linked on this page (you may save these as leads):\n" + \
                                "\n".join(f"- {t}\n  {h}" for t, h in links[:40])
                        if url in fetched and url not in pages and not front_page(url):
                            pages.append(url)
                        if front_page(str(a.get("url", ""))):
                            result = ("[A front page: use it to find articles and open them; it can't be a "
                                      "source itself.]\n" + result)
                    except (OSError, ValueError, TypeError) as e:
                        result = f"That didn't work: {e}"
            elif c["name"] == "note_source" and research:
                result = note_source(agent.id, cards_file, cards, a, fetched, dates, tell)
            elif c["name"] == "finish_dossier" and research:
                if not cards:
                    result = "Not yet: you have noted no sources. Read pages and note them with note_source first."
                elif done:
                    result = "The dossier is written. Finish with one short line."
                else:
                    done.append(save_dossier(agent, topic, cards, cards_file, a, saved, tell))
                    result = "The dossier is saved. Finish with one short line, without a tool call."
            elif c["name"] == "save_lead":
                if left["stories"] <= 0:
                    result = "That's enough leads for this time. Finish with one short line."
                else:
                    result = save_lead(agent, a, fetched, results, dates, saved, tell, mine)
                    if result.startswith("Saved"):
                        left["stories"] -= 1
                    else:
                        tell(f"  refused: {result} ({a.get('url', '')})")
            elif c["name"] == "save_story":
                if left["stories"] <= 0:
                    result = "That's enough stories for this time. Finish with one short line."
                else:
                    result = save_story(agent, a, fetched, saved, tell, dates)
                    if result.startswith("Saved"):
                        left["stories"] -= 1
                    else:
                        given = a.get("sources") or []
                        tell(f"  refused: {result} Sources given: {', '.join(map(str, given)) if isinstance(given, list) else given}")
                        # Models often leave out an address they did read: show them what they have.
                        if pages:
                            result += ("\nThe articles you have read in this hunt (copy the addresses exactly):\n"
                                       + "\n".join(f"- {u}" + (f" (published {dates[u]})" if u in dates else "")
                                                    for u in pages))
            else:
                result = f"There is no tool called {c['name']}."
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": result, "name": c["name"]})
        if left["steps"] <= 0:
            messages.append({"role": "user", "content": "That was the last tool call. Finish with one short line."})


def tidy_runs(aid, keep=50):
    where = os.path.join(folder(aid), "runs")
    try:
        names = sorted(n for n in os.listdir(where) if n.endswith(".txt"))
    except OSError:
        return
    for n in names[:-keep]:
        os.remove(os.path.join(where, n))


def last_log(aid):
    """The text of the newest run, or ""."""
    where = os.path.join(folder(aid), "runs")
    try:
        name = max(n for n in os.listdir(where) if n.endswith(".txt"))
        with open(os.path.join(where, name), encoding="utf-8", errors="replace") as f:
            return f.read()
    except (OSError, ValueError):
        return ""


def detach(aid, take=False, extra=()):
    """Start a run on its own (it goes on when the window closes). extra: --topic TEXT, or
    --articles FILE --persona ID --count N [--wish TEXT]."""
    os.makedirs(os.path.join(folder(aid), "runs"), exist_ok=True)
    with open(os.path.join(folder(aid), "runs", "detached.out"), "w") as out:
        subprocess.Popen([sys.executable, MAIN, "agent", "run", aid] + list(extra) + (["--take-model"] if take else []),
                         stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)


def stop(aid):
    pid = state(aid).get("pid")
    if pid:
        os.kill(pid, signal.SIGINT)


# --- what another machine asks ------------------------------------------------------

def export():
    """Everything about this machine's agents as one JSON-able dict (aicove agent export)."""
    found = []
    for a in load_all().values():
        st = state(a.id)
        found.append({"id": a.id, "text": a.text(), "state": st, "due": describe_schedule(a.schedule),
                      "log": last_log(a.id)[-6000:],
                      "stories": [{"file": s.file, "text": s.text()} for s in stories(a.id)]})
    return {"agents": found, "cron": cron_installed(),
            "personas": {p.id: p.name for p in personas.load_all().values()},
            "launchers": [l.name for l in launchers.find_launchers() if not l.error]}


# --- aicove agent ---------------------------------------------------------------------

USAGE = """aicove agent                        this machine's agents and how they last ran
aicove agent run ID [--take-model]  run one now and wait (--take-model: stop another loaded model)
aicove agent start ID [--take-model]  run one on its own, in the background
    run/start also take --topic "TEXT" (research a topic: sources and a dossier)
    or --articles FILE --persona ID [--count N] [--wish "TEXT"] (articles from a dossier, in that
    persona's voice; the wish like "in German, about 800 words each")
aicove agent stop ID                stop a run
aicove agent due                    run the agents whose time has come (cron, every 10 minutes)
aicove agent stories [ID]           the reports, newest first
aicove agent mark ID FILE STATUS    new | kept | used | dismissed
aicove agent write ID               take the agent file from standard input
aicove agent delete ID              into the agents' trash
aicove agent export                 everything as JSON (what another machine asks)
aicove agent schedule on|off        the crontab line that runs "due\""""


def main(args):
    cmd, rest = (args[0], args[1:]) if args else ("list", [])
    take = "--take-model" in rest
    rest = [r for r in rest if r != "--take-model"]
    opts = {}
    for key in ("--topic", "--articles", "--persona", "--count", "--wish"):
        if key in rest:
            i = rest.index(key)
            if i + 1 >= len(rest):
                print(f"{key} needs a value.")
                return 2
            opts[key] = rest[i + 1]
            del rest[i:i + 2]
    topic = " ".join(opts.get("--topic", "").split()) or None
    articles = None
    if "--articles" in opts:
        if "--persona" not in opts or not SAFE_ID.fullmatch(opts["--persona"]):
            print("--articles needs --persona ID.")
            return 2
        count = int(opts["--count"]) if opts.get("--count", "").isdigit() else 3
        articles = (os.path.basename(opts["--articles"]), opts["--persona"], max(1, min(count, 10)),
                    " ".join(opts.get("--wish", "").split()))
    known = load_all()

    def one():
        if not rest or rest[0] not in known:
            print(f"No agent called {rest[0] if rest else '?'}." + (f" There are: {', '.join(known)}" if known else ""))
            return None
        return known[rest[0]]

    if cmd == "list" and not rest:
        if not known:
            print(f"No agents yet ({AGENT_DIR}).")
        for a in known.values():
            st = state(a.id)
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(st["started"])) if st.get("started") else "never"
            print(f"{a.id:24} {describe_schedule(a.schedule):30} last: {when}, {st.get('result', '-')}")
        print("Schedule: " + ("on (crontab)" if cron_installed() else "off — aicove agent schedule on"))
        return 0
    if cmd in ("run", "start") and len(rest) == 1:
        a = one()
        if a is None:
            return 1
        if cmd == "start":
            if running(a.id):
                print(f"{a.name} is running already.")
                return 1
            extra = (["--topic", topic] if topic else []) + (
                ["--articles", articles[0], "--persona", articles[1], "--count", str(articles[2])]
                + (["--wish", articles[3]] if articles[3] else []) if articles else [])
            detach(a.id, take, extra)
            print(f"{a.name} started.")
            return 0
        return run(a, take=take, topic=topic, articles=articles)
    if cmd == "stop" and len(rest) == 1:
        a = one()
        if a is None:
            return 1
        stop(a.id)
        return 0
    if cmd == "due" and not rest:
        # One at a time: they share the one model.
        for a in known.values():
            if is_due(a):
                run(a)
        return 0
    if cmd == "stories" and len(rest) <= 1:
        for aid in ([rest[0]] if rest else known):
            for s in stories(aid):
                print(f"{s.found:16}  {s.status:9} {aid:16} [{s.theme}] {s.title}  ({s.file})")
        return 0
    if cmd == "mark" and len(rest) == 3:
        a = one()
        if a is None:
            return 1
        try:
            mark(a.id, rest[1], rest[2])
        except (OSError, ValueError) as e:
            print(e)
            return 1
        return 0
    if cmd == "write" and len(rest) == 1 and SAFE_ID.fullmatch(rest[0]):
        Agent.parse(rest[0], sys.stdin.read()).write()
        return 0
    if cmd == "delete" and len(rest) == 1:
        a = one()
        if a is None:
            return 1
        delete(a.id)
        return 0
    if cmd == "export" and not rest:
        print(json.dumps(export(), ensure_ascii=False))
        return 0
    if cmd == "schedule" and rest in (["on"], ["off"]):
        try:
            set_cron(rest[0] == "on")
        except (OSError, subprocess.SubprocessError) as e:
            print(f"Can't change the crontab: {e}")
            return 1
        print("Schedule " + ("on: aicove agent due runs every 10 minutes." if rest[0] == "on" else "off."))
        return 0
    print(USAGE)
    return 0 if cmd in ("help", "-h", "--help") else 2
