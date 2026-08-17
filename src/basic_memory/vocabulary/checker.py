"""Check a record's frontmatter against its project's vocabulary (GAPS W4).

Pure functions over an already-parsed frontmatter mapping — no I/O, no DB. The
caller decides what a violation *means*: the verb, MCP, and API paths reject;
the sync path records the violation and indexes anyway, because a file that is
refused an index is invisible to search and to ``bm doctor`` alike.

Field names, requiredness, and the set-once list are fixed by
``.forked/schema.md`` §2 and §4. Only the type, status, area, and declared-field
*values* come from the project's vocabulary.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from basic_memory.vocabulary.glossary import type_choice
from basic_memory.vocabulary.model import Vocabulary

type Severity = Literal["error", "advisory"]


@dataclass(frozen=True, slots=True)
class Violation:
    """One rule broken by one record.

    Shaped for later persistence keyed by entity: GAPS W5 mechanism A writes
    these rows to a table for ``bm doctor`` to report.
    """

    rule: str
    # The frontmatter key at fault; "" when the record as a whole is at fault.
    field: str
    message: str
    severity: Severity


# --- The schema's fixed vocabulary (not the project's) ---

# At most one date per record, and its name is fixed by the type (schema.md §2).
# A human-added type is absent here and gets the common rules only.
_TYPE_DATE_FIELD: Mapping[str, str] = {
    "task": "opened",
    "finding": "event-date",
    "profile": "since",
}

_TYPE_ONLY_FIELDS: Mapping[str, frozenset[str]] = {
    "status": frozenset({"task"}),
    "not-before": frozenset({"task"}),
    "review-by": frozenset({"finding", "guide"}),
}

_REQUIRED_COMMON: tuple[str, ...] = ("id", "permalink", "title", "source")

# Supersession belongs to one type (schema.md §5/§12), and it is a `## Relations`
# line rather than a frontmatter key — so this rule reads the record's relation
# types, which only a caller that parsed the body has.
_SUPERSEDES_RELATION = "supersedes"
_SUPERSEDES_TYPES: frozenset[str] = frozenset({"finding"})

# Rules a caller cannot decide from this write's frontmatter alone. Public
# because the store needs them: a check that was never given the input a rule
# reads can never emit it, so a writer that replaced a record's whole row set
# would erase what a better-informed caller had recorded (GAPS W5 item 3).
#
#   relations — only a caller that parsed the body has them
#   history   — only a caller that read the previous write has it
RELATION_DERIVED_RULES: frozenset[str] = frozenset({"supersedes-not-on-type"})
HISTORY_DERIVED_RULES: frozenset[str] = frozenset({"set-once-changed"})

# Required beyond the common four, by type.
_REQUIRED_BY_TYPE: Mapping[str, tuple[str, ...]] = {
    "finding": ("event-date", "review-by"),
    "guide": ("review-by",),
}

_PROVENANCE: tuple[str, ...] = ("date-source", "date-confidence", "date-ref")
_DATE_SOURCES: tuple[str, ...] = ("inline", "transcript", "git", "mtime", "inferred")
_DATE_CONFIDENCES: tuple[str, ...] = ("exact", "day", "month", "unknown")
# The two rungs that point at re-openable evidence, and so must carry a ref.
_REF_BEARING_SOURCES: frozenset[str] = frozenset({"transcript", "git"})

_SCHEMA_DATE_FIELDS: tuple[str, ...] = ("opened", "event-date", "since", "review-by", "not-before")

# Set at creation, never revisited (schema.md §4).
_SET_ONCE: tuple[str, ...] = (
    "id",
    "permalink",
    "type",
    "source",
    "opened",
    "event-date",
    "since",
    "date-source",
    "date-confidence",
    "date-ref",
    "review-by",
    "not-before",
    "area",
)

# Every key any rule already judges. A schema key on the wrong type is reported
# once, as `field-not-on-type` — never also as an unknown key.
_SCHEMA_KEYS: frozenset[str] = frozenset(
    (*_REQUIRED_COMMON, "type", "area", "status", *_SCHEMA_DATE_FIELDS, *_PROVENANCE)
)

# Basic Memory writes these itself. Flagging them would put a permanent advisory
# on every governed record.
_HOUSEKEEPING_KEYS: frozenset[str] = frozenset({"created", "modified", "tags"})

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def has_errors(violations: Iterable[Violation]) -> bool:
    """True when any violation blocks a write. Advisories never do."""
    return any(violation.severity == "error" for violation in violations)


def check_frontmatter(
    metadata: Mapping[str, Any],
    vocabulary: Vocabulary,
    *,
    previous: Mapping[str, Any] | None = None,
    relation_types: Sequence[str] | None = None,
) -> list[Violation]:
    """Return every violation in ``metadata``, in a fixed order.

    ``previous`` is the record's frontmatter before this write; ``None`` means a
    creation. It feeds the set-once rule and nothing else.

    ``relation_types`` is the record's outgoing relation types. ``None`` means
    the caller has not parsed them — not that the record has none — and the one
    rule that reads them is skipped rather than guessed.
    """
    violations: list[Violation] = []

    # --- 1. Type. Everything after this is keyed on it, so an unknown type
    # short-circuits: the rest would be noise about rules that may not apply.
    #
    # Set-once is the one exception, and it is reported alongside. That rule
    # compares this write against the previous one field by field and never
    # consults the type, so it is decidable when nothing else is — and a write
    # that changes `type` to an undeclared value breaks *both* rules at once.
    # Reporting only the first would hide the set-once violation from the table
    # GAPS W5 builds (recorded there as owed; closed here with GAPS T22).
    record_type = metadata.get("type")
    if not isinstance(record_type, str) or record_type not in vocabulary.types:
        unknown = [_unknown_type(record_type, vocabulary)]
        if previous is None:
            return unknown
        return unknown + _check_set_once(
            metadata,
            previous,
            record_type if isinstance(record_type, str) else "",
        )

    date_field = _TYPE_DATE_FIELD.get(record_type)

    # --- 2. Fields every type requires ---
    for name in _REQUIRED_COMMON:
        if not _present(metadata, name):
            violations.append(
                Violation(
                    rule="missing-required-field",
                    field=name,
                    message=(
                        f"Missing required field '{name}'. "
                        f"Every record needs {_and_listed(_REQUIRED_COMMON)}."
                    ),
                    severity="error",
                )
            )

    # --- 3. permalink == id, byte-for-byte (schema.md §2) ---
    if _present(metadata, "id") and _present(metadata, "permalink"):
        if metadata["permalink"] != metadata["id"]:
            violations.append(
                Violation(
                    rule="permalink-mismatch",
                    field="permalink",
                    message=(
                        f"permalink '{metadata['permalink']}' must equal id "
                        f"'{metadata['id']}' byte-for-byte. Edges bind to the permalink, "
                        "so a mismatch orphans every relation pointing at this record."
                    ),
                    severity="error",
                )
            )

    # --- 4. Status, on a task only ---
    if record_type == "task":
        violations.extend(_check_status(metadata, vocabulary))

    # --- 5. Fields that belong to another type ---
    violations.extend(_check_type_only_fields(metadata, record_type, date_field))

    # --- 6. Supersession, which only a finding has ---
    violations.extend(_check_supersedes(record_type, relation_types))

    # --- 7. Fields this type requires ---
    for name in _REQUIRED_BY_TYPE.get(record_type, ()):
        if not _present(metadata, name):
            violations.append(
                Violation(
                    rule="missing-required-field",
                    field=name,
                    message=f"Missing required field '{name}'. Every {record_type} needs it.",
                    severity="error",
                )
            )

    # --- 8. Date values ---
    # Declared date fields are checked here too, so `invalid-date` is emitted from
    # one place; step 11 then only has enum values left to judge.
    date_keys = [*_SCHEMA_DATE_FIELDS, *_declared_date_fields(vocabulary)]
    for name in date_keys:
        if _present(metadata, name) and not _is_iso_date(metadata[name]):
            violations.append(
                Violation(
                    rule="invalid-date",
                    field=name,
                    message=(
                        f"'{name}' is {metadata[name]!r}, which is not an ISO calendar date. "
                        "Write it as YYYY-MM-DD."
                    ),
                    severity="error",
                )
            )

    # --- 9. The provenance triple, which exists only alongside a date ---
    if date_field is not None and _present(metadata, date_field):
        violations.extend(_check_provenance(metadata, date_field))
    else:
        for name in _PROVENANCE:
            if _present(metadata, name):
                violations.append(
                    Violation(
                        rule="field-not-on-type",
                        field=name,
                        message=(
                            f"'{name}' records where a date came from, and this record has no "
                            f"date. Remove it."
                        ),
                        severity="error",
                    )
                )

    # --- 10. Area ---
    if _present(metadata, "area") and metadata["area"] not in vocabulary.areas:
        violations.append(_unknown_area(metadata["area"], vocabulary))

    # --- 11. Declared optional fields ---
    for name, declared in vocabulary.fields.items():
        if declared.kind != "enum" or not _present(metadata, name):
            continue
        if metadata[name] not in declared.values:
            violations.append(
                Violation(
                    rule="unknown-enum-value",
                    field=name,
                    message=(
                        f"'{name}' is {metadata[name]!r}, which this project does not declare. "
                        f"Allowed values: {_listed(declared.values)}."
                    ),
                    severity="error",
                )
            )

    # --- 12. Unknown keys — flagged, never rejected (GAPS W4) ---
    known = _SCHEMA_KEYS | _HOUSEKEEPING_KEYS | set(vocabulary.fields)
    for name in metadata:
        if name not in known:
            violations.append(
                Violation(
                    rule="unknown-key",
                    field=name,
                    message=(
                        f"'{name}' is not a schema field and is not declared by this project. "
                        "It is kept and indexed; declare it in vocabulary.yml to make it official."
                    ),
                    severity="advisory",
                )
            )

    # --- 13. Set-once fields ---
    if previous is not None:
        violations.extend(_check_set_once(metadata, previous, record_type))

    return violations


# --- Rule helpers ---


def _unknown_type(record_type: Any, vocabulary: Vocabulary) -> Violation:
    """Build the one message an agent reads at the moment of filing (W19 item 3)."""
    choices = ", ".join(type_choice(name) for name in vocabulary.types)
    opening = (
        "Missing required field 'type'."
        if record_type is None
        else f"Type {record_type!r} is not in this project's vocabulary."
    )
    return Violation(
        rule="unknown-type",
        field="type",
        message=(
            f"{opening} Pick one of: {choices}. A new type cannot be enabled from a write. "
            "File this as 'inbox' and name the type you wanted; the proposal is recorded "
            "for a human to promote."
        ),
        severity="error",
    )


def _check_status(metadata: Mapping[str, Any], vocabulary: Vocabulary) -> list[Violation]:
    if not _present(metadata, "status"):
        return [
            Violation(
                rule="missing-status",
                field="status",
                message=f"A task needs a status. Allowed values: {_listed(vocabulary.statuses)}.",
                severity="error",
            )
        ]
    if metadata["status"] not in vocabulary.statuses:
        return [
            Violation(
                rule="unknown-status",
                field="status",
                message=(
                    f"Status {metadata['status']!r} is not in this project's vocabulary. "
                    f"Allowed values: {_listed(vocabulary.statuses)}."
                ),
                severity="error",
            )
        ]
    return []


def _check_type_only_fields(
    metadata: Mapping[str, Any], record_type: str, date_field: str | None
) -> list[Violation]:
    violations: list[Violation] = []

    # A date whose name belongs to another type. There is physically nowhere to
    # put a date on a guide, state, or inbox record — that is the point (§2).
    for name in _TYPE_DATE_FIELD.values():
        if name != date_field and _present(metadata, name):
            owner = next(key for key, value in _TYPE_DATE_FIELD.items() if value == name)
            expected = (
                f"a {record_type} uses '{date_field}'"
                if date_field
                else f"a {record_type} has no date field"
            )
            violations.append(
                Violation(
                    rule="field-not-on-type",
                    field=name,
                    message=f"'{name}' is the date field of a {owner}; {expected}.",
                    severity="error",
                )
            )

    for name, owners in _TYPE_ONLY_FIELDS.items():
        if record_type not in owners and _present(metadata, name):
            violations.append(
                Violation(
                    rule="field-not-on-type",
                    field=name,
                    message=(
                        f"'{name}' belongs to {_listed(sorted(owners))}, not to a {record_type}."
                    ),
                    severity="error",
                )
            )
    return violations


def _check_supersedes(record_type: str, relation_types: Sequence[str] | None) -> list[Violation]:
    """Report a ``supersedes`` relation on a type that has no supersession.

    ``None`` means the caller did not parse the record's relations — the move
    planner rewrites a path and no relation line — so the rule is undecidable
    and is skipped. Treating it as "no relations" would clear rows a real write
    recorded.

    The match is case-folded: ``Supersedes [[X]]`` reads as the same relation to
    a person, and letting capitalisation defeat the rule is a hole, not a rule.
    """
    if relation_types is None or record_type in _SUPERSEDES_TYPES:
        return []
    if not any(name.strip().lower() == _SUPERSEDES_RELATION for name in relation_types):
        return []
    return [
        Violation(
            rule="supersedes-not-on-type",
            field=_SUPERSEDES_RELATION,
            message=(
                f"Only a finding supersedes another record, and this is a {record_type}. "
                "A finding is never edited, so a correction is a new finding that "
                "supersedes the old one. A task is closed with `bm done`, not superseded; "
                "a guide, a profile, and a state are edited in place."
            ),
            severity="error",
        )
    ]


def _check_provenance(metadata: Mapping[str, Any], date_field: str) -> list[Violation]:
    """Check the triple that makes a record's date re-openable (schema.md §2)."""
    violations: list[Violation] = []

    for name in ("date-source", "date-confidence"):
        if not _present(metadata, name):
            violations.append(
                Violation(
                    rule="missing-required-field",
                    field=name,
                    message=(
                        f"'{date_field}' is set, so '{name}' is required. "
                        "A date without its provenance cannot be re-opened."
                    ),
                    severity="error",
                )
            )

    source = metadata.get("date-source")
    if _present(metadata, "date-source") and source not in _DATE_SOURCES:
        violations.append(
            Violation(
                rule="unknown-date-source",
                field="date-source",
                message=(
                    f"date-source {source!r} is not a rung of the fidelity ladder. "
                    f"Allowed values: {_listed(_DATE_SOURCES)}."
                ),
                severity="error",
            )
        )

    confidence = metadata.get("date-confidence")
    if _present(metadata, "date-confidence") and confidence not in _DATE_CONFIDENCES:
        violations.append(
            Violation(
                rule="unknown-date-confidence",
                field="date-confidence",
                message=(
                    f"date-confidence {confidence!r} is not allowed. "
                    f"Allowed values: {_listed(_DATE_CONFIDENCES)}."
                ),
                severity="error",
            )
        )

    # Decided only for a known rung: on an unknown or missing date-source there is
    # no fact about whether a ref is owed, and guessing would double-report.
    if source in _REF_BEARING_SOURCES and not _present(metadata, "date-ref"):
        violations.append(
            Violation(
                rule="date-ref-required",
                field="date-ref",
                message=(
                    f"date-source is '{source}', so date-ref is required: "
                    "a session id with a line for a transcript, a commit sha for git."
                ),
                severity="error",
            )
        )
    elif source in _DATE_SOURCES and source not in _REF_BEARING_SOURCES:
        if _present(metadata, "date-ref"):
            violations.append(
                Violation(
                    rule="date-ref-forbidden",
                    field="date-ref",
                    message=(
                        f"date-source is '{source}', which points at no evidence, so date-ref "
                        f"must be absent. It is allowed only for "
                        f"{_listed(sorted(_REF_BEARING_SOURCES))}."
                    ),
                    severity="error",
                )
            )
    return violations


def _unknown_area(area: Any, vocabulary: Vocabulary) -> Violation:
    if vocabulary.areas:
        message = (
            f"Area {area!r} is not in this project's vocabulary. "
            f"Allowed values: {_listed(vocabulary.areas)}."
        )
    else:
        message = "This project declares no areas, so omit the 'area' field."
    return Violation(rule="unknown-area", field="area", message=message, severity="error")


def _check_set_once(
    metadata: Mapping[str, Any], previous: Mapping[str, Any], record_type: str
) -> list[Violation]:
    """Report set-once fields this write changes or drops.

    A field absent from ``previous`` and now present is a first set, not a change.
    """
    violations: list[Violation] = []
    for name in _SET_ONCE:
        if name not in previous:
            continue
        if name in metadata and metadata[name] == previous[name]:
            continue
        violations.append(
            Violation(
                rule="set-once-changed",
                field=name,
                message=(
                    f"'{name}' is set once and cannot change: it was "
                    f"{_shown(previous.get(name))} and this write makes it "
                    f"{_shown(metadata.get(name))}. {_set_once_route(record_type)}"
                ),
                severity="error",
            )
        )
    return violations


def _set_once_route(record_type: str) -> str:
    """Name the sanctioned path, which is what makes the rejection actionable."""
    if record_type == "task":
        return "A task's only mutable field is status, changed with `bm done` or `bm mark`."
    if record_type == "finding":
        return "Correct a finding by writing a successor that supersedes it, never by editing it."
    return "Set-once fields are written once, by `bm new`, and never edited."


# --- Small shared predicates ---


def _present(metadata: Mapping[str, Any], name: str) -> bool:
    """True when the key carries a value. An empty string is an absent field."""
    return metadata.get(name) not in (None, "")


def _is_iso_date(value: Any) -> bool:
    """True for a ``YYYY-MM-DD`` calendar date.

    Tolerates a native ``date``: the entity parser normalizes frontmatter to ISO
    strings, but a caller that parsed YAML itself has not.
    """
    if isinstance(value, datetime):
        # A timestamp is not a calendar date; the schema's date fields are days.
        return False
    if isinstance(value, date):
        return True
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _listed(values: Iterable[str]) -> str:
    return ", ".join(values)


def _and_listed(values: Iterable[str]) -> str:
    """Join for prose. Allowed-value lists keep the bare commas of ``_listed``:
    an 'and' between choices reads as "all of these", which is the opposite.
    """
    names = list(values)
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _shown(value: Any) -> str:
    return "absent" if value is None else repr(value)


def _declared_date_fields(vocabulary: Vocabulary) -> tuple[str, ...]:
    return tuple(name for name, declared in vocabulary.fields.items() if declared.kind == "date")
