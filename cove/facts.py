# A persona's facts: what it carries from one chat to the next. Four files per persona, one per
# section, in personas/<id>/facts/ (about-you.md, story-so-far.md, world.md, style-and-rules.md),
# one fact per line ("- " in front is fine). They go into the system prompt of every chat with
# that persona. New facts only come in through the
# user: "Remember…" lets the model propose some from a chat, and the user keeps, changes or drops
# each one; the file can be edited any time.

import json
import os
import re
import urllib.request

from . import personas, summary, turns
from .settings import S

FOLDER = "facts"
SECTIONS = ["About you", "Story so far", "World", "Style and rules"]
HINTS = {
    "About you": "the user: name, life, likes, wishes, what they told about themselves",
    "Story so far": "what happened between you and the user, or in a story you tell together",
    "World": "places, people, things and how they work, in the user's life or in the story",
    "Style and rules": "how the user wants you to talk and behave",
}
HEADER = "[What you know from earlier chats with the user. It is true unless the user says otherwise.]"
# A chat is shown to the model in at most this many characters when it proposes facts.
TRANSCRIPT_CHARS = 40_000

PROPOSE = """You keep the long-term memory of an AI persona called {name}. Below are the facts it \
already remembers and a chat it just had with the user. List what from this chat is worth \
remembering in later chats and is not already in the facts: lasting things, not small talk or \
what was only true for a moment. A fact that changes one already there is listed again in its \
new form. Each fact is one short sentence, written from the persona's point of view ("The user \
lives in Graz.", "We agreed to …").

Sections:
{sections}

Answer with JSON only: a list of objects {{"section": one of the section names, "fact": the \
sentence}}. At most 12. An empty list [] if nothing is worth keeping. Record faithfully whatever \
the content is, without judging or softening."""


def path(pid, section):
    name = re.sub(r"[^a-z0-9]+", "-", section.lower()).strip("-")
    return os.path.join(personas.folder(pid), FOLDER, name + ".md")


def lines_of(text):
    """The facts of a section's text: one per line, without "- " in front."""
    return [re.sub(r"^\s*[-*]\s+", "", line).strip() for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def read(pid):
    """{section: [facts]} of a persona, every section there, maybe empty."""
    found = {}
    for section in SECTIONS:
        try:
            with open(path(pid, section), encoding="utf-8") as f:
                found[section] = lines_of(f.read())
        except OSError:
            found[section] = []
    return found


def write(pid, section, items):
    """The facts of one section; no facts = no file."""
    p = path(pid, section)
    if not items:
        if os.path.exists(p):
            os.remove(p)
        return
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".new", "w", encoding="utf-8") as f:
        f.write("".join(f"- {fact}\n" for fact in items))
    os.replace(p + ".new", p)


def add(pid, items):
    """Add [(section, fact)] to a persona's facts."""
    known = read(pid)
    changed = set()
    for section, fact in items:
        fact = " ".join(fact.split())
        if fact and section in known and fact not in known[section]:
            known[section].append(fact)
            changed.add(section)
    for section in changed:
        write(pid, section, known[section])


def as_text(known):
    """{section: [facts]} as one text, the way the model gets it."""
    return "\n\n".join(f"## {s}\n" + "\n".join(f"- {f}" for f in known[s]) for s in SECTIONS if known.get(s))


def for_prompt(pid):
    """What goes into the system prompt, or "" without facts."""
    text = as_text(read(pid))
    return f"{HEADER}\n{text}" if text else ""


def transcript(chat):
    """The chat as text for the model: its summary, then the messages after it, the newest
    part if it is too long."""
    covered, so_far = summary.current(chat)
    lines = [summary.line_of(m) for m in chat.messages[covered:] if m["role"] in ("user", "assistant") and m["content"]]
    text = "\n\n".join(lines)
    if len(text) > TRANSCRIPT_CHARS:
        text = "…\n" + text[-TRANSCRIPT_CHARS:]
    return (f"(Summary of the earlier part: {so_far})\n\n" if so_far else "") + text


def propose(persona, chat):
    """[(section, fact)] the model thinks are worth keeping from this chat. Raises OSError."""
    known = as_text(read(persona.id)) or "(none yet)"
    request = {
        "messages": [
            {"role": "system", "content": PROPOSE.format(
                name=persona.name, sections="\n".join(f"- {s}: {HINTS[s]}" for s in SECTIONS))},
            {"role": "user", "content": f"Facts it remembers now:\n{known}\n\nThe chat:\n{transcript(chat)}\n\n"
                                        "The JSON list now."}],
        "temperature": 0.2, "max_tokens": 1500, "stream": False,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(S.llama_url() + "/v1/chat/completions", json.dumps(turns.body_in_turns(request)).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        data = json.loads(r.read())
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    found = re.search(r"\[.*\]", text, flags=re.S)
    if not found:
        raise OSError("the model gave no list of facts")
    try:
        items = json.loads(found.group(0))
    except ValueError:
        raise OSError("the model's list of facts could not be read") from None
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and str(item.get("fact", "")).strip():
            section = str(item.get("section", "")).strip()
            section = next((s for s in SECTIONS if s.lower() == section.lower()), SECTIONS[0])
            out.append((section, " ".join(str(item["fact"]).split())))
    return out[:12]
