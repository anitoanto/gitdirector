"""Where the full-screen apps write what goes wrong.

The console and the sidebar own the terminal: a warning printed to stderr is
either swallowed by Textual or drawn over the screen (inside an attach, over
tmux). They log to ``~/.gitdirector/state/<name>.log`` instead, warnings and
up, everything with ``GITDIRECTOR_DEBUG`` set.
"""

from __future__ import annotations

import logging
import os

from . import paths

DEBUG_ENV_VAR = "GITDIRECTOR_DEBUG"
# One older file is kept beside the current one when it outgrows this.
_MAX_BYTES = 1_000_000
_FORMAT = "%(asctime)s %(process)d %(threadName)s %(levelname)s %(name)s: %(message)s"


def log_to_file(name: str) -> None:
    """Send the ``gitdirector`` loggers to *name*'s log file and nowhere else."""
    logger = logging.getLogger("gitdirector")
    logger.setLevel(logging.DEBUG if os.environ.get(DEBUG_ENV_VAR) else logging.WARNING)
    # Nothing may reach Python's last-resort handler, which writes to stderr.
    logger.propagate = False
    path = paths.log_file(name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > _MAX_BYTES:
            os.replace(path, path.with_suffix(".log.1"))
        handler: logging.Handler = logging.FileHandler(path, encoding="utf-8", delay=True)
    except OSError:
        handler = logging.NullHandler()
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)
