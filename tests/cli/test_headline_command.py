"""`bm headline` — the composed one-line "what's next" (GAPS U24).

Real path throughout: the real Typer command, a real registry, a real store
directory, and the real history repo for the commit the verb makes.
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app

# Importing the module registers the verb this file drives.
from basic_memory.cli.commands import headline as headline_command  # noqa: F401
from basic_memory.services.headline import MAX_HEADLINE_CHARS, headline_path
from basic_memory.store.history import store_path

runner = CliRunner()

pytestmark = pytest.mark.usefixtures("bootstrapped_registry")

PROJECT = "headlined"


@pytest.fixture(autouse=True)
def unmarked_working_directory(tmp_path, monkeypatch):
    """Run from a directory with no `.bm.yml` above it, so nothing repoints scope."""
    monkeypatch.chdir(tmp_path)


@dataclass(frozen=True)
class SeededProject:
    """One store-derived project, registered the way `bm project add` leaves it."""

    name: str
    external_id: str
    path: Path


def seed_project(name: str = PROJECT) -> SeededProject:
    """Register one project homed at `store/<external_id>/`."""

    async def _seed() -> SeededProject:
        from basic_memory import db
        from basic_memory.config import ConfigManager
        from basic_memory.repository.project_repository import ProjectRepository

        config = ConfigManager().config
        _, session_maker = await db.get_or_create_db(config.database_path, config=config)
        try:
            external_id = str(uuid.uuid4())
            home = store_path() / external_id
            home.mkdir(parents=True, exist_ok=True)
            async with db.scoped_session(session_maker) as session:
                await ProjectRepository().create(
                    session,
                    {
                        "name": name,
                        "external_id": external_id,
                        "path": str(home),
                        "is_active": True,
                        "is_default": False,
                    },
                )
            return SeededProject(name=name, external_id=external_id, path=home)
        finally:
            await db.shutdown_db()

    return asyncio.run(_seed())


def store_git(*args: str) -> str:
    """Read the store repo with a plain git call, independent of the module."""
    return subprocess.run(
        ["git", "-C", str(store_path()), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


# --- Set ---


def test_set_writes_the_file_and_echoes_the_line() -> None:
    project = seed_project()

    result = runner.invoke(app, ["headline", "ship the verbs", "-p", PROJECT])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == 'headline: "ship the verbs"'
    lines = headline_path(project.external_id).read_text(encoding="utf-8").splitlines()
    assert lines == ["---", "headline: ship the verbs", "---"]


def test_set_commits_to_the_note_history() -> None:
    """The headline lives in the store's worktree, so every change is recoverable."""
    project = seed_project()

    result = runner.invoke(app, ["headline", "ship the verbs", "-p", PROJECT, "--quiet"])

    assert result.exit_code == 0, result.output
    assert store_git("log", "-1", "--format=%s").strip() == (
        f"headline {project.external_id}/headline.md"
    )
    assert store_git("status", "--porcelain", "-uall").strip() == ""


def test_an_over_limit_headline_is_an_error_not_a_truncation() -> None:
    """GAPS U24: a line nobody wrote must never reach the statusline."""
    project = seed_project()
    over = "x" * (MAX_HEADLINE_CHARS + 1)

    result = runner.invoke(app, ["headline", over, "-p", PROJECT])

    assert result.exit_code == 1
    assert "31 chars" in result.stderr
    assert str(MAX_HEADLINE_CHARS) in result.stderr
    assert result.stdout.strip() == ""
    assert not headline_path(project.external_id).exists()


# --- Clear ---


def test_an_empty_argument_clears_the_headline() -> None:
    project = seed_project()
    runner.invoke(app, ["headline", "ship the verbs", "-p", PROJECT, "--quiet"])

    result = runner.invoke(app, ["headline", "", "-p", PROJECT, "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == "headline cleared"
    assert not headline_path(project.external_id).exists()
    # The deletion is committed too: a cleared headline must not report as a
    # dirty store file on every later write.
    assert store_git("status", "--porcelain", "-uall").strip() == ""


def test_clearing_an_absent_headline_is_a_no_op() -> None:
    seed_project()
    # Set and clear once so the store repo exists and the file is absent again.
    runner.invoke(app, ["headline", "ship it", "-p", PROJECT, "--quiet"])
    runner.invoke(app, ["headline", "", "-p", PROJECT, "--quiet"])
    before = store_git("log", "--format=%H").strip()

    result = runner.invoke(app, ["headline", "", "-p", PROJECT, "--quiet"])

    assert result.exit_code == 0, result.output
    # Nothing changed, so nothing was committed.
    assert store_git("log", "--format=%H").strip() == before


# --- Show ---


def test_bare_shows_the_current_line_and_teaches_the_shape() -> None:
    seed_project()
    runner.invoke(app, ["headline", "ship the verbs", "-p", PROJECT, "--quiet"])

    result = runner.invoke(app, ["headline", "-p", PROJECT])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == 'headline: "ship the verbs"'
    assert f"max {MAX_HEADLINE_CHARS} chars" in result.stdout


def test_bare_says_none_set_when_there_is_none() -> None:
    seed_project()

    result = runner.invoke(app, ["headline", "-p", PROJECT])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == "headline: (none set)"


def test_quiet_bare_prints_only_the_payload() -> None:
    seed_project()

    result = runner.invoke(app, ["headline", "-p", PROJECT, "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "headline: (none set)"


# --- Addressing failures ---


def test_an_unknown_project_is_an_error() -> None:
    result = runner.invoke(app, ["headline", "ship it", "-p", "no-such-project"])

    assert result.exit_code == 1
    assert "no-such-project" in result.stderr
    assert result.stdout.strip() == ""
