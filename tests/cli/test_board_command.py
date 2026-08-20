"""Bare `bm` and `bm board` — the project at a glance (GAPS U37).

Real path throughout: the real Typer app (callback included, because bare `bm`
is the callback's route), a real registry, records written through `bm new`.
The claims are about the rendered shape — header, working order, closing
summary — since that shape is what a human and an agent read in one glance.
"""

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app

# Importing the modules registers the verbs these tests drive.
from basic_memory.cli.commands import board as board_command  # noqa: F401
from basic_memory.cli.commands import headline as _headline  # noqa: F401
from basic_memory.cli.commands import new as _new  # noqa: F401
from basic_memory.cli.commands import record_write as _record_write  # noqa: F401
from basic_memory.store.history import store_path

runner = CliRunner()

pytestmark = pytest.mark.usefixtures("bootstrapped_registry")

PROJECT = "boardland"


@pytest.fixture(autouse=True)
def unmarked_working_directory(tmp_path, monkeypatch):
    """Run from a directory with no `.bm.yml` above it, so nothing repoints scope."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def no_embeddings(monkeypatch):
    """Keep the ONNX embedding stack off the write path; the semantic suites own it."""
    monkeypatch.setenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", "false")


@dataclass(frozen=True)
class SeededProject:
    name: str
    external_id: str
    path: Path


@pytest.fixture
def project() -> SeededProject:
    """One store-derived, ungoverned project, registered as `bm project add` would."""

    async def _seed() -> SeededProject:
        from basic_memory import db
        from basic_memory.config import ConfigManager
        from basic_memory.repository.project_repository import ProjectRepository

        config = ConfigManager().config
        _, session_maker = await db.get_or_create_db(config.database_path, config=config)
        external_id = str(uuid.uuid4())
        home = store_path() / external_id
        home.mkdir(parents=True, exist_ok=True)
        async with db.scoped_session(session_maker) as session:
            await ProjectRepository().create(
                session,
                {
                    "name": PROJECT,
                    "external_id": external_id,
                    "path": str(home),
                    "is_active": True,
                    "is_default": False,
                },
            )
        return SeededProject(name=PROJECT, external_id=external_id, path=home)

    return asyncio.run(_seed())


def new_record(title: str, note_type: str = "task") -> str:
    result = runner.invoke(
        app, ["new", note_type, title, "--body", "seeded", "-p", PROJECT, "--quiet"]
    )
    assert result.exit_code == 0, result.output
    return result.stdout.strip().splitlines()[0].split()[0]


def mark(record_id: str, status: str) -> None:
    result = runner.invoke(app, ["mark", record_id, status, "-p", PROJECT, "--quiet"])
    assert result.exit_code == 0, result.output


def board(*args: str) -> str:
    result = runner.invoke(app, ["board", *args, "-p", PROJECT])
    assert result.exit_code == 0, result.output
    return result.stdout


def test_board_orders_doing_then_blocked_then_open(project: SeededProject) -> None:
    open_id = new_record("Waiting work")
    doing_id = new_record("Active work")
    blocked_id = new_record("Stuck work")
    mark(doing_id, "doing")
    mark(blocked_id, "blocked")

    lines = board().strip().splitlines()

    assert lines[0] == f"board: {PROJECT} · headline: (none set)"
    listed = [line.split()[0] for line in lines[1:4]]
    # Working order, not creation order: in motion, stuck, waiting.
    assert listed == [doing_id, blocked_id, open_id]
    assert "3 open items · shelved 0 · inbox 0" in lines


def test_board_counts_shelved_and_inbox_instead_of_listing_them(
    project: SeededProject,
) -> None:
    parked = new_record("Parked work")
    mark(parked, "shelved")
    new_record("Unfiled thing", note_type="inbox")

    output = board()

    # The parked task and the inbox record are counts, never rows (GAPS U23).
    assert parked not in output
    assert "0 open items · shelved 1 · inbox 1" in output


def test_board_lists_plans_inline_before_tasks_of_the_same_rank(
    project: SeededProject,
) -> None:
    """A plan rides the board with the tasks (GAPS U38): the `plan-` prefix
    labels it, and within a status rank the plan sorts first — it is the
    higher-altitude item its stages roll up into."""
    # Plan first, task second: recency alone would list the newer task first,
    # so the assertion below proves the altitude sort, not the tie-break.
    plan_id = new_record("The campaign", note_type="plan")
    task_id = new_record("A stage task")
    assert plan_id.startswith("plan-")

    lines = board().strip().splitlines()

    listed = [line.split()[0] for line in lines[1:3]]
    assert listed == [plan_id, task_id]
    # The affordance is the true last line (contract rule 3); the summary sits above it.
    assert any("2 open items" in line for line in lines)


def test_board_counts_a_shelved_plan_with_the_parked_pile(project: SeededProject) -> None:
    plan_id = new_record("Someday campaign", note_type="plan")
    mark(plan_id, "shelved")

    output = board()

    assert plan_id not in output
    assert "0 open items · shelved 1 · inbox 0" in output


def test_board_shows_the_composed_headline(project: SeededProject) -> None:
    result = runner.invoke(app, ["headline", "ship the board", "-p", PROJECT, "--quiet"])
    assert result.exit_code == 0, result.output

    assert board().splitlines()[0] == f'board: {PROJECT} · headline: "ship the board"'


def test_bare_bm_renders_the_board_for_a_marked_directory(
    project: SeededProject, tmp_path, monkeypatch
) -> None:
    marked = tmp_path / "repo"
    marked.mkdir()
    (marked / ".bm.yml").write_text(f"project: {PROJECT}\n", encoding="utf-8")
    monkeypatch.chdir(marked)

    result = runner.invoke(app, [])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == f"board: {PROJECT} · headline: (none set)"


def test_bare_bm_in_an_unmarked_directory_teaches_the_opt_in(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, [])

    # A fact, not an error (GAPS U37): one line, exit 0, the hook's own wording.
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == board_command.NOT_TRACKED


def test_board_quiet_hides_the_affordance(project: SeededProject) -> None:
    output = board("--quiet")

    assert board_command.BOARD_AFFORDANCE not in output


def test_help_still_prints_usage_not_the_board(project: SeededProject) -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Usage:" in result.stdout
    assert "board:" not in result.stdout


def test_unknown_project_is_an_addressing_failure(project: SeededProject) -> None:
    result = runner.invoke(app, ["board", "-p", "no-such-project"])

    assert result.exit_code == 1
    assert "Project not found" in result.stderr


def test_bare_invocation_logs_as_board() -> None:
    from basic_memory.cmdlog import command_path

    # Truly bare is the board; flags-only stays unnamed (`bm --version`).
    assert command_path([]) == "board"
    assert command_path(["--version"]) == "(none)"
