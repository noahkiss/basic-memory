"""Plain-English words for the record types (GAPS W19).

A type name is read by an agent choosing where to file something, at the moment
it is least inclined to look up a definition. W19 opened because the draft names
described the schema's internal axes — mutability, supersession — rather than
what a person does with the record. These strings are the fix, and they are
shared on purpose: the write-path rejection (item 3) and ``bm types`` (item 4)
must teach the same vocabulary, or the two surfaces drift apart.

Keyed by type name, and every lookup tolerates a miss. A project may declare a
type a human added to its ``vocabulary.yml``; it appears in ``bm types`` and in
every rejection message under its bare name rather than being hidden.
"""

from collections.abc import Mapping
from dataclasses import dataclass

# The one-word test that picks the type, from GAPS W4's type table.
PICKING_QUESTIONS: Mapping[str, str] = {
    "task": "do it",
    "guide": "consult it",
    "finding": "learned it",
    "profile": "refer to it",
    "state": "how things are",
    "inbox": "can't tell",
}


def picking_question(name: str) -> str | None:
    """The picking question for a type, or None for one this glossary does not know."""
    return PICKING_QUESTIONS.get(name)


def type_choice(name: str) -> str:
    """A type with its picking question, or the bare name for a human addition."""
    question = PICKING_QUESTIONS.get(name)
    return f"{name} ({question})" if question else name


# The relation that carries supersession, and the only direction it is stored in:
# the successor owns the edge and the predecessor is never touched
# (`.forked/schema.md` §5/§12). Here for the same reason the ladders below are —
# it is schema vocabulary, not a project's, and four modules were spelling it out
# separately before GAPS U3 needed a fifth.
SUPERSEDES_RELATION = "supersedes"


# What each default relation type is for, in the register `bm types` prints in:
# what you are claiming by writing the edge, not what the graph does with it.
# Keyed by name and every lookup tolerates a miss, exactly like the type prose —
# a relation a human added to their `vocabulary.yml` appears under its bare name.
RELATION_MEANINGS: Mapping[str, str] = {
    "relates_to": "These two belong together. Use it when no stronger word is true.",
    "derived_from": "This record came out of that one — a source, a transcript, a finding.",
    SUPERSEDES_RELATION: "This record replaces that one. Only a finding supersedes another.",
}


def relation_meaning(name: str) -> str | None:
    """What a relation type claims, or None for one this glossary does not know."""
    return RELATION_MEANINGS.get(name)


# The statuses whose name does not say what they mean, and only those. `open`,
# `doing`, `blocked`, `done` and `dropped` each read as themselves, and a line of
# prose under every one of them would be five lines of noise a reader learns to
# skip. `shelved` is the exception it exists for: nothing in the word says
# whether the work comes back (GAPS U23).
STATUS_MEANINGS: Mapping[str, str] = {
    "shelved": "Parked — not in the current set, not dropped. `bm mark <id> open` revives it.",
}


def status_meaning(name: str) -> str | None:
    """What a status means, or None when the name speaks for itself."""
    return STATUS_MEANINGS.get(name)


# --- The date-provenance ladders (`.forked/schema.md` §2) ---
#
# Fixed by the schema, not by a project's `vocabulary.yml`: a project declares
# which *types*, statuses, areas and extra fields it allows, and these two are
# neither. They live here rather than in `checker.py` because this module is the
# one every surface that has to *teach* them already imports — `bm types`, the
# `bm new` flags, and the checker's rejection messages — and it is stdlib-only,
# so the fast CLI path can read them without pulling PyYAML.
#
# Ordered highest fidelity first: `inline` means the source text carried the
# date, `inferred` means nobody stated it and somebody guessed.
DATE_SOURCES: tuple[str, ...] = ("inline", "transcript", "git", "mtime", "inferred")

# Ordered most precise first.
DATE_CONFIDENCES: tuple[str, ...] = ("exact", "day", "month", "unknown")

# The two rungs that point at re-openable evidence, and so must carry a
# `date-ref`. Forbidden on the other three: they point at nothing to open.
REF_BEARING_SOURCES: frozenset[str] = frozenset({"transcript", "git"})


def _or_listed(values: tuple[str, ...]) -> str:
    """Join a closed value list for prose: ``a, b, or c``."""
    return f"{', '.join(values[:-1])}, or {values[-1]}"


# --- What each type is for ---

# One or two sentences, in the register `bm types` prints them in: what you do
# with the record, not what the schema does with it (W19's whole complaint).
TYPE_SUMMARIES: Mapping[str, str] = {
    "task": "Work you mean to get done. Its status is the only field you change later.",
    "guide": (
        "An instruction you keep current. You rewrite the title and the body in "
        "place — that is what keeping it current means."
    ),
    "finding": (
        "Something you learned, tied to the day you learned it. You never edit a "
        "finding: you write a new one that supersedes it."
    ),
    "profile": (
        "A subject you collect facts about — a machine, a person, a service. Its "
        "title, its body, and the fields this project declares all stay current."
    ),
    "state": (
        "How something stands right now. You overwrite it or delete it, and it "
        "keeps no history of its own."
    ),
    "inbox": (
        "Use it when you cannot tell which type fits. It is the escape hatch, and "
        "bm doctor reports what collects here."
    ),
}


@dataclass(frozen=True, slots=True)
class TypeFields:
    """The frontmatter one type carries, split by whether it is required."""

    required: tuple[str, ...]
    optional: tuple[str, ...]


def _fields(required: str, optional: str = "") -> TypeFields:
    """Build a TypeFields from two space-separated field lists.

    Written as strings rather than tuples because the lists are long, a field
    name can never contain whitespace (``model._FIELD_NAME`` enforces that for
    declared fields), and the reading order is the printing order.
    """
    return TypeFields(required=tuple(required.split()), optional=tuple(optional.split()))


# From `.forked/schema.md` §2 (the common fields) and §3 (the per-type ones).
#
# `type` itself is deliberately absent from every row: `bm types` prints these
# under a heading that is the type name, so listing it again teaches nothing.
#
# `date-source` and `date-confidence` are required *with a date*, so they sit in
# `required` for the types whose date is required and in `optional` for
# `profile`, whose date is optional. `FIELD_MEANINGS` carries that condition in
# words, which is the only place a two-list split can express it.
TYPE_FIELDS: Mapping[str, TypeFields] = {
    "task": _fields(
        "id permalink title source status opened date-source date-confidence",
        "area not-before date-ref",
    ),
    "guide": _fields("id permalink title source review-by", "area"),
    "finding": _fields(
        "id permalink title source event-date date-source date-confidence review-by",
        "area date-ref",
    ),
    "profile": _fields(
        "id permalink title source",
        "area since date-source date-confidence date-ref",
    ),
    "state": _fields("id permalink title source", "area"),
    "inbox": _fields("id permalink title source"),
}


# What each field means, once, in the same plain register.
FIELD_MEANINGS: Mapping[str, str] = {
    "id": "The record's permanent name. Written once and never changed.",
    "permalink": "The same string as id, written out so links to this record resolve.",
    "title": "One line that names the record.",
    "source": "Where the content came from: a file path plus the lines it sits on.",
    "area": "Which part of your work this belongs to. The project declares the allowed areas.",
    "status": "Where the task stands. The project declares the allowed values.",
    "opened": "The day you wrote the task down.",
    "not-before": "Do not surface this task before this day.",
    "review-by": (
        "The day this needs a second look. Leave it out and bm sets it "
        "review_months out from today."
    ),
    "event-date": "The day the thing you learned actually happened.",
    "since": "The day this subject started, when you know it.",
    # Both value lists are interpolated rather than spelled out: this prose used
    # to carry its own copy of each ladder, which is exactly the second list that
    # drifts (GAPS U1).
    "date-source": (
        f"How you know the date: {_or_listed(DATE_SOURCES)}. "
        "Required whenever the record carries a date."
    ),
    "date-confidence": (
        f"How precise the date is: {_or_listed(DATE_CONFIDENCES)}. "
        "Required whenever the record carries a date."
    ),
    "date-ref": (
        "The commit or the transcript line the date came from. Required when "
        "date-source is git or transcript, and not allowed otherwise."
    ),
}


def type_summary(name: str) -> str | None:
    """What a type is for, or None for one this glossary does not know."""
    return TYPE_SUMMARIES.get(name)


def type_fields(name: str) -> TypeFields | None:
    """A type's fields, or None for one this glossary does not know."""
    return TYPE_FIELDS.get(name)


def field_meaning(name: str) -> str | None:
    """What a field means, or None for one this glossary does not know."""
    return FIELD_MEANINGS.get(name)
