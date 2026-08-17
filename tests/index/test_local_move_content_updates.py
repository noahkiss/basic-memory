"""Tests for local moved-file content planning and post-commit writes."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from sqlalchemy import Delete, Insert
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.file_utils import compute_checksum
from basic_memory.index.local_moves import (
    LocalMoveEntityService,
    LocalProjectIndexMoveContentUpdater,
    merged_frontmatter_markdown,
)
from basic_memory.indexing.project_index_maintenance import ProjectIndexMovedFile
from basic_memory.markdown import EntityMarkdown
from basic_memory.services import FileService
from basic_memory.vocabulary.model import vocabulary_path


@dataclass(frozen=True, slots=True)
class MovePermalinkConfig:
    """Just the permalink policy flags the move content planner reads."""

    disable_permalinks: bool = False
    update_permalinks_on_move: bool = True


@dataclass(slots=True)
class StaticMoveEntityService:
    """Resolve every permalink to a fixed value for planner tests."""

    app_config: MovePermalinkConfig | None
    permalink: str = "main/archive/renamed"

    async def resolve_permalink(
        self,
        file_path: Path | str,
        markdown: EntityMarkdown | None = None,
        skip_conflict_check: bool = False,
        session: AsyncSession | None = None,
    ) -> str:
        return self.permalink


def _updater(
    tmp_path: Path,
    entity_service: StaticMoveEntityService,
    project_external_id: str = "ungoverned-project",
) -> LocalProjectIndexMoveContentUpdater:
    return LocalProjectIndexMoveContentUpdater(
        entity_service=cast(LocalMoveEntityService, entity_service),
        file_service=FileService(tmp_path),
        project_external_id=project_external_id,
        project_id=1,
    )


def _moved_file(new_path: str = "archive/renamed.md") -> ProjectIndexMovedFile:
    return ProjectIndexMovedFile(
        entity_id=10,
        old_path="notes/original.md",
        new_path=new_path,
        old_permalink="main/notes/original",
    )


@dataclass(slots=True)
class RecordingSession:
    """The one capability the planner uses on a session: executing a statement.

    Enough for a planner unit test, and it keeps the violation writes visible —
    a plan that records nothing executes nothing (GAPS W5 item 3).
    """

    statements: list[Any] = field(default_factory=list)

    async def execute(self, statement: Any, *args: Any, **kwargs: Any) -> None:
        self.statements.append(statement)


def _session(recorder: RecordingSession | None = None) -> AsyncSession:
    return cast(AsyncSession, recorder if recorder is not None else RecordingSession())


def test_merged_frontmatter_markdown_updates_existing_frontmatter() -> None:
    merged = merged_frontmatter_markdown(
        "---\ntitle: Original\npermalink: old\n---\n\n# Body\n",
        {"permalink": "new"},
    )

    assert merged.startswith("---\n")
    assert "title: Original" in merged
    assert "permalink: new" in merged
    assert "permalink: old" not in merged
    assert merged.endswith("# Body")


def test_merged_frontmatter_markdown_creates_frontmatter_when_missing() -> None:
    merged = merged_frontmatter_markdown("# Plain Body\n", {"permalink": "new"})

    assert merged == "---\npermalink: new\n---\n\n# Plain Body"


def test_merged_frontmatter_markdown_treats_malformed_yaml_as_plain_markdown() -> None:
    malformed = "---\ntitle: [unclosed\n---\n\n# Body\n"

    merged = merged_frontmatter_markdown(malformed, {"permalink": "new"})

    assert merged.startswith("---\npermalink: new\n---\n\n")
    # The malformed block is preserved as body content, not silently dropped.
    assert "[unclosed" in merged


@pytest.mark.asyncio
async def test_plan_moved_file_content_requires_app_config(tmp_path: Path) -> None:
    updater = _updater(tmp_path, StaticMoveEntityService(app_config=None))

    with pytest.raises(RuntimeError, match="require app_config"):
        await updater.plan_moved_file_content(_session(), _moved_file())


@pytest.mark.asyncio
async def test_plan_moved_file_content_respects_permalink_policy(tmp_path: Path) -> None:
    disabled = _updater(
        tmp_path,
        StaticMoveEntityService(app_config=MovePermalinkConfig(disable_permalinks=True)),
    )
    assert await disabled.plan_moved_file_content(_session(), _moved_file()) is None

    no_move_updates = _updater(
        tmp_path,
        StaticMoveEntityService(app_config=MovePermalinkConfig(update_permalinks_on_move=False)),
    )
    assert await no_move_updates.plan_moved_file_content(_session(), _moved_file()) is None


@pytest.mark.asyncio
async def test_plan_moved_file_content_skips_non_markdown_and_unchanged_permalinks(
    tmp_path: Path,
) -> None:
    updater = _updater(tmp_path, StaticMoveEntityService(app_config=MovePermalinkConfig()))
    assert (
        await updater.plan_moved_file_content(_session(), _moved_file(new_path="asset.pdf")) is None
    )

    unchanged = _updater(
        tmp_path,
        StaticMoveEntityService(
            app_config=MovePermalinkConfig(),
            permalink="main/notes/original",
        ),
    )
    assert await unchanged.plan_moved_file_content(_session(), _moved_file()) is None


@pytest.mark.asyncio
async def test_plan_moved_file_content_plans_without_writing_then_write_persists(
    config_home: Path,
    tmp_path: Path,
) -> None:
    """Planning must not mutate the file; the write persists exactly the planned bytes."""
    moved_file = _moved_file()
    file_path = tmp_path / moved_file.new_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    original_content = "---\ntitle: Renamed\npermalink: main/notes/original\n---\n\n# Renamed\n"
    file_path.write_text(original_content, encoding="utf-8")

    updater = _updater(tmp_path, StaticMoveEntityService(app_config=MovePermalinkConfig()))
    content_update = await updater.plan_moved_file_content(_session(), moved_file)

    assert content_update is not None
    assert content_update.permalink == "main/archive/renamed"
    assert "permalink: main/archive/renamed" in content_update.markdown_content
    assert content_update.checksum == await compute_checksum(content_update.markdown_content)
    # Planning left the file untouched.
    assert file_path.read_text(encoding="utf-8") == original_content

    await updater.write_moved_file_content(moved_file, content_update)

    assert file_path.read_text(encoding="utf-8") == content_update.markdown_content


# --- GAPS T23: the governed-project arm ---


def _govern(external_id: str) -> None:
    """Give a project a vocabulary file, which is what makes it governed."""
    path = vocabulary_path(external_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # An empty mapping is a present, deliberate opt-in: it governs with defaults.
    path.write_text(yaml.safe_dump({}), encoding="utf-8")


def _write_moved_note(tmp_path: Path, moved_file: ProjectIndexMovedFile, content: str) -> Path:
    file_path = tmp_path / moved_file.new_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return file_path


@pytest.mark.asyncio
async def test_plan_moved_file_content_records_and_skips_a_set_once_permalink_rewrite(
    config_home: Path,
    tmp_path: Path,
    logged_warnings: list[str],
) -> None:
    """A governed project records the violation and then does not do the rewrite."""
    _govern("governed-project")
    moved_file = _moved_file()
    file_path = _write_moved_note(
        tmp_path,
        moved_file,
        "---\ntype: state\nid: tnd-0001\npermalink: tnd-0001\n"
        "title: Kept\nsource: human\n---\n\n# Kept\n",
    )
    updater = _updater(
        tmp_path,
        StaticMoveEntityService(app_config=MovePermalinkConfig()),
        project_external_id="governed-project",
    )

    assert await updater.plan_moved_file_content(_session(), moved_file) is None

    assert [line for line in logged_warnings if "'permalink' is set once and cannot change" in line]
    # No plan means no rewrite: the file is exactly as the human left it.
    assert "permalink: tnd-0001" in file_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_plan_moved_file_content_still_sets_a_first_permalink_on_a_governed_project(
    config_home: Path,
    tmp_path: Path,
    logged_warnings: list[str],
) -> None:
    """Governance stops a permalink *change*, not permalink maintenance as such.

    The positive control for the skip above: same project, same policy, and the
    only difference is that this note carries no permalink to change. An
    unrelated violation — ``type: runbook`` is off-vocabulary — is recorded and
    changes nothing, which is what makes the skip narrow rather than blanket.
    """
    _govern("governed-project")
    moved_file = _moved_file()
    _write_moved_note(
        tmp_path,
        moved_file,
        "---\ntype: runbook\ntitle: Fresh\n---\n\n# Fresh\n",
    )
    updater = _updater(
        tmp_path,
        StaticMoveEntityService(app_config=MovePermalinkConfig()),
        project_external_id="governed-project",
    )

    content_update = await updater.plan_moved_file_content(_session(), moved_file)

    assert content_update is not None
    assert content_update.permalink == "main/archive/renamed"
    assert [line for line in logged_warnings if "is not in this project's vocabulary" in line]
    assert not [line for line in logged_warnings if "'permalink' is set once" in line]


@pytest.mark.asyncio
async def test_plan_moved_file_content_stores_no_rows_when_it_refuses_the_rewrite(
    config_home: Path,
    tmp_path: Path,
    logged_warnings: list[str],
) -> None:
    """A refused rewrite logs and stores nothing, because it changes nothing.

    The violations were judged against a permalink this arm declines to write,
    so no row would describe the file that stays on disk. The file is unchanged
    and conforming, its last index pass's rows still stand, and a permanent
    ``set-once-changed`` row per hand-move is noise the nag repeats forever.
    """
    _govern("governed-project")
    moved_file = _moved_file()
    _write_moved_note(
        tmp_path,
        moved_file,
        "---\ntype: state\nid: tnd-0001\npermalink: tnd-0001\n"
        "title: Kept\nsource: human\n---\n\n# Kept\n",
    )
    updater = _updater(
        tmp_path,
        StaticMoveEntityService(app_config=MovePermalinkConfig()),
        project_external_id="governed-project",
    )
    recorder = RecordingSession()

    assert await updater.plan_moved_file_content(_session(recorder), moved_file) is None

    assert recorder.statements == []
    # The log assertion is what stops "no statements" from also passing when the
    # check never ran at all.
    assert [line for line in logged_warnings if "'permalink' is set once and cannot change" in line]


@pytest.mark.asyncio
async def test_plan_moved_file_content_persists_violations_when_it_rewrites(
    config_home: Path,
    tmp_path: Path,
) -> None:
    """The rewrite arm writes its rows on the batch's own transaction.

    Positive control for the refusal above, and the arm where persisting is the
    only thing that can work: the batch stamps the entity with the planned
    content's checksum, so the rewritten file never presents as modified and no
    later index pass re-checks it (GAPS T23).

    The note carries no permalink to change, so the rewrite goes ahead, and its
    ``type: runbook`` is off-vocabulary, so the check has a row to store.
    """
    _govern("governed-project")
    moved_file = _moved_file()
    _write_moved_note(tmp_path, moved_file, "---\ntype: runbook\ntitle: Fresh\n---\n\n# Fresh\n")
    updater = _updater(
        tmp_path,
        StaticMoveEntityService(app_config=MovePermalinkConfig()),
        project_external_id="governed-project",
    )
    recorder = RecordingSession()

    assert await updater.plan_moved_file_content(_session(recorder), moved_file) is not None

    # A replace is a delete then an insert. The insert is the row itself; the
    # delete is what stops a re-check accumulating duplicates.
    assert [isinstance(statement, Delete) for statement in recorder.statements] == [True, False]
    assert isinstance(recorder.statements[1], Insert)
