"""Where PC Bridge keeps its files. Never next to the program (the install folder is read-only)."""

import json
import logging
import logging.handlers
import os
from pathlib import Path

APP = "pcbridge"


def _dir(env: str, fallback: str) -> Path:
    base = os.environ.get(env) or str(Path.home() / fallback)
    p = Path(base) / APP
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:   # settings
    return _dir("XDG_CONFIG_HOME", ".config")


def state_dir() -> Path:    # log file
    return _dir("XDG_STATE_HOME", ".local/state")


def cache_dir() -> Path:    # single-instance lock
    return _dir("XDG_CACHE_HOME", ".cache")


DEFAULT_SETTINGS = {"show_keys": False}


def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads((config_dir() / "settings.json").read_text()))
    except Exception:
        pass
    return s


def save_settings(s: dict):
    try:
        (config_dir() / "settings.json").write_text(json.dumps(s, indent=2))
    except Exception:
        pass


def file_logger() -> logging.Logger:
    """Details lines are also written here so a crash leaves a record.
    Typed keys are never written to the file, and neither is the session code."""
    log = logging.getLogger("pcbridge")
    if not log.handlers:
        h = logging.handlers.RotatingFileHandler(state_dir() / "pcbridge.log",
                                                 maxBytes=200_000, backupCount=2)
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return log
