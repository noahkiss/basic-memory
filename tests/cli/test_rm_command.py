"""`bm rm` — recoverable record deletion (GAPS U27).

Real path throughout, the same stance as the other write-verb suites: the real
Typer command, the real write stack, a real store repository, real files. The
claims are about disk, index and history together — a deletion that leaves any
one of the three behind is the failure the verb exists to avoid.
"""

import asyncio
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app

# Importing the modules registers the verbs these tests drive. `records` is for
# `bm ls`, `history` for `bm undo`, `new` for seeding through the tool itself.
from basic_memory.cli.commands import history as _history  # noqa: F401
from basic_memory.cli.commands import new as _new  # noqa: F401
from basic_memory.cli.commands import records as _records  # noqa: F401
from basic_memory.cli.commands import rm as rm_command  # noqa: F401
from basic_memory.store.history import store_path

runner = CliRunner()

pytestmark = pytest.mark.usefixtures("bootstrapped_registry")

PROJECT = "rmland"


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


def new_record(title: str) -> str:
    """Create one task through the tool itself and return its id."""
    result = runner.invoke(
        app, ["new", "task", title, "--body", "seeded", "-p", PROJECT, "--quiet"]
    )
    assert result.exit_code == 0, result.output
    record_id = result.stdout.strip().splitlines()[0].split()[0]
    assert record_id.startswith("task-"), result.stdout  # U30
    return record_id


def record_file(project: SeededProject, record_id: str) -> Path:
    matches = list(project.path.rglob(f"{record_id}*.md"))
    assert len(matches) == 1, matches
    return matches[0]


def ls_ids() -> list[str]:
    result = runner.invoke(app, ["ls", "-p", PROJECT, "--quiet"])
    assert result.exit_code == 0, result.output
    return [line.split()[0] for line in result.stdout.strip().splitlines()[:-1]]


def store_log() -> str:
    return subprocess.run(
        ["git", "-C", str(store_path()), "log", "--format=%s"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_rm_removes_the_file_the_index_row_and_commits_the_deletion(
    project: SeededProject,
) -> None:
    record_id = new_record("Delete me")
    path = record_file(project, record_id)

    result = runner.invoke(app, ["rm", record_id, "-p", PROJECT])

    assert result.exit_code == 0, result.output
    assert f"{record_id}  task  deleted" in result.stdout
    assert "1 deleted" in result.stdout
    # Disk, index and history all agree the record is gone (GAPS U27).
    assert not path.exists()
    assert record_id not in ls_ids()
    assert store_log().splitlines()[0].startswith("delete ")


def test_rm_is_recoverable_through_undo(project: SeededProject) -> None:
    """The deletion commit is what makes the verb safe: undo puts it back."""
    record_id = new_record("Delete then restore")
    path = record_file(project, record_id)
    body = path.read_text(encoding="utf-8")

    assert runner.invoke(app, ["rm", record_id, "-p", PROJECT, "--quiet"]).exit_code == 0
    assert not path.exists()

    assert runner.invoke(app, ["undo", "--quiet"]).exit_code == 0
    assert path.read_text(encoding="utf-8") == body
    assert record_id in ls_ids()


def test_rm_deletes_several_ids_in_one_run(project: SeededProject) -> None:
    first = new_record("First")
    second = new_record("Second")

    result = runner.invoke(app, ["rm", first, second, "-p", PROJECT, "--quiet"])

    assert result.exit_code == 0, result.output
    assert "2 deleted" in result.stdout
    assert ls_ids() == []


def test_an_unknown_id_fails_that_id_and_still_deletes_the_rest(
    project: SeededProject,
) -> None:
    record_id = new_record("Survivor batch")

    result = runner.invoke(app, ["rm", "tnd-nope0000", record_id, "-p", PROJECT, "--quiet"])

    # Exit 1 because one id failed, but the resolvable id was still deleted
    # (GAPS U27: a typo in one id is not a reason to keep the other nine).
    assert result.exit_code == 1
    assert "no record 'tnd-nope0000'" in result.output
    assert f"{record_id}  task  deleted" in result.stdout
    assert "1 deleted, 1 failed" in result.stdout
    assert record_id not in ls_ids()


def test_uncommitted_changes_on_the_target_are_refused(project: SeededProject) -> None:
    record_id = new_record("Hand-edited")
    path = record_file(project, record_id)
    path.write_text(path.read_text(encoding="utf-8") + "\nan uncommitted edit\n")

    result = runner.invoke(app, ["rm", record_id, "-p", PROJECT, "--quiet"])

    # The history holds only committed content, so deleting now would lose the
    # edit with nothing to restore — same refusal `bm undo` makes (W3-B).
    assert result.exit_code == 1
    assert "uncommitted changes" in result.output
    assert "bm history commit --all" in result.output
    assert path.exists()
    assert record_id in ls_ids()


def test_quiet_keeps_the_payload_and_drops_the_hints(project: SeededProject) -> None:
    record_id = new_record("Quietly removed")

    result = runner.invoke(app, ["rm", record_id, "-p", PROJECT, "--quiet"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.strip().splitlines()
    assert lines[0] == f"{record_id}  task  deleted"
    assert lines[-1] == "1 deleted"
    assert rm_command.RM_AFFORDANCE not in result.stdout


def test_the_affordance_names_the_recovery_path(project: SeededProject) -> None:
    record_id = new_record("Loudly removed")

    result = runner.invoke(app, ["rm", record_id, "-p", PROJECT])

    assert result.exit_code == 0, result.output
    assert rm_command.RM_AFFORDANCE in result.stdout
