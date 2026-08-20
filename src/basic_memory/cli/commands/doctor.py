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
from basic_memory.cli.runner import run_with_cleanup
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
# `usage` is the third --only group (GAPS U35): machine-wide aggregation of the
# invocation log, informational only — it reports on the tool, not any corpus,
# so it never touches the exit code and never joins a default (all-groups) run.
USAGE = "usage"
GROUPS = (INTEGRITY, HYGIENE)
ONLY_GROUPS = (INTEGRITY, HYGIENE, USAGE)

NO_ISSUES = "  No issues"

# Introduces the one repair line the missing-file check prints (GAPS U10). The
# rows above it name what is broken; this names the single command that fixes
# all of them, which is what nothing pointed at before.
MISSING_FILE_REPAIR_PREFIX = "  repair: "

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


def render_usage() -> list[str]:
    """The usage section: per-command counts from the machine-wide cmdlog.

    Machine-wide by nature — the log is per-user state, not per-project — and
    the header says so, because every other doctor section is project-scoped.
    Never-run names come from the live Typer registry, so a new verb shows up
    here on the day it ships without a list to maintain.
    """
    from collections import Counter

    from basic_memory import cmdlog

    records = cmdlog.entries()
    lines = ["## usage — this machine, all projects (from the bm invocation log)"]
    if not records:
        lines.append("  No invocations logged yet — the log fills as bm runs.")
        return lines

    counts: Counter[str] = Counter(r.get("command", "(unknown)") for r in records)
    failures: Counter[str] = Counter(
        r.get("command", "(unknown)") for r in records if r.get("exit", 0) != 0
    )
    first_ts = next((r.get("ts") for r in records if r.get("ts")), "")
    last_ts = next((r.get("ts") for r in reversed(records) if r.get("ts")), "")
    lines.append(f"  window: {first_ts or '?'} .. {last_ts or '?'}  ({len(records)} invocations)")
    for command, count in counts.most_common():
        failed = f"  ({failures[command]} failed)" if failures[command] else ""
        lines.append(f"  {count:5d}  {command}{failed}")

    registered = {c.name for c in app.registered_commands if c.name} | {
        g.name for g in app.registered_groups if g.name
    }
    seen_heads = {name.split(" ")[0] for name in counts}
    never = sorted(registered - seen_heads)
    if never:
        lines.append(f"  never run: {', '.join(never)}")
    return lines


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
    for missing in integrity.missing_files:
        lines.append(f"  {missing.file_path}  missing-file  permalink={missing.permalink or '-'}")
    if integrity.missing_files:
        # The repair is stated once for the group, not once per row: it is the
        # same command whether one file is gone or a hundred, and repeating it on
        # every line would bury the paths that differ (GAPS U10).
        lines.append(f"{MISSING_FILE_REPAIR_PREFIX}bm reindex -p '{report.project_name}'")
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
    # Trigger: an inbox record that carries no `proposed-type`.
    # Why: "proposes no type" described the record correctly and asked for
    #     something no verb can produce. A proposal only ever arrives as a side
    #     effect of `bm new <undeclared-type>`; ask for `inbox` on purpose — which
    #     is what the type is documented for — and there is no way to attach one,
    #     then or later. The demand made the count unclosable for a corpus that
    #     used the escape hatch as intended (GAPS U5).
    # Why not drop the row: the W5-B notice counts every inbox record as unfiled
    #     and points the reader at this command, so a doctor that showed nothing
    #     would contradict the notice that sent them here.
    # Outcome: the same row, with a demand that can be met — including by
    #     deciding to leave it, which for an unclassifiable note is a real answer.
    for record in hygiene.inbox:
        state = (
            f"proposes '{record.detail}'"
            if record.detail
            else "unfiled — file it with 'bm new <type>' or leave it"
        )
        lines.append(f"  {record.file_path}  inbox  {state}")
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


def exit_code(reports: "list[ProjectDoctorReport]", groups: tuple[str, ...], strict: bool) -> int:
    """The verdict `bm doctor` returns, over the groups it was asked to print.

    Trigger: any integrity issue, or any issue at all under ``--strict``.
    Why: W2 made doctor the gate, and the migration procedure ends with it as an
        acceptance command. A gate that always exits 0 cannot gate anything —
        a hook, a `just` recipe or a CI step would have to parse the text, which
        this contract does not promise to keep stable (GAPS U19).
    Why hygiene does not count by default: hygiene rows are advisory. An unfiled
        inbox record is a legitimate resting state (GAPS U5), so exiting 1 on
        hygiene alone would make the count unclosable again. ``--strict`` is for
        the caller who wants both.
    Outcome: integrity issues → 1, hygiene-only issues → 0, ``--strict`` → 1 on
        either. An empty registry has no reports, so it is 0.

    ``groups`` is what was printed: under ``--only hygiene`` the integrity group
    was never queried, so it cannot contribute a verdict about a corpus nobody
    looked at.
    """
    integrity_issues = (
        sum(report.integrity.issue_count for report in reports) if INTEGRITY in groups else 0
    )
    hygiene_issues = (
        sum(report.hygiene.issue_count for report in reports) if HYGIENE in groups else 0
    )
    if integrity_issues or (strict and hygiene_issues):
        return 1
    return 0


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
            help="Print one group only: integrity, hygiene, or usage (machine-wide command stats).",
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
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Exit 1 on any issue, hygiene included, not just integrity.",
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

    Exits 1 when integrity found something, so a script can gate on it; hygiene
    alone exits 0 because those rows are advisory. `--strict` exits 1 on either.
    """
    if self_test:
        if only is not None:
            raise fail("Error: --self-test takes no --only group.")
        # Refused rather than ignored: the self-test's exit code already reports
        # whether this install can write a note and read it back, and a flag that
        # silently does nothing is worse than one that says so.
        if strict:
            raise fail("Error: --self-test takes no --strict; it already exits 1 on failure.")
        run_self_test()
        return

    if only is not None and only not in ONLY_GROUPS:
        # An unusable flag value cannot be scoped, so it is an addressing
        # failure rather than an empty result (contract rule 5).
        raise fail(f"Error: --only must be one of {', '.join(ONLY_GROUPS)}; got '{only}'.")

    if only == USAGE:
        # Machine-wide and informational: no project scope, no notices, no
        # verdict — printing it and exiting 0 is the whole contract (GAPS U35).
        if strict:
            raise fail("Error: --only usage is informational; --strict cannot gate on it.")
        typer.echo("\n".join(render_usage()))
        return

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
    # The call is here, and it prints nothing, on purpose. `doctor` is the one
    # command the notice suppresses — it has just printed every row the notice
    # would summarize — and stating that at the call site is what keeps the
    # guard in `tests/cli/test_notice_guard.py` honest about which verbs carry it.
    emit_notices(scope, quiet=quiet, command="doctor")
    if not quiet:
        typer.echo(f"\n{render_affordances()}")

    # The verdict is the last thing that happens: the whole report, its notices
    # and its hints are already on stdout, so exit 1 says "issues found" without
    # withholding the rows that name them (GAPS U19; contract rule 6's
    # partial-corpus clause has the same shape).
    verdict = exit_code(reports, groups, strict)
    if verdict:
        raise typer.Exit(verdict)
