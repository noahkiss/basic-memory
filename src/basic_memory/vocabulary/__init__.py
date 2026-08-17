"""The closed record vocabulary and its checker (GAPS W4).

Humans extend a project's vocabulary; agents may only select from it. A project
is governed only when its ``vocabulary.yml`` exists — see ``model.py``.

Dependency-free by design: nothing here imports the DB, the API, or MCP, so the
fast CLI path can reach it.
"""

from basic_memory.vocabulary.checker import (
    HISTORY_DERIVED_RULES,
    RELATION_DERIVED_RULES,
    SET_ONCE_FIELDS,
    Severity,
    Violation,
    check_frontmatter,
    has_errors,
)
from basic_memory.vocabulary.model import (
    DEFAULT_VOCABULARY,
    TERMINAL_STATUSES,
    VOCABULARY_FILENAME,
    DeclaredField,
    FieldKind,
    Vocabulary,
    VocabularyError,
    default_review_by,
    load_vocabulary,
    parse_vocabulary,
    terminal_statuses,
    vocabulary_document,
    vocabulary_path,
    write_default_vocabulary,
)

__all__ = [
    "DEFAULT_VOCABULARY",
    "HISTORY_DERIVED_RULES",
    "RELATION_DERIVED_RULES",
    "SET_ONCE_FIELDS",
    "TERMINAL_STATUSES",
    "VOCABULARY_FILENAME",
    "DeclaredField",
    "FieldKind",
    "Severity",
    "Violation",
    "Vocabulary",
    "VocabularyError",
    "check_frontmatter",
    "default_review_by",
    "has_errors",
    "load_vocabulary",
    "parse_vocabulary",
    "terminal_statuses",
    "vocabulary_document",
    "vocabulary_path",
    "write_default_vocabulary",
]
