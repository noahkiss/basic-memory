"""Repository for persisted vocabulary violations (GAPS W5 mechanism A).

Sync and the move planner check a record and write its violations here; ``bm
doctor`` and the per-command notice read them back. The rows are derived state,
so every write replaces an entity's whole set — a record that now checks clean
leaves none behind.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from sqlalchemy import delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.models import Entity, Violation
from basic_memory.repository.repository import Repository
from basic_memory.vocabulary import Violation as CheckedViolation


@dataclass(frozen=True, slots=True)
class ViolationReason:
    """How many records break one rule on one field, across the queried projects."""

    rule: str
    field: str
    count: int


@dataclass(frozen=True, slots=True)
class ViolationRow:
    """One violation with the file it belongs to, for a doctor report line."""

    file_path: str
    rule: str
    field: str
    message: str
    severity: str
    detected_at: datetime


class ViolationRepository(Repository[Violation]):
    """Violation rows, written per entity and read per project.

    ``project_id`` is optional: the reads take their scope explicitly because the
    notice rolls up across every project when the cwd is unmarked (GAPS W5-C).
    """

    def __init__(self, project_id: int | None = None):
        super().__init__(Violation, project_id=project_id)

    async def replace_for_entity(
        self,
        session: AsyncSession,
        entity_id: int,
        project_id: int,
        violations: Sequence[CheckedViolation],
        *,
        preserve_rules: Sequence[str] = (),
    ) -> int:
        """Replace this entity's violations with ``violations``, and return how many landed.

        Delete-then-insert rather than an upsert: the rows are the checker's whole
        answer for this record, so a rule that stopped firing has to disappear.
        An empty sequence is therefore the clean case, not a no-op — it clears.

        ``preserve_rules`` names rules this check could not decide, and their rows
        survive the delete. A caller that passes ``relation_types=None`` never
        emits ``supersedes-not-on-type``, so without this a move — which parses no
        relations — would erase a violation a real write recorded, and nothing
        would put it back until the note itself changed (GAPS W5 item 3).

        The caller's session is used as given. Opening a second one deadlocks the
        one-connection pool (GAPS W4).
        """
        cleared = delete(Violation).where(Violation.entity_id == entity_id)
        if preserve_rules:
            cleared = cleared.where(Violation.rule.not_in(tuple(preserve_rules)))
        await session.execute(cleared)
        if not violations:
            return 0

        # One timestamp for the batch: these rows were all decided by one check,
        # and doctor groups by it.
        detected_at = datetime.now().astimezone()
        await session.execute(
            insert(Violation),
            [
                {
                    "entity_id": entity_id,
                    "project_id": project_id,
                    "rule": violation.rule,
                    "field": violation.field,
                    "message": violation.message,
                    "severity": violation.severity,
                    "detected_at": detected_at,
                }
                for violation in violations
            ],
        )
        return len(violations)

    async def clear_for_project(self, session: AsyncSession, project_id: int) -> None:
        """Delete every violation row in one project.

        For the one state where no rule can apply to any record: the project's
        ``vocabulary.yml`` is gone, so every row is stale by definition and none
        of them is undecidable. One statement rather than a delete per entity —
        there is no per-record verdict left to compute (GAPS W5 item 4).

        The caller's session is used as given. Opening a second one deadlocks the
        one-connection pool (GAPS W4).
        """
        await session.execute(delete(Violation).where(Violation.project_id == project_id))

    async def count_for_projects(self, session: AsyncSession, project_ids: Sequence[int]) -> int:
        """Count violations across the given projects.

        Named apart from the base ``count`` because the scope is a list of
        projects rather than the repository's single one — the unscoped notice
        rolls up every project it can see.
        """
        if not project_ids:
            return 0

        result = await session.execute(
            select(func.count()).select_from(Violation).where(Violation.project_id.in_(project_ids))
        )
        return result.scalar_one()

    async def count_by_reason(
        self, session: AsyncSession, project_ids: Sequence[int]
    ) -> list[ViolationReason]:
        """Group violations by rule and field, commonest first.

        This is the notice's "top reason" (GAPS W5-B): a bare count only
        relocates the lookup into ``doctor``. Ties break on rule then field so
        the line does not change between runs over unchanged data.
        """
        if not project_ids:
            return []

        rows = await session.execute(
            select(Violation.rule, Violation.field, func.count().label("total"))
            .where(Violation.project_id.in_(project_ids))
            .group_by(Violation.rule, Violation.field)
            .order_by(func.count().desc(), Violation.rule, Violation.field)
        )
        return [
            ViolationReason(rule=rule, field=field, count=total)
            for rule, field, total in rows.all()
        ]

    async def list_for_project(
        self,
        session: AsyncSession,
        project_id: int,
        severity: str | None = None,
    ) -> list[ViolationRow]:
        """List one project's violations with each row's file path, for doctor.

        Ordered by file then rule so a corpus that did not change prints the same
        report twice.
        """
        query = (
            select(
                Entity.file_path,
                Violation.rule,
                Violation.field,
                Violation.message,
                Violation.severity,
                Violation.detected_at,
            )
            .join(Entity, Entity.id == Violation.entity_id)
            .where(Violation.project_id == project_id)
            .order_by(Entity.file_path, Violation.rule, Violation.field)
        )
        if severity is not None:
            query = query.where(Violation.severity == severity)

        rows = await session.execute(query)
        return [
            ViolationRow(
                file_path=file_path,
                rule=rule,
                field=field,
                message=message,
                severity=row_severity,
                detected_at=detected_at,
            )
            for file_path, rule, field, message, row_severity, detected_at in rows.all()
        ]
