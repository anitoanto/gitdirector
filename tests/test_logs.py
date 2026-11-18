"""The full-screen apps log to a file, never to the screen they draw on."""

import logging

import pytest

from gitdirector import logs, paths


@pytest.fixture
def gitdirector_logger():
    logger = logging.getLogger("gitdirector")
    saved = logger.handlers[:], logger.level, logger.propagate
    yield logger
    for handler in logger.handlers:
        if handler not in saved[0]:
            handler.close()
    logger.handlers[:], logger.level, logger.propagate = saved


def test_warnings_go_to_the_log_file_and_not_to_stderr(gitdirector_logger, capfd):
    logs.log_to_file("console")
    logging.getLogger("gitdirector.repo").warning("git status failed")
    logging.getLogger("gitdirector.repo").info("routine detail")
    for handler in gitdirector_logger.handlers:
        handler.flush()

    text = paths.log_file("console").read_text()
    assert "git status failed" in text
    assert "routine detail" not in text
    assert capfd.readouterr().err == ""


def test_debug_logging_on_request(gitdirector_logger, monkeypatch):
    monkeypatch.setenv(logs.DEBUG_ENV_VAR, "1")
    logs.log_to_file("sidebar")
    assert gitdirector_logger.level == logging.DEBUG


def test_an_outgrown_log_keeps_one_older_file(gitdirector_logger, monkeypatch):
    monkeypatch.setattr(logs, "_MAX_BYTES", 10)
    path = paths.log_file("sidebar")
    path.parent.mkdir(parents=True)
    path.write_text("x" * 20)
    logs.log_to_file("sidebar")
    assert path.with_suffix(".log.1").read_text() == "x" * 20
    assert not path.exists() or path.stat().st_size == 0
