"""The per-command notice (GAPS W5 mechanism B).

Agents do not run `bm doctor` on their own initiative, and they do not have to:
every project-touching verb ends by stating what is outstanding, in the message
the caller was already reading. The model is `git push` reporting outstanding
advisories.

Four rules govern the line, all decided in GAPS W5-B and W8:

- **After the payload, on stdout** (output contract rule 4). No stderr split and
  no `notices` field — W20 settled that question for every verb at once.
- **Never changes an exit code.** Violations are corpus state, not command
  failure. A non-zero exit would break every script that runs a read verb over
  an imperfect corpus. This is also why a *failure to gather* the counts prints
  nothing rather than propagating: the payload already succeeded.
- **No throttle.** The same condition prints the same line every time, because
  the condition is still true. Rate-limiting means the one command an agent runs
  in a session may be the suppressed one.
- **`--quiet` drops it**, and `bm doctor` suppresses it — doctor is about to
  print all of it in full.

**The notice covers exactly what the verb read.** A verb that resolved a
`ReadScope` passes it; a verb whose scope is fixed by its own shape passes that
shape — `bm project list` always lists every project, so its notice is unscoped,
and `bm project ls --name X` is pinned to X. Nothing here re-derives a scope the
caller did not use, because a notice about projects the payload did not cover is
the cross-project leak W5-C exists to prevent.

**Order and cap** come from W8: at most two notices, highest priority first.
Two of W8's six rows are absent, and their absence is the design rather than an
omission — see `_ABSENT_CONDITIONS` below.

**Cost on the warm path.** One SQLite connection, one `stat` + `sha256` of each
in-scope `vocabulary.yml` (the revalidation trigger, which returns on a string
compare when nothing changed), three indexed counts, and — only when fewer than
two higher-priority conditions fired — one `git status --porcelain`.

Nothing here may pull the API or the MCP tool layer: the notice rides on the
fast verbs, so its imports are theirs
(`tests/cli/test_native_command_import_guard.py` enforces it).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import typer
from loguru import logger

if TYPE_CHECKING:  # pragma: no cover
    from basic_memory.cli.scope import ReadScope

# W8 caps the notice at two lines per command, highest priority first. More than
# that is a report, and a report is what `bm doctor` is for.
MAX_NOTICES = 2

# The one command that prints all of this itself, at length, one record per line.
SUPPRESSED_COMMANDS = frozenset({"doctor"})

# Two of W8's six conditions are deliberately not built:
#
# - "open items exist, nothing read yet this session" needs `bm` to know what a
#   session is and to remember what it already printed. W5-B's no-throttle rule
#   and W19 item 5's correction both rule that state out.
# - "sessions in this project never mined" needs `bm mine` to record what it
#   mined. `bm mine` reads transcripts and writes nothing, so there is no such
#   record to query (GAPS W1).
#
# W8's third row points at `bm ls --type inbox`, which does not exist. The inbox
# notice points at `bm doctor --only hygiene` instead, which lists the same pile:
# an affordance naming a verb that answers "no such command" teaches the surface
# wrongly (GAPS W19 item 5).
_ABSENT_CONDITIONS = ("nothing read yet this session", "unmined sessions")


@dataclass(frozen=True, slots=True)
class TopReason:
    """The commonest rule+field pair behind the violation count, and where it is."""

    rule: str
    field: str
    count: int
    project: str

    def describe(self, *, name_project: bool) -> str:
        """Render the parenthetical. The project is named only when unscoped."""
        where = f" on '{self.field}'" if self.field else ""
        whose = f" in '{self.project}'" if name_project else ""
        return f"{self.count} {self.rule}{where}{whose}"


@dataclass(frozen=True, slots=True)
class NoticeCounts:
    """Everything the notice needs, in W8's priority order."""

    violations: int = 0
    top_reason: TopReason | None = None
    review_due: int = 0
    inbox: int = 0
    dirty: int = 0
    # False when the scope covers every project, which is what decides whether
    # the top reason names its project (GAPS W5-C).
    pinned: bool = True


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def notice_lines(counts: NoticeCounts) -> list[str]:
    """Render at most `MAX_NOTICES` lines, in W8's documented order.

    Each line states a condition and names the command that answers it, which is
    what makes it actionable — a bare count only relocates the lookup.
    """
    lines: list[str] = []

    if counts.violations:
        reason = ""
        if counts.top_reason is not None:
            reason = f" ({counts.top_reason.describe(name_project=not counts.pinned)})"
        verb = _plural(counts.violations, "needs", "need")
        lines.append(
            f"{counts.violations} {_plural(counts.violations, 'record', 'records')} "
            f"{verb} attention{reason} — run 'bm doctor'"
        )

    if counts.review_due:
        lines.append(
            f"{counts.review_due} {_plural(counts.review_due, 'record', 'records')} "
            f"past review-by — run 'bm doctor --only hygiene'"
        )

    if counts.inbox:
        lines.append(
            f"{counts.inbox} unfiled {_plural(counts.inbox, 'record', 'records')} "
            f"in the inbox — run 'bm doctor --only hygiene'"
        )

    if counts.dirty:
        lines.append(
            f"{counts.dirty} note {_plural(counts.dirty, 'file has', 'files have')} "
            f"uncommitted changes — run 'bm history dirty'"
        )

    return lines[:MAX_NOTICES]


async def gather_notice_counts(scope: "ReadScope") -> NoticeCounts:
    """Count what is outstanding in ``scope``, revalidating the vocabulary first.

    The revalidation call is not optional and it is not an optimization: a
    vocabulary edit changes which records are in violation, and the notice is the
    surface that must never report a stale count. It costs one hash compare per
    in-scope project when nothing changed (GAPS W5 item 4).

    Raises ValueError for a project the registry does not hold, and
    ``VocabularyError`` for a malformed ``vocabulary.yml``. Both reach the caller
    — ``emit_notices`` is what decides that a notice never fails a command.
    """
    # Deferred: the service layer pulls SQLAlchemy, which must not load at CLI
    # import time — only when a command actually runs (#886).
    from datetime import date

    from basic_memory import db
    from basic_memory.cli.direct import direct_revalidate_vocabulary
    from basic_memory.config import ConfigManager
    from basic_memory.repository.entity_repository import (
        count_inbox_records,
        count_review_due_records,
    )
    from basic_memory.repository.project_repository import ProjectRepository
    from basic_memory.repository.violation_repository import ViolationRepository
    from basic_memory.store.history import dirty_count

    await direct_revalidate_vocabulary(scope.project)

    config = ConfigManager().config
    _, session_maker = await db.get_or_create_db(config.database_path, config=config)
    async with db.scoped_session(session_maker) as session:
        repository = ProjectRepository()
        if scope.project is None:
            projects = await repository.find_all(session)
        else:
            project = await repository.get_by_name(session, scope.project)
            if project is None:
                raise ValueError(f"Project not found: '{scope.project}'")
            projects = [project]

        names = {project.id: project.name for project in projects}
        project_ids = list(names)
        if not project_ids:
            return NoticeCounts(pinned=scope.is_pinned)

        violations = ViolationRepository()
        total = await violations.count_for_projects(session, project_ids)
        reasons = await violations.count_by_reason(session, project_ids) if total else []
        top_reason = (
            TopReason(
                rule=reasons[0].rule,
                field=reasons[0].field,
                count=reasons[0].count,
                project=names[reasons[0].project_id],
            )
            if reasons
            else None
        )

        review_due = await count_review_due_records(session, project_ids, date.today())
        inbox = await count_inbox_records(session, project_ids)

        # A pinned scope counts only its own store directory; unscoped counts the
        # whole store, which is the same set of projects it just counted rows for.
        prefix = projects[0].external_id if scope.is_pinned else None

    # Outside the session on purpose: this forks git, and holding the one pooled
    # connection open across a subprocess buys nothing.
    dirty = 0
    if sum(1 for count in (total, review_due, inbox) if count) < MAX_NOTICES:
        dirty = dirty_count(prefix)

    return NoticeCounts(
        violations=total,
        top_reason=top_reason,
        review_due=review_due,
        inbox=inbox,
        dirty=dirty,
        pinned=scope.is_pinned,
    )


def emit_notices(scope: "ReadScope", *, quiet: bool, command: str) -> None:
    """Print the notice after a verb's payload. Never fails, never exits non-zero.

    ``command`` is the verb's own name, so suppression is stated at the call site
    rather than inferred from the process arguments.
    """
    if quiet or command in SUPPRESSED_COMMANDS:
        return

    # Deferred: asyncio and the DB stack are the verb's cost, not the CLI's.
    import asyncio

    from basic_memory import db

    # Decision point: dispose only an engine this call opened. A verb that ran its
    # payload through `run_with_cleanup` already disposed of its own, so the
    # notice opens the next one and owes its cleanup. Disposing one we borrowed
    # would kill an in-memory database outright.
    owns_engine = not db.has_active_engine()

    async def run() -> NoticeCounts:
        try:
            return await gather_notice_counts(scope)
        finally:
            if owns_engine:
                await db.shutdown_db()

    # Trigger: any failure at all while gathering — unreadable vocabulary,
    # missing project, locked or un-migrated database.
    # Why: the payload has already been printed and the command has already
    # succeeded. A notice is an addition to a successful run, so its failure must
    # not become the run's failure (GAPS W5-B: never changes an exit code).
    # Outcome: log for the operator, print nothing, leave the exit code alone.
    #
    # WARNING, not DEBUG: the CLI configures loguru at INFO (`config_models.py`
    # log_level), so a debug line would go nowhere and a broken database would
    # turn every command into silent success with no trace anywhere. The log
    # file is the operator's only record that the notice stopped working.
    try:
        counts = asyncio.run(run())
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all, see above
        logger.warning(f"notices: suppressed {type(exc).__name__}: {exc}")
        return

    for line in notice_lines(counts):
        typer.echo(line)
