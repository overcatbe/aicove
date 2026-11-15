# Pictures in the chat: switched on per chat ("Pictures: on" above the input), the persona gets a
# make_picture tool. The picture is made by an image server of its own, stable-diffusion.cpp's
# sd-server (or anything with the OpenAI image API: POST /v1/images/generations). Setting
# "pictures": the tool (Tools tab) that is the server, started when needed; or the address of one
# running elsewhere. Pictures are kept in the persona's chats/pictures/ and shown in the chat; a
# picture no message needs any more (chat or message deleted) is deleted. A persona with a "lora:" line
# has its own face: when it is in the picture itself (me), the Pictures tool, whose sd-server has
# --lora-model-dir "$AICOVE_LORAS" (setting loras_dir), renders with that LoRA and its trigger word.

import base64
import json
import os
import random
import time
import urllib.error
import urllib.request

from . import personas, tools
from .settings import S, loras_folder

FOLDER = "pictures"   # in the persona's chats/
# Small faces (full body, wide scenes) come out mushy: the face is only ~100 px, a dozen latent cells.
# When the Pictures tool's sd-server is started with --ad-model <face detector>, each picture with a
# LoRA gets a second pass: every face smaller than FACE_MAX_AREA of the picture is cut out, repainted
# at 512x512 with the same prompt and LoRA, and blended back. Close-ups are left alone. The detector's
# file must be named face_yolov8n.safetensors (the server matches the request's name to its file).
FACE_DETECTOR = "face_yolov8n.pt"
FACE_MAX_AREA = 0.08
FACE_FIX = {"ad_model": FACE_DETECTOR, "ad_mask_max_ratio": FACE_MAX_AREA, "ad_denoising_strength": 0.4,
            "ad_inpaint_only_masked_padding": 32}
SHAPES = {"square": "1024x1024", "portrait": "832x1216", "landscape": "1216x832",
          "wide": "1344x768",      # 16:9 at the model's own ~1 megapixel: bodies and faces come out right
          "wide-hd": "1920x1088"}  # Full HD 16:9 (1080 is not divisible by 32); slower, 2 MP: more stretched or doubled bodies
DEFAULT_SHAPE = "wide"   # title pictures for the sites are always 16:9
LORA_TOOL = "pictures"   # the tool whose server knows the LoRAs
HINT = ("You can make pictures with make_picture. Use it when the user asks for a picture, or when "
        "showing something says more than words. The user sees the picture in the chat by itself: don't "
        "put the picture or a link to it in your answer, and don't describe it again at length.\n"
        "The user's request is often one line. The image model knows nothing of this chat, so you write "
        "its whole prompt, as a film's director of photography would, using what the picture is for (the "
        "article, story or idea of this chat). The description you pass to make_picture:\n"
        "- 150 to 250 words of English, one flowing paragraph.\n"
        "- Everything the user asked for, as they said it: their objects stay their objects (hard disks "
        "stay hard disks), their number of people stays exact. Add nothing that goes against it.\n"
        "- If you are in the picture yourself and your instructions say how you look, copy that description "
        "word for word, in the third person, then add your expression, pose and angle, and set me to true.\n"
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
        "shape": {"type": "string", "enum": list(SHAPES), "description": "wide (default: 16:9, e.g. a title picture), square, portrait, landscape, or wide-hd (Full HD 16:9, slower and more often with odd bodies: only when asked for Full HD)."},
        "me": {"type": "boolean", "description": "True when you yourself are in the picture."},
        "seed": {"type": "integer", "description":
                 "Only to remake an earlier picture with changes: its seed (told when it was made). The same "
                 "seed with a slightly changed description keeps the picture's composition. Leave out for a "
                 "new picture."}},
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


def loras():
    """The LoRA files personas can choose from."""
    try:
        return sorted(f for f in os.listdir(loras_folder()) if f.endswith(".safetensors"))
    except OSError:
        return []


def new_seed():
    return random.randint(1, 2**31 - 1)


def request_for(description, shape, lora, seed):
    """The image server's request: with a LoRA its own API, which is the one that takes them. The seed
    is chosen here, not by the server, so the chat can show it and the picture can be made again."""
    size = SHAPES.get(shape, SHAPES[DEFAULT_SHAPE])
    if lora:
        name, trigger = lora.split()
        width, height = size.split("x")
        url, body = "/sdapi/v1/txt2img", {"prompt": f"{trigger}, {description}", "width": int(width),
                                          "height": int(height), "steps": 8, "cfg_scale": 1.0, "seed": seed,
                                          "lora": [{"path": name, "multiplier": 1.0}]}
        t = tool()
        if t is not None and "--ad-model" in t.start:
            body["alwayson_scripts"] = {"ADetailer": {"args": [True, False, FACE_FIX]}}
    else:
        # sd-server takes a seed on its OpenAI route only inside the prompt; a server at a plain address
        # may be something else, so it gets the prompt as it is.
        extra = f" <sd_cpp_extra_args>{json.dumps({'seed': seed})}</sd_cpp_extra_args>" if tool() is not None else ""
        url, body = "/v1/images/generations", {"prompt": description + extra, "n": 1, "size": size,
                                               "output_format": "jpeg", "output_compression": 90}
    return urllib.request.Request(address() + url, json.dumps(body).encode(), {"Content-Type": "application/json"})


def make(pid, description, shape=DEFAULT_SHAPE, say=print, lora="", seed=None):
    """Make a picture; returns (its file name in the persona's pictures folder, its seed). Raises OSError."""
    ready(say)
    t = tool()
    lora = lora if t is not None and t.name == LORA_TOOL else ""
    seed = seed if isinstance(seed, int) and seed > 0 else new_seed()
    req = request_for(description, shape, lora, seed)
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise OSError(f"image server: {e.read()[:300].decode('utf-8', 'replace') or e}") from None
    try:
        image = base64.b64decode(data["images"][0] if lora else data["data"][0]["b64_json"])
    except (KeyError, IndexError, TypeError, ValueError):
        raise OSError("the image server sent no picture") from None
    os.makedirs(folder(pid), exist_ok=True)
    ext = ".png" if lora else ".jpg"
    name = time.strftime("%Y%m%d-%H%M%S") + ext
    n = 2
    while os.path.exists(os.path.join(folder(pid), name)):
        name, n = time.strftime("%Y%m%d-%H%M%S") + f"-{n}{ext}", n + 1
    with open(os.path.join(folder(pid), name), "wb") as f:
        f.write(image)
    return name, seed


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
        seed = int(a["seed"]) if str(a.get("seed") or "").strip().isdigit() else None
        made, seed = make(persona.id, description, str(a.get("shape") or DEFAULT_SHAPE), say=lambda t: None,
                          lora=persona.lora if a.get("me") else "", seed=seed)
    except OSError as e:
        return f"No picture: {e}", {}
    return (f"The picture is made (seed {seed}) and the user sees it now.",
            {"picture": made, "seed": seed})


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
