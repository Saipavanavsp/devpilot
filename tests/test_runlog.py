import json
import os

import pytest

from devpilot import runlog
from devpilot.runlog import REPLACE_ATTEMPTS, RunLog


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(runlog.time, "sleep", lambda _: None)


def flaky_replace(monkeypatch, failures):
    real_replace = os.replace
    calls = []

    def replace(src, dst):
        calls.append(dst)
        if len(calls) <= failures:
            raise PermissionError(5, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(runlog.os, "replace", replace)
    return calls


def test_save_retries_while_the_file_is_briefly_locked(tmp_path, monkeypatch):
    log = RunLog(tmp_path, task="t", model="m")
    calls = flaky_replace(monkeypatch, failures=2)

    log.add_step({"step": 1})

    assert len(calls) == 3
    assert json.loads(log.path.read_text())["steps"][0]["step"] == 1


def test_save_gives_up_on_a_persistent_lock(tmp_path, monkeypatch):
    log = RunLog(tmp_path, task="t", model="m")
    calls = flaky_replace(monkeypatch, failures=REPLACE_ATTEMPTS)

    with pytest.raises(PermissionError):
        log.add_step({"step": 1})
    assert len(calls) == REPLACE_ATTEMPTS
