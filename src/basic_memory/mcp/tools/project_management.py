"""Project management tools for Basic Memory MCP server.

These tools allow users to switch between projects, list available projects,
and manage project context during conversations.
"""

import os
from typing import Literal

from fastmcp import Context

from basic_memory.mcp.async_client import get_client
from basic_memory.mcp.server import mcp
from basic_memory.schemas.project_info import ProjectInfoRequest, ProjectList
from basic_memory.utils import generate_permalink


def _project_rows(project_list: ProjectList) -> list[dict]:
    """Render the project list as sorted, serializable rows."""
    return [
        {
            "name": project.name,
            "external_id": project.external_id,
            "path": project.path,
            "is_default": project.is_default,
        }
        for project in sorted(project_list.projects, key=lambda item: item.permalink)
    ]


def _format_project_list_text(projects: list[dict]) -> str:
    """Format the project list as human-readable text."""
    result = "Available projects:\n"

    for project in projects:
        external_id = project["external_id"]
        id_suffix = f" [{external_id}]" if external_id else ""
        result += f"- {project['name']}{id_suffix}\n"

    result += "\n" + "─" * 40 + "\n"
    result += "Next: Ask which project to use for this session.\n"
    result += "Example: 'Which project should I use for this task?'\n\n"
    result += (
        "Session reminder: Track the selected project for all subsequent "
        "operations in this conversation.\n"
    )
    result += "The user can say 'switch to [project]' to change projects."
    return result


def _format_project_list_json(
    projects: list[dict],
    default_project: str | None,
    constrained_project: str | None,
) -> dict:
    """Format the project list as structured JSON."""
    return {
        "projects": projects,
        "default_project": default_project,
        "constrained_project": constrained_project,
    }


@mcp.tool(
    "list_memory_projects",
    title="List Memory Projects",
    tags={"projects"},
    annotations={
        "title": "List Memory Projects",
        "readOnlyHint": True,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def list_memory_projects(
    output_format: Literal["text", "json"] = "text",
    context: Context | None = None,
) -> str | dict:
    """List all available projects with their status.

    Each project entry includes an `external_id` (UUID). Pass that value as the
    `project_id` parameter on other tools to address a specific project
    unambiguously.

    Args:
        output_format: "text" returns the existing human-readable project list.
            "json" returns structured project metadata.
        context: Optional FastMCP context for progress/status logging.
    """
    if context:  # pragma: no cover
        await context.info("Listing all available projects")

    constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")

    from basic_memory.mcp.clients import ProjectClient

    async with get_client() as client:
        project_client = ProjectClient(client)
        project_list = await project_client.list_projects()

    projects = _project_rows(project_list)

    if output_format == "json":
        return _format_project_list_json(
            projects, project_list.default_project, constrained_project
        )

    if constrained_project:
        return _format_constrained_text(constrained_project)

    return _format_project_list_text(projects)


def _format_constrained_text(constrained_project: str) -> str:
    """Format text output when the MCP server is constrained to a single project."""
    result = f"Project: {constrained_project}\n\n"
    result += "Note: This MCP server is constrained to a single project.\n"
    result += "All operations will automatically use this project."
    return result


def _format_indexing_details(indexing: dict[str, object]) -> str:
    """Format the details returned by an indexing run."""
    result = "Indexing:\n"
    if status := indexing.get("status"):
        result += f"• Status: {status}\n"
    if message := indexing.get("message"):
        result += f"• Message: {message}\n"
    for key, label in (
        ("total_files", "Files discovered"),
        ("enqueued_files", "Files enqueued"),
        ("enqueued_batches", "Batches enqueued"),
        ("deleted_files", "Orphans deleted"),
    ):
        if key in indexing:
            result += f"• {label}: {indexing[key]}\n"
    return result


@mcp.tool(
    "create_memory_project",
    title="Create Memory Project",
    tags={"projects"},
    annotations={
        "title": "Create Memory Project",
        "readOnlyHint": False,
        "destructiveHint": False,
        "openWorldHint": False,
    },
)
async def create_memory_project(
    project_name: str,
    project_path: str,
    set_default: bool = False,
    output_format: Literal["text", "json"] = "text",
    context: Context | None = None,
) -> str | dict:
    """Create a new Basic Memory project.

    Creates a new project with the specified name and path. The project directory
    will be created if it doesn't exist. Optionally sets the new project as default.

    Args:
        project_name: Name for the new project (must be unique)
        project_path: File system path where the project will be stored
        set_default: Whether to set this project as the default (optional, defaults to False)
        output_format: "text" returns the existing human-readable result text.
            "json" returns structured project creation metadata.
        context: Optional FastMCP context for progress/status logging.

    Returns:
        Confirmation message with project details

    Example:
        create_memory_project("my-research", "~/Documents/research")
        create_memory_project("work-notes", "/home/user/work", set_default=True)
    """
    # Trigger: MCP server is constrained to a single project.
    # Why: constrained sessions cannot create projects.
    # Outcome: return the existing disabled response before opening a client.
    constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")
    if constrained_project:
        if output_format == "json":
            return {
                "name": project_name,
                "path": project_path,
                "is_default": False,
                "created": False,
                "already_exists": False,
                "error": "PROJECT_CONSTRAINED",
                "message": (
                    f"Project creation disabled - MCP server is constrained to project "
                    f"'{constrained_project}'."
                ),
            }
        return f'# Error\n\nProject creation disabled - MCP server is constrained to project \'{constrained_project}\'.\nUse the CLI to create projects: `basic-memory project add "{project_name}" "{project_path}"`'

    async with get_client() as client:
        if context:  # pragma: no cover
            await context.info(f"Creating project: {project_name} at {project_path}")

        # Create the project request
        project_request = ProjectInfoRequest(
            name=project_name, path=project_path, set_default=set_default
        )

        # Import here to avoid circular import
        from basic_memory.mcp.clients import ProjectClient

        # Use typed ProjectClient for API calls
        project_client = ProjectClient(client)
        existing = await project_client.list_projects()
        existing_match = next(
            (p for p in existing.projects if p.name.casefold() == project_name.casefold()),
            None,
        )
        if existing_match:
            is_default = bool(
                existing_match.is_default or existing.default_project == existing_match.name
            )
            # Trigger: a previous create may have committed the project record
            # before its indexing request failed or timed out.
            # Why: create retries must repair that partial success instead of
            # permanently short-circuiting on the existing project record.
            # Outcome: idempotently request indexing again.
            indexing = await project_client.index(
                existing_match.external_id,
                run_in_background=False,
            )
            if output_format == "json":
                return {
                    "name": existing_match.name,
                    "external_id": existing_match.external_id,
                    "path": existing_match.path,
                    "is_default": is_default,
                    "created": False,
                    "already_exists": True,
                    "indexing": indexing,
                }
            result = (
                f"✓ Project already exists: {existing_match.name}\n\n"
                f"Project Details:\n"
                f"• Name: {existing_match.name}\n"
                f"• External ID: {existing_match.external_id}\n"
                f"• Path: {existing_match.path}\n"
                f"{'• Set as default project\n' if is_default else ''}"
                "\n"
            )
            result += _format_indexing_details(indexing)
            result += "\nProject is already available for use in tool calls; indexing completed.\n"
            return result

        status_response = await project_client.create_project(project_request.model_dump())
        from basic_memory.mcp.project_context import invalidate_project_caches

        await invalidate_project_caches(context)

        new_project = status_response.new_project
        if new_project is None:
            raise RuntimeError("Project creation succeeded without returning the new project")

        # Indexing runs inline so retained files are immediately available.
        indexing = await project_client.index(
            new_project.external_id,
            run_in_background=False,
        )

        if output_format == "json":
            return {
                "name": new_project.name,
                "external_id": new_project.external_id,
                "path": new_project.path,
                "is_default": bool(new_project.is_default or set_default),
                "created": True,
                "already_exists": False,
                "indexing": indexing,
            }

        result = f"✓ {status_response.message}\n\n"

        result += "Project Details:\n"
        result += f"• Name: {new_project.name}\n"
        result += f"• External ID: {new_project.external_id}\n"
        result += f"• Path: {new_project.path}\n"

        if set_default:
            result += "• Set as default project\n"

        result += "\n"
        result += _format_indexing_details(indexing)
        result += "\nProject is now available for use in tool calls; indexing completed.\n"
        result += f"Use '{project_name}' as the project parameter in MCP tool calls.\n"

        return result


def _format_note_file_delete_result(
    status: Literal["pending", "skipped", "complete", "failed"] | None,
) -> str:
    """Describe note-file deletion without overstating backend completion."""
    if status == "pending":
        return "Note-file deletion on disk was queued and is pending.\n"
    if status == "complete":
        return "Note files on disk were deleted along with the project.\n"
    if status == "failed":
        return "Note-file deletion on disk failed; note files may remain.\n"
    if status == "skipped":
        return "Note-file deletion on disk was skipped; note files remain.\n"
    return "Note-file deletion on disk did not report a completion status; note files may remain.\n"


@mcp.tool(
    title="Delete Project",
    tags={"projects"},
    annotations={
        "title": "Delete Project",
        "readOnlyHint": False,
        "destructiveHint": True,
        "openWorldHint": False,
    },
)
async def delete_project(
    project_name: str,
    delete_notes: bool = False,
    context: Context | None = None,
) -> str:
    """Delete a Basic Memory project.

    Removes a project from Basic Memory's configuration and database records.
    By default the project's note files are retained on disk. Pass
    delete_notes=True to also delete the note files themselves.

    Args:
        project_name: Name of the project to delete
        delete_notes: Also delete the project's note files from disk. Defaults
            to False, which only stops tracking the project.

    Returns:
        Confirmation message describing what was deleted and whether note
        files were removed or retained.

    Example:
        delete_project("old-project")
        delete_project("old-project", delete_notes=True)

    Warning:
        This action cannot be undone. With delete_notes=False the project must
        be re-added to access its content through Basic Memory again; with
        delete_notes=True the note files themselves are permanently deleted.
    """
    # Trigger: MCP server is constrained to a single project.
    # Why: constrained sessions cannot delete projects.
    # Outcome: return the existing disabled message before opening a client.
    constrained_project = os.environ.get("BASIC_MEMORY_MCP_PROJECT")
    if constrained_project:
        return f"# Error\n\nProject deletion disabled - MCP server is constrained to project '{constrained_project}'.\nUse the CLI to delete projects: `basic-memory project remove \"{project_name}\"`"

    async with get_client() as client:
        if context:  # pragma: no cover
            await context.info(f"Deleting project: {project_name}")

        # Import here to avoid circular import
        from basic_memory.mcp.clients import ProjectClient

        # Use typed ProjectClient for API calls
        project_client = ProjectClient(client)

        # Get project info before deletion to validate it exists
        project_list = await project_client.list_projects()

        # Find the project by permalink (derived from name).
        # Note: The API response uses `ProjectItem` which derives `permalink` from `name`,
        # so a separate case-insensitive name match would be redundant here.
        project_permalink = generate_permalink(project_name)
        target_project = None
        for p in project_list.projects:
            # Match by permalink (handles case-insensitive input)
            if p.permalink == project_permalink:
                target_project = p
                break

        if not target_project:
            available_projects = [p.name for p in project_list.projects]
            raise ValueError(
                f"Project '{project_name}' not found. Available projects: {', '.join(available_projects)}"
            )

        # Delete project using project external_id
        status_response = await project_client.delete_project(
            target_project.external_id, delete_notes=delete_notes
        )
        from basic_memory.mcp.project_context import invalidate_project_caches

        await invalidate_project_caches(context)

        result = f"✓ {status_response.message}\n\n"

        if status_response.old_project:
            result += "Removed project details:\n"
            result += f"• Name: {status_response.old_project.name}\n"
            if hasattr(status_response.old_project, "path"):
                result += f"• Path: {status_response.old_project.path}\n"

        if status_response.deletion_status or status_response.job_id:
            result += "\nDeletion tracking:\n"
            if status_response.deletion_status:
                result += f"• Project deletion status: {status_response.deletion_status}\n"
            if status_response.job_id:
                result += f"• Deletion job ID: {status_response.job_id}\n"

        if delete_notes:
            result += _format_note_file_delete_result(status_response.file_delete_status)
        else:
            result += (
                "Note files remain on disk but the project is no longer tracked by Basic Memory.\n"
            )
            result += "Re-add the project to access its content again.\n"

        return result
