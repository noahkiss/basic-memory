from basic_memory.vocabulary.checker import Violation


class FileOperationError(Exception):
    """Raised when file operations fail"""

    pass


class EntityNotFoundError(Exception):
    """Raised when an entity cannot be found"""

    pass


class AmbiguousIdentifierError(Exception):
    """Raised when a non-exact identifier matches multiple entities under strict resolution.

    Strict resolution backs destructive operations (edit, move). It must never silently pick
    one of several same-title notes (e.g. an original plus a ``-1`` duplicate), so the caller is
    told to disambiguate with an exact permalink or external_id. Non-strict resolution (wiki
    links, reads) keeps its shortest-path preference and does not raise. See issue #1148.
    """

    def __init__(self, identifier: str, candidates: list[tuple[str | None, str]]) -> None:
        self.identifier = identifier
        self.candidates = candidates
        listing = "; ".join(
            f"{file_path} (permalink: {permalink})" if permalink else f"{file_path} (no permalink)"
            for permalink, file_path in candidates
        )
        super().__init__(
            f"Ambiguous identifier '{identifier}' matches {len(candidates)} notes: {listing}. "
            "Pass an exact permalink or external_id to disambiguate."
        )


class EntityCreationError(Exception):
    """Raised when an entity cannot be created"""

    pass


class EntityAlreadyExistsError(EntityCreationError):
    """Raised when an entity file already exists"""

    pass


class DirectoryOperationError(Exception):
    """Raised when directory operations fail"""

    pass


class VocabularyViolationError(ValueError):
    """Raised when a write is refused because its frontmatter is off vocabulary.

    Only the *reject* mode of ``EntityService._enforce_vocabulary`` raises this —
    the verb, MCP, and API write paths. The sync path records violations and
    indexes anyway, because a file refused an index is invisible to search and
    to ``bm doctor`` alike (GAPS W4).

    A ``ValueError`` because that is what the CLI and API boundaries already
    render as a user-facing rejection rather than a 500.
    """

    def __init__(self, file_path: str, violations: list[Violation]) -> None:
        self.file_path = file_path
        # Kept whole so a caller can render them per-field instead of as prose.
        self.violations = violations
        # Advisories never block a write, so listing one here would name a
        # reason that is not in fact the reason (GAPS W4).
        blocking = [violation for violation in violations if violation.severity == "error"]
        detail = "\n".join(f"  - {violation.message}" for violation in blocking)
        super().__init__(f"{file_path} is off this project's vocabulary:\n{detail}")


class SyncFatalError(Exception):
    """Raised when sync encounters a fatal error that prevents continuation.

    Fatal errors include:
    - Project deleted during sync (FOREIGN KEY constraint)
    - Database corruption
    - Critical system failures

    When this exception is raised, the entire sync operation should be terminated
    immediately rather than attempting to continue with remaining files.
    """

    pass
