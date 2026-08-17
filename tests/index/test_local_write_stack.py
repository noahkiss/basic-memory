"""The local accepted-note write stack, driven end to end (verbs item A, GAPS T29).

Every test here calls `LocalNoteWriteStack` the way a native verb will: a real
project, the real accepted-note runner, real files on disk, a real index. Nothing
is mocked, because the two failures this stack exists to prevent are both
structural — a write that lands in the database with no file (GAPS T12), and a
write path that reaches the service layer through fastapi (GAPS T18).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.index.local_write_stack import (
    LocalNoteWriteError,
    LocalNoteWriteStack,
    build_local_note_write_stack,
)
from basic_memory.models import Entity, Project, Violation
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.schemas.base import Entity as EntitySchema
from basic_memory.schemas.request import EditEntityRequest
from basic_memory.schemas.search import SearchQuery
from basic_memory.services.headline import headline_path
from basic_memory.store.history import store_path
from basic_memory.store.write_hook import OFF_STORE_NOTICE
from basic_memory.vocabulary.model import vocabulary_path

# The common four the checker requires, with a permalink equal to the id
# byte-for-byte. A frontmatter permalink nothing else claims is honoured verbatim.
BASE_FRONTMATTER: dict[str, str] = {
    "id": "tnd-0001",
    "permalink": "tnd-0001",
    "source": "cli",
}


def note_content(body: str, **fields: Any) -> str:
    """Note markdown, carrying an explicit frontmatter block when fields are given."""
    if not fields:
        return f"{body}\n"
    dumped = yaml.safe_dump(dict(fields), sort_keys=True)
    return f"---\n{dumped}---\n\n{body}\n"


def guide_frontmatter(**overrides: str) -> dict[str, str]:
    """A conforming `guide` record's frontmatter, which the checker accepts whole."""
    return {**BASE_FRONTMATTER, "type": "guide", "review-by": "2027-01-01", **overrides}


@pytest.fixture
def write_stack(
    app_config: BasicMemoryConfig,
    session_maker: async_sessionmaker[AsyncSession],
) -> LocalNoteWriteStack:
    """The stack under test, built from the same two inputs a verb would pass."""
    return build_local_note_write_stack(app_config, session_maker)


@pytest.fixture
def govern_project(test_project: Project):
    """Give the test project a vocabulary file, which is what governs it."""

    def _govern(**content: Any) -> Path:
        path = vocabulary_path(test_project.external_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(content), encoding="utf-8")
        return path

    return _govern


def written_notes(test_project: Project) -> list[Path]:
    """Every markdown file the project has on disk."""
    return sorted(Path(test_project.path).rglob("*.md"))


async def entity_rows(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> Sequence[Entity]:
    """Every entity row the project owns."""
    async with db.scoped_session(session_maker) as session:
        return await EntityRepository(project_id=test_project.id).find_all(session)


async def violation_rows(
    session_maker: async_sessionmaker[AsyncSession],
    test_project: Project,
) -> list[Violation]:
    """Every persisted violation row the project owns."""
    async with db.scoped_session(session_maker) as session:
        result = await session.execute(
            select(Violation).where(Violation.project_id == test_project.id)
        )
        return list(result.scalars().all())


# --- The two calls, in order: rows accepted, then a file on disk and indexed ---


@pytest.mark.asyncio
async def test_create_lands_on_disk_and_in_the_index(write_stack, test_project, search_service):
    """A create writes the file and indexes it, not just the row.

    The row-only outcome is GAPS T12: a caller that runs the mutation service
    without the materializer leaves a note that no `ls` and no search can find.
    """
    result = await write_stack.write_note(
        project_external_id=test_project.external_id,
        data=EntitySchema(
            title="Restore A Backup",
            directory="notes",
            content=note_content("How to restore a backup."),
        ),
    )

    assert result.title == "Restore A Backup"
    assert result.file_path == "notes/Restore A Backup.md"
    assert result.permalink is not None
    assert result.entity_id > 0

    on_disk = Path(test_project.path, result.file_path)
    assert on_disk.is_file()
    assert "How to restore a backup." in on_disk.read_text(encoding="utf-8")

    hits = await search_service.search(SearchQuery(text="restore a backup"))
    assert [hit.permalink for hit in hits if hit.permalink == result.permalink]


@pytest.mark.asyncio
async def test_update_replaces_content_on_disk_and_in_the_index(
    write_stack, test_project, search_service
):
    """A full replacement rewrites the file, and the index follows it."""
    created = await write_stack.write_note(
        project_external_id=test_project.external_id,
        data=EntitySchema(
            title="Rotate The Keys",
            directory="notes",
            content=note_content("Original body about pelicans."),
        ),
    )

    updated = await write_stack.update_note(
        project_external_id=test_project.external_id,
        entity_external_id=created.external_id,
        data=EntitySchema(
            title="Rotate The Keys",
            directory="notes",
            content=note_content("Replacement body about kingfishers."),
        ),
    )

    assert updated.external_id == created.external_id
    on_disk = Path(test_project.path, updated.file_path)
    body = on_disk.read_text(encoding="utf-8")
    assert "kingfishers" in body
    assert "pelicans" not in body

    hits = await search_service.search(SearchQuery(text="kingfishers"))
    assert [hit.permalink for hit in hits if hit.permalink == updated.permalink]
    assert await search_service.search(SearchQuery(text="pelicans")) == []


@pytest.mark.asyncio
async def test_edit_appends_to_the_file_and_reindexes(write_stack, test_project, search_service):
    """An append edit reaches disk and the index without a full replacement."""
    created = await write_stack.write_note(
        project_external_id=test_project.external_id,
        data=EntitySchema(
            title="Deploy Checklist",
            directory="notes",
            content=note_content("First step."),
        ),
    )

    edited = await write_stack.edit_note(
        project_external_id=test_project.external_id,
        entity_external_id=created.external_id,
        data=EditEntityRequest(operation="append", content="\nSecond step: verify the rollback."),
    )

    body = Path(test_project.path, edited.file_path).read_text(encoding="utf-8")
    assert "First step." in body
    assert "verify the rollback" in body

    hits = await search_service.search(SearchQuery(text="rollback"))
    assert [hit.permalink for hit in hits if hit.permalink == edited.permalink]


@pytest.mark.asyncio
async def test_forward_reference_resolves_before_the_write_returns(
    write_stack, test_project, session_maker
):
    """Relation resolution is awaited, not scheduled.

    The router schedules this on the event loop; a CLI process exits when the
    verb returns, so a scheduled pass would never run and the inbound edge would
    stay a forward reference until some unrelated later write.
    """
    pointer_result = await write_stack.write_note(
        project_external_id=test_project.external_id,
        data=EntitySchema(
            title="Pointer Note",
            directory="notes",
            content="Body.\n\n## Relations\n- references [[Target Note]]\n",
        ),
    )

    target = await write_stack.write_note(
        project_external_id=test_project.external_id,
        data=EntitySchema(
            title="Target Note",
            directory="notes",
            content=note_content("The target."),
        ),
    )

    async with db.scoped_session(session_maker) as session:
        pointer = await EntityRepository(project_id=test_project.id).get_by_external_id(
            session,
            pointer_result.external_id,
            load_relations=True,
        )
    assert pointer is not None
    assert [relation.to_id for relation in pointer.outgoing_relations] == [target.entity_id]


# --- Refusals ---


@pytest.mark.asyncio
async def test_rejected_write_leaves_no_file_and_no_row(
    write_stack, test_project, govern_project, session_maker
):
    """A vocabulary rejection must leave nothing behind.

    `type: note` is the default every write carries, and under a vocabulary it is
    an off-vocabulary type like any other.
    """
    govern_project()

    with pytest.raises(LocalNoteWriteError) as excinfo:
        await write_stack.write_note(
            project_external_id=test_project.external_id,
            data=EntitySchema(
                title="Off Vocabulary",
                directory="notes",
                content=note_content("Just a note.", **BASE_FRONTMATTER),
            ),
        )

    assert "A new type cannot be enabled from a write" in excinfo.value.message
    assert written_notes(test_project) == []
    assert await entity_rows(session_maker, test_project) == []


@pytest.mark.asyncio
async def test_unknown_project_is_refused(write_stack):
    """An unaddressable write is a failure, not an empty result (contract rule 5)."""
    with pytest.raises(LocalNoteWriteError) as excinfo:
        await write_stack.write_note(
            project_external_id="00000000-0000-4000-8000-000000000000",
            data=EntitySchema(title="Nowhere", directory="notes", content="Body."),
        )

    assert "Project not found" in excinfo.value.message


# --- T29: advisories from an accepted write are persisted ---


@pytest.mark.asyncio
async def test_advisory_from_an_accepted_write_persists_a_violation_row(
    write_stack, test_project, govern_project, session_maker
):
    """The T29 reproduction, now recorded instead of logged and lost.

    An undeclared frontmatter key is an advisory: the write is accepted and the
    key is kept, and `bm doctor` reads the row back. This drives the whole stack,
    so it asserts the outcome rather than which writer produced it — the index
    pass at the end of the write records the same set. The control that isolates
    the accepted write is `test_the_accepted_write_alone_persists_the_advisory`.
    """
    govern_project()

    result = await write_stack.write_note(
        project_external_id=test_project.external_id,
        data=EntitySchema(
            title="Advisory Guide",
            directory="notes",
            content=note_content("Body.", **guide_frontmatter(), topic="runtime"),
        ),
    )

    rows = await violation_rows(session_maker, test_project)
    assert [(row.rule, row.field, row.severity) for row in rows] == [
        ("unknown-key", "topic", "advisory")
    ]
    assert rows[0].entity_id == result.entity_id
    # Accepted, not refused: the file is on disk with the key intact.
    assert "topic: runtime" in Path(test_project.path, result.file_path).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_the_accepted_write_alone_persists_the_advisory(
    write_stack, test_project, govern_project, session_maker
):
    """The control that isolates T29 from the index pass that follows it.

    Every other test here drives the whole stack, and the index pass at the end
    of it re-checks the file and rewrites the same entity's rows through
    `EntityService` (GAPS W5 item 3). So an end-to-end assertion cannot tell
    T29's rows from the index pass's, and would pass with T29 reverted.

    This calls the mutation service and stops: no materialization, no index, no
    second writer. A row here can only have come from the accepted write.
    """
    govern_project()

    bundle = await write_stack._project_bundle(test_project.external_id)
    await bundle.mutation_service.create_note(
        project_external_id=test_project.external_id,
        data=EntitySchema(
            title="Unmaterialized Guide",
            directory="notes",
            content=note_content("Body.", **guide_frontmatter(), topic="runtime"),
        ),
        user_profile_id=None,
        source="cli",
    )

    rows = await violation_rows(session_maker, test_project)
    assert [(row.rule, row.field, row.severity) for row in rows] == [
        ("unknown-key", "topic", "advisory")
    ]
    # The file never reached disk, which is what makes the index pass impossible.
    assert written_notes(test_project) == []


@pytest.mark.asyncio
async def test_an_error_still_rejects_before_any_violation_row_lands(
    write_stack, test_project, govern_project, session_maker
):
    """Positive control for the test above: errors reject, and persist nothing.

    Without this, "advisories persist" could mean "every violation persists,
    including the ones that were supposed to stop the write".
    """
    govern_project()

    with pytest.raises(LocalNoteWriteError):
        await write_stack.write_note(
            project_external_id=test_project.external_id,
            data=EntitySchema(
                title="Errored Guide",
                directory="notes",
                # `type: runbook` is not in the vocabulary: an error, not an advisory.
                content=note_content(
                    "Body.",
                    **{**BASE_FRONTMATTER, "type": "runbook"},
                    topic="runtime",
                ),
            ),
        )

    assert await violation_rows(session_maker, test_project) == []


@pytest.mark.asyncio
async def test_a_later_clean_write_clears_the_earlier_violation_rows(
    write_stack, test_project, govern_project, session_maker
):
    """A record that now checks clean stops being reported.

    The rows are derived state, so an accepted write replaces the record's whole
    set — an empty answer clears rather than no-ops.

    The record is fixed here by declaring the field, not by dropping the key: a
    replacement merges the note's existing frontmatter (`note_preparation.py`,
    `prepare_update_entity_content`), so a key left out of a PUT stays on the
    note. An advisory about a key that is still there would be right to keep.
    """
    govern_project()

    created = await write_stack.write_note(
        project_external_id=test_project.external_id,
        data=EntitySchema(
            title="Fixable Guide",
            directory="notes",
            content=note_content("Body.", **guide_frontmatter(), topic="runtime"),
        ),
    )
    assert len(await violation_rows(session_maker, test_project)) == 1

    govern_project(fields={"topic": "string"})
    await write_stack.update_note(
        project_external_id=test_project.external_id,
        entity_external_id=created.external_id,
        data=EntitySchema(
            title="Fixable Guide",
            directory="notes",
            content=note_content("Body, now with a declared key.", **guide_frontmatter()),
        ),
    )

    assert await violation_rows(session_maker, test_project) == []


@pytest.mark.asyncio
async def test_an_edit_records_the_advisory_the_edited_note_still_carries(
    write_stack, test_project, govern_project, session_maker
):
    """An edit is judged too, on the frontmatter the edited note keeps.

    An edit changes no frontmatter, so it cannot introduce an advisory — but it
    builds on one, and the runner's third call site has to persist what it finds
    or the edit silently clears the create's rows.
    """
    govern_project()

    created = await write_stack.write_note(
        project_external_id=test_project.external_id,
        data=EntitySchema(
            title="Edited Guide",
            directory="notes",
            content=note_content("Body.", **guide_frontmatter(), topic="runtime"),
        ),
    )

    await write_stack.edit_note(
        project_external_id=test_project.external_id,
        entity_external_id=created.external_id,
        data=EditEntityRequest(operation="append", content="\nOne more line."),
    )

    rows = await violation_rows(session_maker, test_project)
    assert [(row.rule, row.field) for row in rows] == [("unknown-key", "topic")]


# --- The import boundary ---


def test_the_write_stack_imports_no_banned_module():
    """The stack must not drag fastapi, the API, MCP, or `deps` in behind it.

    Run in a subprocess rather than asserting over this process's `sys.modules`:
    pytest imports every test module into one interpreter, and several of them
    import fastapi and the MCP tools, so an in-process assertion would fail on
    imports this module never made.
    """
    probe = (
        "import sys, basic_memory.index.local_write_stack;"
        "banned = ('fastapi', 'basic_memory.deps', 'basic_memory.api', 'basic_memory.mcp');"
        "print(sorted(m for m in sys.modules if m.startswith(banned)))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == "[]"


# --- The history hookup and the headline (verbs items C and D) ---


def task_content(body: str, **fields: Any) -> str:
    """A `task` note's markdown. The project is ungoverned, so nothing is required."""
    return note_content(body, type="task", status="open", **fields)


@pytest_asyncio.fixture
async def store_project(
    config_home: Path, session_maker: async_sessionmaker[AsyncSession]
) -> Project:
    """A project whose notes live in the store, which is what W3 can commit.

    `test_project` deliberately does not: its path is the user-chosen kind that
    decision D3 leaves working and un-recorded, so both halves get a caller.
    """
    external_id = "8c1d0f2a-3b4c-5d6e-7f80-91a2b3c4d5e6"
    path = store_path() / external_id
    path.mkdir(parents=True)
    async with db.scoped_session(session_maker) as session:
        return await ProjectRepository().create(
            session,
            {
                "name": "store-project",
                "external_id": external_id,
                "path": str(path),
                "is_active": True,
            },
        )


def store_git(*args: str) -> str:
    """Read the store repo with a plain git call, independent of the module."""
    return subprocess.run(
        ["git", "-C", str(store_path()), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.mark.asyncio
async def test_a_store_write_commits_the_note_and_its_headline(write_stack, store_project):
    """W3's hookup: the write path is the first caller of `commit_paths`."""
    result = await write_stack.write_note(
        project_external_id=store_project.external_id,
        data=EntitySchema(
            title="Ship The Verbs",
            directory="tasks",
            content=task_content("Body."),
        ),
    )

    assert result.history_sha is not None
    assert result.notices == ()
    message = store_git("log", "-1", "--format=%B")
    assert message.splitlines()[0] == f"create {store_project.external_id}/{result.file_path}"
    assert "Actor: cli" in message
    # splitlines, not split: a note title contains spaces, so whitespace
    # splitting would shred one path into three.
    committed = sorted(store_git("show", "--name-only", "--format=", "HEAD").splitlines())
    assert committed == sorted(
        [
            f"{store_project.external_id}/{result.file_path}",
            f"{store_project.external_id}/headline.md",
        ]
    )
    # The store is clean afterwards: a headline left out of the commit would
    # report as someone else's dirty file on every later write.
    assert store_git("status", "--porcelain", "-uall").strip() == ""


@pytest.mark.asyncio
async def test_a_store_write_leaves_the_headline_a_consumer_can_parse(write_stack, store_project):
    """W9's three-line shape, written by the same call that wrote the note."""
    await write_stack.write_note(
        project_external_id=store_project.external_id,
        data=EntitySchema(
            title="Ship The Verbs",
            directory="tasks",
            content=task_content("Body."),
        ),
    )

    lines = headline_path(store_project.external_id).read_text(encoding="utf-8").splitlines()
    assert lines == ["---", "headline: Ship The Verbs", "---"]


@pytest.mark.asyncio
async def test_an_off_store_project_writes_and_says_history_is_not_recorded(
    write_stack, test_project
):
    """Decision D3: an off-store project keeps working and is told why, once."""
    result = await write_stack.write_note(
        project_external_id=test_project.external_id,
        data=EntitySchema(
            title="Unrecorded Note",
            directory="notes",
            content=note_content("Body."),
        ),
    )

    assert Path(test_project.path, result.file_path).is_file()
    assert result.history_sha is None
    assert result.notices == (OFF_STORE_NOTICE,)


@pytest.mark.asyncio
async def test_another_dirty_store_file_surfaces_as_a_notice(write_stack, store_project):
    """W3-B: report what else is dirty, never weld it into this commit."""
    stray = Path(store_project.path, "hand-edited.md")
    stray.write_text("someone else wrote this\n", encoding="utf-8")

    result = await write_stack.write_note(
        project_external_id=store_project.external_id,
        data=EntitySchema(
            title="Ship The Verbs",
            directory="tasks",
            content=task_content("Body."),
        ),
    )

    assert len(result.notices) == 1
    assert "1 other file has uncommitted changes" in result.notices[0]
    assert "bm history dirty" in result.notices[0]
    assert f"{store_project.external_id}/hand-edited.md" not in store_git(
        "show", "--name-only", "--format=", "HEAD"
    )


@pytest.mark.asyncio
async def test_an_update_is_refused_when_the_history_cannot_record_it(
    write_stack, store_project, session_maker
):
    """W3-A: an overwrite refuses, and refuses early enough to change nothing."""
    created = await write_stack.write_note(
        project_external_id=store_project.external_id,
        data=EntitySchema(
            title="Ship The Verbs",
            directory="tasks",
            content=task_content("Original body."),
        ),
    )
    on_disk = Path(store_project.path, created.file_path)
    before = on_disk.read_text(encoding="utf-8")
    # A `.git` git cannot use is what a broken store looks like from here.
    shutil.rmtree(store_path() / ".git")
    (store_path() / ".git").write_text("not a git directory\n", encoding="utf-8")

    with pytest.raises(LocalNoteWriteError) as excinfo:
        await write_stack.update_note(
            project_external_id=store_project.external_id,
            entity_external_id=created.external_id,
            data=EntitySchema(
                title="Ship The Verbs",
                directory="tasks",
                content=task_content("Replacement body."),
            ),
        )

    assert "Refused to overwrite" in str(excinfo.value)
    assert str(store_path()) in str(excinfo.value)
    assert on_disk.read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_a_create_is_written_even_when_the_history_is_broken(write_stack, store_project):
    """W3-A's other half: nothing is lost, so the note lands and warns."""
    store_path().mkdir(parents=True, exist_ok=True)
    (store_path() / ".git").write_text("not a git directory\n", encoding="utf-8")

    result = await write_stack.write_note(
        project_external_id=store_project.external_id,
        data=EntitySchema(
            title="Ship The Verbs",
            directory="tasks",
            content=task_content("Body."),
        ),
    )

    assert Path(store_project.path, result.file_path).is_file()
    assert result.history_sha is None
    assert "not recorded in the note history" in result.notices[0]


@pytest.mark.asyncio
async def test_an_update_that_creates_is_recorded_as_a_create(write_stack, store_project):
    """`update_note` creates when the note is absent, and the label follows.

    Labelling it an overwrite would put it under W3-A's destructive half, where a
    broken history refuses the write — over content that does not exist.
    """
    result = await write_stack.update_note(
        project_external_id=store_project.external_id,
        entity_external_id="1f0e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
        data=EntitySchema(
            title="Made By Put",
            directory="tasks",
            content=task_content("Body."),
        ),
    )

    subject = store_git("log", "-1", "--format=%s").strip()
    assert subject == f"create {store_project.external_id}/{result.file_path}"


@pytest.mark.asyncio
async def test_an_update_that_creates_is_not_refused_by_a_broken_history(
    write_stack, store_project
):
    """The preflight refuses overwrites, and a create is not one (W3-A)."""
    store_path().mkdir(parents=True, exist_ok=True)
    (store_path() / ".git").write_text("not a git directory\n", encoding="utf-8")

    result = await write_stack.update_note(
        project_external_id=store_project.external_id,
        entity_external_id="1f0e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
        data=EntitySchema(
            title="Made By Put",
            directory="tasks",
            content=task_content("Body."),
        ),
    )

    assert Path(store_project.path, result.file_path).is_file()
    assert result.history_sha is None
    assert "not recorded in the note history" in result.notices[0]


@pytest.mark.asyncio
async def test_a_headline_that_cannot_be_written_is_a_notice_not_a_failure(
    write_stack, store_project
):
    """The note already succeeded, so a convenience file must not fail the write.

    A directory where the headline file belongs is a real filesystem refusal, so
    no mock is needed to produce one.
    """
    headline_path(store_project.external_id).mkdir(parents=True)

    result = await write_stack.write_note(
        project_external_id=store_project.external_id,
        data=EntitySchema(
            title="Ship The Verbs",
            directory="tasks",
            content=task_content("Body."),
        ),
    )

    assert Path(store_project.path, result.file_path).is_file()
    assert result.history_sha is not None
    assert any("headline file could not be updated" in notice for notice in result.notices)
