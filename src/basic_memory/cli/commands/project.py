"""Command module for basic-memory project management."""

import os
from pathlib import Path
from typing import Optional

import typer

from basic_memory.cli.app import app
from basic_memory.cli.direct import direct_project_service
from basic_memory.cli.notices import emit_notices
from basic_memory.cli.runner import run_with_cleanup
from basic_memory.cli.scope import ReadScope

# Module level, unlike this file's other `project_registry` imports: it names a
# constant rather than reaching the registry, and `cli/commands/mcp.py` already
# puts this module on every invocation's import path (GAPS.md T30).
from basic_memory.project_registry import PROJECT_HOME_EXTERNAL
from basic_memory.schemas.project_info import ProjectItem, ProjectList
from basic_memory.utils import generate_permalink

# Create a project subcommand
project_app = typer.Typer(help="Manage multiple Basic Memory projects")
app.add_typer(project_app, name="project")


def format_path(path: str) -> str:
    """Format a path for display, using ~ for home directory."""
    home = str(Path.home())
    if path.startswith(home):
        return path.replace(home, "~", 1)  # pragma: no cover
    return path


def fail(message: str) -> typer.Exit:
    """Write one error line to stderr and return the exit the caller raises.

    Output contract rule 6: errors are a single line on stderr, exit 1, and
    nothing lands on stdout on the error path.
    """
    typer.echo(message, err=True)
    return typer.Exit(1)


async def fetch_project_list() -> ProjectList:
    """Fetch the project registry via the direct service path.

    Native read commands talk to the service layer directly instead of routing
    through the in-process FastAPI app — the ASGI path costs ~2.5 CPU-seconds
    and ~100 MB per invocation just in imports (GAPS.md T18).
    """
    service = await direct_project_service()
    projects = await service.list_projects()
    # Trigger: an empty registry, which nothing bootstraps away any more (GAPS U15).
    # Why: `get_default_project_name` raises when no project is flagged default,
    #     and an install with no projects at all has none — that is the honest
    #     first-run state, not a fault, and it must render as "0 projects" rather
    #     than as "Error listing projects: No default project configured".
    # Outcome: skip the lookup; `ProjectList.default_project` is already optional.
    default_project = await service.get_default_project_name() if projects else None
    return ProjectList(
        projects=[
            ProjectItem(
                id=project.id,
                external_id=project.external_id,
                name=project.name,
                path=project.path,
                is_default=project.is_default or False,
            )
            for project in projects
        ],
        default_project=default_project,
    )


async def fetch_project_info(name: str):
    """Fetch one project's info via the direct service path (GAPS U-series, 2026-08-20).

    `ProjectService.get_project_info` builds the whole payload from the
    repository layer, so nothing about this read needed the in-process ASGI app
    it used to route through — that path cost ~3.1 s user CPU and ~220 MB per
    invocation in imports alone (measured 2026-08-19; AGENTS.md "Measured
    baseline").
    """
    service = await direct_project_service()
    return await service.get_project_info(name)


@project_app.command("list")
def list_projects(
    quiet: bool = typer.Option(False, "--quiet", help="Hide the status lines and next-step hints"),
) -> None:
    """List Basic Memory projects."""

    try:
        result = run_with_cleanup(fetch_project_list())
    except typer.Exit:
        raise
    except Exception as e:
        raise fail(f"Error listing projects: {e}")

    projects = sorted(result.projects, key=lambda project: project.name)
    # Name is the identifier callers pass to --project, so it leads the row and
    # keeps a fixed width; nothing else may push it out of column one.
    name_width = max((len(project.name) for project in projects), default=0)
    for project in projects:
        marker = "  (default)" if project.is_default else ""
        typer.echo(f"{project.name:<{name_width}}  {format_path(project.path)}{marker}")
    typer.echo(f"{len(projects)} projects")

    # The listing covers every project, so the notice does too — no marker walk,
    # because the payload ignored one (see `cli/notices.py`).
    emit_notices(ReadScope(project=None, origin="unscoped"), quiet=quiet, command="project list")


def mark_here(name: str, only_here: Optional[bool] = None) -> None:
    """Write the current directory's `.bm.yml` for a registered project (GAPS U21).

    Both the marker's keys are resolved from the registry rather than taken from
    the caller: `project:` is the registered spelling of the name, so a marker
    written from a permalink still resolves, and `id:` is the project's
    `external_id`, which is also its store directory name.

    `only_here` is the `scope:` key (GAPS U40): True narrows the marker to this
    directory, False widens it to the tree, and None — what a caller that did
    not ask passes — keeps whatever an existing marker declares.

    Reads the registry through the synchronous sqlite path, not the service
    layer — the marker write needs two columns of one row, and this keeps the
    verb off the SQLAlchemy import (AGENTS.md, "Measured baseline").
    """
    from basic_memory.project_marker import (
        MarkerError,
        read_marker_only_here,
        repo_identity,
        write_marker,
    )
    from basic_memory.project_registry import (
        lookup_project_external_id,
        lookup_project_repo,
        record_project_repo,
    )

    registered, external_id = lookup_project_external_id(name)
    if registered is None or external_id is None:
        raise fail(f"Error: '{name}' is not a registered project (see 'bm project list')")

    try:
        marker = write_marker(Path.cwd(), registered, external_id, only_here=only_here)
    except MarkerError as error:
        raise fail(f"Error: {error}")
    except OSError as error:
        raise fail(f"Error: could not write the marker: {error}")

    # Repo identity capture (GAPS U36): marking is the one moment a directory
    # and a project are known to belong together, so it is when the registry
    # learns the repo's origin URL. Fill-empty-only: a mismatch is evidence a
    # second repo is claiming this project, and the human gets a warning, not
    # a silent overwrite.
    repo_line: Optional[str] = None
    observed_repo = repo_identity(Path.cwd())
    if observed_repo is not None:
        recorded_repo = lookup_project_repo(registered)
        if recorded_repo is None:
            if record_project_repo(registered, observed_repo):
                repo_line = observed_repo
        elif recorded_repo != observed_repo:
            typer.echo(
                f"warning: this directory's origin is {observed_repo}, but '{registered}' "
                f"is recorded at {recorded_repo} — not overwritten",
                err=True,
            )

    # Contract rule 1: a single record renders as labelled lines. `marker:` is
    # last because it is the longest value and the one a reader scans for.
    typer.echo(f"project: {registered}")
    typer.echo(f"id: {external_id}")
    if repo_line is not None:
        typer.echo(f"repo: {repo_line}")
    # Read back rather than echo the flag: on the preserve path (`only_here` is
    # None) the file is the only place that knows which shape was written, and
    # what the user needs told is what is now on disk (GAPS U40).
    scope_note = " (only here)" if read_marker_only_here(marker) else ""
    typer.echo(f"marker: {marker}{scope_note}")


@project_app.command("mark")
def mark_project(
    name: Optional[str] = typer.Argument(
        None, help="Project to mark. Defaults to the name the existing .bm.yml carries."
    ),
    if_repo_matches: bool = typer.Option(
        False,
        "--if-repo-matches",
        help="Mark only when a registered project's recorded repo equals this "
        "directory's git origin URL. Exit 3 on no match, 4 when several match.",
    ),
    only_here: bool = typer.Option(
        False,
        "--only-here",
        help="Mark only this directory; subdirectories do not inherit the marker",
    ),
) -> None:
    """Point the current directory at a project by writing its `.bm.yml`.

    The marker records the project's name **and** its store id, so a script can
    reach `<store>/<id>/headline.md` without paying for a `bm` invocation. This
    is also the retrofit path: a marker written before the id existed carries
    only the name, and `bm project mark` with no argument fills the id in.

    `--only-here` writes `scope: here`, which stops the marker at its own
    directory: a catch-all workspace project can then sit at `~/develop`
    without claiming the scratch folders under it (GAPS U40). Without the flag
    the scope an existing marker declares is kept, so the retrofit above never
    widens a marker behind the user's back — delete the `scope:` line to widen
    one deliberately.

    Marking also records the directory's git origin URL on the project (GAPS
    U36), which is what `--if-repo-matches` later matches against: markers are
    gitignored, so a fresh clone arrives unmarked, and the session hook uses
    this flag to re-mark it mechanically instead of asking. With the flag, the
    NAME argument is optional — a single match supplies it — and the exit code
    is the answer: 0 marked, 3 no match (or no origin remote here), 4 more
    than one project claims this repo.

    Refuses when the directory's marker names a different project — that tree
    belongs to something else. Remove the marker first.

    Examples:
        bm project mark research
        bm project mark
        bm project mark --if-repo-matches
        bm project mark workspace --only-here
    """
    # A bare flag is two-state, and the scope is three: absent must mean
    # "preserve", not "widen to the tree" — see `write_marker` (GAPS U40).
    requested_scope = True if only_here else None

    if if_repo_matches:
        from basic_memory.project_marker import repo_identity
        from basic_memory.project_registry import lookup_projects_by_repo

        # Exit codes, not prose, are this flag's interface: the session hook
        # branches on them without parsing a line that may later be reworded.
        observed_repo = repo_identity(Path.cwd())
        if observed_repo is None:
            typer.echo("no origin remote here — nothing to match against", err=True)
            raise typer.Exit(3)

        matches = lookup_projects_by_repo(observed_repo)
        if name is not None:
            matches = [match for match in matches if match[0] == name]
        if not matches:
            typer.echo(f"no registered project records repo {observed_repo}", err=True)
            raise typer.Exit(3)
        if len(matches) > 1:
            names = ", ".join(match_name for match_name, _ in matches)
            typer.echo(
                f"{len(matches)} projects record repo {observed_repo}: {names} — "
                "name one: bm project mark <name>",
                err=True,
            )
            raise typer.Exit(4)

        mark_here(matches[0][0], requested_scope)
        return

    if name is None:
        from basic_memory.project_marker import MARKER_FILENAME, MarkerError, read_marker_project

        # Only this directory's own marker, never one walked up to: `mark` writes
        # here, and defaulting the name from a parent's marker would silently
        # adopt that project into a subdirectory the caller never named.
        marker = Path.cwd() / MARKER_FILENAME
        try:
            name = read_marker_project(marker) if marker.is_file() else None
        except MarkerError as error:
            raise fail(f"Error: {error}")
        if name is None:
            raise fail(
                "Error: no .bm.yml here to refresh — name the project: bm project mark <name>"
            )

    mark_here(name, requested_scope)


@project_app.command("vocab-sync")
def vocab_sync(
    name: Optional[str] = typer.Argument(
        None, help="Project to sync. Defaults to the .bm.yml above the current directory."
    ),
    quiet: bool = typer.Option(False, "--quiet", help="Hide the notices."),
) -> None:
    """Bring a governed project's vocabulary up to the current defaults, additively.

    The explicit half of GAPS U39: an untouched machine snapshot upgrades
    itself, but a hand-edited `vocabulary.yml` is the human's file, so new
    default names (a type like `plan`, a relation like `part_of`) wait here for
    a human to ask. Additive only — nothing the file declares is removed or
    reordered; missing default types, statuses, relations and aliases are
    appended, and `areas`, `fields` and `review_months` pass through untouched.

    Running it on an untouched snapshot performs the same upgrade the automatic
    path would. Running it twice is a no-op that says so.
    """
    from basic_memory.project_marker import resolve_cli_project
    from basic_memory.project_registry import lookup_project_external_id
    from basic_memory.vocabulary.model import (
        VocabularyError,
        defaults_delta,
        load_vocabulary,
        matches_superseded_defaults,
        sync_vocabulary_with_defaults,
        upgrade_snapshot_vocabulary,
    )

    resolved = name if name is not None else resolve_cli_project(None)
    if not resolved:
        raise fail(
            "Error: no project — pass a name, or run from a directory whose .bm.yml names one"
        )
    registered, external_id = lookup_project_external_id(resolved)
    if registered is None or external_id is None:
        raise fail(f"Error: '{resolved}' is not a registered project (see 'bm project list')")

    try:
        vocabulary = load_vocabulary(external_id)
    except VocabularyError as error:
        raise fail(f"Error: {error}")
    if vocabulary is None:
        raise fail(
            f"Error: '{registered}' is not governed — there is no vocabulary to sync "
            "(a project created with --ungoverned has none)"
        )

    delta = defaults_delta(vocabulary)
    if delta.empty:
        typer.echo("vocabulary already current")
    elif matches_superseded_defaults(vocabulary):
        # A snapshot converges on the canonical current defaults, not on an
        # append-merge: the file stays byte-identical to a fresh one, which is
        # what keeps it snapshot-detectable at the NEXT generation too.
        upgrade_snapshot_vocabulary(external_id)
        typer.echo(f"added {delta.describe()}")
    else:
        sync_vocabulary_with_defaults(external_id, vocabulary)
        typer.echo(f"added {delta.describe()}")

    # The write chain resolved the project, so the scope is pinned the same way
    # record_write pins its own — and the notice pass is what revalidates the
    # project's records against the vocabulary this command just changed.
    emit_notices(
        ReadScope(project=registered, origin="write"), quiet=quiet, command="project vocab-sync"
    )


@project_app.command("add")
def add_project(
    name: str = typer.Argument(..., help="Name of the project"),
    path: Optional[str] = typer.Argument(
        None, help="A directory whose notes to adopt. Omit it to use the store."
    ),
    set_default: bool = typer.Option(False, "--default", help="Set as default project"),
    ungoverned: bool = typer.Option(
        False,
        "--ungoverned",
        help="Skip the default record vocabulary; writes to this project go unchecked.",
    ),
    # Kept only so the callers that still pass it keep working — the session hook
    # and the migration skill both spell it out. It asks for what already happens,
    # so it is accepted and does nothing (GAPS U49).
    governed: bool = typer.Option(
        False,
        "--governed",
        hidden=True,
        help="Deprecated no-op: a new project is governed by default. Removed in a later release.",
    ),
    here: bool = typer.Option(
        False,
        "--here",
        help="Write a .bm.yml in the current directory pointing at the new project.",
    ),
    only_here: bool = typer.Option(
        False,
        "--only-here",
        help="Mark only this directory; subdirectories do not inherit the marker",
    ),
    home_here: bool = typer.Option(
        False,
        "--home-here",
        help="Home the notes in ./.bm, for a directory something else already versions.",
    ),
    quiet: bool = typer.Option(False, "--quiet", help="Hide the status lines and next-step hints"),
) -> None:
    """Add a new project, homed in the store.

    A project's notes live under `<data dir>/store/<id>/`, which is what puts
    every write in the note history. The data dir is `$BASIC_MEMORY_CONFIG_DIR`,
    else `$XDG_CONFIG_HOME/basic-memory` when that variable is set, else
    `~/.basic-memory`. A path argument names an *import source* — somewhere notes
    already are — and that project keeps living there.

    A new project is governed: the default record vocabulary is written into it,
    which turns the schema checks on for every write. `--ungoverned` skips that
    file, and records are then written unchecked. The default vocabulary declares
    `note` alongside the seven record types, so an ordinary note still has a home
    in a governed project — what governance costs is that every write is checked.

    `--here` leaves a `.bm.yml` in the current directory, so every `bm` command
    run from it means this project without naming it. Add `--only-here` to stop
    the marker at that directory, leaving subdirectories unclaimed (GAPS U40).

    `--home-here` homes the notes in `./.bm` instead of the store, for a
    directory something else already versions — a Claude Code skill yadm carries
    between machines. The vocabulary travels beside the records. It implies
    `--here` over the whole tree, takes no path argument, and never resolves
    `./.bm`: under yadm's link mode that name is a per-machine symlink.

    Example:
        bm project add research
    """
    # Contradictory flags are an error, never a precedence rule: the caller who
    # spelled both out has no idea which one this command would honour.
    if governed and ungoverned:
        raise fail("Error: --governed and --ungoverned contradict each other; pass one")

    # `--home-here` names the directory itself, so a path argument would be a
    # second, contradictory answer to the same question.
    if home_here and path is not None:
        raise fail("Error: --home-here homes the notes in ./.bm and takes no path argument")
    # A skill's `.bm.yml` has to claim the tree: `bm` run from `references/` must
    # still mean the skill, and `--only-here` would stop the marker at the root.
    if home_here and only_here:
        raise fail("Error: --home-here claims the whole tree; --only-here would narrow it")

    # `--home-here` implies `--here`: the notes sit one directory down, so the
    # directory above them is what has to resolve to this project. Scope stays
    # whatever `mark_here` gives an unqualified marker — the tree.
    here = here or home_here

    # Decision point: the marker conflict is checked before the project is
    # created, not after it is written.
    # Why: refusing after the create would leave a registered project behind and
    #     report a failure, so the caller could not tell what had happened.
    # Outcome: a foreign marker fails the whole command with nothing created.
    if only_here and not here:
        raise fail("Error: --only-here narrows the marker that --here writes; pass both")
    if here:
        from basic_memory.project_marker import MarkerError, marker_conflict

        try:
            conflict = marker_conflict(Path.cwd(), name)
        except MarkerError as error:
            raise fail(str(error))
        if conflict:
            raise fail(
                f"Error: this directory's .bm.yml already names project '{conflict}'; "
                f"remove it first to mark it as '{name}'"
            )
    # Resolve to absolute path. None stays None: it means "no import source", and
    # the service gives the project its store-derived home (decision D3).
    #
    # Constraint: the `--home-here` path is joined and never resolved. Under
    # yadm's link mode `.bm` is a symlink to `.bm##class.home`, so a path
    # resolved on this machine would not match the literal one the next machine
    # records — and `bm project adopt` compares them. `Path.cwd()` is already
    # absolute, and the service's own path branch uses `os.path.abspath`, which
    # normalizes without following symlinks either.
    if home_here:
        resolved_path = (Path.cwd() / ".bm").as_posix()
    elif path is None:
        resolved_path = None
    else:
        resolved_path = Path(os.path.abspath(os.path.expanduser(path))).as_posix()

    # Deferred: the MCP client graph costs ~0.04 s of import beyond what the CLI
    # already pays, and only the client-routed project subcommands need it.
    # `cli/main.py` imports this module for every invocation, so a module-level
    # import would put that cost on `project list` and every native verb
    # (GAPS.md T30).
    from basic_memory.mcp.async_client import get_client
    from basic_memory.mcp.clients import ProjectClient

    async def _add_project():
        async with get_client() as client:
            data: dict[str, str | bool | None] = {
                "name": name,
                "path": resolved_path,
                "set_default": set_default,
                # Only the opt-out carries information now; `--governed` names
                # the default and adds nothing to it (GAPS U49).
                "governed": not ungoverned,
            }
            # Sent only when declared. `ProjectInfoRequest.home` defaults to
            # None, so an absent key says exactly what an explicit null would,
            # and every other `project add` keeps the payload it always sent.
            if home_here:
                data["home"] = PROJECT_HOME_EXTERNAL
            return await ProjectClient(client).create_project(data)

    try:
        result = run_with_cleanup(_add_project())
    except typer.Exit:
        raise
    except Exception as e:
        raise fail(f"Error adding project: {e}")

    typer.echo(result.message)

    # The marker is written from the registry, after the create: the project's
    # `external_id` is assigned by the service, and the registry is where it is
    # readable without depending on what the response happened to carry.
    if here:
        # Same two-into-three mapping as `mark`: no flag means "preserve", so
        # no path in this file can widen a narrowed marker silently (GAPS U40).
        mark_here(name, True if only_here else None)

    # Trigger: the caller named a directory instead of taking the store default.
    # Why: notes outside `store/<id>/` are not in the history repo's worktree, so
    #     every write to that project skips its commit and says so. Stating it
    #     once at creation beats discovering it one notice at a time.
    # Outcome: one notice naming the consequence, dropped by --quiet. A
    #     `--home-here` project is off-store too but never reaches here — it
    #     takes no path argument — and that is deliberate: it declared its home,
    #     and whatever versions that directory records the writes bm would have.
    if path is not None and not quiet:
        typer.echo(
            f"note: '{name}' keeps its notes at {resolved_path}, outside the store — "
            "writes there are not recorded in the note history"
        )

    # Trigger: the service made the new project the default without being
    #     asked — it is the first project in an empty registry.
    # Why: the default is what every unqualified command targets, and a silent
    #     move means the next `bm` invocation writes somewhere the user did not
    #     choose. `bm project remove` then refuses, citing a default nobody set.
    # Outcome: the move is stated, with the command to put it back.
    if result.default and not set_default and not quiet:
        typer.echo(f"'{name}' is now the default project.")
        typer.echo("Change it with: bm project default <name>")


@project_app.command("remove")
def remove_project(
    name: str = typer.Argument(..., help="Name of the project to remove"),
    delete_notes: bool = typer.Option(
        False, "--delete-notes", help="Delete project files from disk"
    ),
) -> None:
    """Remove a project."""
    # Deferred: the MCP client graph costs ~0.04 s of import beyond what the CLI
    # already pays, and only the client-routed project subcommands need it.
    # `cli/main.py` imports this module for every invocation, so a module-level
    # import would put that cost on `project list` and every native verb
    # (GAPS.md T30).
    from basic_memory.mcp.async_client import get_client
    from basic_memory.mcp.clients import ProjectClient

    async def _remove_project():
        async with get_client() as client:
            project_client = ProjectClient(client)
            # Convert name to permalink for efficient resolution
            project_permalink = generate_permalink(name)
            target_project = await project_client.resolve_project(project_permalink)
            return await project_client.delete_project(
                target_project.external_id, delete_notes=delete_notes
            )

    try:
        result = run_with_cleanup(_remove_project())
    except typer.Exit:
        raise
    except Exception as e:
        # str() of httpx transport errors is often empty (#1034) — never print a blank error.
        raise fail(f"Error removing project: {str(e) or repr(e)}")

    typer.echo(result.message)


@project_app.command("default")
def set_default_project(
    name: str = typer.Argument(..., help="Name of the project to set as CLI default"),
) -> None:
    """Set the project that bm uses when a command names no project."""
    # Deferred: the MCP client graph costs ~0.04 s of import beyond what the CLI
    # already pays, and only the client-routed project subcommands need it.
    # `cli/main.py` imports this module for every invocation, so a module-level
    # import would put that cost on `project list` and every native verb
    # (GAPS.md T30).
    from basic_memory.mcp.async_client import get_client
    from basic_memory.mcp.clients import ProjectClient

    async def _set_default():
        async with get_client() as client:
            project_client = ProjectClient(client)
            # Convert name to permalink for efficient resolution
            project_permalink = generate_permalink(name)
            target_project = await project_client.resolve_project(project_permalink)
            return await project_client.set_default(target_project.external_id)

    try:
        result = run_with_cleanup(_set_default())
    except typer.Exit:
        raise
    except Exception as e:
        raise fail(f"Error setting default project: {e}")

    typer.echo(result.message)


@project_app.command("move")
def move_project(
    name: str = typer.Argument(..., help="Name of the project to move"),
    new_path: str = typer.Argument(..., help="New absolute path for the project"),
    quiet: bool = typer.Option(False, "--quiet", help="Hide the status lines and next-step hints"),
) -> None:
    """Point a project at a new location on disk.

    The command updates the stored path only. It moves no files.
    """
    # Resolve to absolute path
    resolved_path = Path(os.path.abspath(os.path.expanduser(new_path))).as_posix()

    # Deferred: the MCP client graph costs ~0.04 s of import beyond what the CLI
    # already pays, and only the client-routed project subcommands need it.
    # `cli/main.py` imports this module for every invocation, so a module-level
    # import would put that cost on `project list` and every native verb
    # (GAPS.md T30).
    from basic_memory.mcp.async_client import get_client
    from basic_memory.mcp.clients import ProjectClient

    async def _move_project():
        async with get_client() as client:
            project_client = ProjectClient(client)
            project_info = await project_client.resolve_project(name)
            return await project_client.update_project(
                project_info.external_id, {"path": resolved_path}
            )

    try:
        result = run_with_cleanup(_move_project())
    except typer.Exit:
        raise
    except Exception as e:
        raise fail(f"Error moving project: {e}")

    typer.echo(result.message)

    # The command moves configuration only; the files stay where they were, so
    # a caller that stops reading here would leave the project pointing at an
    # empty directory.
    if not quiet:
        typer.echo("Only the configuration moved — the files are still in the old location.")
        typer.echo(f"Move the project files to: {resolved_path}")


@project_app.command("ls")
def ls_project_command(
    name: str = typer.Option(..., "--name", help="Project name to list files from"),
    path: str = typer.Argument(None, help="Path within project (optional)"),
    quiet: bool = typer.Option(False, "--quiet", help="Hide the status lines and next-step hints"),
) -> None:
    """List files in a project.

    Examples:
      bm project ls --name research
      bm project ls --name research subfolder
    """

    def _list_local_files(project_path: str, subpath: str | None = None) -> list[tuple[str, int]]:
        project_root = Path(project_path).expanduser().resolve()
        target_dir = project_root

        if subpath:
            requested = Path(subpath)
            if requested.is_absolute():
                raise ValueError("Path must be relative to the project root")
            target_dir = (project_root / requested).resolve()
            if not target_dir.is_relative_to(project_root):
                raise ValueError("Path must stay within the project root")

        if not target_dir.exists():
            raise ValueError(f"Path not found: {target_dir}")
        if not target_dir.is_dir():
            raise ValueError(f"Path is not a directory: {target_dir}")

        return [
            (file_path.relative_to(project_root).as_posix(), file_path.stat().st_size)
            for file_path in sorted(target_dir.rglob("*"))
            if file_path.is_file()
        ]

    async def _get_project():
        projects_list = await fetch_project_list()
        for proj in projects_list.projects:
            if generate_permalink(proj.name) == generate_permalink(name):
                return proj
        return None

    try:
        project_data = run_with_cleanup(_get_project())
        if not project_data:
            raise fail(f"Error: Project '{name}' not found")
        files = _list_local_files(project_data.path, path)
    except typer.Exit:
        raise
    except Exception as e:
        raise fail(f"Error: {e}")

    path_width = max((len(relative) for relative, _ in files), default=0)
    for relative, size in files:
        typer.echo(f"{relative:<{path_width}}  {size}")
    typer.echo(f"{len(files)} files")

    # `--name` is mandatory here, so the read is pinned by the verb's own shape.
    emit_notices(
        ReadScope(project=project_data.name, origin="flag"), quiet=quiet, command="project ls"
    )


@project_app.command("info")
def display_project_info(
    name: str = typer.Argument(..., help="Name of the project"),
    quiet: bool = typer.Option(False, "--quiet", help="Hide the status lines and next-step hints"),
):
    """Show the settings, the counts, and the system details for one project."""
    try:
        info = run_with_cleanup(fetch_project_info(name))
    except typer.Exit:
        raise
    except Exception as e:
        raise fail(f"Error getting project info: {e}")

    statistics = info.statistics
    system = info.system

    typer.echo("Project")
    typer.echo(f"name: {info.project_name}")
    typer.echo(f"path: {format_path(info.project_path)}")
    # Only when declared. NULL is the default — store-homed, or a legacy
    # off-store project — and it has nothing to say, while an external home
    # changes what the reader can expect from `bm history` and `bm undo`. So the
    # line carries that consequence rather than the bare word.
    if info.project_home == PROJECT_HOME_EXTERNAL:
        typer.echo(
            f"home: {PROJECT_HOME_EXTERNAL} — {format_path(info.project_path)}; "
            "bm records no history for it"
        )
    # Only when captured: an absent repo is the common historical state, and a
    # `repo: None` line would read as a value (GAPS U36).
    if info.project_repo:
        typer.echo(f"repo: {info.project_repo}")
    typer.echo(f"default project: {info.default_project}")

    typer.echo("")
    typer.echo("Statistics")
    typer.echo(f"entities: {statistics.total_entities}")
    typer.echo(f"observations: {statistics.total_observations}")
    typer.echo(f"relations: {statistics.total_relations}")
    typer.echo(f"unresolved relations: {statistics.total_unresolved_relations}")
    typer.echo(f"isolated entities: {statistics.isolated_entities}")
    for note_type, count in sorted(
        statistics.note_types.items(), key=lambda item: (-item[1], item[0])
    ):
        typer.echo(f"note type {note_type}: {count}")

    embeddings = info.embedding_status
    if embeddings:
        typer.echo("")
        typer.echo("Embeddings")
        typer.echo(
            f"semantic search: {'enabled' if embeddings.semantic_search_enabled else 'disabled'}"
        )
        if embeddings.semantic_search_enabled:
            typer.echo(f"provider: {embeddings.embedding_provider or ''}")
            typer.echo(f"model: {embeddings.embedding_model or ''}")
            typer.echo(
                f"indexed entities: {embeddings.total_entities_with_chunks}"
                f"/{embeddings.total_indexed_entities}"
            )
            typer.echo(f"chunks: {embeddings.total_chunks}")

    typer.echo("")
    typer.echo("System")
    typer.echo(f"version: {system.version}")
    typer.echo(f"database: {system.database_path}")
    typer.echo(f"database size: {system.database_size}")
    typer.echo(f"timestamp: {system.timestamp}")

    # A stale vector index answers semantic queries from content that no longer
    # exists, so the recommendation is a notice with the command that clears it.
    if embeddings and embeddings.reindex_recommended and not quiet:
        reason = f" — {embeddings.reindex_reason}" if embeddings.reindex_reason else ""
        typer.echo(f"Reindex recommended{reason}")
        typer.echo(f"Run 'bm reindex --project {info.project_name}' to rebuild the index.")

    # The argument is mandatory, so this read is pinned by the verb's own shape.
    emit_notices(
        ReadScope(project=info.project_name, origin="flag"), quiet=quiet, command="project info"
    )
