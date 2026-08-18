"""`bm undo` — the store-history undo verb (VERBS_PLAN item H, D4; GAPS W3).

These drive the real caller path end to end: a real store git repository, real
project rows, real files, the real Typer command, and the real project index.
Nothing stubs git and nothing stubs indexing, because the claims are about what
undo does to disk *and* to the database — a restored file that never reaches the
index is invisible to every read verb (GAPS T2), which is the failure this verb
exists to avoid.

The store repository is the only scope: `bm undo` takes no `--project`, because
one repository holds every project's notes.
"""

import asyncio
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app

# Importing the module registers `undo` on the app; the tests also read its constants.
from basic_memory.cli.commands import history as history_cmd

# `records` registers `ls`, which `ls_lines()` invokes. Without this import the
# file passes only when some other test file in the same worker imported it
# first — a pass by accident, and the whole file fails when run alone.
from basic_memory.cli.commands import records as _records  # noqa: F401
from basic_memory.store.history import commit_paths, ensure_store_repo

runner = CliRunner()

PROJECT = "notes"
SESSION = "sess-1111"
OTHER_SESSION = "sess-2222"

TASK_ID = "tnd-aaaa1111"
TASK_FILE = f"tasks/{TASK_ID}--move-backups.md"


def record(title: str, body: str) -> str:
    """One governed record: id and permalink equal, byte for byte (schema §2)."""
    return (
        "---\n"
        f"id: {TASK_ID}\n"
        f"permalink: {TASK_ID}\n"
        "type: task\n"
        f"title: {title}\n"
        "source: cli\n"
        "status: open\n"
        "---\n"
        f"\n{body}\n"
    )


FIRST = record("Move backups off-container", "The original body.")
SECOND = record("Move backups off-container v2", "The replacement body.")


# --- Fixtures ---


@pytest.fixture(autouse=True)
def unmarked_working_directory(tmp_path, monkeypatch) -> None:
    """Run from a directory with no `.bm.yml` above it, so nothing pins a scope."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def store(isolated_home, monkeypatch) -> Path:
    """A real store repo and a migrated registry, with embeddings off.

    The bootstrap runs here rather than through `bootstrapped_registry` so the
    environment is already in place when the config is first read: undo reindexes
    for real, and `semantic_search_enabled` defaults to True wherever fastembed is
    importable, which would make every test below pay the ONNX embedding stack.
    """
    monkeypatch.setenv("BASIC_MEMORY_SEMANTIC_SEARCH_ENABLED", "false")

    from basic_memory import config as config_module

    config_module._CONFIG_CACHE = None
    config_module._CONFIG_MTIME = None
    config_module._CONFIG_SIZE = None

    async def bootstrap() -> None:
        from basic_memory import db
        from basic_memory.config import ConfigManager
        from basic_memory.services.initialization import ensure_project_registry

        try:
            await ensure_project_registry(ConfigManager().config)
        finally:
            await db.shutdown_db()

    asyncio.run(bootstrap())
    return ensure_store_repo()


@pytest.fixture
def project(store: Path) -> Path:
    """A project whose files live inside the store, which is where W3 can see them."""
    home = store / PROJECT
    home.mkdir(parents=True, exist_ok=True)

    async def create() -> None:
        from basic_memory import db
        from basic_memory.config import ConfigManager
        from basic_memory.repository.project_repository import ProjectRepository

        config = ConfigManager().config
        _, session_maker = await db.get_or_create_db(config.database_path, config=config)
        try:
            async with db.scoped_session(session_maker) as session:
                repository = ProjectRepository()
                await repository.create(
                    session,
                    {
                        "name": PROJECT,
                        "path": str(home),
                        "is_active": True,
                        "is_default": False,
                    },
                )
        finally:
            await db.shutdown_db()

    asyncio.run(create())
    return home


# --- Helpers ---


def git(store: Path, *args: str) -> str:
    """Read the store repo with a plain git call, independent of the module."""
    return subprocess.run(
        ["git", "-C", str(store), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def write(project_home: Path, relative: str, text: str) -> str:
    """Write one note and return the path git stages for it."""
    target = project_home / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return f"{PROJECT}/{relative}"


def commit(paths: list[str], message: str, session: str | None = None) -> str:
    result = commit_paths(paths, message, actor="agent", session_id=session)
    assert result is not None, f"nothing to commit for {paths}"
    return result.sha


def index() -> None:
    """Bring the database level with disk, the way a real earlier write would have."""

    async def run() -> None:
        from basic_memory import db
        from basic_memory.config import ConfigManager
        from basic_memory.index.local_project import (
            LocalProjectIndexRuntimeFactory,
            run_local_project_index_for_project,
        )
        from basic_memory.repository.project_repository import ProjectRepository

        config = ConfigManager().config
        _, session_maker = await db.get_or_create_db(config.database_path, config=config)
        try:
            async with db.scoped_session(session_maker) as session:
                row = await ProjectRepository().get_by_name(session, PROJECT)
            assert row is not None
            await run_local_project_index_for_project(
                row, runtime_factory=LocalProjectIndexRuntimeFactory()
            )
        finally:
            await db.shutdown_db()

    asyncio.run(run())


def search_titles(text: str) -> list[str]:
    """Full-text search over the project, returned as the titles that matched."""

    async def run() -> list[str]:
        from basic_memory import db
        from basic_memory.config import ConfigManager
        from basic_memory.repository.project_repository import ProjectRepository
        from basic_memory.repository.search_repository import create_search_repository

        config = ConfigManager().config
        _, session_maker = await db.get_or_create_db(config.database_path, config=config)
        try:
            async with db.scoped_session(session_maker) as session:
                row = await ProjectRepository().get_by_name(session, PROJECT)
            assert row is not None
            repository = create_search_repository(
                session_maker, project_id=row.id, app_config=config
            )
            rows = await repository.search(search_text=text)
            return [hit.title for hit in rows if hit.title]
        finally:
            await db.shutdown_db()

    return asyncio.run(run())


def ls_lines() -> list[str]:
    """What `bm ls` reports for the project — the read verb's view of the index."""
    result = runner.invoke(app, ["ls", "--project", PROJECT, "--quiet"])
    assert result.exit_code == 0, result.output
    return result.stdout.strip().splitlines()


# --- One commit: content goes back ---


def test_undo_restores_the_previous_content(project: Path) -> None:
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks")
    write(project, TASK_FILE, SECOND)
    commit([path], "update notes/tasks")

    result = runner.invoke(app, ["undo", "--quiet"])

    assert result.exit_code == 0, result.output
    assert (project / TASK_FILE).read_text(encoding="utf-8") == FIRST


def test_undo_prints_the_sha_and_path_then_the_count(project: Path) -> None:
    """Contract rules 1-3: one record per line, and the count closes the listing."""
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks")
    write(project, TASK_FILE, SECOND)
    undone = commit([path], "update notes/tasks")

    result = runner.invoke(app, ["undo", "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [f"{undone}  {path}", "1 files restored"]


def test_undo_adds_a_commit_rather_than_resetting(project: Path) -> None:
    """W3: history is the thing being protected, so undoing a change grows it."""
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks")
    write(project, TASK_FILE, SECOND)
    undone = commit([path], "update notes/tasks")
    before = len(git(project.parent, "log", "--format=%H").split())

    result = runner.invoke(app, ["undo", "--quiet"])

    assert result.exit_code == 0, result.output
    log = git(project.parent, "log", "--format=%H").split()
    assert len(log) == before + 1
    assert undone in log, "the undone commit must still be in the history"
    assert git(project.parent, "log", "-1", "--format=%s").strip() == f"undo {undone}"


def test_the_undo_commit_names_the_cli_and_carries_no_session(project: Path) -> None:
    """An undo corrects a session's work rather than joining it (see the verb)."""
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks", session=SESSION)
    write(project, TASK_FILE, SECOND)
    commit([path], "update notes/tasks", session=SESSION)

    assert runner.invoke(app, ["undo", "--quiet"]).exit_code == 0

    message = git(project.parent, "log", "-1", "--format=%B")
    assert "Actor: cli" in message
    assert "Session:" not in message


def test_undoing_the_undo_puts_the_change_back(project: Path) -> None:
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks")
    write(project, TASK_FILE, SECOND)
    commit([path], "update notes/tasks")

    assert runner.invoke(app, ["undo", "--quiet"]).exit_code == 0
    assert (project / TASK_FILE).read_text(encoding="utf-8") == FIRST

    assert runner.invoke(app, ["undo", "--quiet"]).exit_code == 0
    assert (project / TASK_FILE).read_text(encoding="utf-8") == SECOND


# --- A commit that only added a file becomes a deletion ---


def test_undo_of_a_create_deletes_the_file(project: Path) -> None:
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks")

    result = runner.invoke(app, ["undo", "--quiet"])

    assert result.exit_code == 0, result.output
    assert not (project / TASK_FILE).exists()


# --- The database follows disk (GAPS T2) ---


def test_undo_of_a_create_takes_the_record_out_of_the_index(project: Path) -> None:
    """Positive control first: the record is listed before undo and gone after."""
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks")
    index()

    assert ls_lines()[-1] == "1 record"

    assert runner.invoke(app, ["undo", "--quiet"]).exit_code == 0

    assert ls_lines() == ["0 records"]


def test_undo_reindexes_restored_content_so_search_finds_it_again(project: Path) -> None:
    """A restored file that never reached the index is invisible to search."""
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks")
    write(project, TASK_FILE, SECOND)
    commit([path], "update notes/tasks")
    index()

    # Positive control: the newer title is what search answers with before undo.
    assert any("v2" in title for title in search_titles("backups"))

    assert runner.invoke(app, ["undo", "--quiet"]).exit_code == 0

    titles = search_titles("backups")
    assert titles, "the restored note must still be indexed"
    assert not any("v2" in title for title in titles)


# --- --session walks every commit that session recorded ---


def test_session_undo_walks_every_matching_commit_newest_first(project: Path) -> None:
    """The store ends on the content it held before the session began."""
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks", session=OTHER_SESSION)
    write(project, TASK_FILE, SECOND)
    commit([path], "update notes/tasks", session=SESSION)
    third = write(project, "tasks/tnd-bbbb2222--rotate.md", record("Rotate the deploy key", "New."))
    commit([third], "create notes/tasks", session=SESSION)

    result = runner.invoke(app, ["undo", "--session", SESSION, "--yes", "--quiet"])

    assert result.exit_code == 0, result.output
    # The other session's commit is untouched, so its file survives with its content.
    assert (project / TASK_FILE).read_text(encoding="utf-8") == FIRST
    assert not (project / "tasks/tnd-bbbb2222--rotate.md").exists()
    assert result.stdout.splitlines()[-1] == "2 files restored"


def test_session_undo_of_one_commit_needs_no_confirmation(project: Path) -> None:
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks", session=OTHER_SESSION)
    write(project, TASK_FILE, SECOND)
    commit([path], "update notes/tasks", session=SESSION)

    result = runner.invoke(app, ["undo", "--session", SESSION, "--quiet"])

    assert result.exit_code == 0, result.output
    assert (project / TASK_FILE).read_text(encoding="utf-8") == FIRST


def test_more_than_one_commit_is_refused_without_yes(project: Path) -> None:
    """Rule 6: the refusal is the whole output — nothing lands on stdout."""
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks", session=SESSION)
    write(project, TASK_FILE, SECOND)
    commit([path], "update notes/tasks", session=SESSION)

    result = runner.invoke(app, ["undo", "--session", SESSION])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "undo 2 commits" in result.stderr
    assert "--yes" in result.stderr
    # Nothing was touched: the refusal came before the restore.
    assert (project / TASK_FILE).read_text(encoding="utf-8") == SECOND


def test_a_session_id_that_is_a_prefix_of_another_matches_nothing(project: Path) -> None:
    """The grep is anchored to a whole trailer line, so a prefix cannot bleed in."""
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks", session=f"{SESSION}-longer")

    result = runner.invoke(app, ["undo", "--session", SESSION, "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == "nothing to undo"
    assert (project / TASK_FILE).read_text(encoding="utf-8") == FIRST


# --- An uncommitted edit is never discarded (review H2) ---


def test_an_uncommitted_edit_to_a_restored_path_is_refused(project: Path) -> None:
    """`git checkout` would overwrite it silently, with nothing in the history."""
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks")
    write(project, TASK_FILE, SECOND)
    head = git(project.parent, "rev-parse", "HEAD").strip()
    by_hand = record("Edited by hand", "Not committed by anything.")
    (project / TASK_FILE).write_text(by_hand, encoding="utf-8")

    result = runner.invoke(app, ["undo"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert path in result.stderr
    assert "bm history commit" in result.stderr
    # Nothing was touched: the refusal came before the restore, and no commit ran.
    assert (project / TASK_FILE).read_text(encoding="utf-8") == by_hand
    assert git(project.parent, "rev-parse", "HEAD").strip() == head


def test_an_uncommitted_file_outside_the_target_set_does_not_block(project: Path) -> None:
    """Positive control: only the paths the restore would overwrite can refuse it."""
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks")
    write(project, TASK_FILE, SECOND)
    commit([path], "update notes/tasks")
    elsewhere = project / "tasks/tnd-bbbb2222--rotate.md"
    elsewhere.write_text(record("Rotate the deploy key", "Never committed."), encoding="utf-8")

    result = runner.invoke(app, ["undo", "--quiet"])

    assert result.exit_code == 0, result.output
    assert (project / TASK_FILE).read_text(encoding="utf-8") == FIRST
    assert elsewhere.exists(), "an unrelated uncommitted file must survive untouched"


def test_the_dirty_refusal_comes_before_the_confirmation_gate(project: Path) -> None:
    """A --yes cannot clear it, so saying so first beats asking for a flag."""
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks", session=SESSION)
    write(project, TASK_FILE, SECOND)
    commit([path], "update notes/tasks", session=SESSION)
    (project / TASK_FILE).write_text(record("Edited by hand", "Uncommitted."), encoding="utf-8")

    result = runner.invoke(app, ["undo", "--session", SESSION])

    assert result.exit_code == 1
    assert "uncommitted changes" in result.stderr
    assert "--yes" not in result.stderr


# --- Empties are results, not failures (contract rule 5) ---


def test_nothing_to_undo_on_an_empty_store_is_a_result(store: Path) -> None:
    result = runner.invoke(app, ["undo", "--quiet"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["nothing to undo"]


def test_an_unknown_session_is_a_result(project: Path) -> None:
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks", session=SESSION)

    result = runner.invoke(app, ["undo", "--session", "sess-nothing", "--quiet"])

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["nothing to undo"]
    assert (project / TASK_FILE).exists()


# --- Notices and --quiet (contract rules 4 and 7) ---


def test_the_new_commit_and_the_affordance_follow_the_payload(project: Path) -> None:
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks")
    write(project, TASK_FILE, SECOND)
    commit([path], "update notes/tasks")

    result = runner.invoke(app, ["undo"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[1] == "1 files restored"
    assert lines[2].startswith("note: recorded as ")
    assert lines[-1] == history_cmd.UNDO_AFFORDANCE


def test_quiet_leaves_the_payload_alone(project: Path) -> None:
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks")
    write(project, TASK_FILE, SECOND)
    commit([path], "update notes/tasks")

    result = runner.invoke(app, ["undo", "--quiet"])

    assert result.exit_code == 0, result.output
    assert len(result.stdout.splitlines()) == 2
    assert history_cmd.UNDO_AFFORDANCE not in result.stdout


def test_a_restore_that_changes_nothing_says_so(project: Path) -> None:
    """The file already holds the restored content, so there is no diff to commit."""
    path = write(project, TASK_FILE, FIRST)
    commit([path], "create notes/tasks")
    write(project, TASK_FILE, SECOND)
    commit([path], "update notes/tasks", session=SESSION)
    write(project, TASK_FILE, FIRST)
    commit([path], "update notes/tasks")

    # The session's one commit changed the file to SECOND; the file is back at
    # FIRST already, which is exactly what restoring that commit asks for.
    result = runner.invoke(app, ["undo", "--session", SESSION])

    assert result.exit_code == 0, result.output
    assert "no commit was written" in result.stdout
    assert (project / TASK_FILE).read_text(encoding="utf-8") == FIRST


def test_a_path_in_no_registered_project_is_named_as_unindexable(store: Path) -> None:
    """A restore outside every project is honest about what it could not reindex."""
    loose = store / "loose"
    loose.mkdir(parents=True, exist_ok=True)
    (loose / "note.md").write_text("loose\n", encoding="utf-8")
    commit(["loose/note.md"], "create loose/note.md")

    result = runner.invoke(app, ["undo"])

    assert result.exit_code == 0, result.output
    assert "in no registered project" in result.stdout
    assert not (loose / "note.md").exists()
