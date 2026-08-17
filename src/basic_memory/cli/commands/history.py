"""Command module for the note store's local git history, and `bm undo`.

Imports stay narrow on purpose: `bm history dirty` and `bm history commit` read a
git repository, so nothing here may pull the API or MCP import graph onto its
path. `bm undo` is the one verb here that also touches the database — it has to,
because a file restored on disk is invisible to search until it is indexed (GAPS
T2) — and it reaches the indexing layer directly, never through `mcp` or `api`.

`undo` ships flat as `bm undo` (AGENTS.md's verb list) while living in this file,
next to the two verbs that read the same repository. The documented verb list is
the contract; the file layout is not.
"""

from collections.abc import Sequence
from typing import Annotated, Optional

import typer

from basic_memory.cli.app import app
from basic_memory.cli.notices import emit_notices
from basic_memory.cli.runner import run_with_cleanup
from basic_memory.cli.scope import ReadScope
from basic_memory.store.history import (
    CommitResult,
    HistoryError,
    commit_paths,
    commits_for_session,
    dirty_paths,
    latest_commit,
    paths_in_commit,
    restore_from_commit,
    sweep_commit,
)
from basic_memory.store.write_hook import project_store_prefix

# The store holds every project's notes in one repository, so a history verb
# reads across all of them and its notice says so (GAPS W5-C).
STORE_SCOPE = ReadScope(project=None, origin="unscoped")

# Static affordance (GAPS W19 item 5, VERBS_PLAN §5 J). Static is the
# requirement: a hint that appears only sometimes teaches the surface unreliably.
UNDO_AFFORDANCE = "bm history dirty see what is uncommitted · bm show <id> read the restored entry"

# `bm undo` is a human-typed command, so `cli` is what the tool actually knows
# about who ran it (GAPS W3-B: silence beats a guess).
UNDO_ACTOR = "cli"

history_app = typer.Typer(help="Inspect and commit the note store's local history")
app.add_typer(history_app, name="history")


def fail(message: str) -> typer.Exit:
    """Write one error line to stderr and return the exit the caller raises.

    Output contract rule 6: errors are a single line on stderr, exit 1, and
    nothing lands on stdout on the error path.
    """
    typer.echo(message, err=True)
    return typer.Exit(1)


@history_app.command()
def dirty(
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the status lines and next-step hints."),
    ] = False,
) -> None:
    """List note-store files with uncommitted changes."""
    try:
        entries = dirty_paths()
    except HistoryError as exc:
        raise fail(f"Error: {exc}")

    # Path leads the row: it is the identifier a caller acts on (contract rule 2).
    path_width = max((len(path) for _, path in entries), default=0)
    for status, path in entries:
        typer.echo(f"{path:<{path_width}}  {status}")
    typer.echo(f"{len(entries)} dirty files")

    emit_notices(STORE_SCOPE, quiet=quiet, command="history dirty")


@history_app.command()
def commit(
    paths: Annotated[
        Optional[list[str]],
        typer.Argument(help="Store-relative paths to commit."),
    ] = None,
    commit_all: Annotated[
        bool,
        typer.Option("--all", help="Commit every dirty file as one commit."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the status lines and next-step hints."),
    ] = False,
) -> None:
    """Commit note changes that bm did not make itself.

    The sweep is its own command rather than a flag on a write verb: a flag
    would weld unrelated changes into one commit, and undoing the tool's work
    would then undo somebody else's.
    """
    if commit_all and paths:
        raise fail("Error: pass either paths or --all, not both.")
    if not commit_all and not paths:
        raise fail("Error: pass one or more paths, or --all.")

    scope = "--all" if commit_all else " ".join(paths or ())
    try:
        result = sweep_commit(f"bm history commit {scope}", None if commit_all else paths)
    except HistoryError as exc:
        raise fail(f"Error: {exc}")

    # An empty sweep is a result, not a failure (contract rule 5).
    if result is None:
        typer.echo("nothing to commit")
        emit_notices(STORE_SCOPE, quiet=quiet, command="history commit")
        return

    typer.echo(f"sha: {result.sha}")
    typer.echo(f"paths: {len(result.paths)} committed")

    if result.dirty_others and not quiet:
        typer.echo(
            f"note: {len(result.dirty_others)} other files have uncommitted changes "
            "(not included in this commit)"
        )
        typer.echo("run 'bm history dirty' to review")

    emit_notices(STORE_SCOPE, quiet=quiet, command="history commit")


# --- bm undo ---


async def reindex_restored_paths(paths: Sequence[str]) -> tuple[int, int]:
    """Reindex the projects that own ``paths``; return (projects, unowned paths).

    A restore writes files straight into the store's worktree, behind the write
    path. Those files are invisible to search until they are indexed (GAPS T2),
    so undo has to close the loop itself.

    The project index is the smallest *correct* call, not the cheapest
    imaginable one. No public per-file entry point reconciles a *deletion*, and
    undoing a note's creation produces exactly that. The index work it does is
    incremental — change detection compares mtime, size and checksum — so only
    the files undo changed are re-read and re-indexed. What it still pays is one
    walk of the project directory to find them, so the cost scales with the
    project's file count rather than with the size of the undo.
    """
    # Deferred: the indexing stack pulls SQLAlchemy and the repository layer,
    # which must not load at CLI import time — only when a command runs (#886).
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.index.local_project import (
        LocalProjectIndexRuntimeFactory,
        run_local_project_index_for_project,
    )
    from basic_memory.repository.project_repository import ProjectRepository
    from basic_memory.services.initialization import (
        ensure_project_registry,
        recover_project_materializations,
    )

    config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(config.database_path, config=config)
    await ensure_project_registry(config)
    async with db.scoped_session(session_maker) as session:
        projects = await ProjectRepository().get_active_projects(session)

    directories = {project.id: project_directory(project.path) for project in projects}
    targets = [
        project for project in projects if any(owns(directories[project.id], p) for p in paths)
    ]
    unowned = sum(
        1 for p in paths if not any(owns(directory, p) for directory in directories.values())
    )

    factory = LocalProjectIndexRuntimeFactory()
    for project in targets:
        # Trigger: a note whose accepted row was written but whose file never
        # was (a crash mid-materialization).
        # Why: the scan below reconciles missing files as deletes, so it would
        # destroy that entity and its accepted content — the loss `bm reindex`
        # documents at cli/commands/db.py.
        # Outcome: re-drive the stuck write to disk first, then scan.
        await recover_project_materializations(project, session_maker)
        await run_local_project_index_for_project(project, runtime_factory=factory)

    return len(targets), unowned


def project_directory(project_path: str) -> str | None:
    """A project's directory relative to the store, or None when it is outside it.

    A project whose files are not in the store's worktree has nothing in the
    history, so no restored path can belong to it (VERBS_PLAN D3).
    """
    prefix = project_store_prefix(project_path)
    return None if prefix is None else prefix.as_posix()


def owns(directory: str | None, store_relative: str) -> bool:
    """Whether one project directory contains one store-relative path."""
    if directory is None:
        return False
    # A project rooted at the store itself owns everything under it.
    return directory == "." or store_relative.startswith(f"{directory}/")


def undo_message(commits: Sequence[str], session: str | None) -> str:
    """The new commit's subject: byte-stable, naming what it reversed.

    No timestamp and no counter, for the reason W3 requires stable serialization
    everywhere else — otherwise every entry is a diff nobody can read past.
    """
    if session is not None:
        return f"undo session {session}"
    return f"undo {commits[0]}"


@app.command(name="undo")
def undo(
    session: Annotated[
        Optional[str],
        typer.Option("--session", help="Undo every commit this session id recorded."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Confirm undoing more than one commit."),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the notices and the next-step hints."),
    ] = False,
) -> None:
    """Put the note store back to the content it held before its last change.

    Restores every path the newest commit touched to the version its parent held
    — a file that commit created is removed — then records the restore as a
    **new** commit and reindexes what changed. It never resets: the history is
    the thing being protected, so undoing a change adds to it.

    `--session <id>` does the same for every commit carrying that session's
    trailer, newest first, so the store ends on the content it held before the
    session began.
    """
    try:
        targets = _target_commits(session)
    except HistoryError as exc:
        raise fail(f"Error: {exc}")

    # Contract rule 5: a well-scoped request whose answer is "nothing there" is a
    # result, not a failure.
    if not targets:
        typer.echo("nothing to undo")
        emit_notices(STORE_SCOPE, quiet=quiet, command="undo")
        if not quiet:
            typer.echo(UNDO_AFFORDANCE)
        return

    # Trigger: a path the restore would overwrite has uncommitted changes.
    # Why: `git checkout <parent> -- <path>` replaces the worktree silently, so
    #     that edit is gone with nothing in the history to recover it. W3-B is
    #     careful about exactly this elsewhere — `commit_paths` never stages what
    #     it did not write, and `dirty_others` reports an outside edit rather
    #     than sweeping it in. Undo must not reach past both.
    # Outcome: refuse before touching anything, and name the command that makes
    #     the edit recoverable. Checked ahead of the --yes gate because a
    #     confirmation cannot clear it: the run would be refused either way.
    try:
        at_risk = _uncommitted_in_target(targets)
    except HistoryError as exc:
        raise fail(f"Error: {exc}")

    if at_risk:
        raise fail(
            f"Error: undo would discard uncommitted changes in: {', '.join(at_risk)}. "
            "Record them first with 'bm history commit --all', then re-run."
        )

    # Rule 6: the refusal is the whole output — the paths are not printed,
    # because nothing may land on stdout on the error path.
    if len(targets) > 1 and not yes:
        raise fail(
            f"Error: this would undo {len(targets)} commits. "
            "Re-run with --yes to confirm, or 'bm history dirty' to look first."
        )

    try:
        restored = _restore(targets)
        result = commit_paths(
            sorted(restored),
            undo_message(targets, session),
            actor=UNDO_ACTOR,
            # No Session: trailer. An undo corrects a session's work rather than
            # joining it; stamping the current id would fold the undo into the
            # set that `bm undo --session <same id>` walks, and a second run
            # would then undo the undo.
            session_id=None,
        )
    except HistoryError as exc:
        raise fail(f"Error: {exc}")

    # Payload first, reindex second. The restore and its commit are already on
    # disk, so a caller must see what moved even if indexing then fails — the
    # partial shape output contract rule 6 names.
    paths = sorted(restored)
    for path in paths:
        typer.echo(f"{restored[path]}  {path}")
    typer.echo(f"{len(paths)} files restored")

    projects, unowned = run_with_cleanup(reindex_restored_paths(paths))

    if not quiet:
        for line in _undo_notices(result, projects, unowned):
            typer.echo(line)
    emit_notices(STORE_SCOPE, quiet=quiet, command="undo")
    if not quiet:
        typer.echo(UNDO_AFFORDANCE)


def _target_commits(session: str | None) -> tuple[str, ...]:
    """The commits this invocation undoes, newest first."""
    if session is not None:
        return commits_for_session(session)
    newest = latest_commit()
    return () if newest is None else (newest,)


def _uncommitted_in_target(targets: Sequence[str]) -> list[str]:
    """Paths the restore would overwrite that hold uncommitted changes.

    Untracked files count: a path a target commit added, then deleted and
    rewritten by hand, is untracked now and the restore would still discard it.
    """
    at_risk = {path for sha in targets for path in paths_in_commit(sha)}
    return sorted(path for _, path in dirty_paths() if path in at_risk)


def _restore(targets: Sequence[str]) -> dict[str, str]:
    """Restore every target's paths, newest commit first.

    Returns each path mapped to the commit whose parent content it now holds.
    Walking newest first means the oldest commit is restored last, so its version
    is the one that survives — which is what puts a whole session back to the
    state it started from.
    """
    restored: dict[str, str] = {}
    for sha in targets:
        for path in restore_from_commit(sha):
            restored[path] = sha
    return restored


def _undo_notices(result: CommitResult | None, projects: int, unowned: int) -> list[str]:
    """Notices for what the restore recorded and what it could not reindex."""
    lines: list[str] = []
    if result is None:
        # The files now hold the restored content, but they already did, so
        # there was no diff to commit. Saying so beats an unexplained missing sha.
        lines.append("note: the store already held that content — no commit was written")
    else:
        lines.append(f"note: recorded as {result.sha}")

    if unowned:
        subject = "1 restored path is" if unowned == 1 else f"{unowned} restored paths are"
        lines.append(
            f"note: {subject} in no registered project and could not be reindexed — "
            "run 'bm project list' to check"
        )
    elif projects == 0 and result is not None:
        lines.append("note: nothing was reindexed")
    return lines
