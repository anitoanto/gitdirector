"""Shared timeout policy for tests that synchronize with threads or workers.

These timeouts exist only so a genuinely deadlocked test fails instead of
hanging CI forever. They are *not* a way to express "this should be fast", so
they are deliberately generous: CI runners are far slower and far more
contended than a developer laptop (the suite runs under ``pytest -n auto`` on
2-core hosts), and a wait sized to local timings turns into a random failure
there.

Two rules keep these waits honest:

* Size them for the worst plausible machine, not the typical one. A correct
  test passes just as well with a 30 second bound as with a 1 second one; only
  a broken test notices the difference.
* Always assert the result. ``Event.wait`` returns ``False`` on timeout rather
  than raising, so an unchecked wait lets a test silently continue with the
  synchronization it was waiting for never having happened -- which shows up
  later as a confusing assertion failure somewhere else.
"""

# Upper bound for waiting on an event another thread or worker is expected to
# set. Generous on purpose -- see the module docstring.
SYNC_TIMEOUT = 30.0

# Upper bound for polling real external state (a tmux server starting up,
# a pane exiting). Slower than SYNC_TIMEOUT because it covers process spawn
# and shell startup on a loaded runner.
POLL_TIMEOUT = 30.0

# Upper bound for a single ``tmux`` client command. Covers the case where the
# command has to start the server first, which is process spawn plus config
# parsing on a machine that may be running several test workers at once.
TMUX_CMD_TIMEOUT = 30

# Upper bound for a helper subprocess that has to boot a fresh interpreter and
# import the package. Interpreter startup is easily seconds on a cold, loaded
# CI runner.
SUBPROCESS_TIMEOUT = 60
