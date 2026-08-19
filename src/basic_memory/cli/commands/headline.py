"""`bm headline` — set, clear, or show the project's one-line "what's next".

The headline is the line a statusline shows for a project, kept in
`store/<external_id>/headline.md` where scripts read it without paying for a
`bm` invocation (GAPS W9). It used to be derived — the most recent open task's
title, truncated to 30 chars — which produced mush and could not say anything a
task list did not already say. GAPS U24 reversed that: the headline is composed
by whoever knows what is next, and this verb is how.

Three shapes:

- `bm headline "<text>"` sets it. Over 30 chars is a hard error, never a
  truncation — a line nobody wrote must not reach the statusline.
- `bm headline ""` clears it. Absence is the honest "nothing is next"; the
  consumers fall back to their own default on a missing file.
- `bm headline` prints the current line, so an agent can check it before and
  after closing work. `bm brief` and the `bm done`/`bm mark` footers point
  here, which is what keeps the line fresh without any derivation.

Native fast path: project resolution through `project_marker`, the registry
through `cli.direct`, the event loop from `cli.runner`. Nothing here may reach
the MCP or API graphs (AGENTS.md, "Measured baseline"); the import guard runs
this verb in a subprocess to keep that true.
"""

from __future__ import annotations

from typing import Annotated, Optional

import typer

from basic_memory.cli.app import app
from basic_memory.cli.notices import emit_notices
from basic_memory.cli.runner import run_with_cleanup
from basic_memory.cli.scope import ReadScope


def _fail(message: str) -> typer.Exit:
    """One error line on stderr, exit 1, nothing on stdout (contract rule 6)."""
    typer.echo(message, err=True)
    return typer.Exit(1)


async def _resolve_external_id(project_name: str) -> str:
    """The store directory key for one project's headline file."""
    from basic_memory.cli.direct import direct_project_refs

    refs = await direct_project_refs(project_name)
    return refs[0].external_id


def _project_or_fail(project: Optional[str]) -> str:
    """Resolve the one project this verb works on: `--project`, marker, default.

    A write needs one home, so the write chain applies to every shape — the
    bare read included, because "which headline" has the same answer as "which
    project would I set".
    """
    from basic_memory.project_marker import resolve_cli_project

    name = resolve_cli_project(project)
    if not name:
        raise _fail(
            "Error: no project — pass --project, or run from a directory whose .bm.yml names one"
        )
    return name


@app.command(name="headline")
def headline(
    text: Annotated[
        Optional[str],
        typer.Argument(
            help='The line to set (max 30 chars). "" clears it; omit it to show the current one.'
        ),
    ] = None,
    project: Annotated[
        Optional[str],
        typer.Option("--project", "-p", help="Project to read or write. Defaults to .bm.yml."),
    ] = None,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the notices and the usage hint."),
    ] = False,
) -> None:
    """Set or show the project's headline — its one-line "what's next".

    The headline is what a statusline shows for this project. It is composed,
    never derived: closing a task does not move it, so update it whenever what
    is next changes. `bm headline "<text>"` sets it (max 30 chars),
    `bm headline ""` clears it, and bare `bm headline` prints it.
    """
    from basic_memory.services.headline import (
        MAX_HEADLINE_CHARS,
        HeadlineError,
        clear_headline,
        read_headline,
        set_headline,
    )
    from basic_memory.store.write_hook import record_headline_change

    project_name = _project_or_fail(project)

    try:
        external_id = run_with_cleanup(_resolve_external_id(project_name))
    except typer.Exit:
        raise
    except Exception as exc:
        raise _fail(f"Error: {exc}")

    # Bare: show the current line, then teach the shape. The hint is what makes
    # the 30-char limit known before it is hit rather than after (GAPS U24).
    if text is None:
        current = read_headline(external_id)
        typer.echo(f'headline: "{current}"' if current is not None else "headline: (none set)")
        if not quiet:
            typer.echo(
                f"the headline is this project's one-line \"what's next\", shown in the "
                f'statusline · bm headline "<text>" sets it (max {MAX_HEADLINE_CHARS} chars) · '
                f'bm headline "" clears it'
            )
        emit_notices(
            ReadScope(project=project_name, origin="write"), quiet=quiet, command="headline"
        )
        return

    # Clear: an empty argument is the stated way to say "nothing is next".
    if not text.strip():
        changed = clear_headline(external_id)
        typer.echo("headline cleared")
    else:
        try:
            changed = set_headline(external_id, text)
        except HeadlineError as exc:
            raise _fail(f"Error: {exc}")
        # The payload echoes the stripped line actually written, so what the
        # caller reads is what the statusline will show.
        typer.echo(f'headline: "{text.strip()}"')

    # An unchanged file is not committed — the no-op skip exists so mtime stays
    # a staleness signal, and an empty commit attempt would be refused anyway.
    # --quiet hides the notices, never the commit.
    if changed:
        for notice in record_headline_change(external_id).notices:
            if not quiet:
                typer.echo(notice)

    # After the payload, like every project-touching verb (GAPS W5-B). The
    # write chain resolved the project, so the scope is pinned the same way
    # record_write pins its own.
    emit_notices(ReadScope(project=project_name, origin="write"), quiet=quiet, command="headline")
