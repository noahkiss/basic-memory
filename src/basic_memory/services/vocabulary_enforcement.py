"""The single vocabulary enforcement funnel (GAPS W4, relocated by GAPS T22).

Every note write passes through this module and nothing else validates
frontmatter. A write path that skips the funnel is a bug, not a policy choice:
hooking only the paths someone remembered is how the predecessor tool ended up
rejecting a type in its CLI while its API wrote the same type to disk
(``.forked/decisions.md`` R5).

**Where the funnel lives, and why it moved.** W4 put it on ``EntityService``,
which was the agent write path when the sentence was written. It is not now:
every agent-facing write reaches the database through the accepted-note mutation
runner, so reject mode never fired (GAPS T22). Enforcement moved here — a
runtime-neutral function every layer calls — rather than being duplicated into
each, because two funnels are two things to keep in step.

Three callers now. Reject: ``indexing/accepted_note_mutation_runner.py``.
Record: ``EntityService``, the sync path, and ``index/local_moves.py``, the move
planner — a hand-move is a human act, and its permalink rewrite is invisible to
any later index pass, so it is judged where it is planned (GAPS T23).

The function is synchronous and takes no session. A project's vocabulary is a
file keyed by ``external_id``, so the check needs no database work at all once
the caller has that id in hand. That also removes the deadlock W4 recorded: the
funnel can no longer open a second connection because it opens none.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from loguru import logger

from basic_memory.markdown.entity_parser import normalize_frontmatter_metadata
from basic_memory.services.exceptions import VocabularyViolationError
from basic_memory.vocabulary.checker import Violation, check_frontmatter, has_errors
from basic_memory.vocabulary.model import Vocabulary, load_vocabulary

# What the *caller* declares a violation means. Static per write path: the
# write's origin decides, never the content it carries (GAPS W4).
#   reject — verbs, MCP, the API. The write does not happen.
#   record — the sync/watcher path, reading a file a human may have hand-edited.
#            It always indexes: a file refused an index is invisible to search
#            and to `bm doctor` alike.
type VocabularyEnforcementMode = Literal["reject", "record"]


def enforce_vocabulary(
    metadata: Mapping[str, Any] | None,
    *,
    project_external_id: str,
    mode: VocabularyEnforcementMode,
    file_path: str,
    previous: Mapping[str, Any] | None = None,
    relation_types: Sequence[str] | None = None,
) -> list[Violation]:
    """Check one write's frontmatter against its project's vocabulary.

    ``previous`` is the record's frontmatter before this write, and ``None``
    means a creation; it feeds the set-once rule and nothing else. Both sides are
    normalized first so a native YAML date on the incoming side and the ISO
    string already stored on the previous side compare equal.

    ``relation_types`` carries the record's outgoing relation types, because one
    rule — supersession — lives in ``## Relations`` rather than in frontmatter.
    ``None`` means the caller has not parsed them and that rule is skipped.

    Raises ``VocabularyViolationError`` in reject mode when the write breaks a
    rule that blocks. Advisories never block.
    """
    return apply_vocabulary(
        metadata,
        load_vocabulary(project_external_id),
        mode=mode,
        file_path=file_path,
        previous=previous,
        relation_types=relation_types,
    )


def apply_vocabulary(
    metadata: Mapping[str, Any] | None,
    vocabulary: Vocabulary | None,
    *,
    mode: VocabularyEnforcementMode,
    file_path: str,
    previous: Mapping[str, Any] | None = None,
    relation_types: Sequence[str] | None = None,
) -> list[Violation]:
    """Apply an already-loaded vocabulary to one write.

    Split from ``enforce_vocabulary`` for the one caller that holds the
    vocabulary already: the sync path resolves it once per ``EntityService``
    instance and reuses it across a whole reindex, so it must not re-read the
    file per note. Both entry points run the same rules on the same data.
    """
    # No vocabulary.yml means the project is not governed, so no rule applies and
    # the checker must not run — an absent file is not the defaults.
    if vocabulary is None:
        return []

    violations = check_frontmatter(
        normalize_frontmatter_metadata(dict(metadata or {})),
        vocabulary,
        previous=None if previous is None else normalize_frontmatter_metadata(dict(previous)),
        relation_types=relation_types,
    )

    if mode == "reject" and has_errors(violations):
        # Raised before the caller accepts anything: a rejection that leaves a
        # written file or a committed row behind is worse than no rejection.
        raise VocabularyViolationError(file_path, violations)

    # Logged, not persisted. GAPS W5 mechanism A writes these rows to a table
    # keyed by entity so `bm doctor` can query them; that table, its Alembic
    # migration, and the revalidation trigger are W5's scope and are deliberately
    # not built here.
    for violation in violations:
        message = f"Vocabulary violation in {file_path}: {violation.message}"
        if violation.severity == "error":
            logger.warning(message, rule=violation.rule, field=violation.field)
        else:
            logger.debug(message, rule=violation.rule, field=violation.field)

    return violations
