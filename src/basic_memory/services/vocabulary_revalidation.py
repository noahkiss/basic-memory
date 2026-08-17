"""Re-check a project's records when its vocabulary changes (GAPS W5 item 4).

Violations are persisted state, written when a record is indexed or moved. That
makes them stale in exactly one situation: the rules changed and the records did
not. Editing ``vocabulary.yml`` is "a deliberate human act" (`.forked/schema.md`
§3) — adding a type legalises every record that used it, and removing one makes
every record that used it wrong — and no record's own mtime moves, so nothing on
the index path would ever look again.

The trigger is a stamp on the project row: the sha256 of the vocabulary file's
bytes as of the last check. Hashing beats mtime because W3 commits the store on
every write, and a ``git checkout`` rewrites mtimes without changing content.

The warm path is a ``stat``, a hash of a few hundred bytes, and one string
compare, which is what lets the caller run this before every count query.
"""

from hashlib import sha256

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from basic_memory.models.knowledge import Entity
from basic_memory.models.project import Project
from basic_memory.repository.violation_repository import ViolationRepository
from basic_memory.services.vocabulary_enforcement import apply_vocabulary
from basic_memory.vocabulary import (
    HISTORY_DERIVED_RULES,
    RELATION_DERIVED_RULES,
    Vocabulary,
    load_vocabulary,
    vocabulary_path,
)

__all__ = ["revalidate_if_vocabulary_changed", "vocabulary_stamp"]

# Records re-checked per round trip. Revalidation reads whole frontmatter blocks,
# so the whole corpus must not land in memory at once; keyset paging on the
# primary key keeps the working set flat and the statement count low.
_REVALIDATION_CHUNK_SIZE = 500

# Neither input exists here: revalidation reads ``entity_metadata``, which is the
# frontmatter and nothing else — no parsed relations, and no previous write to
# compare against. The rules that read them are preserved from the write that
# recorded them rather than cleared by a check that could not have found them.
# They are preserved only while the project is governed: with no vocabulary there
# is no rule to be undecided about, and every row goes.
_UNDECIDABLE_RULES: tuple[str, ...] = tuple(sorted(RELATION_DERIVED_RULES | HISTORY_DERIVED_RULES))


def vocabulary_stamp(external_id: str) -> str:
    """Return the sha256 of a project's ``vocabulary.yml``, or ``""`` when absent.

    ``""`` is a real state, not a missing value: it says "checked, and this
    project is not governed". ``None`` on the column means nothing has checked it
    yet, which is why the two are kept apart.
    """
    path = vocabulary_path(external_id)
    if not path.is_file():
        return ""
    return sha256(path.read_bytes()).hexdigest()


async def _recheck_every_record(
    session: AsyncSession,
    repository: ViolationRepository,
    project: Project,
    vocabulary: Vocabulary,
) -> int:
    """Re-check one governed project's records against ``vocabulary``. Return how many.

    Pages by primary key so the whole corpus never lands in memory at once: each
    row carries a whole frontmatter block, and the working set has to stay flat
    however large the project is.
    """
    revalidated = 0
    last_id = 0
    while True:
        rows = (
            await session.execute(
                select(Entity.id, Entity.file_path, Entity.entity_metadata)
                .where(Entity.project_id == project.id, Entity.id > last_id)
                .order_by(Entity.id)
                .limit(_REVALIDATION_CHUNK_SIZE)
            )
        ).all()
        if not rows:
            return revalidated

        for entity_id, file_path, entity_metadata in rows:
            violations = apply_vocabulary(
                entity_metadata,
                vocabulary,
                mode="record",
                file_path=file_path,
                relation_types=None,
            )
            await repository.replace_for_entity(
                session,
                entity_id,
                project.id,
                violations,
                preserve_rules=_UNDECIDABLE_RULES,
            )
            revalidated += 1

        last_id = rows[-1][0]


async def revalidate_if_vocabulary_changed(session: AsyncSession, project: Project) -> int:
    """Re-check every record in ``project`` if its vocabulary changed. Return how many.

    Returns 0 when the stamp matches, which is the ordinary case and costs one
    hash and one compare.

    A malformed ``vocabulary.yml`` raises ``VocabularyError`` and leaves the stamp
    alone, so the next call raises again. Stamping a file that could not be parsed
    would turn a typo into permanent silence — the state W4 refuses to conflate
    with "not governed".

    Deleting the vocabulary clears every row in one statement and stamps ``""``.
    An ungoverned project has no rule any record can break, so there is no
    per-record verdict to compute — running the governed loop to insert nothing
    would be one delete per record to reach the same state.

    Uses the caller's session throughout, including the stamp write. Opening a
    second one deadlocks the one-connection pool (GAPS W4).
    """
    stamp = vocabulary_stamp(project.external_id)
    if stamp == project.vocabulary_stamp:
        return 0

    # Read a second time, as text, and only on the cold path: `load_vocabulary`
    # owns the parse and its error, and duplicating that here to save one read of
    # a few hundred bytes would give the file two parsers to keep in step.
    vocabulary = load_vocabulary(project.external_id)
    repository = ViolationRepository(project_id=project.id)

    if vocabulary is None:
        await repository.clear_for_project(session, project.id)
        # The return value counts records decided, and a bulk delete decides every
        # record in the project without reading one — so the count is asked for
        # rather than accumulated. One statement, on a path a human edit triggers.
        revalidated: int = (
            await session.execute(
                select(func.count()).select_from(Entity).where(Entity.project_id == project.id)
            )
        ).scalar_one()
    else:
        revalidated = await _recheck_every_record(session, repository, project, vocabulary)

    # Stamped last: an exception anywhere above must leave the project looking
    # unchecked, so the next call redoes the work rather than trusting rows that
    # were only partly rewritten.
    # A statement rather than an attribute write, because ``project`` may have
    # been loaded by another session and a detached assignment would never reach
    # the database. The in-memory object is brought into line afterwards so a
    # caller that keeps holding it does not revalidate the same change twice.
    await session.execute(
        update(Project).where(Project.id == project.id).values(vocabulary_stamp=stamp)
    )
    project.vocabulary_stamp = stamp
    return revalidated
