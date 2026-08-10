"""CLI tool commands for Basic Memory.

Every command calls its MCP tool with output_format="json" and renders the
result as plain, one-record-per-line text.  Each verb has exactly one
rendering: there is no --json, no --plain, no TTY detection, and no output
style config.  See docs/OUTPUT_CONTRACT.md (version 2) for the binding rules —
payload on stdout, notices and affordances after it, errors on stderr with
exit 1 and nothing on stdout.
"""

import json
import sys
from typing import Annotated, Any, Dict, List, NoReturn, Optional, Sequence, cast

import typer
from loguru import logger

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.project_marker import resolve_cli_project

# MCP tool functions are imported inside each command: importing
# basic_memory.mcp.tools loads the entire tool stack (fastmcp, mcp SDK,
# SQLAlchemy), which would slow every CLI invocation, including --help (#886).

tool_app = typer.Typer()
app.add_typer(tool_app, name="tool", help="Access to MCP tools via CLI")

VALID_EDIT_OPERATIONS = ["append", "prepend", "find_replace", "replace_section"]

# One record per line means a value may never carry a newline, and a long value
# may never push the columns that follow it off screen.
LINE_VALUE_LIMIT = 120

# Single-record field order per verb: identifier first (contract rule 2), then
# the fields an agent acts on.  Internal ids and checksums are omitted — they
# carry no meaning for a caller of the CLI.
WRITE_NOTE_FIELDS: tuple[tuple[str, str], ...] = (
    ("permalink", "permalink"),
    ("file_path", "file_path"),
    ("title", "title"),
    ("action", "action"),
)

EDIT_NOTE_FIELDS: tuple[tuple[str, str], ...] = (
    ("permalink", "permalink"),
    ("file_path", "file_path"),
    ("title", "title"),
    ("operation", "operation"),
    ("fileCreated", "file_created"),
)

DELETE_NOTE_FIELDS: tuple[tuple[str, str], ...] = (
    ("permalink", "permalink"),
    ("file_path", "file_path"),
    ("identifier", "identifier"),
    ("title", "title"),
    ("deleted", "deleted"),
)

DELETE_DIRECTORY_FIELDS: tuple[tuple[str, str], ...] = (
    ("identifier", "identifier"),
    ("is_directory", "is_directory"),
    ("deleted", "deleted"),
    ("total_files", "total_files"),
    ("successful_deletes", "successful_deletes"),
    ("failed_deletes", "failed_deletes"),
)


# --- Shared helpers ---


def _one_line(text: str, limit: int = LINE_VALUE_LIMIT) -> str:
    """Flatten a value onto a single line, truncated to ``limit`` characters."""
    flat = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."


def _field_value(value: Any) -> str:
    """Render a scalar field value plainly — no Python reprs (contract rule)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return _one_line(str(value))


def _print_record(result: dict[str, Any], fields: Sequence[tuple[str, str]]) -> None:
    """Print a single record as labelled lines in the given field order.

    Absent and null fields are skipped: a missing value is not a value, and an
    agent reading the output should not have to distinguish "" from null.
    """
    for key, label in fields:
        value = result.get(key)
        if value is None:
            continue
        print(f"{label}: {_field_value(value)}")


def _fail(message: str) -> NoReturn:
    """Report a failure on stderr and exit 1, leaving stdout untouched."""
    typer.echo(message, err=True)
    raise typer.Exit(1)


def _require_record(result: object) -> dict[str, Any]:
    """Narrow an MCP JSON payload to a record.

    Trigger: output_format="json" returned text instead of a record.
    Why: the text shape is the MCP tool's own error/guidance rendering, which
         carries no fields to lay out in columns.
    Outcome: the text becomes the CLI's error message, and stdout stays empty.
    """
    if not isinstance(result, dict):
        _fail(f"Error: {result}")
    return cast(dict[str, Any], result)


def _render_search_results(result: dict[str, Any], *, quiet: bool) -> None:
    """Render search results one per line: permalink, score, title, snippet."""
    results: list[dict[str, Any]] = list(result.get("results", []))

    for item in results:
        permalink = str(item.get("permalink") or "")
        score = item.get("score")
        # An absent score renders empty rather than as a sentinel value: the
        # column position still identifies it.
        score_str = f"{score:.2f}" if isinstance(score, (int, float)) else ""
        title = str(item.get("title") or "")
        # matched_chunk is the most relevant snippet; content is the fallback.
        raw_snippet = item.get("matched_chunk") or item.get("content") or ""
        snippet = _one_line(str(raw_snippet))
        print(f"{permalink}  {score_str}  {title}  {snippet}".rstrip())

    raw_total = result.get("total")
    # total is an int when the count is known and null/absent when it is not —
    # never a sentinel (docs/OUTPUT_CONTRACT.md).  An empty page is a knowable
    # zero, so it always gets a count line.
    if not results:
        print("0 results")
    elif isinstance(raw_total, int):
        print(f"{raw_total} results")

    if result.get("has_more") is True and not quiet:
        print("more results available")


def _render_read_note(result: dict[str, Any], *, include_frontmatter: bool) -> None:
    """Write the note content byte-exactly, or report the miss.

    Round-tripping is part of the contract, so the content is written with no
    decoration and no added or trimmed newline.  With --frontmatter the payload
    is the literal file; without it the API has already stripped the block and
    left the boundary newlines it created, which are not part of the body.
    """
    raw_content = result.get("content")
    content = raw_content if isinstance(raw_content, str) else ""
    if include_frontmatter:
        sys.stdout.write(content)
        return

    # Trigger: read_note returns an all-null payload when no note resolves.
    # Why: silence would make a miss indistinguishable from an empty note.
    # Outcome: report the miss and keep any related-note suggestions.
    if raw_content is None and not result.get("title") and not result.get("permalink"):
        print("Note not found.")
        raw_related = result.get("related_results")
        related = (
            [item for item in raw_related if isinstance(item, dict)]
            if isinstance(raw_related, list)
            else []
        )
        if not related:
            print("No note or related content found.")
            return
        print("Related results:")
        for item in related:
            permalink = str(item.get("permalink") or "")
            title = str(item.get("title") or "")
            print(f"{permalink}  {title}".rstrip())
        return

    body = content.strip("\n") if content else ""
    if body:
        print(body)


def _render_build_context(result: dict[str, Any]) -> None:
    """Render context as one line per primary record with indented children."""
    metadata = result.get("metadata", {})
    print(f"Context: {metadata.get('uri', '')}".rstrip())

    context_items: list[dict[str, Any]] = list(result.get("results", []))
    total_observations = 0
    total_related = 0

    for context_result in context_items:
        primary = context_result.get("primary_result", {})
        permalink = str(primary.get("permalink") or "")
        p_type = str(primary.get("type") or "")
        p_title = str(primary.get("title") or "")
        print(f"{permalink}  {p_type}  {p_title}".rstrip())

        # An entity summary can carry the note body independently of its
        # observations, so a prose-heavy note would otherwise render as a bare
        # identifier line with no content at all.
        raw_primary_content = primary.get("content")
        if isinstance(raw_primary_content, str) and raw_primary_content.strip():
            for line in raw_primary_content.strip().splitlines():
                print(f"  {line}")

        observations: list[dict[str, Any]] = list(context_result.get("observations", []))
        total_observations += len(observations)
        for obs in observations:
            content = _one_line(str(obs.get("content") or ""))
            print(f"  [{obs.get('category', '')}] {content}")

        related: list[dict[str, Any]] = list(context_result.get("related_results", []))
        total_related += len(related)
        for rel_item in related:
            relation = str(rel_item.get("relation_type") or "")
            rel_type = str(rel_item.get("type") or "")
            rel_title = str(rel_item.get("title") or rel_item.get("permalink") or "")
            print(f"  {relation} {rel_type} {rel_title}".rstrip())

    print(
        f"{len(context_items)} primary, {total_observations} observations, {total_related} related"
    )


def _render_recent_activity(result: list[dict[str, Any]]) -> None:
    """Render recent activity one item per line, project column when present."""
    # The project column only exists when the rows are cross-project; a constant
    # empty trailing column would be noise on the common single-project run.
    show_project = any(item.get("project") for item in result)

    for item in result:
        permalink = str(item.get("permalink") or "")
        item_type = str(item.get("type") or "")
        title = str(item.get("title") or "")
        updated = str(item.get("updated_at") or item.get("created_at") or "")
        line = f"{permalink}  {item_type}  {title}  {updated}"
        if show_project:
            line += f"  {item.get('project') or ''}"
        print(line.rstrip())

    print(f"{len(result)} results")


def _delete_note_failure_message(result: dict[str, Any]) -> str | None:
    """Return the CLI failure message for delete-note JSON results, if any."""
    error = result.get("error")
    if error:
        return str(error)

    failed_deletes = result.get("failed_deletes")
    # Trigger: directory deletion can partially fail without raising from the service.
    # Why: cleanup scripts need a non-zero exit when files remain undeleted.
    # Outcome: the CLI fails even if older MCP JSON did not include an error field.
    if (
        result.get("is_directory") is True
        and isinstance(failed_deletes, int)
        and failed_deletes > 0
    ):
        return f"Directory delete incomplete: {failed_deletes} file(s) failed"

    return None


# --- Commands ---


@tool_app.command()
def write_note(
    title: Annotated[str, typer.Option(help="The title of the note")],
    folder: Annotated[str, typer.Option(help="The folder to create the note in")],
    content: Annotated[
        Optional[str],
        typer.Option(
            help="The content of the note. If not provided, content will be read from stdin."
        ),
    ] = None,
    tags: Annotated[
        Optional[List[str]], typer.Option(help="A list of tags to apply to the note")
    ] = None,
    note_type: Annotated[
        str,
        typer.Option(
            "--type",
            help=(
                "Note type stored in frontmatter (e.g. 'guide', 'report'). "
                "A 'type:' in the note's own content frontmatter takes precedence."
            ),
        ),
    ] = "note",
    project: Annotated[
        Optional[str],
        typer.Option(
            help="The project to write to. If not provided, the default project will be used."
        ),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects.",
        ),
    ] = None,
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Replace an existing note on conflict (matches MCP write_note overwrite=True)",
    ),
):
    """Create or update a markdown note. Content can be provided via --content or stdin.

    Examples:

    bm tool write-note --title "My Note" --folder "notes" --content "Note content"
    bm tool write-note --title "My Guide" --folder "notes" --content "..." --type guide
    echo "content" | bm tool write-note --title "My Note" --folder "notes"
    bm tool write-note --title "My Note" --folder "notes" --overwrite
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import write_note as mcp_write_note

    try:
        # If content is not provided, read from stdin
        if content is None:
            if not sys.stdin.isatty():
                content = sys.stdin.read()
            else:  # pragma: no cover
                _fail(
                    "No content provided. Please provide content via --content "
                    "or by piping to stdin."
                )

        if content is not None and not content.strip():
            _fail("Empty content provided. Please provide non-empty content.")

        assert content is not None

        result = run_with_cleanup(
            mcp_write_note(
                title=title,
                content=content,
                directory=folder,
                project=resolve_cli_project(project),
                project_id=project_id,
                tags=tags,
                note_type=note_type,
                overwrite=overwrite,
                output_format="json",
            )
        )

        # MCP tool returns an error field on failure in JSON mode (e.g.
        # NOTE_ALREADY_EXISTS on a blocked overwrite, SECURITY_VALIDATION_ERROR).
        record = _require_record(result)
        if record.get("error"):
            _fail(f"Error: {record['error']}")

        _print_record(record, WRITE_NOTE_FIELDS)
    except ValueError as e:
        _fail(f"Error: {e}")
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            _fail(f"Error during write_note: {e}")
        raise


@tool_app.command()
def read_note(
    identifier: str,
    include_frontmatter: bool = typer.Option(
        False,
        "--frontmatter",
        "--include-frontmatter",
        help="Include YAML frontmatter in output (--include-frontmatter is a deprecated alias)",
    ),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects.",
        ),
    ] = None,
):
    """Read a markdown note from the knowledge base.

    The note content is written verbatim, so redirection round-trips the file.

    Examples:

    bm tool read-note my-note
    bm tool read-note my-note --frontmatter > note.md
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import read_note as mcp_read_note

    try:
        result = run_with_cleanup(
            mcp_read_note(
                identifier=identifier,
                project=resolve_cli_project(project),
                project_id=project_id,
                include_frontmatter=include_frontmatter,
                output_format="json",
            )
        )

        # A string result is already note-shaped text (the MCP fallback), so it
        # is the payload; only a structured error is a failure.
        if isinstance(result, str):
            sys.stdout.write(result)
            return

        # MCP tool returns an error field on failure in JSON mode (e.g.
        # SECURITY_VALIDATION_ERROR on a path-traversal identifier). A genuine
        # not-found returns null fields with no `error` key, so it still exits 0.
        if result.get("error"):
            _fail(f"Error: {result['error']}")

        _render_read_note(result, include_frontmatter=include_frontmatter)
    except ValueError as e:
        _fail(f"Error: {e}")
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            _fail(f"Error during read_note: {e}")
        raise


@tool_app.command("delete-note")
def delete_note(
    identifier: str,
    is_directory: bool = typer.Option(
        False, "--is-directory", help="Delete a directory instead of a single note"
    ),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects.",
        ),
    ] = None,
) -> None:
    """Delete a note or directory from the knowledge base.

    Examples:

    bm tool delete-note notes/old-draft
    bm tool delete-note docs/archive --is-directory
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import delete_note as mcp_delete_note

    try:
        result = run_with_cleanup(
            mcp_delete_note(
                identifier=identifier,
                is_directory=is_directory,
                project=resolve_cli_project(project),
                project_id=project_id,
                output_format="json",
            )
        )

        record = _require_record(result)
        failure_message = _delete_note_failure_message(record)
        if failure_message:
            _fail(f"Error: {failure_message}")

        fields = DELETE_DIRECTORY_FIELDS if record.get("is_directory") else DELETE_NOTE_FIELDS
        _print_record(record, fields)
    except ValueError as e:
        _fail(f"Error: {e}")
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            _fail(f"Error during delete_note: {e}")
        raise


@tool_app.command()
def edit_note(
    identifier: str,
    operation: Annotated[str, typer.Option("--operation", help="Edit operation to apply")],
    content: Annotated[str, typer.Option("--content", help="Content for the edit operation")],
    find_text: Annotated[
        Optional[str], typer.Option("--find-text", help="Text to find for find_replace operation")
    ] = None,
    section: Annotated[
        Optional[str],
        typer.Option("--section", help="Section heading for replace_section operation"),
    ] = None,
    expected_replacements: int = typer.Option(
        1,
        "--expected-replacements",
        help="Expected replacement count for find_replace operation",
    ),
    replace_subsections: bool = typer.Option(
        True,
        "--replace-subsections/--no-replace-subsections",
        help=(
            "For replace_section, replace nested subsections too; use "
            "--no-replace-subsections to preserve them"
        ),
    ),
    project: Annotated[
        Optional[str],
        typer.Option(
            help="The project to edit. If not provided, the default project will be used."
        ),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects.",
        ),
    ] = None,
):
    """Edit an existing markdown note using append/prepend/find_replace/replace_section.

    Examples:

    bm tool edit-note my-note --operation append --content "new content"
    bm tool edit-note my-note --operation find_replace --find-text "old" --content "new"
    bm tool edit-note my-note --operation replace_section --section "## Notes" --content "updated"
    bm tool edit-note my-note --operation replace_section --section "## Notes" --content "updated" --no-replace-subsections
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import edit_note as mcp_edit_note

    try:
        result = run_with_cleanup(
            mcp_edit_note(
                identifier=identifier,
                operation=operation,
                content=content,
                project=resolve_cli_project(project),
                project_id=project_id,
                section=section,
                find_text=find_text,
                expected_replacements=expected_replacements,
                replace_subsections=replace_subsections,
                output_format="json",
            )
        )

        # MCP tool returns error field on failure in JSON mode
        record = _require_record(result)
        if record.get("error"):
            _fail(f"Error: {record['error']}")

        _print_record(record, EDIT_NOTE_FIELDS)
    except ValueError as e:
        _fail(f"Error: {e}")
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            _fail(f"Error during edit_note: {e}")
        raise


@tool_app.command()
def build_context(
    url: str,
    depth: Optional[int] = typer.Option(1, "--depth", help="Depth of context to build"),
    timeframe: Optional[str] = typer.Option(
        "7d", "--timeframe", help="Timeframe filter (e.g., '7d', '1 week')"
    ),
    page: int = typer.Option(1, "--page", help="Page number for pagination"),
    page_size: int = typer.Option(10, "--page-size", help="Number of results per page"),
    max_related: int = typer.Option(10, "--max-related", help="Maximum related items to return"),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects.",
        ),
    ] = None,
):
    """Get context needed to continue a discussion.

    Examples:

    bm tool build-context memory://specs/search
    bm tool build-context specs/search --depth 2 --timeframe 30d
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import build_context as mcp_build_context

    try:
        result = run_with_cleanup(
            mcp_build_context(
                url=url,
                project=resolve_cli_project(project),
                project_id=project_id,
                depth=depth,
                timeframe=timeframe,
                page=page,
                page_size=page_size,
                max_related=max_related,
                output_format="json",
            )
        )

        # A string result carries an MCP error message, not a context graph.
        if isinstance(result, str):
            _fail(result)

        _render_build_context(result)
    except ValueError as e:
        _fail(f"Error: {e}")
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            _fail(f"Error during build_context: {e}")
        raise


@tool_app.command()
def recent_activity(
    type: Annotated[Optional[List[str]], typer.Option(help="Filter by item type")] = None,
    depth: Optional[int] = typer.Option(1, "--depth", help="Depth of context to build"),
    timeframe: Optional[str] = typer.Option(
        "7d", "--timeframe", help="Timeframe filter (e.g., '7d', '1 week')"
    ),
    page: int = typer.Option(1, "--page", help="Page number for pagination"),
    # Match the MCP recent_activity default (page_size=10) so identical default
    # invocations return the same number of rows from CLI and MCP.
    page_size: int = typer.Option(10, "--page-size", help="Number of results per page"),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects.",
        ),
    ] = None,
):
    """Get recent activity across the knowledge base.

    Examples:

    bm tool recent-activity
    bm tool recent-activity --timeframe 30d --page-size 20
    bm tool recent-activity --type entity --type observation
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import recent_activity as mcp_recent_activity

    try:
        result = run_with_cleanup(
            mcp_recent_activity(
                type=type or "",
                depth=depth if depth is not None else 1,
                timeframe=timeframe if timeframe is not None else "7d",
                page=page,
                page_size=page_size,
                project=resolve_cli_project(project),
                project_id=project_id,
                output_format="json",
            )
        )

        # A string result carries an MCP error message, not an activity list.
        if isinstance(result, str):
            _fail(result)

        _render_recent_activity(result)
    except ValueError as e:
        _fail(f"Error: {e}")
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            _fail(f"Error during recent_activity: {e}")
        raise


@tool_app.command("search-notes")
def search_notes(
    query: Annotated[
        Optional[str],
        typer.Argument(help="Search query string (optional when using metadata filters)"),
    ] = "",
    permalink: Annotated[bool, typer.Option("--permalink", help="Search permalink values")] = False,
    title: Annotated[bool, typer.Option("--title", help="Search title values")] = False,
    vector: Annotated[bool, typer.Option("--vector", help="Use vector retrieval")] = False,
    hybrid: Annotated[bool, typer.Option("--hybrid", help="Use hybrid retrieval")] = False,
    after_date: Annotated[
        Optional[str],
        typer.Option("--after_date", help="Search results after date, eg. '2d', '1 week'"),
    ] = None,
    tags: Annotated[
        Optional[List[str]],
        typer.Option("--tag", help="Filter by frontmatter tag (repeatable)"),
    ] = None,
    status: Annotated[
        Optional[str],
        typer.Option("--status", help="Filter by frontmatter status"),
    ] = None,
    note_types: Annotated[
        Optional[List[str]],
        typer.Option("--type", help="Filter by frontmatter type (repeatable)"),
    ] = None,
    entity_types: Annotated[
        Optional[List[str]],
        typer.Option(
            "--entity-type",
            help="Filter by search item type: entity, observation, relation (repeatable)",
        ),
    ] = None,
    categories: Annotated[
        Optional[List[str]],
        typer.Option(
            "--category",
            help=(
                "Filter observation results to exact categories (repeatable); "
                "pair with --entity-type observation"
            ),
        ),
    ] = None,
    meta: Annotated[
        Optional[List[str]],
        typer.Option("--meta", help="Filter by frontmatter key=value (repeatable)"),
    ] = None,
    filter_json: Annotated[
        Optional[str],
        typer.Option("--filter", help="JSON metadata filter (advanced)"),
    ] = None,
    page: int = typer.Option(1, "--page", help="Page number for pagination"),
    page_size: int = typer.Option(10, "--page-size", help="Number of results per page"),
    quiet: bool = typer.Option(False, "--quiet", help="Drop notices, leaving the results alone"),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects.",
        ),
    ] = None,
):
    """Search across all content in the knowledge base.

    Examples:

    bm tool search-notes "my query"
    bm tool search-notes --permalink "specs/*"
    bm tool search-notes --tag python --tag async
    bm tool search-notes --meta status=draft
    bm tool search-notes "auth" --entity-type observation --category requirement
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import search_notes as mcp_search

    try:
        mode_flags = [permalink, title, vector, hybrid]
        if sum(1 for enabled in mode_flags if enabled) > 1:
            _fail("Use only one mode flag: --permalink, --title, --vector, or --hybrid. Exiting.")

        # --- Build metadata filters from --filter and --meta ---
        metadata_filters: Dict[str, Any] | None = {}
        if filter_json:
            try:
                metadata_filters = json.loads(filter_json)
                if not isinstance(metadata_filters, dict):
                    raise ValueError("Metadata filter JSON must be an object")
            except json.JSONDecodeError as e:
                _fail(f"Invalid JSON for --filter: {e}")

        if meta:
            for item in meta:
                if "=" not in item:
                    _fail(f"Invalid --meta entry '{item}'. Use key=value format.")
                key, value = item.split("=", 1)
                key = key.strip()
                if not key:
                    _fail(f"Invalid --meta entry '{item}'.")
                metadata_filters[key] = value

        if not metadata_filters:
            metadata_filters = None

        # --- Determine search type from mode flags ---
        search_type: str | None = None
        if permalink:
            search_type = "permalink"
        if title:
            search_type = "title"
        if vector:
            search_type = "vector"
        if hybrid:
            search_type = "hybrid"

        result = run_with_cleanup(
            mcp_search(
                query=query or None,
                project=resolve_cli_project(project),
                project_id=project_id,
                search_type=search_type,
                output_format="json",
                page=page,
                after_date=after_date,
                page_size=page_size,
                note_types=note_types,
                entity_types=entity_types,
                categories=categories,
                metadata_filters=metadata_filters,
                tags=tags,
                status=status,
            )
        )

        # MCP tool may return a string error message
        if isinstance(result, str):
            _fail(result)

        _render_search_results(result, quiet=quiet)
    except ValueError as e:
        _fail(f"Error: {e}")
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            logger.exception("Error during search", e)
            _fail(f"Error during search: {e}")
        raise


# --- list-projects ---


@tool_app.command("list-projects")
def list_projects():
    """List all available projects with their paths.

    Examples:

    bm tool list-projects
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import list_memory_projects as mcp_list_projects

    try:
        result = _require_record(run_with_cleanup(mcp_list_projects(output_format="json")))
        projects: list[dict[str, Any]] = list(result.get("projects", []))
        for item in projects:
            # The default marker is a column, not a prefix, so the identifier
            # stays first on every line.
            marker = "  (default)" if item.get("is_default") else ""
            print(f"{item.get('name', '')}  {item.get('path', '')}{marker}")
        print(f"{len(projects)} projects")
    except ValueError as e:
        _fail(f"Error: {e}")
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            _fail(f"Error during list_projects: {e}")
        raise


# --- schema-validate ---


@tool_app.command("schema-validate")
def schema_validate(
    target: Annotated[
        Optional[str],
        typer.Argument(help="Note path or note type to validate"),
    ] = None,
    strict: bool = typer.Option(False, "--strict", help="Exit 1 when any note fails validation"),
    quiet: bool = typer.Option(False, "--quiet", help="Drop notices, leaving the report alone"),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects.",
        ),
    ] = None,
):
    """Validate notes against their schemas.

    TARGET can be a note path (e.g., people/ada-lovelace.md) or a note type
    (e.g., person). If omitted, validates all notes that have schemas.

    Examples:

    bm tool schema-validate person
    bm tool schema-validate people/ada-lovelace.md
    bm tool schema-validate person --strict
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import schema_validate as mcp_schema_validate

    try:
        # Heuristic: if target contains / or ., treat as identifier; otherwise as note type
        note_type, identifier = None, None
        if target:
            if "/" in target or "." in target:
                identifier = target
            else:
                note_type = target

        result = run_with_cleanup(
            mcp_schema_validate(
                note_type=note_type,
                identifier=identifier,
                project=resolve_cli_project(project),
                project_id=project_id,
                output_format="json",
            )
        )
        report = _require_record(result)
        if report.get("error"):
            _fail(f"Error: {report['error']}")

        # The report renderer is shared with `bm schema validate`; importing it
        # lazily keeps this module's import cost unchanged.
        from basic_memory.cli.commands.schema import render_validate_report

        render_validate_report(report, quiet=quiet)

        # Trigger: --strict with at least one failing note.
        # Why: gating scripts need an exit code, but the report is still the
        #      answer to the question asked, so it is rendered first.
        error_count = report.get("error_count", 0)
        # Same strict message as `bm schema validate` — one contract, two entry points.
        if strict and isinstance(error_count, int) and error_count > 0:
            _fail(f"strict: {error_count} errors")
    except ValueError as e:
        _fail(f"Error: {e}")
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            _fail(f"Error during schema_validate: {e}")
        raise


# --- schema-infer ---


@tool_app.command("schema-infer")
def schema_infer(
    note_type: Annotated[
        str,
        typer.Argument(help="Note type to analyze (e.g., person, meeting)"),
    ],
    threshold: float = typer.Option(
        0.25, "--threshold", help="Minimum frequency for optional fields (0-1)"
    ),
    quiet: bool = typer.Option(False, "--quiet", help="Drop notices, leaving the report alone"),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects.",
        ),
    ] = None,
):
    """Infer schema from existing notes of a type.

    Examples:

    bm tool schema-infer person
    bm tool schema-infer meeting --threshold 0.5
    bm tool schema-infer person --project research
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import schema_infer as mcp_schema_infer

    try:
        result = run_with_cleanup(
            mcp_schema_infer(
                note_type=note_type,
                threshold=threshold,
                project=resolve_cli_project(project),
                project_id=project_id,
                output_format="json",
            )
        )
        report = _require_record(result)
        if report.get("error"):
            _fail(f"Error: {report['error']}")

        # The report renderer is shared with `bm schema infer`; importing it
        # lazily keeps this module's import cost unchanged.
        from basic_memory.cli.commands.schema import render_infer_report

        render_infer_report(report, quiet=quiet)
    except ValueError as e:
        _fail(f"Error: {e}")
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            _fail(f"Error during schema_infer: {e}")
        raise


# --- schema-diff ---


@tool_app.command("schema-diff")
def schema_diff(
    note_type: Annotated[
        str,
        typer.Argument(help="Note type to check for drift"),
    ],
    quiet: bool = typer.Option(False, "--quiet", help="Drop notices, leaving the report alone"),
    project: Annotated[
        Optional[str],
        typer.Option(help="The project to use. If not provided, the default project will be used."),
    ] = None,
    project_id: Annotated[
        Optional[str],
        typer.Option(
            "--project-id",
            help="Project external_id (UUID). Takes precedence over --project; use to disambiguate same-named projects.",
        ),
    ] = None,
):
    """Show drift between schema and actual usage.

    Examples:

    bm tool schema-diff person
    bm tool schema-diff person --project research
    """
    # Deferred: loading the MCP tool stack at module import slows CLI startup (#886).
    from basic_memory.mcp.tools import schema_diff as mcp_schema_diff

    try:
        result = run_with_cleanup(
            mcp_schema_diff(
                note_type=note_type,
                project=resolve_cli_project(project),
                project_id=project_id,
                output_format="json",
            )
        )
        report = _require_record(result)
        if report.get("error"):
            _fail(f"Error: {report['error']}")

        # The report renderer is shared with `bm schema diff`; importing it
        # lazily keeps this module's import cost unchanged.
        from basic_memory.cli.commands.schema import render_diff_report

        render_diff_report(report, quiet=quiet)
    except ValueError as e:
        _fail(f"Error: {e}")
    except Exception as e:  # pragma: no cover
        if not isinstance(e, typer.Exit):
            _fail(f"Error during schema_diff: {e}")
        raise
