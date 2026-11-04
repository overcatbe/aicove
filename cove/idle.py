# Stopping what sits unused in memory: a model of this machine after "model_idle" minutes, the
# image server (the Pictures tool) after "pictures_idle" minutes; 0 = never. Unused = its log has
# not changed for that long: llama-server writes a line for every request (chat, Writing, agents,
# another machine through the tunnel) but none while it waits, sd-server for every picture.

import os
import time

from . import pictures, server
from .settings import S


def quiet_for(path):
    """Seconds since the file last changed, or None."""
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return None


def model_due():
    """A model of ours has been loaded and unused for longer than model_idle."""
    minutes = S.model_idle
    if not minutes or server.llama_pid() is None or server.remote_host():
        return False
    quiet = quiet_for(server.LLAMA_LOG)
    return quiet is not None and quiet > minutes * 60


def pictures_due():
    """The Pictures tool, when AIcove started it and it has made no picture for longer than
    pictures_idle; else None."""
    minutes = S.pictures_idle
    tool = pictures.tool()
    if not minutes or tool is None or tool.error or tool.stop or tool.terminal or tool.pid() is None:
        return None
    quiet = quiet_for(tool.logfile())
    return tool if quiet is not None and quiet > minutes * 60 else None
