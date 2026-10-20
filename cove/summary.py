# Long chats: when a chat fills most of the model's context, the older messages are summarised
# by the loaded model itself and the persona gets that summary instead of them; the newest part
# stays word for word. The chat on screen stays complete. The summary is kept in the chat file
# ("summary": text, how many messages it covers, a fingerprint of those) and shown as "So far"
# above the chat, where it can be edited or dropped. Editing or deleting one of the covered
# messages drops it too, and the next message makes a new one.

import hashlib
import json
import re
import urllib.request

from .settings import S

# Summarise when the chat fills this share of the context; keep this share word for word.
TRIGGER = 0.70
KEEP = 0.30
WORDS = 700
# A tool result counts with at most this much of its text in a summary.
TOOL_CHARS = 1500
# What a picture takes in the context of a model that sees (roughly; it depends on the model).
PICTURE_TOKENS = 1200

INSTRUCTIONS = """You keep the running summary of a long conversation between a user and an AI \
character or assistant. The older messages will be deleted and replaced by your summary, so \
anything you leave out is lost.

Write the updated summary. Keep, in chronological order:
- who the participants are and how they relate, including any persona or role being played
- facts, names, places, numbers, dates and decisions that were established
- the current state of things: where the story or task stands right now, and what is unresolved
- what each side wants, promised, refused or is angry about, including running jokes and grudges
- instructions the user gave about style, format or behaviour
- exact wording of phrases, terms or quotations that matter
- files that were read or written, and what was found in them, when it still matters

Summarise faithfully whatever the content is. Do not judge, soften, warn or leave things out \
because they are dark, sexual or offensive. Do not comment, do not address anyone, do not \
continue the conversation. Dense plain prose, no headings. At most {words} words."""

HEADER = ("[Summary of the earlier part of this conversation. The original messages are no "
          "longer visible to you; treat this as what has happened so far.]")


def post(path, body, timeout=900):
    req = urllib.request.Request(S.llama_url() + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def context_size():
    with urllib.request.urlopen(S.llama_url() + "/props", timeout=20) as r:
        return int(json.loads(r.read())["default_generation_settings"]["n_ctx"])


def count(text):
    return len(post("/tokenize", {"content": text}, 60)["tokens"])


def line_of(m):
    """One message as a line of the transcript the summary is made from."""
    if m["role"] == "tool":
        text = m["content"]
        cut = "…" if len(text) > TOOL_CHARS else ""
        return f"tool ({m.get('summary') or m.get('name', '')}): {text[:TOOL_CHARS]}{cut}"
    if m["role"] == "assistant" and m.get("tool_calls"):
        calls = "; ".join(f"{c['name']} {c['arguments']}" for c in m["tool_calls"])
        return f"assistant: {m['content']}\n(calls tools: {calls})".strip()
    picture = " (with a picture)" if m.get("picture") and m["role"] == "user" else ""
    return f"{m['role']}{picture}: {m['content']}"


def sent_of(m):
    """One message as long as the model really gets it: tool results whole, unlike line_of."""
    if m["role"] == "tool":
        return f"tool: {m['content']}"
    return line_of(m)


def size_of(lines, messages):
    """Tokens of these message lines, with room for the pictures that go along."""
    return count("\n".join(lines)) + 8 * len(lines) + PICTURE_TOKENS * sum(
        1 for m in messages if m["role"] == "user" and m.get("picture"))


def digest(messages):
    h = hashlib.sha256()
    for m in messages:
        h.update(line_of(m).encode())
        h.update(b"\0")
    return h.hexdigest()


def current(chat):
    """(number of messages the summary covers, its text), (0, "") without a valid one."""
    s = chat.summary or {}
    covered = s.get("covered", 0)
    if isinstance(covered, int) and 0 < covered <= len(chat.messages) and s.get("text", "").strip() \
            and s.get("digest") == digest(chat.messages[:covered]):
        return covered, s["text"].strip()
    return 0, ""


def set_text(chat, text):
    """The user's own version of the summary."""
    covered, _ = current(chat)
    if covered:
        chat.summary = {**chat.summary, "text": text.strip(), "edited": True}


def summarise(summary, transcript):
    prompt = (f"Summary so far:\n{summary or '(nothing yet)'}\n\n"
              f"Next part of the conversation:\n{transcript}\n\nWrite the updated summary now.")
    request = {
        "messages": [{"role": "system", "content": INSTRUCTIONS.format(words=WORDS)},
                     {"role": "user", "content": prompt}],
        "temperature": 0.3, "max_tokens": WORDS * 2, "stream": False,
        # Thinking models would otherwise spend the whole answer on thinking.
        "chat_template_kwargs": {"enable_thinking": False},
    }
    for _ in range(2):
        data = post("/v1/chat/completions", request)
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
        if text:
            return text
        # The template ignored the switch: give it room to think and answer.
        request["max_tokens"] = WORDS * 2 + 4000
    raise OSError("the model returned no summary")


def usage(chat, fixed, n_ctx=None):
    """(tokens the next request takes, the model's context): what keep_up measures, also for the
    meter under the chat. fixed is what goes with every request besides the messages."""
    covered, text = current(chat)
    n_ctx = n_ctx or context_size()
    lines = [sent_of(m) for m in chat.messages[covered:]]
    used = count(fixed + "\n" + text)
    if lines:
        used += size_of(lines, chat.messages[covered:])
    return used, n_ctx


def keep_up(chat, fixed, say=lambda text: None):
    """Summarise older messages if the chat has grown past TRIGGER of the context. fixed is
    what goes with every request besides the messages (system prompt, tools). Returns True if
    the summary changed. Raises OSError when llama-server can't be asked."""
    messages = chat.messages
    covered, text = current(chat)
    if len(messages) - covered < 4:
        return False
    n_ctx = context_size()
    lines = [line_of(m) for m in messages]
    # Measured as sent (whole tool results), summarised from the shorter lines.
    sent = [sent_of(m) for m in messages]
    base = count(fixed + "\n" + text)
    live = size_of(sent[covered:], messages[covered:])
    if base + live <= TRIGGER * n_ctx:
        return False
    # Walk back from the newest message until the share to keep is full; the kept part starts
    # with a message of the user's, so no tool result is cut off from its call.
    per_char = live / max(sum(len(l) for l in sent[covered:]), 1)
    budget, used, cut = KEEP * n_ctx, 0.0, None
    for i in range(len(messages) - 1, covered, -1):
        used += len(sent[i]) * per_char + 8 + (PICTURE_TOKENS if messages[i].get("picture") else 0)
        if used > budget and cut is not None:
            break
        if messages[i]["role"] == "user":
            cut = i
    if cut is None or cut <= covered:
        return False
    say(f"The chat is getting long — summarising {cut - covered} older messages…")
    # The old part goes in pieces the model can take in one go.
    piece_chars = int(0.5 * n_ctx / max(per_char, 0.01))
    piece = ""
    for line in lines[covered:cut] + [None]:
        if line is None or (piece and len(piece) + len(line) > piece_chars):
            text = summarise(text, piece[:piece_chars * 2])
            piece = ""
        if line is not None:
            piece += line + "\n\n"
    chat.summary = {"text": text, "covered": cut, "digest": digest(messages[:cut])}
    chat.save()
    say("")
    return True
