"""Tests for the 'basic-memory history' CLI commands."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

import basic_memory.cli.commands.history as history_cmd  # noqa: F401
from basic_memory.cli.main import app as cli_app
from basic_memory.store.history import HistoryError, ensure_store_repo

runner = CliRunner()


@pytest.fixture
def store(monkeypatch, tmp_path: Path) -> Path:
    """A real store repo in a temp data dir — these tests exercise the git path."""
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(tmp_path / "data"))
    return ensure_store_repo()


def write(store: Path, relative: str, text: str) -> None:
    path = store / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def git(store: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(store), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_dirty_lists_paths_then_the_count(store: Path) -> None:
    write(store, "notes/one.md", "one\n")

    result = runner.invoke(cli_app, ["history", "dirty"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert result.stdout.splitlines() == ["notes/one.md  ??", "1 dirty files"]


def test_dirty_on_a_clean_store_is_a_result(store: Path) -> None:
    result = runner.invoke(cli_app, ["history", "dirty"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["0 dirty files"]


@patch("basic_memory.cli.commands.history.dirty_paths")
def test_dirty_reports_a_broken_repo_on_stderr(mock_dirty, store: Path) -> None:
    mock_dirty.side_effect = HistoryError("git is not installed or not on PATH")

    result = runner.invoke(cli_app, ["history", "dirty"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "git is not installed or not on PATH" in result.stderr


def test_commit_all_takes_everything_without_an_actor(store: Path) -> None:
    write(store, "notes/one.md", "one\n")
    write(store, "notes/two.md", "two\n")

    result = runner.invoke(cli_app, ["history", "commit", "--all"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines[0].startswith("sha: ")
    assert lines[1] == "paths: 2 committed"
    assert "Actor:" not in git(store, "log", "-1", "--format=%B")


def test_commit_of_one_path_notices_the_rest(store: Path) -> None:
    write(store, "notes/mine.md", "mine\n")
    write(store, "notes/theirs.md", "theirs\n")

    result = runner.invoke(cli_app, ["history", "commit", "notes/mine.md"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines[1] == "paths: 1 committed"
    assert lines[2] == (
        "note: 1 other files have uncommitted changes (not included in this commit)"
    )
    assert lines[3] == "run 'bm history dirty' to review"


def test_quiet_drops_the_notice_and_affordance(store: Path) -> None:
    write(store, "notes/mine.md", "mine\n")
    write(store, "notes/theirs.md", "theirs\n")

    result = runner.invoke(cli_app, ["history", "commit", "--quiet", "notes/mine.md"])

    assert result.exit_code == 0
    assert len(result.stdout.splitlines()) == 2


def test_commit_with_nothing_dirty_is_a_result(store: Path) -> None:
    result = runner.invoke(cli_app, ["history", "commit", "--all"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["nothing to commit"]


def test_commit_needs_paths_or_all(store: Path) -> None:
    result = runner.invoke(cli_app, ["history", "commit"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "--all" in result.stderr


def test_commit_refuses_paths_and_all_together(store: Path) -> None:
    write(store, "notes/one.md", "one\n")

    result = runner.invoke(cli_app, ["history", "commit", "--all", "notes/one.md"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "not both" in result.stderr
