# The AIcove folder (setting cove_folder, ~/AIcove): everything that is yours, in one place to
# back up or carry to another machine. What belongs to the machine (settings, launchers,
# tools, running state, the model files) stays outside.
#   personas/<id>/   persona.txt, picture, knowledge/, notes/, chats
#   documents/       for every persona to read (when it may read files)
#   work/            where personas may write (when they may)

import os
import sys

from .settings import COVE_DIR, CONFIG_FILE, PERSONA_DIR, S

DOCUMENTS_DIR = os.path.join(COVE_DIR, "documents")
WORK_DIR = os.path.join(COVE_DIR, "work")
PICTURE_ENDINGS = (".png", ".jpg", ".webp", ".gif")


def short(path):
    """A path the way the settings write it: ~/... inside the home folder."""
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path


def move_in(say=None):
    """Make the folder's parts. Safe to call at every start."""
    say = say or (lambda text: print(text, file=sys.stderr))
    if not S.exists:
        return
    try:
        for d in (PERSONA_DIR, DOCUMENTS_DIR, WORK_DIR):
            os.makedirs(d, exist_ok=True)
        if "cove_folder" not in open(CONFIG_FILE, encoding="utf-8").read():
            S.save()
        add_assistant()
    except OSError as e:
        say(f"Can't set up {COVE_DIR}: {e}")


def add_assistant():
    """The personas AIcove comes with, each made once: the plain Assistant (no prompt, no
    knowledge, any model; for trying models) and Balthazar the Unsupervised, to show what a
    persona can be. Deleted, they stay away (the mark stays behind); one of the same name
    that is there already is left as it is."""
    from . import personas
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    for pid, name, prompt in (("assistant", "Assistant", None),
                              ("balthazar-the-unsupervised", "Balthazar the Unsupervised", "balthazar.txt")):
        mark = os.path.join(PERSONA_DIR, f".{pid.split('-')[0]}-made")
        if os.path.exists(mark):
            continue
        if not os.path.exists(personas.folder(pid)):
            system = ""
            if prompt:
                with open(os.path.join(here, prompt), encoding="utf-8") as f:
                    system = f.read().strip()
            personas.Persona(pid, name, system).write()
        open(mark, "w").close()
