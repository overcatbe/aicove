# Pictures in the chat: switched on per chat ("Pictures: on" above the input), the persona gets a
# make_picture tool. The picture is made by an image server of its own, stable-diffusion.cpp's
# sd-server (or anything with the OpenAI image API: POST /v1/images/generations). Setting
# "pictures": the tool (Tools tab) that is the server, started when needed; or the address of one
# running elsewhere. Pictures are kept in the persona's chats/pictures/ and shown in the chat; a
# picture no message needs any more (chat or message deleted) is deleted.

import base64
import json
import os
import time
import urllib.error
import urllib.request

from . import personas, tools
from .settings import S

FOLDER = "pictures"   # in the persona's chats/
SHAPES = {"square": "1024x1024", "portrait": "832x1216", "landscape": "1216x832"}
HINT = ("You can make pictures with make_picture. Use it when the user asks for a picture, or when "
        "showing something says more than words. The user sees the picture in the chat by itself: don't "
        "put the picture or a link to it in your answer, and don't describe it again at length.\n"
        "The user's request is often one line. The image model knows nothing of this chat, so you write "
        "its whole prompt, as a film's director of photography would, using what the picture is for (the "
        "article, story or idea of this chat). The description you pass to make_picture:\n"
        "- 150 to 250 words of English, one flowing paragraph.\n"
        "- Everything the user asked for, as they said it: their objects stay their objects (hard disks "
        "stay hard disks), their number of people stays exact. Add nothing that goes against it.\n"
        "- Style: a photo unless the user asks for something else.\n"
        "- The camera: where it stands, how wide, which lens.\n"
        "- Depth in layers: what is in the foreground, the middle and the background.\n"
        "- Light with named sources and colours, and a contrast, e.g. a warm desk lamp against a cold "
        "blue city, the last orange light on the horizon. Time of day, weather, mood.\n"
        "- Composition: the subject, faces and everything important in the middle band of the frame; the "
        "top and bottom edges only sky, ceiling, floor or table, because the picture may be cropped there.\n"
        "- No text, letters, signs or labels in the picture unless the user asks for them; then the exact "
        "words in quotes.")

TOOLS = [{"type": "function", "function": {
    "name": "make_picture",
    "description": "Make a picture and show it to the user. Takes up to a minute or two.",
    "parameters": {"type": "object", "properties": {
        "description": {"type": "string", "description":
                        "The full prompt for the image model, 150-250 words of English, written as the "
                        "instructions about pictures say."},
        "shape": {"type": "string", "enum": list(SHAPES), "description": "square (default), portrait or landscape."}},
        "required": ["description"]}}}]
NAMES = {"make_picture"}


def tool():
    where = S.pictures.strip()
    return None if not where or "://" in where else tools.get(where)


def address():
    where = S.pictures.strip()
    if "://" in where:
        return where.rstrip("/")
    t = tool()
    return t.address.rstrip("/") if t is not None and t.address else ""


def ready(say=print):
    """Make sure the image server answers, starting its tool if needed. Raises OSError."""
    t = tool()
    if t is not None:
        if t.state() != "running" and tools.start(t, say) != 0:
            raise OSError(f"{t.title} didn't start (see the Tools tab)")
        return
    if not address():
        raise OSError("no image server: set one on the Settings tab (Pictures)")


def folder(pid):
    return os.path.join(personas.folder(pid), "chats", FOLDER)


def make(pid, description, shape="square", say=print):
    """Make a picture; returns its file name in the persona's pictures folder. Raises OSError."""
    ready(say)
    body = {"prompt": description, "n": 1, "size": SHAPES.get(shape, SHAPES["square"]), "output_format": "jpeg",
            "output_compression": 90}
    req = urllib.request.Request(address() + "/v1/images/generations", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise OSError(f"image server: {e.read()[:300].decode('utf-8', 'replace') or e}") from None
    try:
        image = base64.b64decode(data["data"][0]["b64_json"])
    except (KeyError, IndexError, TypeError, ValueError):
        raise OSError("the image server sent no picture") from None
    os.makedirs(folder(pid), exist_ok=True)
    name = time.strftime("%Y%m%d-%H%M%S") + ".jpg"
    n = 2
    while os.path.exists(os.path.join(folder(pid), name)):
        name, n = time.strftime("%Y%m%d-%H%M%S") + f"-{n}.jpg", n + 1
    with open(os.path.join(folder(pid), name), "wb") as f:
        f.write(image)
    return name


def run(name, arguments, persona):
    """(what the model is told, extra fields for the chat's tool message)."""
    try:
        a = json.loads(arguments or "{}") if isinstance(arguments, str) else dict(arguments or {})
    except ValueError:
        return "The arguments were not valid JSON. Call the tool again with proper arguments.", {}
    description = str(a.get("description") or "").strip()
    if not description:
        return "Give a description of the picture.", {}
    try:
        made = make(persona.id, description, str(a.get("shape") or "square"), say=lambda t: None)
    except OSError as e:
        return f"No picture: {e}", {}
    return "The picture is made and the user sees it now.", {"picture": made}


def describe(name, arguments):
    try:
        a = json.loads(arguments or "{}")
    except ValueError:
        a = {}
    text = " ".join(str(a.get("description", "")).split())
    return "Made a picture" + (f": {text[:80]}{'…' if len(text) > 80 else ''}" if text else "")


def tidy(pid):
    """Delete the pictures no message of the persona's chats shows any more."""
    where = folder(pid)
    if not os.path.isdir(where):
        return
    keep = set()
    chats = os.path.dirname(where)
    for name in os.listdir(chats):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(chats, name), encoding="utf-8") as f:
                keep |= {m["picture"] for m in personas.chat_messages(json.load(f)) if m.get("picture")}
        except (OSError, ValueError):
            return   # better keep too much than delete what a chat we can't read needs
    for name in os.listdir(where):
        if name.endswith((".jpg", ".png", ".webp")) and name not in keep:
            try:
                os.remove(os.path.join(where, name))
            except OSError:
                pass
