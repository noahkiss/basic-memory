"""Status command for basic-memory CLI."""

from typing import Annotated, Optional

import typer
from loguru import logger

from basic_memory.cli.app import app
from basic_memory.mcp.async_client import get_client
from basic_memory.project_marker import resolve_cli_project
from basic_memory.mcp.clients import ProjectClient
from basic_memory.schemas import ProjectIndexStatusResponse
from basic_memory.mcp.project_context import get_active_project


def display_project_index_status(
    project_name: str,
    status: ProjectIndexStatusResponse,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Write the project-index observation as labelled lines, notices last."""
    typer.echo(f"project: {project_name}")
    typer.echo(f"total files: {status.total_files}")
    typer.echo(f"unindexed files: {status.unindexed_file_count}")

    if verbose:
        path_width = max(
            (len(observed.path) for observed in status.observed_files),
            default=0,
        )
        for observed in sorted(status.observed_files, key=lambda observed: observed.path):
            checksum = observed.checksum[:8] if observed.checksum else ""
            # An unindexed file is the one case where the listing would otherwise read as
            # a clean bill of health for a note no query can reach, so mark it inline.
            marker = "" if observed.indexed else " not indexed"
            typer.echo(f"{observed.path:<{path_width}}  {checksum}{marker}".rstrip())

    # Trigger: the scan saw files that have no index row.
    # Why: observation is a filesystem walk, and only indexing makes a file reachable by
    #      search or read. Folding both into one "observed" count is a silent wrong answer:
    #      the total looks healthy while every query against those files returns nothing.
    # Outcome: report the gap and name the command that closes it.
    if status.unindexed_file_count and not quiet:
        plural = status.unindexed_file_count != 1
        typer.echo(
            f"{status.unindexed_file_count} file{'s' if plural else ''} not indexed — "
            "invisible to search and read until reindexed"
        )
        typer.echo("Run 'basic-memory reindex' to index them.")


async def run_status(
    project: Optional[str] = None,
    wait: bool = False,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> tuple[str, ProjectIndexStatusResponse]:
    """Fetch current project-index observation status.

    The event-index flow no longer exposes a pending-change counter. The watcher
    is the incremental path, and explicit project indexing is a full fanout.
    ``wait`` is accepted as a compatibility flag and returns the current
    observation immediately.

    Returns (project_name, project_index_status) for the caller to render.

    """
    project = resolve_cli_project(project)

    # Reuse a single client/context across polls so we don't reconnect each loop.
    async with get_client() as client:
        project_item = await get_active_project(client, project, None)
        project_client = ProjectClient(client)

        # Trigger: caller did not request --wait
        # Why: preserve the original single-scan behavior for the common case
        # Outcome: one status scan, returned as-is
        if not wait:
            project_index_status = await project_client.get_status(project_item.external_id)
            return project_item.name, project_index_status

        logger.debug(
            "status --wait is a compatibility no-op for event-based project indexing",
            timeout=timeout,
            poll_interval=poll_interval,
        )
        project_index_status = await project_client.get_status(project_item.external_id)
        return project_item.name, project_index_status


@app.command()
def status(
    project: Annotated[
        Optional[str],
        typer.Option(help="The project name."),
    ] = None,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="List each file the scan saw"),
    quiet: bool = typer.Option(False, "--quiet", help="Hide the status lines and next-step hints"),
    wait: bool = typer.Option(
        False,
        "--wait",
        help="Compatibility flag. The command reports the current counts at once",
    ),
    timeout: float = typer.Option(30.0, "--timeout", help="Compatibility option for --wait"),
):
    """Show how many files bm saw in the project, and how many it has indexed.

    The --wait flag is kept for compatibility. It reports the current counts at
    once and waits for nothing.
    """
    from basic_memory.cli.commands.command_utils import run_with_cleanup

    # Deferred: ToolError lives in the mcp SDK, which must not load at CLI startup (#886).
    from mcp.server.fastmcp.exceptions import ToolError

    # Trigger: --wait with a negative --timeout
    # Why: a negative deadline times out on the very first poll, producing a confusing
    #      "Timed out after -5s" message instead of flagging the bad input. Raised
    #      before the try/except so typer renders a clean usage error (exit 2).
    # Outcome: reject it up front with a clear parameter error.
    if wait and timeout < 0:
        raise typer.BadParameter("--timeout must be >= 0", param_hint="'--timeout'")

    try:
        project_name, project_index_status = run_with_cleanup(
            run_status(project, wait=wait, timeout=timeout)
        )
    except (ValueError, ToolError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:  # pragma: no cover
        logger.error(f"Error checking status: {e}")
        typer.echo(f"Error checking status: {e}", err=True)
        raise typer.Exit(code=1)

    display_project_index_status(project_name, project_index_status, verbose, quiet)
