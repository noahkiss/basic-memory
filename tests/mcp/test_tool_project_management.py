"""Tests for MCP project management tools."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from basic_memory import db
from basic_memory.mcp.tools import list_memory_projects, create_memory_project, delete_project
from basic_memory.mcp.tools.project_management import _format_note_file_delete_result
from basic_memory.models.project import Project
from basic_memory.schemas.project_info import ProjectItem, ProjectList


# --- Helpers ---


def _make_project(
    name: str,
    path: str,
    *,
    id: int = 1,
    external_id: str = "00000000-0000-0000-0000-000000000001",
    is_default: bool = False,
) -> ProjectItem:
    return ProjectItem(
        id=id,
        external_id=external_id,
        name=name,
        path=path,
        is_default=is_default,
    )


def _make_list(projects: list[ProjectItem], default: str | None = None) -> ProjectList:
    return ProjectList(projects=projects, default_project=default)


# --- list_memory_projects ---


@pytest.mark.asyncio
async def test_list_memory_projects_unconstrained(app, test_project):
    result = await list_memory_projects()
    assert "Available projects:" in result
    assert f"- {test_project.name}" in result


@pytest.mark.asyncio
async def test_list_memory_projects_lists_names_with_external_ids(app, client, test_project):
    """Text output lists projects sorted by permalink, annotated with the external id."""
    main = _make_project("main", "/tmp/main", is_default=True)
    # An empty external_id must not render an empty "[]" suffix.
    legacy = _make_project("alpha", "/tmp/alpha", id=2, external_id="")
    mock_list = _make_list([main, legacy], default="main")

    with patch(
        "basic_memory.mcp.clients.project.ProjectClient.list_projects",
        new_callable=AsyncMock,
        return_value=mock_list,
    ):
        result = await list_memory_projects()

    assert isinstance(result, str)
    assert "- main [00000000-0000-0000-0000-000000000001]" in result
    assert "- alpha\n" in result
    # Sorted by permalink, so alpha precedes main.
    assert result.index("- alpha") < result.index("- main")
    assert "Next: Ask which project to use for this session." in result


@pytest.mark.asyncio
async def test_list_memory_projects_json_output(app, client, test_project):
    """JSON output carries the project rows plus the default/constrained context."""
    main = _make_project("main", "/tmp/main", is_default=True)
    mock_list = _make_list([main], default="main")

    with patch(
        "basic_memory.mcp.clients.project.ProjectClient.list_projects",
        new_callable=AsyncMock,
        return_value=mock_list,
    ):
        result = await list_memory_projects(output_format="json")

    assert result == {
        "projects": [
            {
                "name": "main",
                "external_id": "00000000-0000-0000-0000-000000000001",
                "path": "/tmp/main",
                "is_default": True,
            }
        ],
        "default_project": "main",
        "constrained_project": None,
    }


@pytest.mark.asyncio
async def test_list_memory_projects_constrained_env(monkeypatch, app, test_project):
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", test_project.name)
    result = await list_memory_projects()
    assert f"Project: {test_project.name}" in result
    assert "constrained to a single project" in result


# --- create_memory_project / delete_project ---


@pytest.mark.asyncio
async def test_create_and_delete_project_and_name_match_branch(
    app, tmp_path_factory, session_maker
):
    # Create a project through the tool (exercises POST + response formatting).
    project_root = tmp_path_factory.mktemp("extra-project-home")
    result = await create_memory_project(
        project_name="My Project",
        project_path=str(project_root),
        set_default=False,
    )
    assert isinstance(result, str)
    assert result.startswith("✓")
    assert "My Project" in result

    # Make permalink intentionally not derived from name so delete_project hits the name-match branch.
    async with db.scoped_session(session_maker) as session:
        project = (
            await session.execute(select(Project).where(Project.name == "My Project"))
        ).scalar_one()
        project.permalink = "custom-permalink"
        await session.commit()

    delete_result = await delete_project("My Project")
    assert delete_result.startswith("✓")
    # delete_notes=False: files are retained on disk.
    assert "Note files remain on disk" in delete_result
    assert "Re-add the project" in delete_result
    assert project_root.exists()


@pytest.mark.asyncio
async def test_create_memory_project_retry_indexes_partial_create(app, tmp_path_factory):
    """Retrying after a post-create index failure repairs the existing project."""
    from basic_memory.mcp.clients import ProjectClient

    project_root = tmp_path_factory.mktemp("partial-create-project-home")
    completed_index = {
        "total_files": 0,
        "enqueued_files": 0,
        "enqueued_batches": 0,
        "deleted_files": 0,
    }

    with patch.object(
        ProjectClient,
        "index",
        new_callable=AsyncMock,
        side_effect=[RuntimeError("index timeout"), completed_index],
    ) as mock_index:
        with pytest.raises(RuntimeError, match="index timeout"):
            await create_memory_project(
                project_name="Partial Create Project",
                project_path=str(project_root),
                output_format="json",
            )

        retry_result = await create_memory_project(
            project_name="Partial Create Project",
            project_path=str(project_root),
            output_format="json",
        )

    assert isinstance(retry_result, dict)
    assert retry_result["created"] is False
    assert retry_result["already_exists"] is True
    assert retry_result["indexing"] == completed_index
    assert mock_index.await_count == 2


@pytest.mark.asyncio
async def test_create_memory_project_retry_text_output_reports_existing_project(
    app, tmp_path_factory
):
    """The text retry path names the existing project and its indexing outcome."""
    from basic_memory.mcp.clients import ProjectClient

    project_root = tmp_path_factory.mktemp("existing-text-project-home")
    await create_memory_project(
        project_name="Existing Text Project",
        project_path=str(project_root),
        set_default=True,
    )

    with patch.object(
        ProjectClient,
        "index",
        new_callable=AsyncMock,
        return_value={"status": "completed", "message": "Indexed 0 files"},
    ):
        result = await create_memory_project(
            project_name="Existing Text Project",
            project_path=str(project_root),
        )

    assert isinstance(result, str)
    assert result.startswith("✓ Project already exists: Existing Text Project")
    assert "• Set as default project" in result
    assert "• Status: completed" in result
    assert "• Message: Indexed 0 files" in result
    assert "indexing completed" in result


@pytest.mark.asyncio
async def test_delete_project_delete_notes_removes_local_files(app, tmp_path_factory):
    """delete_notes=True flows through to the API and removes the project files (#1034)."""
    project_root = tmp_path_factory.mktemp("delete-notes-project-home")
    (project_root / "note.md").write_text("# Note\n\ncontent\n")

    result = await create_memory_project(
        project_name="Delete Notes Project",
        project_path=str(project_root),
        set_default=False,
    )
    assert isinstance(result, str)
    assert result.startswith("✓")

    delete_result = await delete_project("Delete Notes Project", delete_notes=True)
    assert delete_result.startswith("✓")
    assert "did not report a completion status" in delete_result
    assert "Note files on disk were deleted" not in delete_result
    assert "Re-add the project" not in delete_result
    assert not project_root.exists()


@pytest.mark.asyncio
async def test_create_memory_project_requires_created_project(app, tmp_path_factory):
    """A malformed create response fails before attempting to index."""
    from basic_memory.mcp.clients import ProjectClient
    from basic_memory.schemas.project_info import ProjectStatusResponse

    project_root = tmp_path_factory.mktemp("missing-created-project-home")
    fake_status = ProjectStatusResponse(
        message="Project created",
        status="success",
        default=False,
        new_project=None,
    )

    with (
        patch.object(
            ProjectClient,
            "list_projects",
            new_callable=AsyncMock,
            return_value=_make_list([], default=None),
        ),
        patch.object(
            ProjectClient,
            "create_project",
            new_callable=AsyncMock,
            return_value=fake_status,
        ),
        patch.object(ProjectClient, "index", new_callable=AsyncMock) as mock_index,
        patch(
            "basic_memory.mcp.project_context.invalidate_project_caches",
            new_callable=AsyncMock,
        ),
    ):
        with pytest.raises(
            RuntimeError,
            match="Project creation succeeded without returning the new project",
        ):
            await create_memory_project(
                project_name="Missing Created Project",
                project_path=str(project_root),
            )

    mock_index.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_memory_project_indexes_inline(app, tmp_path_factory):
    """Creation indexes the new project synchronously so its files are usable at once."""
    from basic_memory.mcp.clients import ProjectClient
    from basic_memory.schemas.project_info import ProjectStatusResponse

    project_root = tmp_path_factory.mktemp("inline-index-project-home")
    fake_status = ProjectStatusResponse(
        message="Project created",
        status="success",
        default=False,
        new_project=_make_project("Inline Index Project", str(project_root)),
    )

    with (
        patch.object(
            ProjectClient,
            "list_projects",
            new_callable=AsyncMock,
            return_value=_make_list([], default=None),
        ),
        patch.object(
            ProjectClient,
            "create_project",
            new_callable=AsyncMock,
            return_value=fake_status,
        ),
        patch.object(
            ProjectClient,
            "index",
            new_callable=AsyncMock,
            return_value={
                "total_files": 0,
                "enqueued_files": 0,
                "enqueued_batches": 0,
                "deleted_files": 0,
            },
        ) as mock_index,
        patch(
            "basic_memory.mcp.project_context.invalidate_project_caches",
            new_callable=AsyncMock,
        ),
    ):
        result = await create_memory_project(
            project_name="Inline Index Project",
            project_path=str(project_root),
            output_format="json",
        )

    mock_index.assert_awaited_once_with(
        "00000000-0000-0000-0000-000000000001",
        run_in_background=False,
    )
    assert isinstance(result, dict)
    assert result["created"] is True
    assert result["already_exists"] is False
    assert result["external_id"] == "00000000-0000-0000-0000-000000000001"


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["text", "json"])
async def test_create_memory_project_constrained_returns_disabled_message(
    monkeypatch, tmp_path_factory, output_format
):
    """A constrained MCP session rejects creation before opening a client."""
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "locked-project")
    project_root = tmp_path_factory.mktemp("constrained-create-project-home")

    result = await create_memory_project(
        project_name="Any Project",
        project_path=str(project_root),
        output_format=output_format,
    )

    if output_format == "json":
        assert isinstance(result, dict)
        assert result["created"] is False
        assert result["error"] == "PROJECT_CONSTRAINED"
        assert "locked-project" in result["message"]
    else:
        assert isinstance(result, str)
        assert "Project creation disabled" in result
        assert "locked-project" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_notes", [False, True])
async def test_delete_project_passes_delete_notes_through(app, delete_notes):
    """delete_notes is passed through to the typed client and reported back (#1034)."""
    from basic_memory.mcp.clients import ProjectClient
    from basic_memory.schemas.project_info import ProjectStatusResponse

    target_project = _make_project(
        "Tracked Project",
        "/tracked-project",
        external_id="project-uuid",
    )

    fake_status = ProjectStatusResponse(
        message="Project deleted",
        status="success",
        default=False,
        old_project=target_project,
        deletion_status="pending",
        file_delete_status="pending" if delete_notes else "skipped",
        job_id="28993",
    )

    with (
        patch.object(
            ProjectClient,
            "list_projects",
            new_callable=AsyncMock,
            return_value=_make_list([target_project], default=None),
        ),
        patch.object(
            ProjectClient,
            "delete_project",
            new_callable=AsyncMock,
            return_value=fake_status,
        ) as mock_delete_project,
        patch(
            "basic_memory.mcp.project_context.invalidate_project_caches",
            new_callable=AsyncMock,
        ),
    ):
        result = await delete_project("Tracked Project", delete_notes=delete_notes)

    mock_delete_project.assert_awaited_once_with("project-uuid", delete_notes=delete_notes)
    assert result.startswith("✓")
    assert "• Name: Tracked Project" in result
    assert "• Path: /tracked-project" in result
    assert "Project deletion status: pending" in result
    assert "Deletion job ID: 28993" in result
    if delete_notes:
        assert "Note-file deletion on disk was queued and is pending" in result
        assert "were deleted" not in result
    else:
        assert "Note files remain on disk" in result
        assert "Re-add the project" in result


@pytest.mark.asyncio
async def test_delete_project_unknown_name_raises(app, test_project):
    """Deleting a project that is not tracked names the available projects."""
    with pytest.raises(ValueError, match="Project 'no-such-project' not found"):
        await delete_project("no-such-project")


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("pending", "queued and is pending"),
        ("complete", "were deleted along with the project"),
        ("failed", "failed; note files may remain"),
        ("skipped", "was skipped; note files remain"),
        (None, "did not report a completion status"),
    ],
)
def test_format_note_file_delete_result_reports_backend_status(status, expected):
    """Only an explicit completion status reports success."""
    result = _format_note_file_delete_result(status)

    assert expected in result
    if status != "complete":
        assert "were deleted" not in result


@pytest.mark.asyncio
async def test_delete_project_constrained_returns_disabled_message(monkeypatch):
    """A constrained MCP session rejects deletion before opening a client."""
    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "locked-project")

    result = await delete_project("Any Project")

    assert "Project deletion disabled" in result
    assert "locked-project" in result
