"""Status command for basic-memory CLI."""

import json
from typing import Annotated, Optional

import typer
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.tree import Tree

from basic_memory.cli.app import app
from basic_memory.mcp.async_client import get_client
from basic_memory.project_marker import resolve_cli_project
from basic_memory.mcp.clients import ProjectClient
from basic_memory.schemas import ProjectIndexStatusResponse
from basic_memory.mcp.project_context import get_active_project

# Create rich console
console = Console()


def add_observed_files_to_tree(tree: Tree, status: ProjectIndexStatusResponse) -> None:
    """Add observed project-index files to the tree, grouped by directory."""
    by_dir: dict[str, list[tuple[str, str, str | None, bool]]] = {}
    for observed_file in status.observed_files:
        path = observed_file.path
        parts = path.split("/", 1)
        dir_name = parts[0] if len(parts) > 1 else ""
        file_name = parts[1] if len(parts) > 1 else parts[0]
        checksum = observed_file.checksum[:8] if observed_file.checksum else None
        by_dir.setdefault(dir_name, []).append((file_name, path, checksum, observed_file.indexed))

    for dir_name, files in sorted(by_dir.items()):
        if dir_name:
            branch = tree.add(f"[bold]{dir_name}/[/bold]")
        else:
            branch = tree

        for file_name, _, checksum, indexed in sorted(files):
            detail = f" ({checksum})" if checksum else ""
            # An unindexed file is the one case where the listing would otherwise read as
            # a clean bill of health for a note no query can reach, so mark it inline.
            marker = "" if indexed else " [yellow]not indexed[/yellow]"
            branch.add(f"[cyan]{file_name}[/cyan]{detail}{marker}")


def display_project_index_status(
    project_name: str,
    title: str,
    status: ProjectIndexStatusResponse,
    verbose: bool = False,
) -> None:
    """Display project-index observation status using Rich."""
    tree = Tree(f"{project_name}: {title}")
    tree.add(f"{status.total_files} observed file{'s' if status.total_files != 1 else ''}")

    # Trigger: the scan saw files that have no index row.
    # Why: observation is a filesystem walk, and only indexing makes a file reachable by
    #      search or read. Folding both into one "observed" count is a silent wrong answer:
    #      the total looks healthy while every query against those files returns nothing.
    # Outcome: report the gap and name the command that closes it.
    if status.unindexed_file_count:
        plural = status.unindexed_file_count != 1
        tree.add(
            f"[bold yellow]{status.unindexed_file_count} observed file"
            f"{'s are' if plural else ' is'} NOT indexed[/bold yellow] — "
            "invisible to search and read until 'basic-memory reindex'"
        )

    if verbose and status.observed_files:
        files_branch = tree.add("[cyan]Observed Files[/cyan]")
        add_observed_files_to_tree(files_branch, status)

    console.print(Panel(tree, expand=False))


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
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed file information"),
    json_output: bool = typer.Option(False, "--json", help="Output in JSON format"),
    wait: bool = typer.Option(
        False,
        "--wait",
        help="Compatibility flag; returns the current project-index observation",
    ),
    timeout: float = typer.Option(30.0, "--timeout", help="Compatibility option for --wait"),
):
    """Show current project-index observation status.

    Use --json for machine-readable output.
    The --wait flag is accepted for compatibility and returns the current
    project-index observation immediately.
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

        if json_output:
            print(
                json.dumps(
                    project_index_status.model_dump(mode="json"),
                    indent=2,
                    default=str,
                )
            )
        else:
            display_project_index_status(
                project_name,
                "Project Index",
                project_index_status,
                verbose,
            )
    except (ValueError, ToolError) as e:
        if json_output:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:
        logger.error(f"Error checking status: {e}")
        if json_output:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            typer.echo(f"Error checking status: {e}", err=True)
        raise typer.Exit(code=1)  # pragma: no cover
