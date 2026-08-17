"""`bm doctor` — the one command that checks a corpus (GAPS W2, W5 item 5).

Two groups, because they need different readers:

- **integrity** — dangling relations, permalink invariants, and every violation
  the checker called an error. These have right answers.
- **hygiene** — expired reviews, guessed dates, stale state records, the inbox
  pile, and every advisory. These need a person.

There is deliberately no second checking command: a `bm gc` or a `bm check`
would immediately be the one nobody runs, so the gardener's jobs land here
(GAPS W2, decided 2026-08-05).

Scope follows GAPS W5-C: `--project` pins one project, a `.bm.yml` marker pins
the project it names, and an unmarked directory reports **every** project rather
than falling back to the configured default. Each section names its project, so
a roll-up reads the same as a single-project run.

The report is on the fast path and must stay there: nothing at module level may
pull the API, the MCP tool layer, fastapi, or dateparser
(`tests/cli/test_native_command_import_guard.py` enforces it). The `--self-test`
branch genuinely needs the in-process app, so it imports it inside itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Optional

from loguru import logger
import typer

from basic_memory.cli.app import app
from basic_memory.cli.commands.command_utils import run_with_cleanup
from basic_memory.cli.direct import STALE_STATE_DAYS, direct_doctor_report
from basic_memory.cli.notices import emit_notices
from basic_memory.cli.scope import resolve_read_scope
from basic_memory.project_marker import MarkerError

if TYPE_CHECKING:  # pragma: no cover
    from basic_memory.cli.direct import ProjectDoctorReport
    from basic_memory.mcp.clients import ProjectClient
    from basic_memory.repository.entity_repository import HygieneRecord
    from basic_memory.repository.violation_repository import ViolationRow

INTEGRITY = "integrity"
HYGIENE = "hygiene"
GROUPS = (INTEGRITY, HYGIENE)

NO_ISSUES = "  No issues"

# W19 item 5: a fixed list of next verbs, no conditions and no memory. `--quiet`
# is the only thing that suppresses it.
AFFORDANCES = (
    ("bm types", "see what this project allows"),
    ("bm doctor --only hygiene", "the checks that need a person"),
)


def fail(message: str) -> typer.Exit:
    """Write one error line to stderr and return the exit the caller raises.

    Output contract rule 6: errors are a single line on stderr, exit 1, and
    nothing lands on stdout on the error path.
    """
    typer.echo(message, err=True)
    return typer.Exit(1)


# --- Render ---


def count_line(total: int) -> str:
    """The count that closes a section (contract rule 3)."""
    return f"  {total} issue{'' if total == 1 else 's'}"


def _violation_lines(rows: "list[ViolationRow]") -> list[str]:
    """One line per violation row: file, rule, field, then the checker's message."""
    return [f"  {row.file_path}  {row.rule}  {row.field or '-'}  {row.message}" for row in rows]


def _hygiene_lines(check: str, records: "list[HygieneRecord]", detail_prefix: str) -> list[str]:
    """One line per hygiene record: file, check name, then what matched."""
    lines = []
    for record in records:
        # A record with no value to show prints the check name alone. Keeping the
        # prefix would print a bare label ("date") that reads as a value.
        detail = f"{detail_prefix}{record.detail}" if record.detail else ""
        lines.append(f"  {record.file_path}  {check}  {detail}".rstrip())
    return lines


def render_integrity(report: "ProjectDoctorReport") -> list[str]:
    """The integrity section for one project: heading, rows, count."""
    lines = [f"{INTEGRITY}  project '{report.project_name}'"]
    integrity = report.integrity

    for row in integrity.unresolved:
        touched = row.source_updated_at.date().isoformat()
        lines.append(
            f"  {row.file_path}  unresolved-relation  "
            f"-{row.relation_type}-> [[{row.to_name}]]  source touched {touched}"
        )
    for issue in integrity.permalink_issues:
        detail = f"permalink={issue.permalink}"
        if issue.issue == "drift":
            detail += f"  frontmatter={issue.frontmatter_permalink}"
        lines.append(f"  {issue.file_path}  permalink-{issue.issue}  {detail}")
    lines.extend(_violation_lines(integrity.errors))

    if integrity.issue_count == 0:
        lines.append(NO_ISSUES)
        return lines
    # Contract rule 3: the count closes the listing, on its own line.
    lines.append(count_line(integrity.issue_count))
    return lines


def render_hygiene(report: "ProjectDoctorReport") -> list[str]:
    """The hygiene section for one project: heading, rows, count."""
    lines = [f"{HYGIENE}  project '{report.project_name}'"]
    hygiene = report.hygiene

    lines.extend(_hygiene_lines("review-due", hygiene.review_due, "review-by "))
    lines.extend(_hygiene_lines("date-inferred", hygiene.inferred_dates, "date "))
    lines.extend(
        _hygiene_lines(
            "stale-state",
            hygiene.stale_states,
            # The threshold is stated on the row rather than in a footnote: a
            # reader who sees one line out of context still knows what "stale"
            # means here, and nothing declares the number (see STALE_STATE_DAYS).
            f"unchanged for over {STALE_STATE_DAYS} days, last changed ",
        )
    )
    for record in hygiene.inbox:
        proposed = f"proposes '{record.detail}'" if record.detail else "proposes no type"
        lines.append(f"  {record.file_path}  inbox  {proposed}")
    lines.extend(_violation_lines(hygiene.advisories))

    if hygiene.issue_count == 0:
        lines.append(NO_ISSUES)
        return lines
    lines.append(count_line(hygiene.issue_count))
    return lines


def render(reports: "list[ProjectDoctorReport]", groups: tuple[str, ...]) -> str:
    """Render every requested group for every project, in a fixed order."""
    sections: list[list[str]] = []
    for report in reports:
        if INTEGRITY in groups:
            sections.append(render_integrity(report))
        if HYGIENE in groups:
            sections.append(render_hygiene(report))
    if not sections:
        # An empty registry is a result, not a failure (contract rule 5).
        return "No projects to check."
    return "\n\n".join("\n".join(section) for section in sections)


def render_affordances() -> str:
    """The static next-verb list (GAPS W19 item 5)."""
    width = max(len(command) for command, _ in AFFORDANCES)
    lines = ["next:"]
    lines.extend(f"  {command:<{width + 2}}{purpose}" for command, purpose in AFFORDANCES)
    return "\n".join(lines)


# --- The self-test ---


async def run_doctor() -> None:
    """Run the file <-> database self-test in a throwaway project.

    Everything the self-test needs is imported here: it drives the in-process
    app on purpose, and that graph must not land on the report's path.
    """
    import tempfile
    import uuid

    from basic_memory.markdown.entity_parser import EntityParser
    from basic_memory.markdown.markdown_processor import MarkdownProcessor
    from basic_memory.markdown.schemas import EntityFrontmatter, EntityMarkdown
    from basic_memory.mcp.async_client import get_client
    from basic_memory.mcp.clients import KnowledgeClient, ProjectClient, SearchClient
    from basic_memory.schemas import ProjectIndexRunResponse
    from basic_memory.schemas.base import Entity
    from basic_memory.schemas.project_info import ProjectInfoRequest
    from basic_memory.schemas.search import SearchQuery

    typer.echo("Running Basic Memory doctor checks...")

    project_name = f"doctor-{uuid.uuid4().hex[:8]}"
    api_note_title = "Doctor API Note"
    manual_note_title = "Doctor Manual Note"
    manual_permalink = "doctor/manual-note"

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        async with get_client() as client:
            project_client = ProjectClient(client)
            project_request = ProjectInfoRequest(
                name=project_name,
                path=str(temp_path),
                set_default=False,
            )

            project_id: str | None = None

            try:
                status = await project_client.create_project(project_request.model_dump())
                if not status.new_project:
                    raise ValueError("Failed to create doctor project")
                project_id = status.new_project.external_id
                # Use the resolved path from the server — when project_root is configured,
                # the actual project directory differs from the requested temp_path
                project_path = Path(status.new_project.path)
                typer.echo(f"OK  Created doctor project: {project_name}")

                # --- DB -> File: create an entity via API ---
                knowledge_client = KnowledgeClient(client, project_id)
                api_note = Entity(
                    title=api_note_title,
                    directory="doctor",
                    note_type="note",
                    content_type="text/markdown",
                    content=f"# {api_note_title}\n\n- [note] API to file check",
                    entity_metadata={"tags": ["doctor"]},
                )
                api_result = await knowledge_client.create_entity(api_note.model_dump())

                api_file = project_path / api_result.file_path
                if not api_file.exists():
                    raise ValueError(f"API note file missing: {api_result.file_path}")

                api_text = api_file.read_text(encoding="utf-8")
                if api_note_title not in api_text:
                    raise ValueError("API note content missing from file")

                typer.echo("OK  API write created file")

                # --- File -> DB: write markdown file directly, then index ---
                parser = EntityParser(project_path)
                processor = MarkdownProcessor(parser)
                manual_markdown = EntityMarkdown(
                    frontmatter=EntityFrontmatter(
                        metadata={
                            "title": manual_note_title,
                            "type": "note",
                            "permalink": manual_permalink,
                            "tags": ["doctor"],
                        }
                    ),
                    content=f"# {manual_note_title}\n\n- [note] File to DB check",
                )

                manual_path = project_path / "doctor" / "manual-note.md"
                await processor.write_file(manual_path, manual_markdown)
                typer.echo("OK  Manual file written")

                index_data = await project_client.index(
                    project_id, force_full=False, run_in_background=False
                )
                project_index_run = ProjectIndexRunResponse.model_validate(index_data)
                if project_index_run.enqueued_files == 0:
                    raise ValueError("Project index did not enqueue any files")

                typer.echo("OK  Project index processed manual file")

                search_client = SearchClient(client, project_id)
                search_query = SearchQuery(title=manual_note_title)
                search_results = await search_client.search(
                    search_query.model_dump(), page=1, page_size=5
                )
                if not any(result.title == manual_note_title for result in search_results.results):
                    raise ValueError("Manual note not found in search index")

                typer.echo("OK  Search confirmed manual file")

                status_report = await project_client.get_status(project_id)
                observed_paths = {
                    observed_file.path for observed_file in status_report.observed_files
                }
                if "doctor/manual-note.md" not in observed_paths:
                    raise ValueError("Project index status did not observe manual note")

                typer.echo("OK  Status observed indexed file")

            finally:
                if project_id:
                    await _delete_doctor_project(project_client, project_name, project_id)

    typer.echo("Doctor checks passed.")


def _is_default_project_delete_error(error: Exception) -> bool:
    """Return True only for the API guard that blocks deleting the default project."""
    error_text = str(error)
    return "Cannot delete default project" in error_text


async def _delete_doctor_project_locally(project_name: str, project_id: str) -> None:
    """Remove the generated doctor project when the public API guard blocks cleanup."""
    from basic_memory import db
    from basic_memory.config import ConfigManager
    from basic_memory.repository import ProjectRepository

    config_manager = ConfigManager()
    repository = ProjectRepository()
    _, session_maker = await db.get_or_create_db(
        db_path=config_manager.config.database_path,
        db_type=db.DatabaseType.FILESYSTEM,
    )

    async with db.scoped_session(session_maker) as session:
        project = await repository.get_by_external_id(session, project_id)
        if project is None:
            raise ValueError(f"Doctor cleanup project '{project_id}' not found")
        if project.name != project_name:
            raise ValueError(
                f"Doctor cleanup expected project '{project_name}', found '{project.name}'"
            )
        await repository.delete(session, project.id)


async def _delete_doctor_project(
    project_client: ProjectClient, project_name: str, project_id: str
) -> None:
    """Delete the generated doctor project without weakening the public API guard."""
    # Deferred: ToolError lives in the mcp SDK, which must not load at CLI startup (#886).
    from mcp.server.fastmcp.exceptions import ToolError

    try:
        await project_client.delete_project(project_id)
    except ToolError as exc:
        if not _is_default_project_delete_error(exc):
            raise

        # Trigger: a registry that was empty before doctor ran promotes the
        # generated doctor project to default, because it is the first project.
        # Why: the project is disposable doctor-owned state, while the public API
        # must keep rejecting default-project deletion for normal callers.
        # Outcome: cleanup removes only the exact doctor project it created.
        await _delete_doctor_project_locally(project_name, project_id)


def run_self_test() -> None:
    """Run the self-test and turn its failures into the contract's error shape."""
    # Deferred: ToolError lives in the mcp SDK, which must not load at CLI startup (#886).
    from mcp.server.fastmcp.exceptions import ToolError

    try:
        run_with_cleanup(run_doctor())
    except (ToolError, ValueError) as e:
        # str() of a message-less exception (e.g. httpx.ReadTimeout) is empty;
        # fall back to repr so the failure line always names the error (#1027).
        raise fail(f"Doctor failed: {str(e) or repr(e)}")
    except Exception as e:
        error_detail = str(e) or repr(e)
        logger.error(f"Doctor failed: {error_detail}")
        raise fail(f"Doctor failed: {error_detail}")


# --- Verb ---


@app.command()
def doctor(
    project: Annotated[
        Optional[str],
        typer.Option(
            "--project",
            "-p",
            help="Check this project only. Defaults to .bm.yml, then every project.",
        ),
    ] = None,
    only: Annotated[
        Optional[str],
        typer.Option(
            "--only",
            help="Print one group only: integrity or hygiene.",
        ),
    ] = None,
    self_test: Annotated[
        bool,
        typer.Option(
            "--self-test",
            help="Check that this install can write a note and read it back, "
            "instead of checking your notes.",
        ),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", help="Hide the next-step hints."),
    ] = False,
) -> None:
    """Check your notes for broken links, id problems, and records that need a person.

    Integrity problems have right answers; hygiene problems need a decision. With
    no project named, every project is checked.
    """
    if self_test:
        if only is not None:
            raise fail("Error: --self-test takes no --only group.")
        run_self_test()
        return

    if only is not None and only not in GROUPS:
        # An unusable flag value cannot be scoped, so it is an addressing
        # failure rather than an empty result (contract rule 5).
        raise fail(f"Error: --only must be one of {', '.join(GROUPS)}; got '{only}'.")
    groups = GROUPS if only is None else (only,)

    try:
        scope = resolve_read_scope(project, Path.cwd())
    except MarkerError as exc:
        raise fail(f"Error: {exc}")
    project_names = None if scope.project is None else (scope.project,)

    try:
        reports = run_with_cleanup(
            direct_doctor_report(
                project_names,
                include_integrity=INTEGRITY in groups,
                include_hygiene=HYGIENE in groups,
            )
        )
    except typer.Exit:
        raise
    except ValueError as exc:
        raise fail(f"Error: {exc}")

    typer.echo(render(reports, groups))
    # Violations are corpus state, not command failure: doctor reports them and
    # exits 0, so a script over an imperfect corpus keeps working (GAPS W5-B).
    #
    # The call is here, and it prints nothing, on purpose. `doctor` is the one
    # command the notice suppresses — it has just printed every row the notice
    # would summarize — and stating that at the call site is what keeps the
    # guard in `tests/cli/test_notice_guard.py` honest about which verbs carry it.
    emit_notices(scope, quiet=quiet, command="doctor")
    if not quiet:
        typer.echo(f"\n{render_affordances()}")
