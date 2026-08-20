"""The invocation log (GAPS U34): append, ring, and the never-raise contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basic_memory import cmdlog


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path, monkeypatch):
    """Point the state dir at tmp so tests never touch this machine's log."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path / "state" / "basic-memory"


def test_command_path_takes_the_first_positional():
    assert cmdlog.command_path(["new", "task", "Title", "--quiet"]) == "new"


def test_command_path_joins_group_and_subcommand():
    assert cmdlog.command_path(["project", "list"]) == "project list"


def test_command_path_skips_leading_flags():
    assert cmdlog.command_path(["--quiet", "ls"]) == "ls"


def test_command_path_with_no_positionals_is_labeled():
    assert cmdlog.command_path(["--version"]) == "(none)"


def test_start_finish_appends_one_record(isolated_state_dir):
    invocation = cmdlog.start(["ls", "--quiet"])
    cmdlog.finish(invocation, 0)

    lines = cmdlog.cmdlog_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["command"] == "ls"
    assert record["exit"] == 0
    assert record["duration_ms"] >= 0


def test_finish_records_nonzero_exit(isolated_state_dir):
    cmdlog.finish(cmdlog.start(["doctor"]), 1)
    assert json.loads(cmdlog.cmdlog_path().read_text())["exit"] == 1


def test_ring_trims_to_keep_lines(isolated_state_dir, monkeypatch):
    # A tiny trim threshold forces the ring without writing 150 KB.
    monkeypatch.setattr(cmdlog, "RING_TRIM_BYTES", 200)
    for _ in range(10):
        cmdlog.finish(cmdlog.start(["ls"]), 0)
    lines = cmdlog.cmdlog_path().read_text(encoding="utf-8").splitlines()
    # Trimming keeps at most RING_KEEP_LINES; with the tiny threshold the file
    # was rewritten at least once and every surviving line still parses.
    assert 0 < len(lines) <= cmdlog.RING_KEEP_LINES
    assert all(json.loads(line)["command"] == "ls" for line in lines)


def test_finish_swallows_an_unwritable_log(isolated_state_dir, monkeypatch):
    # The never-raise contract: telemetry failure must not surface to the verb.
    monkeypatch.setattr(
        cmdlog, "cmdlog_path", lambda: Path("/proc/definitely/not/writable/cmdlog.jsonl")
    )
    cmdlog.finish(cmdlog.start(["ls"]), 0)  # must not raise


def test_tail_returns_newest_lines(isolated_state_dir):
    for index in range(5):
        cmdlog.finish(cmdlog.start([f"cmd{index}"]), 0)
    tail = cmdlog.tail(2)
    assert len(tail) == 2
    assert json.loads(tail[-1])["command"] == "cmd4"


def test_tail_of_missing_log_is_empty(isolated_state_dir):
    assert cmdlog.tail() == []


def test_entries_skips_torn_lines(isolated_state_dir):
    cmdlog.finish(cmdlog.start(["ls"]), 0)
    with cmdlog.cmdlog_path().open("a", encoding="utf-8") as handle:
        handle.write('{"command": "torn\n')  # a crashed writer's partial line
    records = cmdlog.entries()
    assert len(records) == 1
    assert records[0]["command"] == "ls"


def test_start_reads_the_markers_project(isolated_state_dir, tmp_path, monkeypatch):
    workdir = tmp_path / "repo"
    workdir.mkdir()
    (workdir / ".bm.yml").write_text("project: sample\nid: abc\n", encoding="utf-8")
    monkeypatch.chdir(workdir)
    assert cmdlog.start(["ls"]).project == "sample"
