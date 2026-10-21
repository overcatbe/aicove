# A persona's settings per model: personas/<id>/models/<machine>.toml. Each machine writes only
# its own file (so syncing never meets two machines in one file) and reads the others' to show
# them. A section per launcher holds what differs from the persona's own settings
# (persona.txt), plus "used", the day it last answered with that model. A launcher's section
# appears the first time the persona answers on it, empty: it then runs on the persona's settings.
#
#   ["defiant"]
#   used = "2026-10-21"
#   temperature = "0.9"

import os
import re
import socket
import time
import tomllib

from . import personas
from .settings import S

SECTION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def machine():
    """This machine's name for the files: the "machine" setting, else the hostname's first part."""
    name = (S.machine or "").strip() or socket.gethostname().split(".")[0]
    return re.sub(r"[^A-Za-z0-9._-]", "-", name) or "this"


def folder(pid):
    return os.path.join(personas.folder(pid), "models")


def path(pid, who=None):
    return os.path.join(folder(pid), f"{who or machine()}.toml")


def read(pid, who=None):
    """{launcher: {"used": day or "", "samplers": {key: value}}} of one machine (this one)."""
    try:
        with open(path(pid, who), "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    found = {}
    for name, section in data.items():
        if not SECTION.fullmatch(name) or not isinstance(section, dict):
            continue
        samplers = {}
        for key, value in section.items():
            if key in personas.SAMPLERS and personas.sampler_value(key, value) is not None:
                samplers[key] = personas.sampler_value(key, value)
        found[name] = {"used": str(section.get("used", "")), "samplers": samplers}
    return found


def write(pid, found):
    os.makedirs(folder(pid), exist_ok=True)
    lines = [f"# {pid}: its settings per model on {machine()}. AIcove writes this file; only this "
             "machine does.", ""]
    for name in sorted(found, key=str.lower):
        # Quoted: a launcher name may have a dot in it (qwen0.5), which TOML reads as a sub-table.
        lines.append(f'["{name}"]')
        if found[name].get("used"):
            lines.append(f'used = "{found[name]["used"]}"')
        for key in personas.SAMPLERS:
            if key in found[name]["samplers"]:
                lines.append(f'{key} = "{found[name]["samplers"][key]}"')
        lines.append("")
    tmp = path(pid) + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    os.replace(tmp, path(pid))


def others(pid):
    """{machine: what read() gives} of the other machines."""
    try:
        names = sorted(f[:-len(".toml")] for f in os.listdir(folder(pid)) if f.endswith(".toml"))
    except OSError:
        return {}
    return {n: read(pid, n) for n in names if n != machine()}


def samplers_for(persona, launcher):
    """The persona's settings with this machine's ones for that launcher over them."""
    merged = dict(persona.samplers)
    if launcher:
        merged.update(read(persona.id).get(launcher, {}).get("samplers", {}))
    return merged


def used(pid, launcher):
    """The persona just answered on launcher: note the day (adds the launcher if it is new). The
    file is only written when something changes, so it isn't rewritten with every message."""
    if not launcher or not SECTION.fullmatch(launcher) or not os.path.isdir(personas.folder(pid)):
        return
    found = read(pid)
    today = time.strftime("%Y-%m-%d")
    entry = found.setdefault(launcher, {"used": "", "samplers": {}})
    if entry["used"] != today:
        entry["used"] = today
        write(pid, found)


def save_samplers(pid, wanted, forgotten=()):
    """From the Personas tab: wanted {launcher: samplers} replaces those settings (the days are
    kept), forgotten launchers go; others (added since the form was filled) stay."""
    found = read(pid)
    for name in forgotten:
        found.pop(name, None)
    for name, samplers in wanted.items():
        found[name] = {"used": found.get(name, {}).get("used", ""), "samplers": dict(samplers)}
    if found or os.path.exists(path(pid)):
        write(pid, found)
