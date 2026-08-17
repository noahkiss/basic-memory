"""Vocabulary enforcement on the real agent write path (GAPS W4, closed by T22).

Every test here drives an MCP tool over the live ASGI app, which is the path an
agent actually takes: tool → typed client → v2 API → accepted-note mutation
runner. That is the whole point of the file. W4's own suite drove
``EntityService`` directly and passed while reject mode was unreachable, because
no caller had used that layer for some time (GAPS T22) — **a guard over a layer
proves nothing about whether callers use that layer**.

A project is governed only when ``store/<external_id>/vocabulary.yml`` exists, so
every test governs the project before its first write.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml
from mcp.server.fastmcp.exceptions import ToolError

from basic_memory.mcp.tools import edit_note, move_note, write_note
from basic_memory.vocabulary.model import vocabulary_path

# The common four the checker requires, minus the type, plus a permalink equal to
# the id byte-for-byte. A frontmatter permalink that nothing else claims is
# honoured verbatim, so these two stay equal through the write.
BASE_FRONTMATTER: dict[str, str] = {
    "id": "tnd-0001",
    "permalink": "tnd-0001",
    "source": "agent",
}


def note_content(body: str, **fields: Any) -> str:
    """Build note markdown carrying an explicit frontmatter block.

    The write path merges a content frontmatter block into the note it writes, so
    this is how a test states the frontmatter a write will persist.
    """
    dumped = yaml.safe_dump(dict(fields), sort_keys=True)
    return f"---\n{dumped}---\n\n{body}\n"


@pytest.fixture
def govern_project(test_project):
    """Give the test project a vocabulary file, which is what governs it."""

    def _govern(**content: Any) -> Path:
        path = vocabulary_path(test_project.external_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        # An empty mapping is a present, deliberate opt-in: it governs with the
        # default six types and five statuses.
        path.write_text(yaml.safe_dump(content), encoding="utf-8")
        return path

    return _govern


def written_notes(test_project) -> list[Path]:
    """Every markdown file the project has on disk."""
    return sorted(Path(test_project.path).rglob("*.md"))


# --- write_note ---


@pytest.mark.asyncio
async def test_write_note_is_refused_and_writes_no_file(app, test_project, govern_project):
    """The reproduction from GAPS T22, now refused instead of accepted.

    ``type: note`` is the default every write carries, and under a vocabulary it
    is an off-vocabulary type like any other: there is no ungoverned seventh
    type. Before this fix the write landed on disk with exit 0 and the violation
    was logged afterwards by the indexer.
    """
    govern_project()

    with pytest.raises(ToolError) as excinfo:
        await write_note(
            project=test_project.name,
            title="Off Vocabulary",
            directory="notes",
            content=note_content("Just a note.", **BASE_FRONTMATTER),
        )

    message = str(excinfo.value)
    # The picking question is the part an agent can act on at the moment of
    # filing; the bare type names alone were what W19 opened over.
    assert "task (do it)" in message
    assert "guide (consult it)" in message
    assert "finding (learned it)" in message
    assert "A new type cannot be enabled from a write" in message

    # A rejection must leave nothing behind: a refused write that still wrote the
    # file is worse than no rejection at all.
    assert written_notes(test_project) == []


@pytest.mark.asyncio
async def test_write_note_on_vocabulary_succeeds(app, test_project, govern_project):
    """Positive control: a governed project still accepts a conforming record.

    Without this, the rejection above could mean "governed projects refuse
    everything" rather than "governed projects refuse what is off vocabulary".
    """
    govern_project()

    result = await write_note(
        project=test_project.name,
        title="On Vocabulary",
        directory="notes",
        content=note_content(
            "How to restore a backup.",
            **BASE_FRONTMATTER,
            type="guide",
            **{"review-by": "2027-01-01"},
        ),
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["action"] == "created"
    assert result["permalink"] == "tnd-0001"
    assert [path.name for path in written_notes(test_project)] == ["On Vocabulary.md"]


@pytest.mark.asyncio
async def test_ungoverned_project_still_accepts_the_default_type(app, test_project):
    """The gate: no vocabulary.yml means no rule applies.

    An absent file must mean "not governed", never "use the defaults" — every
    note is typed ``note`` by default, so defaulting-when-absent would reject
    every existing write on the spot.
    """
    assert not vocabulary_path(test_project.external_id).exists()

    result = await write_note(
        project=test_project.name,
        title="Ungoverned Note",
        directory="notes",
        content="Just a note.",
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["action"] == "created"


@pytest.mark.asyncio
async def test_overwrite_is_refused_when_it_rewrites_a_set_once_field(
    app, test_project, govern_project
):
    """A full replacement may not rewrite a set-once field.

    ``write_note(overwrite=True)`` reaches the PUT path, which is a different
    runner from the create above and needs its own proof.
    """
    govern_project()

    await write_note(
        project=test_project.name,
        title="Set Once Guide",
        directory="notes",
        content=note_content(
            "Body",
            **BASE_FRONTMATTER,
            type="guide",
            **{"review-by": "2027-01-01"},
        ),
    )

    with pytest.raises(ToolError) as excinfo:
        await write_note(
            project=test_project.name,
            title="Set Once Guide",
            directory="notes",
            content=note_content(
                "Replaced body",
                **{**BASE_FRONTMATTER, "source": "human"},
                type="guide",
                **{"review-by": "2027-01-01"},
            ),
            overwrite=True,
        )

    message = str(excinfo.value)
    assert "'source' is set once" in message
    assert "'agent'" in message and "'human'" in message


# --- edit_note ---


@pytest.mark.asyncio
async def test_edit_note_is_refused_on_an_off_vocabulary_note(app, test_project, govern_project):
    """An edit builds on frontmatter that must itself be on vocabulary.

    An edit cannot introduce a set-once violation, but the note it edits may
    already be off vocabulary — written before the project was governed, or by
    hand. Refusing to build on it is the intended outcome.
    """
    written = await write_note(
        project=test_project.name,
        title="Written Ungoverned",
        directory="notes",
        content="Body written before the vocabulary existed.",
        output_format="json",
    )
    assert isinstance(written, dict)
    govern_project()

    result = await edit_note(
        identifier=written["permalink"],
        operation="append",
        content="An agent's addition",
        project=test_project.name,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert "is not in this project's vocabulary" in str(result["error"])

    # The refused line must not be on disk either.
    persisted = Path(test_project.path, "notes/Written Ungoverned.md").read_text(encoding="utf-8")
    assert "An agent's addition" not in persisted


# --- move_note ---


@pytest.mark.asyncio
async def test_move_note_is_refused_when_it_rewrites_the_permalink(
    app, app_config, test_project, govern_project
):
    """permalink is set-once, and a move under this setting rewrites it.

    Every edge binds to the permalink, so rewriting one orphans every relation
    pointing at the record. This is the case GAPS T23 names on the watcher path;
    on the agent path it is refused outright.
    """
    govern_project()
    app_config.update_permalinks_on_move = True

    await write_note(
        project=test_project.name,
        title="Movable Guide",
        directory="notes",
        content=note_content(
            "Body",
            **BASE_FRONTMATTER,
            type="guide",
            **{"review-by": "2027-01-01"},
        ),
    )

    result = await move_note(
        identifier="notes/Movable Guide.md",
        destination_path="archive/Movable Guide.md",
        project=test_project.name,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["moved"] is False
    assert "'permalink' is set once" in str(result["error"])

    # The file must still be where it was.
    assert [path.name for path in written_notes(test_project)] == ["Movable Guide.md"]
    assert Path(test_project.path, "notes/Movable Guide.md").exists()


@pytest.mark.asyncio
async def test_move_note_succeeds_when_the_permalink_holds(
    app, app_config, test_project, govern_project
):
    """Positive control: a move that keeps the permalink is allowed.

    With ``update_permalinks_on_move`` off a move is a pure path change — nothing
    set-once moves, so nothing is refused. False is the production default, but
    the shared ``app_config`` fixture overrides it to True, so the test sets it.
    """
    govern_project()
    app_config.update_permalinks_on_move = False

    await write_note(
        project=test_project.name,
        title="Movable Guide",
        directory="notes",
        content=note_content(
            "Body",
            **BASE_FRONTMATTER,
            type="guide",
            **{"review-by": "2027-01-01"},
        ),
    )

    result = await move_note(
        identifier="notes/Movable Guide.md",
        destination_path="archive/Movable Guide.md",
        project=test_project.name,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["moved"] is True
    assert Path(test_project.path, "archive/Movable Guide.md").exists()
    assert not Path(test_project.path, "notes/Movable Guide.md").exists()


# --- move_note over a directory, which is a different endpoint ---


@pytest.mark.asyncio
async def test_directory_move_is_refused_per_note_and_reports_the_failure(
    app, app_config, test_project, govern_project
):
    """A directory move is a batch of single-note moves, funnel included.

    It was the last write endpoint entering through ``EntityService``, which is
    why the funnel had two entry layers to keep in step (GAPS T22). One refused
    note is reported as a failed move rather than raised, so the batch does not
    strand the rest half-moved.
    """
    govern_project()
    app_config.update_permalinks_on_move = True

    await write_note(
        project=test_project.name,
        title="Batch Guide",
        directory="notes",
        content=note_content(
            "Body",
            **BASE_FRONTMATTER,
            type="guide",
            **{"review-by": "2027-01-01"},
        ),
    )

    result = await move_note(
        identifier="notes",
        destination_path="archive",
        is_directory=True,
        project=test_project.name,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["is_directory"] is True
    assert result["total_files"] == 1
    assert result["successful_moves"] == 0
    assert result["failed_moves"] == 1
    assert Path(test_project.path, "notes/Batch Guide.md").exists()


@pytest.mark.asyncio
async def test_directory_move_succeeds_when_every_note_conforms(
    app, app_config, test_project, govern_project
):
    """Positive control for the rewritten directory-move endpoint.

    Permalink rewriting stays off, so every note in the batch is a pure path
    change and the funnel has nothing to refuse.
    """
    govern_project()
    app_config.update_permalinks_on_move = False

    for index in (1, 2):
        await write_note(
            project=test_project.name,
            title=f"Batch Guide {index}",
            directory="notes",
            content=note_content(
                "Body",
                id=f"tnd-000{index}",
                permalink=f"tnd-000{index}",
                source="agent",
                type="guide",
                **{"review-by": "2027-01-01"},
            ),
        )

    result = await move_note(
        identifier="notes",
        destination_path="archive",
        is_directory=True,
        project=test_project.name,
        output_format="json",
    )

    assert isinstance(result, dict)
    assert result["total_files"] == 2
    assert result["successful_moves"] == 2
    assert result["failed_moves"] == 0
    assert sorted(path.name for path in written_notes(test_project)) == [
        "Batch Guide 1.md",
        "Batch Guide 2.md",
    ]
    assert Path(test_project.path, "archive/Batch Guide 1.md").exists()
