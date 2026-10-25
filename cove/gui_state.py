# Small things the window remembers between runs (where a divider was left), in a JSON file
# in the state folder: window.json, or another file for a tab that keeps its own.

import json
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSplitter

from .settings import STATE_DIR


def remembered(key, default=None, file="window.json"):
    try:
        with open(os.path.join(STATE_DIR, file), encoding="utf-8") as f:
            return json.load(f).get(key, default)
    except (OSError, ValueError):
        return default


def remember(key, value, file="window.json"):
    path = os.path.join(STATE_DIR, file)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data[key] = value
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def resizable(top, bottom, key, height):
    """top over bottom with a divider to drag; bottom starts height px high, or as it was left."""
    split = QSplitter(Qt.Orientation.Vertical)
    split.addWidget(top)
    split.addWidget(bottom)
    split.setCollapsible(0, False)
    split.setCollapsible(1, False)
    split.setStretchFactor(0, 1)
    split.setStretchFactor(1, 0)
    kept = remembered(key)
    split.setSizes([600, kept if isinstance(kept, int) and kept > 0 else height])
    split.splitterMoved.connect(lambda *_: remember(key, split.sizes()[1]))
    return split
