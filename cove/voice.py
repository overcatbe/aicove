# Personas with a voice: an answer is read aloud by a TTS server of its own, the voice
# configurator (VoxCPM2, Gradio; see the README for its API). AIcove only talks to its API: voices lists the
# saved voice profiles, speak says a text with one. Setting "voice": the tool (Tools tab) that is
# the server, started when needed; or the address of one that runs elsewhere. A persona names its
# profile in persona.txt ("voice: my_voice"). What was said is kept next to the chats as
# MP3, so listening again is instant; audio no message needs any more (deleted or edited, another
# voice) is deleted whenever a chat is saved or deleted.

import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request

from . import personas, tools
from .settings import S

FOLDER = "audio"   # in the persona's chats/


def tool():
    """The tool that is the voice server, or None (an address in the setting, or no such tool)."""
    where = S.voice.strip()
    return None if not where or "://" in where else tools.get(where)


def address():
    where = S.voice.strip()
    if "://" in where:
        return where.rstrip("/")
    t = tool()
    return t.address.rstrip("/") if t is not None and t.address else ""


def ready(say=print):
    """Make sure the server answers, starting its tool if needed. Raises OSError."""
    t = tool()
    if t is not None:
        if t.state() != "running" and tools.start(t, say) != 0:
            raise OSError(f"{t.title} didn't start (see the Tools tab)")
        return
    if not address():
        raise OSError("no voice server: set one on the Settings tab (Voice)")


def call(name, data, timeout=900):
    """One call of the Gradio API: post, then wait for its result."""
    base = address()
    req = urllib.request.Request(f"{base}/gradio_api/call/{name}", json.dumps({"data": data}).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        event = json.loads(r.read())["event_id"]
    with urllib.request.urlopen(f"{base}/gradio_api/call/{name}/{event}", timeout=timeout) as r:
        kind = None
        for raw in r:
            line = raw.decode("utf-8", errors="replace").strip()
            if line.startswith("event:"):
                kind = line[6:].strip()
            elif line.startswith("data:") and kind in ("complete", "error"):
                payload = line[5:].strip()
                if kind == "error":
                    try:
                        payload = json.loads(payload) or "the voice server failed"
                    except ValueError:
                        pass
                    raise OSError(f"voice server: {payload}")
                return json.loads(payload)
    raise OSError("the voice server gave no answer")


def voices(say=print):
    """[{id, name, language, gender, style, ...}] of the saved voice profiles."""
    ready(say)
    return call("voices", [], timeout=60)[0]


def label(v):
    bits = [b for b in (v.get("language", "").upper(), v.get("gender"), v.get("style")) if b]
    return f"{v['name']}" + (f" ({', '.join(bits)})" if bits else "") + f"  [{v['id']}]"


def for_speech(text):
    """An answer as plain spoken text: no code, no Markdown marks, links as their words."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"<think>.*?</think>", " ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}(#{1,6}|>|[-*+]|\d+[.)])\s+", "", text)
    text = re.sub(r"(?m)^\s*([-*_]\s*){3,}$", " ", text)
    text = re.sub(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1", r"\2", text)
    text = re.sub(r"\|", " ", text)
    # Lines that end without a mark become sentences, so the voice pauses there.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [line if line[-1] in ".!?:;…\"'”)" else line + "." for line in lines]
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def folder(pid):
    return os.path.join(personas.folder(pid), "chats", FOLDER)


def key(profile, spoken):
    return hashlib.sha1(f"{profile}\n{spoken}".encode()).hexdigest()[:20]


def said(pid, profile, text):
    """The audio of this text in this voice if it was made before, else None."""
    base = os.path.join(folder(pid), key(profile, for_speech(text)))
    return next((base + ext for ext in (".mp3", ".wav") if os.path.exists(base + ext)), None)


def tidy(pid):
    """Delete the audio no message of the persona's chats needs any more, in its voice of now."""
    where = folder(pid)
    if not os.path.isdir(where):
        return
    try:
        profile = personas.Persona.read(pid).voice
    except OSError:
        return
    keep = set()
    chats = os.path.dirname(where)
    for name in os.listdir(chats):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(chats, name), encoding="utf-8") as f:
                messages = personas.chat_messages(json.load(f))
        except (OSError, ValueError):
            return   # better keep too much than delete what a chat we can't read needs
        keep |= {key(profile, for_speech(m["content"])) for m in messages
                 if m.get("role") == "assistant" and m.get("content")}
    for name in os.listdir(where):
        if name.endswith((".mp3", ".wav")) and name.split(".")[0] not in keep:
            try:
                os.remove(os.path.join(where, name))
            except OSError:
                pass


def speak(pid, profile, text, say=print):
    """The audio file of an answer in this voice: made once, then kept. Raises OSError."""
    spoken = for_speech(text)
    if not spoken:
        raise OSError("nothing to say in this answer")
    made = said(pid, profile, text)
    if made:
        return made
    base = os.path.join(folder(pid), key(profile, spoken))
    ready(say)
    result = call("speak", [spoken, profile])[0]
    url = result.get("url") or ""
    # Only fetch from the voice server itself.
    if urllib.parse.urlsplit(url).netloc != urllib.parse.urlsplit(address()).netloc:
        raise OSError(f"the voice server pointed somewhere else: {url}")
    os.makedirs(os.path.dirname(base), exist_ok=True)
    wav = base + ".wav"
    with urllib.request.urlopen(url, timeout=120) as r, open(wav + ".part", "wb") as f:
        f.write(r.read())
    os.replace(wav + ".part", wav)
    # MP3 is a tenth of the size; without ffmpeg the WAV stays.
    if shutil.which("ffmpeg"):
        done = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-codec:a", "libmp3lame",
                               "-q:a", "4", base + ".mp3.part.mp3"], capture_output=True)
        if done.returncode == 0:
            os.replace(base + ".mp3.part.mp3", base + ".mp3")
            os.remove(wav)
            return base + ".mp3"
        try:
            os.remove(base + ".mp3.part.mp3")
        except OSError:
            pass
    return wav
