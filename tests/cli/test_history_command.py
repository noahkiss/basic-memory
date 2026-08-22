"""Tests for the 'basic-memory history' CLI commands."""

import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

import basic_memory.cli.commands.history as history_cmd  # noqa: F401
from basic_memory.cli.main import app as cli_app
from basic_memory.project_registry import PROJECT_HOME_EXTERNAL, registry_database_path
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


# --- What this repository does not cover (skill-homed projects) ---


def register_project(name: str, home: str | None) -> None:
    """Add one project row to the registry these verbs read.

    Only the four columns the exclusion-line reader touches. It goes at the
    SQLite file with the stdlib driver — deliberately, to keep SQLAlchemy off
    `dirty` and `commit` — so a hand-built table is the honest fixture here;
    `tests/test_project_home_migration.py` is where the column meets Alembic.
    """
    database = registry_database_path()
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS project (name TEXT, path TEXT, is_active INT, home TEXT)"
        )
        connection.execute(
            "INSERT INTO project (name, path, is_active, home) VALUES (?, ?, 1, ?)",
            (name, f"/somewhere/{name}", home),
        )
        connection.commit()
    finally:
        connection.close()


def excluded_lines(output: str) -> list[str]:
    return [line for line in output.splitlines() if "homed elsewhere" in line]


def test_dirty_names_the_project_homed_elsewhere(store: Path) -> None:
    """No dirty file will ever be that project's, and silence would hide why."""
    register_project("skill-notes", PROJECT_HOME_EXTERNAL)

    result = runner.invoke(cli_app, ["history", "dirty"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert excluded_lines(result.stdout) == [
        "note: this history excludes 1 project homed elsewhere: skill-notes — yadm/git record it"
    ]


def test_dirty_says_nothing_when_no_project_is_homed_elsewhere(store: Path) -> None:
    """Positive control: a registry that could produce the line, and does not."""
    register_project("store-notes", None)

    result = runner.invoke(cli_app, ["history", "dirty"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert excluded_lines(result.stdout) == []


def test_commit_names_every_project_homed_elsewhere(store: Path) -> None:
    """Two of them, so the line's plural half is exercised and the order is sorted."""
    register_project("zebra-skill", PROJECT_HOME_EXTERNAL)
    register_project("alpha-skill", PROJECT_HOME_EXTERNAL)
    write(store, "notes/one.md", "one\n")

    result = runner.invoke(cli_app, ["history", "commit", "--all"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert excluded_lines(result.stdout) == [
        "note: this history excludes 2 projects homed elsewhere: alpha-skill, zebra-skill "
        "— yadm/git record them"
    ]


def test_commit_with_nothing_to_do_still_names_them(store: Path) -> None:
    """The empty sweep is exactly when "nothing there" needs the qualification."""
    register_project("skill-notes", PROJECT_HOME_EXTERNAL)

    result = runner.invoke(cli_app, ["history", "commit", "--all"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    lines = result.stdout.splitlines()
    assert lines[0] == "nothing to commit"
    assert len(excluded_lines(result.stdout)) == 1


def test_quiet_drops_the_exclusion_line(store: Path) -> None:
    """Contract rule 7: `--quiet` leaves the payload and nothing else."""
    register_project("skill-notes", PROJECT_HOME_EXTERNAL)

    result = runner.invoke(cli_app, ["history", "dirty", "--quiet"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    assert excluded_lines(result.stdout) == []
