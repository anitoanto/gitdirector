"""The OpenCode status plugin, run by Node against recorded event sequences."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from gitdirector.agents import opencode_status_plugin_path

from ._timeouts import TMUX_CMD_TIMEOUT

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node required")

# Stands in for Bun's `$`: records each state the plugin writes to tmux.
_HARNESS = """
const [pluginUrl, eventsJson] = process.argv.slice(1);
process.env.TMUX_PANE = "%1";
const states = [];
const $ = (strings, ...values) => {
  states.push(values[values.length - 1]);
  const done = Promise.resolve({ exitCode: 0 });
  return { quiet: () => ({ nothrow: () => done }) };
};
const { GitDirectorStatus } = await import(pluginUrl);
const hooks = await GitDirectorStatus({ $ });
for (const event of JSON.parse(eventsJson)) {
  await hooks.event({ event });
}
console.log(JSON.stringify(states));
"""


def _states(events: list[dict]) -> list[str]:
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            _HARNESS,
            opencode_status_plugin_path().as_uri(),
            json.dumps(events),
        ],
        capture_output=True,
        text=True,
        timeout=TMUX_CMD_TIMEOUT,
        check=True,
    )
    return json.loads(result.stdout)


def _status(session: str, state: str) -> dict:
    return {
        "type": "session.status",
        "properties": {"sessionID": session, "status": {"type": state}},
    }


def _created(session: str, parent: str | None = None) -> dict:
    info = {"id": session, **({"parentID": parent} if parent else {})}
    return {"type": "session.created", "properties": {"info": info}}


def test_a_background_subagent_leaves_its_parent_pending():
    # Recorded from OpenCode 1.18.32 with OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS.
    events = [
        _created("main"),
        _status("main", "busy"),
        _created("sub", parent="main"),
        _status("sub", "busy"),
        _status("main", "idle"),
        _status("sub", "idle"),
        _status("main", "busy"),
        _status("main", "idle"),
    ]
    assert _states(events) == ["idle", "running", "pending", "idle", "running", "idle"]


def test_a_foreground_subagent_keeps_its_parent_running():
    events = [
        _created("main"),
        _status("main", "busy"),
        _created("sub", parent="main"),
        _status("sub", "busy"),
        _status("sub", "idle"),
        _status("main", "idle"),
    ]
    assert _states(events) == ["idle", "running", "idle"]


def test_a_question_from_a_subagent_still_waits():
    events = [
        _created("sub", parent="main"),
        _status("sub", "busy"),
        {"type": "question.asked", "properties": {"id": "q1", "sessionID": "sub"}},
        {"type": "question.replied", "properties": {"requestID": "q1", "sessionID": "sub"}},
    ]
    assert _states(events) == ["idle", "pending", "waiting", "pending"]
