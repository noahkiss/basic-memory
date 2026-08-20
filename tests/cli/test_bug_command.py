"""`bm bug` (GAPS U34): the report path is the payload; repeats bump, not multiply."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from basic_memory import bugs
from basic_memory.bugs import BugConfig
from basic_memory.cli.app import app
from basic_memory.cli.commands import bug as bug_command  # noqa: F401 — registers the verb

runner = CliRunner()


@pytest.fixture
def bug_config(tmp_path, monkeypatch) -> BugConfig:
    config = BugConfig(dir=tmp_path / "bugs", autocapture=True, followup="")
    monkeypatch.setattr(bugs, "load_bug_config", lambda: config)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return config


def test_bug_writes_a_report_and_prints_its_path(bug_config):
    result = runner.invoke(app, ["bug", "bm ls printed a stack trace"])

    assert result.exit_code == 0
    reported = bug_config.dir / result.stdout.splitlines()[0].split("/")[-1]
    assert reported.exists()
    assert "report filed" in result.stdout


def test_repeat_report_says_it_bumped(bug_config):
    runner.invoke(app, ["bug", "same shape"])
    result = runner.invoke(app, ["bug", "same shape"])

    assert result.exit_code == 0
    assert "known failure" in result.stdout
    assert len(list(bug_config.dir.glob("*.md"))) == 1


def test_quiet_prints_only_the_path(bug_config):
    result = runner.invoke(app, ["bug", "quiet shape", "--quiet"])
    assert result.exit_code == 0
    assert len(result.stdout.strip().splitlines()) == 1


def test_empty_message_is_refused(bug_config):
    result = runner.invoke(app, ["bug", "   "])
    assert result.exit_code == 1
    assert not list(bug_config.dir.glob("*.md")) if bug_config.dir.exists() else True
