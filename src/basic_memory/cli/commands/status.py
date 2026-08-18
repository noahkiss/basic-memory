"""Status command for basic-memory CLI.

Scope follows GAPS W5-C: `--project` > nearest `.bm.yml` > every project. The
registry default retired from this read path — an unmarked working directory now
reports every project, one section each, rather than one arbitrary project.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Optional, Sequence

import typer
from loguru import logger

from basic_memory.cli.app import app
from basic_memory.cli.notices import emit_notices
from basic_memory.cli.scope import resolve_read_scope
from basic_memory.schemas import ProjectIndexStatusResponse


@dataclass(frozen=True, slots=True)
class MissingProject:
    """One registered project whose directory is not there any more (GAPS U12)."""

    name: str
    path: str


@dataclass(frozen=True, slots=True)
class StatusScan:
    """What one status pass could report, and which projects it could not read.

    The same shape `direct_revalidate_vocabulary` uses: a whole-registry read
    must not lose every project to one broken one.
    """

    reports: list[tuple[str, ProjectIndexStatusResponse]] = field(default_factory=list)
    missing: list[MissingProject] = field(default_factory=list)


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
    projects: Optional[Sequence[str]] = None,
    wait: bool = False,
    timeout: float = 30.0,
    poll_interval: float = 0.5,
) -> StatusScan:
    """Fetch current project-index observation status, one row per project.

    ``projects`` names the projects to report. ``None`` means every registered
    project, which is what an unscoped read resolves to (GAPS W5-C).

    Trigger: a registered project whose directory has gone (GAPS U12).
    Why: the status endpoint walks that path, so an unguarded pass died on
        ``Error checking status: [Errno 2] …`` and reported *no* project — the
        healthy ones included. That is the same wall `bm reindex` hit.
    Outcome: the project is named in ``missing`` and skipped, the rest report as
        usual, and the caller decides what to print and what to exit with. The
        check is a ``stat`` rather than a caught exception, so a real failure
        still propagates.

    The event-index flow no longer exposes a pending-change counter. The watcher
    is the incremental path, and explicit project indexing is a full fanout.
    ``wait`` is accepted as a compatibility flag and returns the current
    observation immediately.

    Returns [(project_name, project_index_status)] for the caller to render.
    """
    # Deferred: the MCP client graph costs ~0.04 s of import beyond what the CLI
    # already pays, and `cli/main.py` imports this module for every invocation —
    # a module-level import would put that cost on every native verb (GAPS.md T30).
    from basic_memory.mcp.async_client import get_client
    from basic_memory.mcp.clients import ProjectClient
    from basic_memory.mcp.project_context import get_active_project

    # Trigger: --wait on any scope.
    # Why: the event-based index exposes no pending counter to poll for, so waiting
    #      would sleep against a number that never moves.
    # Outcome: log it once and report the current observation.
    if wait:
        logger.debug(
            "status --wait is a compatibility no-op for event-based project indexing",
            timeout=timeout,
            poll_interval=poll_interval,
        )

    # One client for every project in scope — reconnecting per project would pay the
    # ASGI setup cost once per row.
    async with get_client() as client:
        project_client = ProjectClient(client)

        if projects is None:
            listed = await project_client.list_projects()
            items = list(listed.projects)
        else:
            items = [await get_active_project(client, name, None) for name in projects]

        scan = StatusScan()
        for item in items:
            if not Path(item.path).is_dir():
                scan.missing.append(MissingProject(name=item.name, path=item.path))
                continue
            scan.reports.append((item.name, await project_client.get_status(item.external_id)))
        return scan


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
    """Show how many files bm saw in each project, and how many it has indexed.

    Reports every project unless `--project` or a `.bm.yml` above the working
    directory pins one.

    The --wait flag is kept for compatibility. It reports the current counts at
    once and waits for nothing.
    """
    from basic_memory.cli.runner import run_with_cleanup

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
        scope = resolve_read_scope(project)
        projects = None if scope.project is None else [scope.project]
        scan = run_with_cleanup(run_status(projects, wait=wait, timeout=timeout))
    except (ValueError, ToolError) as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as e:  # pragma: no cover
        logger.error(f"Error checking status: {e}")
        typer.echo(f"Error checking status: {e}", err=True)
        raise typer.Exit(code=1)

    # Trigger: an unscoped run against an empty registry.
    # Why: contract rule 5 — a well-scoped request whose answer is "nothing there" is a
    #      result, not a failure, and silence would read as a healthy corpus.
    # Outcome: state it and exit 0.
    if not scan.reports and not scan.missing:
        typer.echo("no projects registered")
        emit_notices(scope, quiet=quiet, command="status")
        return

    # A grouped report renders as one plain section per project (contract rule 1). The
    # per-project block is unchanged, so a pinned run prints exactly what it always did.
    for position, (project_name, project_index_status) in enumerate(scan.reports):
        if position:
            typer.echo("")
        display_project_index_status(project_name, project_index_status, verbose, quiet)

    emit_notices(scope, quiet=quiet, command="status")

    # A project that could not be read is an error about that project, so it goes to
    # stderr — the payload above stays parseable — and it decides the exit code. The
    # healthy projects still reported, which is the whole point of the degradation.
    for missing in scan.missing:
        typer.echo(f"Error: project '{missing.name}' has no directory at {missing.path}", err=True)
    if scan.missing:
        raise typer.Exit(code=1)
