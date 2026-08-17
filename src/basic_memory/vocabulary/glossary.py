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
    "date-source": (
        "How you know the date: inline, transcript, git, mtime, or inferred. "
        "Required whenever the record carries a date."
    ),
    "date-confidence": (
        "How precise the date is: exact, day, month, or unknown. "
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
