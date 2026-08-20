"""The console entry's telemetry envelope (GAPS U34).

`main()` is what the installed `bm` runs; these tests drive it with the app
stubbed, proving every exit shape lands one cmdlog line and failures reach
autocapture. CliRunner tests everywhere else bypass the envelope on purpose.
"""

from __future__ import annotations

import json

import pytest

from basic_memory import cmdlog
from basic_memory.cli import main as main_module


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture
def captured(monkeypatch):
    calls: list[tuple[str, int, BaseException | None]] = []

    def record(command, exit_code, exc):
        calls.append((command, exit_code, exc))

    # `main` imports bugs inside the function and resolves `bugs.autocapture`
    # at call time, so patching the source module is sufficient and exact.
    import basic_memory.bugs as bugs_module

    monkeypatch.setattr(bugs_module, "autocapture", record)
    return calls


def _run(monkeypatch, argv: list[str], app_behavior) -> BaseException | None:
    monkeypatch.setattr(main_module.sys, "argv", ["bm", *argv])
    monkeypatch.setattr(main_module, "app", app_behavior)
    try:
        main_module.main()
    except BaseException as exc:  # noqa: BLE001 — the envelope re-raises by contract
        return exc
    return None


def _log_records() -> list[dict]:
    return cmdlog.entries()


def test_success_logs_exit_zero_and_skips_capture(monkeypatch, captured):
    outcome = _run(monkeypatch, ["ls"], lambda: None)
    assert outcome is None
    assert _log_records()[-1]["exit"] == 0
    assert captured == []


def test_usage_error_logs_exit_two_and_captures(monkeypatch, captured):
    def usage_error():
        raise SystemExit(2)

    outcome = _run(monkeypatch, ["nonsense"], usage_error)
    assert isinstance(outcome, SystemExit)
    assert _log_records()[-1]["exit"] == 2
    assert captured == [("nonsense", 2, None)]


def test_uncaught_crash_logs_and_captures_the_exception(monkeypatch, captured):
    boom = RuntimeError("boom")

    def crash():
        raise boom

    outcome = _run(monkeypatch, ["ls"], crash)
    assert outcome is boom  # re-raised, never masked
    assert _log_records()[-1]["exit"] == 1
    assert captured == [("ls", 1, boom)]


def test_keyboard_interrupt_is_logged_but_never_captured(monkeypatch, captured):
    def interrupt():
        raise KeyboardInterrupt()

    outcome = _run(monkeypatch, ["ls"], interrupt)
    assert isinstance(outcome, KeyboardInterrupt)
    assert _log_records()[-1]["exit"] == 130
    assert captured == []


def test_the_log_line_carries_the_command_path(monkeypatch, captured):
    _run(monkeypatch, ["project", "list", "--quiet"], lambda: None)
    record = json.loads(cmdlog.cmdlog_path().read_text().splitlines()[-1])
    assert record["command"] == "project list"
