# AIcove's own chat, without a window: a persona's chats as files, and talking to whatever
# llama-server has loaded. The Chat tab (gui_chat.py) and "aicove chat" both use this.
#   aicove chat PERSONA                  the persona's chats, newest first
#   aicove chat PERSONA "message"        a new chat with that first message
#   aicove chat PERSONA ID "message"     go on with chat ID (ID may be "last")
# A chat is personas/<id>/chats/<chat id>.json: title, when it was started, and the messages,
# each with role, content, time, and for replies the model and the thinking (shown, never sent
# back to the model).

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

from . import facts, files, knowledge, launchers, personas, pictures, profiles, server, summary, voice, web
from .settings import S

CHAT_ID = re.compile(r"\d{8}-\d{6}(-\d+)?")
# Settings of a persona that go to llama-server as they are (thinking goes its own way).
SENT = [k for k in personas.SAMPLERS if k != "thinking"]


def chats_dir(pid):
    return os.path.join(personas.folder(pid), "chats")


class Chat:
    def __init__(self, pid, cid, title="", created=None, messages=None, summary=None, controls=None):
        self.persona = pid
        self.id = cid
        self.title = title
        self.created = created or time.time()
        self.messages = list(messages or [])
        # The summary of the older messages, once the chat outgrew the context (summary.py).
        self.summary = dict(summary or {})
        # The controls set for this chat: {"style", "thinking", "length"}, missing = as set.
        self.controls = dict(controls or {})

    @property
    def path(self):
        return os.path.join(chats_dir(self.persona), self.id + ".json")

    @classmethod
    def new(cls, pid):
        cid = time.strftime("%Y%m%d-%H%M%S")
        base, n = cid, 2
        while os.path.exists(os.path.join(chats_dir(pid), cid + ".json")):
            cid, n = f"{base}-{n}", n + 1
        return cls(pid, cid)

    @classmethod
    def load(cls, pid, cid):
        with open(os.path.join(chats_dir(pid), cid + ".json"), encoding="utf-8") as f:
            data = json.load(f)
        return cls(pid, cid, data.get("title", ""), data.get("created"), data.get("messages", []),
                   data.get("summary"), data.get("controls"))

    def save(self):
        os.makedirs(chats_dir(self.persona), exist_ok=True)
        if not self.title:
            first = next((m["content"] for m in self.messages if m["role"] == "user"), "")
            self.title = title_of(first)
        data = {"title": self.title, "created": self.created, "messages": self.messages}
        if self.summary:
            data["summary"] = self.summary
        if any(self.controls.values()):
            data["controls"] = {k: v for k, v in self.controls.items() if v}
        tmp = self.path + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)
        voice.tidy(self.persona)
        pictures.tidy(self.persona)

    def delete(self):
        """Into the persona's trash, like a deleted persona. Its audio is deleted."""
        trash = os.path.join(personas.TRASH_DIR, "chats", self.persona)
        os.makedirs(trash, exist_ok=True)
        os.replace(self.path, os.path.join(trash, self.id + ".json"))
        voice.tidy(self.persona)
        pictures.tidy(self.persona)

    def add(self, role, content, **extra):
        if role == "user":
            # Earlier answers can only be switched at the end of the chat; it goes on now.
            for m in self.messages:
                m.pop("answers", None)
                m.pop("shown", None)
        message = {"role": role, "content": content, "time": time.time(), **extra}
        self.messages.append(message)
        return message

    # Earlier answers to the last message of the user's are kept with it: "answers" holds the
    # other versions (each a list of messages: tool steps and the answer), "shown" says where the
    # one in the chat stands among all of them.

    def asked(self):
        """The index of the user's last message, or None."""
        return next((i for i in range(len(self.messages) - 1, -1, -1) if self.messages[i]["role"] == "user"), None)

    def versions(self):
        """(which one is shown, how many there are) of the answers to the last question."""
        u = self.asked()
        if u is None or not self.messages[u].get("answers") or u == len(self.messages) - 1:
            return 0, 1
        question = self.messages[u]
        return min(question.get("shown", 0), len(question["answers"])), len(question["answers"]) + 1

    def put_aside(self):
        """Keep the answer to the last question as an earlier version, for a new one to come."""
        u = self.asked()
        if u is None or u == len(self.messages) - 1:
            return
        question = self.messages[u]
        others = question.setdefault("answers", [])
        others.insert(min(question.get("shown", len(others)), len(others)), self.messages[u + 1:])
        question["shown"] = len(others)
        del self.messages[u + 1:]

    def new_version(self):
        """A new answer to the last question is coming: it comes after the earlier ones."""
        u = self.asked()
        if u is not None and u == len(self.messages) - 1 and self.messages[u].get("answers"):
            self.messages[u]["shown"] = len(self.messages[u]["answers"])

    def show_version(self, k):
        """Show answer k (0 = the first) to the last question instead of the one shown."""
        u = self.asked()
        shown, count = self.versions()
        if u is None or count < 2 or not 0 <= k < count or k == shown:
            return
        question = self.messages[u]
        every = list(question["answers"])
        every.insert(shown, self.messages[u + 1:])
        now = every.pop(k)
        question["answers"], question["shown"] = every, k
        self.messages[u + 1:] = now

    def no_empty_version(self):
        """When a new answer came to nothing, show the one before it again."""
        u = self.asked()
        if u is not None and u == len(self.messages) - 1 and self.messages[u].get("answers"):
            question = self.messages[u]
            self.messages[u + 1:] = question["answers"].pop()
            question["shown"] = len(question["answers"])
            if not question["answers"]:
                del question["answers"], question["shown"]


def title_of(text, length=60):
    # Without the Markdown marks: **bold** reads as bold, not as stars, in a list of titles.
    line = " ".join(re.sub(r"[*_`#>~]+", "", text).split())
    return line if len(line) <= length else line[:length].rsplit(" ", 1)[0] + "…"


def list_chats(pid):
    """The persona's chats as (id, title, last changed), newest first. Titles are read from
    the files, which stays quick for a few hundred chats."""
    found = []
    try:
        names = os.listdir(chats_dir(pid))
    except OSError:
        return []
    for name in names:
        cid = name[:-len(".json")]
        if not name.endswith(".json") or not CHAT_ID.fullmatch(cid):
            continue
        path = os.path.join(chats_dir(pid), name)
        try:
            with open(path, encoding="utf-8") as f:
                title = json.load(f).get("title", "")
            found.append((cid, title or cid, os.path.getmtime(path)))
        except (OSError, ValueError):
            continue
    return sorted(found, key=lambda c: c[2], reverse=True)


# --- the model -------------------------------------------------------------------

def loaded_launcher():
    """The launcher whose model llama-server has loaded, or None."""
    now = server.running()
    if not now:
        return None
    return next((l for l in launchers.find_launchers() if not l.error and l.model == now[0]), None)


def launcher_for(persona):
    """The launcher to start a persona on here: the first of its models this machine has,
    or None."""
    for name in persona.models:
        launcher = launchers.get(name)
        if launcher is not None and not launcher.error and launcher.present:
            return launcher
    return None


def runs_on(persona, launcher):
    """Whether this persona may run on the loaded launcher (one of its models, or it names none)."""
    return launcher is not None and (not persona.models or launcher.name in persona.models)


# A reply may call tools this many times in a row before it has to answer.
MAX_ROUNDS = 12

# The controls above the chat's input: per chat, over the persona's own settings. "" = as set.
# The styles are the writing-style presets of the Launchers tab.
STYLES = {
    "Factual": {"temperature": 0.3, "dry_multiplier": 0.0, "xtc_probability": 0.0},
    "Creative": {"temperature": 1.0, "min_p": 0.05, "dry_multiplier": 0.8, "xtc_probability": 0.0},
    "Story": {"temperature": 1.1, "min_p": 0.05, "dry_multiplier": 0.8, "xtc_probability": 0.5,
              "xtc_threshold": 0.1},
}
LENGTHS = {"Short": 600, "Medium": 2000, "Long": 6000}


def request_for(persona, messages, controls=None, tools=()):
    """The body for llama-server's /v1/chat/completions: the persona's settings, then the
    chat's controls over them."""
    body = {"messages": messages, "stream": True}
    loaded = loaded_launcher()
    samplers = profiles.samplers_for(persona, loaded.name if loaded else "")
    for key in SENT:
        if key in samplers:
            value = samplers[key]
            body[key] = int(value) if key in personas.WHOLE else float(value)
    controls = controls or {}
    body.update(STYLES.get(controls.get("style"), {}))
    if controls.get("length") in LENGTHS:
        body["max_tokens"] = LENGTHS[controls["length"]]
    thinking = controls.get("thinking") or samplers.get("thinking")
    if thinking in ("on", "off"):
        body["chat_template_kwargs"] = {"enable_thinking": thinking == "on"}
    if tools:
        body["tools"] = list(tools)
    return body


def setup(persona, chat=None):
    """(system prompt, tools) for the next request: the persona's prompt, its facts, its
    knowledge (all of it, or a list of it with tools to look things up when it is too big), its
    file tools, and web search when the chat has it on. No tools for a model whose template
    doesn't know them."""
    loaded = loaded_launcher()
    tools_ok = loaded is None or launchers.can_use_tools(loaded.model) is not False
    try:
        n_ctx = summary.context_size()
    except (OSError, ValueError, KeyError):
        n_ctx = 8192
    parts = [persona.system.strip(), facts.for_prompt(persona.id)]
    known, look_up = knowledge.for_prompt(persona.id, n_ctx, tools_ok)
    parts.append(known)
    tools = list(files.tools_for(persona)) if tools_ok else []
    if tools:
        parts.append(files.HINT)
    if look_up:
        tools += knowledge.TOOLS
    if tools_ok and chat is not None and chat.controls.get("web") == "on":
        tools += web.TOOLS
        parts.append(web.HINT)
    if tools_ok and chat is not None and chat.controls.get("pictures") == "on":
        tools += pictures.TOOLS
        parts.append(pictures.HINT)
    return "\n\n".join(p for p in parts if p).strip(), tools


# A picture of yours goes along with your message if the model can see; else this note does.
NO_EYES = "[The user attached a picture here, but you can't see pictures.]"
PICTURE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def user_content(chat, m, eyes):
    """A message of the user's as the model gets it: with its picture if the model has eyes."""
    if not m.get("picture"):
        return m["content"]
    path = os.path.join(pictures.folder(chat.persona), m["picture"])
    kind = PICTURE_TYPES.get(os.path.splitext(path)[1].lower())
    if not eyes or not kind or not os.path.isfile(path):
        return (m["content"] + "\n\n" + NO_EYES).strip()
    with open(path, "rb") as f:
        url = f"data:{kind};base64," + base64.b64encode(f.read()).decode()
    content = [{"type": "image_url", "image_url": {"url": url}}]
    if m["content"]:
        content.append({"type": "text", "text": m["content"]})
    return content


def for_model(chat, system, eyes=False):
    """What the model gets: the system prompt, the summary of the older messages if there
    is one, then the conversation with its tool calls and their results (no thinking). Pictures
    of the user's go along when the model has eyes."""
    covered, text = summary.current(chat)
    if covered:
        system = (system + "\n\n" + summary.HEADER + "\n" + text).strip()
    sent = [{"role": "system", "content": system}] if system else []
    calls = set()
    for m in chat.messages[covered:]:
        if m["role"] == "user":
            sent.append({"role": "user", "content": user_content(chat, m, eyes)})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            sent.append({"role": "assistant", "content": m["content"], "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                for c in m["tool_calls"]]})
            calls |= {c["id"] for c in m["tool_calls"]}
        elif m["role"] == "assistant" and m["content"]:
            sent.append({"role": "assistant", "content": m["content"]})
        elif m["role"] == "tool" and m.get("tool_call_id") in calls:
            sent.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
    return sent


def stream(body, on_part, stop=lambda: False, timings=None):
    """Send a request to llama-server and hand every piece of the answer to on_part(kind, text),
    kind being "thinking" or "text". stop() is asked between pieces; when it says yes the answer
    ends there. timings (a dict) gets llama-server's numbers of the answer, if it sends them. Returns (why it ended: "stop", "length", "tool_calls", "stopped" (by us);
    the tool calls as [{id, name, arguments}]), or raises OSError."""
    req = urllib.request.Request(S.llama_url() + "/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=600)
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read())["error"]["message"]
        except (ValueError, KeyError, TypeError, OSError):
            detail = str(e)
        raise OSError(f"llama-server: {detail}") from None
    # Stays None if the answer just breaks off (llama-server stopped or crashed on the way).
    ended = None
    calls = {}
    # Some templates put the thinking into the text inside <think> ... </think>.
    in_think = None
    with r:
        for raw in r:
            if stop():
                return "stopped", []
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                ended = ended or "stop"
                break
            try:
                piece = json.loads(data)
                if timings is not None and isinstance(piece.get("timings"), dict):
                    timings.update(piece["timings"])
                choice = piece["choices"][0]
            except (ValueError, KeyError, IndexError, AttributeError):
                continue
            delta = choice.get("delta") or {}
            if delta.get("reasoning_content"):
                on_part("thinking", delta["reasoning_content"])
            # A tool call comes in pieces: its name first, the arguments bit by bit.
            for piece in delta.get("tool_calls") or []:
                call = calls.setdefault(piece.get("index", len(calls)), {"id": "", "name": "", "arguments": ""})
                call["id"] = piece.get("id") or call["id"]
                function = piece.get("function") or {}
                call["name"] += function.get("name") or ""
                call["arguments"] += function.get("arguments") or ""
            text = delta.get("content") or ""
            if text:
                if in_think is None:
                    in_think = text.lstrip().startswith("<think>")
                    if in_think:
                        text = text.lstrip()[len("<think>"):]
                if in_think:
                    head, closed, text = text.partition("</think>")
                    if head:
                        on_part("thinking", head)
                    if not closed:
                        continue
                    in_think = False
                    text = text.lstrip("\n")
                if text:
                    on_part("text", text)
            ended = choice.get("finish_reason") or ended
    found = [c for _, c in sorted(calls.items()) if c["name"]]
    for n, c in enumerate(found):
        c["id"] = c["id"] or f"call_{int(time.time())}_{n}"
    return ("tool_calls" if found else ended or "broken off"), found


def describe(call):
    if call["name"] in knowledge.NAMES:
        return knowledge.describe(call["name"], call["arguments"])
    if call["name"] in web.NAMES:
        return web.describe(call["name"], call["arguments"])
    if call["name"] in pictures.NAMES:
        return pictures.describe(call["name"], call["arguments"])
    return files.describe(call["name"], call["arguments"])


def reply(persona, chat, on_part, stop=lambda: False, ask=None):
    """Get the next answer for a chat whose last message is the user's (or an answer to go on
    with), add it to the chat and save. Tool calls are carried out on the way (ask(title,
    message) -> bool asks the user before a write) and kept in the chat. on_part(kind, text)
    gets "thinking" and "text" pieces, and ("round", "") when a new round after tools starts.
    Returns the last new message."""
    launcher = loaded_launcher()
    if launcher is not None:
        try:
            profiles.used(persona.id, launcher.name)
        except OSError:
            pass
    message = None
    for round_ in range(MAX_ROUNDS + 1):
        system, tools = setup(persona, chat)
        try:
            summary.keep_up(chat, system + json.dumps(tools), lambda text: on_part("status", text))
        except (OSError, ValueError, KeyError) as e:
            # Never block a chat because summarising failed: it goes on as it is.
            on_part("status", "")
            on_part("note", f"Couldn't summarise the older messages ({e}).")
        body = request_for(persona, for_model(chat, system, launcher is not None and launcher.sees),
                           chat.controls, tools)
        if round_ == MAX_ROUNDS:
            # Enough tools: now it has to answer with what it found.
            body.pop("tools", None)
        parts = {"thinking": "", "text": ""}
        timings = {}

        def collect(kind, text):
            parts[kind] += text
            on_part(kind, text)

        # Whatever came before a stop or an error is kept.
        ended, calls = "broken off", []
        try:
            ended, calls = stream(body, collect, stop, timings)
        finally:
            if parts["text"].strip() or parts["thinking"].strip() or calls or ended != "broken off":
                message = chat.add("assistant", parts["text"].strip(), model=launcher.name if launcher else "")
                if parts["thinking"].strip():
                    message["thinking"] = parts["thinking"].strip()
                if timings.get("predicted_n"):
                    message["stats"] = {"tokens": int(timings["predicted_n"]),
                                        "speed": round(float(timings.get("predicted_per_second") or 0), 1)}
                if calls:
                    message["tool_calls"] = calls
                elif ended != "stop":
                    message["ended"] = ended
                elif not parts["text"].strip() and parts["thinking"].strip():
                    message["ended"] = "only thought"

                chat.save()
        if not calls:
            return message
        for call in calls:
            extra = {}
            if stop():
                result = "The user stopped the answer before this tool ran."
            else:
                on_part("tool", describe(call))
                if call["name"] in knowledge.NAMES:
                    result = knowledge.run(call["name"], call["arguments"], persona)
                elif call["name"] in web.NAMES:
                    result = web.run(call["name"], call["arguments"])
                elif call["name"] in pictures.NAMES:
                    result, extra = pictures.run(call["name"], call["arguments"], persona)
                else:
                    result = files.run(call["name"], call["arguments"], persona, ask)
            chat.add("tool", result, tool_call_id=call["id"], name=call["name"], summary=describe(call), **extra)
            chat.save()
        if stop():
            return message
        on_part("round", "")
    return message


# --- aicove chat -----------------------------------------------------------------

def main(args):
    if not args:
        print("usage: aicove chat PERSONA [ID|last] [\"message\"]")
        return 2
    persona = personas.load_all().get(args[0])
    if persona is None:
        print(f"No persona called {args[0]}. See: aicove list")
        return 1
    if len(args) == 1:
        for cid, title, mtime in list_chats(persona.id):
            print(f"{cid}  {time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime))}  {title}")
        return 0
    if len(args) == 2:
        chat = Chat.new(persona.id)
    else:
        cid = args[1]
        if cid == "last":
            found = list_chats(persona.id)
            cid = found[0][0] if found else ""
        if not CHAT_ID.fullmatch(cid):
            print(f"No chat {args[1]} for {persona.name}.")
            return 1
        try:
            chat = Chat.load(persona.id, cid)
        except (OSError, ValueError) as e:
            print(f"Can't read chat {cid}: {e}")
            return 1
    launcher = loaded_launcher()
    if not runs_on(persona, launcher):
        wanted = launcher_for(persona)
        if wanted is None:
            print(f"{persona.name}'s models ({', '.join(persona.models) or 'none'}) aren't on this machine.")
            return 1
        if server.llama_start(wanted) != 0:
            return 1
    chat.add("user", args[-1])
    chat.save()
    shown = {"kind": None}

    def show(kind, text):
        if kind == "round":
            shown["kind"] = None
            return
        if kind in ("status", "note"):
            if text:
                print(f"\n[{text}]", flush=True)
            return
        if kind == "tool":
            print(f"\n[tool] {text}", flush=True)
            shown["kind"] = "tool"
            return
        if kind != shown["kind"]:
            print("\n[thinking]" if kind == "thinking" else ("\n[answer]" if shown["kind"] else ""), flush=True)
            shown["kind"] = kind
        sys.stdout.write(text)
        sys.stdout.flush()

    def ask(title, message):
        if not sys.stdin.isatty():
            print(f"\n[{title}: not written, no terminal to ask in]")
            return False
        print(f"\n\n{title}\n\n{message}\n")
        return input("Write it? [y/N] ").strip().lower() in ("y", "yes")

    try:
        reply(persona, chat, show, ask=ask)
    except OSError as e:
        print(f"\nNo answer: {e}")
        return 1
    except KeyboardInterrupt:
        print("\n(stopped)")
    print(f"\n\n(chat {chat.id})")
    return 0
