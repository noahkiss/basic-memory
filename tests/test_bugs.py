"""Bug reports (GAPS U34): dedup, autocapture, recursion latch, followup."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from basic_memory import bugs
from basic_memory.bugs import BugConfig


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture
def bug_config(tmp_path) -> BugConfig:
    return BugConfig(dir=tmp_path / "bugs", autocapture=True, followup="")


def test_write_report_creates_a_file_with_context(bug_config):
    path, created = bugs.write_report(
        "bm undo restored the wrong file", command="undo", config=bug_config
    )

    assert created
    text = path.read_text(encoding="utf-8")
    assert "dedup-key:" in text
    assert "command: undo" in text
    assert "count: 1" in text
    assert "## cmdlog tail" in text
    assert "bm undo restored the wrong file" in text


def test_repeat_bumps_the_existing_report(bug_config):
    first, created_first = bugs.write_report("same failure", command="ls", config=bug_config)
    second, created_second = bugs.write_report("same failure", command="ls", config=bug_config)

    assert created_first and not created_second
    assert first == second
    assert "count: 2" in first.read_text(encoding="utf-8")
    assert len(list(bug_config.dir.glob("*.md"))) == 1


def test_different_shapes_get_different_files(bug_config):
    bugs.write_report("failure one", command="ls", config=bug_config)
    bugs.write_report("failure two", command="ls", config=bug_config)
    assert len(list(bug_config.dir.glob("*.md"))) == 2


def test_dedup_key_ignores_later_lines():
    key_one = bugs.dedup_key("ls", "ValueError", "boom\nat /tmp/path-a")
    key_two = bugs.dedup_key("ls", "ValueError", "boom\nat /tmp/path-b")
    assert key_one == key_two


def test_autocapture_writes_a_report_for_a_nonzero_exit(bug_config, monkeypatch):
    monkeypatch.setattr(bugs, "load_bug_config", lambda: bug_config)
    bugs.autocapture("ls", 2, None)
    reports = list(bug_config.dir.glob("*.md"))
    assert len(reports) == 1
    assert "kind: exit-2" in reports[0].read_text(encoding="utf-8")


def test_autocapture_respects_the_config_switch(tmp_path, monkeypatch):
    config = BugConfig(dir=tmp_path / "bugs", autocapture=False, followup="")
    monkeypatch.setattr(bugs, "load_bug_config", lambda: config)
    bugs.autocapture("ls", 1, None)
    assert not (tmp_path / "bugs").exists()


def test_autocapture_never_fires_for_the_bug_verb_itself(bug_config, monkeypatch):
    # The recursion story's first half: a failing `bm bug` must not report itself.
    monkeypatch.setattr(bugs, "load_bug_config", lambda: bug_config)
    bugs.autocapture("bug", 1, None)
    assert not bug_config.dir.exists()


def test_autocapture_swallows_its_own_failures(bug_config, monkeypatch):
    # The second half: a crash inside capture surfaces nothing and re-fires nothing.
    monkeypatch.setattr(bugs, "load_bug_config", lambda: bug_config)

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(bugs, "write_report", explode)
    bugs.autocapture("ls", 1, None)  # must not raise
    assert bugs._capturing is False  # the latch is released on the way out


def test_autocapture_carries_the_exception_shape(bug_config, monkeypatch):
    monkeypatch.setattr(bugs, "load_bug_config", lambda: bug_config)
    bugs.autocapture("new", 1, ValueError("bad frontmatter"))
    report = next(iter(bug_config.dir.glob("*.md"))).read_text(encoding="utf-8")
    assert "kind: ValueError" in report
    assert "bad frontmatter" in report


def test_followup_runs_from_the_bugs_dir(bug_config, monkeypatch):
    calls: list[dict] = []

    def record_run(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(subprocess, "run", record_run)
    settings = BugConfig(dir=bug_config.dir, autocapture=True, followup="echo synced")
    settings.dir.mkdir(parents=True)
    bugs.run_followup(settings)

    assert calls and calls[0]["args"][0] == "echo synced"
    assert calls[0]["kwargs"]["cwd"] == settings.dir


def test_empty_followup_is_a_no_op(bug_config, monkeypatch):
    def forbidden(*args, **kwargs):  # pragma: no cover - the assertion is that it never runs
        raise AssertionError("followup ran despite being empty")

    monkeypatch.setattr(subprocess, "run", forbidden)
    bugs.run_followup(bug_config)


def test_load_bug_config_defaults_survive_a_broken_config(monkeypatch, tmp_path):
    # A broken config is a prime moment to capture a bug, so loading degrades
    # to defaults instead of raising.
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(tmp_path / "data"))

    def explode():
        raise RuntimeError("config.json is torn")

    import basic_memory.config as config_module

    monkeypatch.setattr(config_module, "ConfigManager", explode)
    settings = bugs.load_bug_config()
    assert settings.dir == Path(tmp_path / "data") / "bugs"
    assert settings.autocapture is True
