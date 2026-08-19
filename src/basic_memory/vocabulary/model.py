"""The per-project record vocabulary (GAPS W4).

A project is *governed* when ``store/<external-id>/vocabulary.yml`` exists.
Humans edit that file; agents may only select from what is in it. The file lives
in the store so W3's history sees every edit to it — a commit carrying an
``Actor: agent`` trailer is the only real check on agent field-extension.

Like ``store/history.py``, this module is deliberately dependency-free: no DB, no
SQLAlchemy, no API, no MCP. It has to be importable on the fast CLI path.
"""

import re
from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, NoReturn

import yaml
from loguru import logger

from basic_memory.store.history import HistoryError, commit_paths, store_path
from basic_memory.store.write_hook import session_id

VOCABULARY_FILENAME = "vocabulary.yml"

# The three kinds a declared optional field may take. Bounded on purpose: no
# required-if, no cross-field rules, no defaults (GAPS W4 extension rules).
type FieldKind = Literal["string", "date", "enum"]

_FIELD_KINDS: frozenset[str] = frozenset({"string", "date", "enum"})
_ALLOWED_KEYS: frozenset[str] = frozenset(
    {"types", "statuses", "areas", "relations", "review_months", "fields"}
)
_ALLOWED_FIELD_KEYS: frozenset[str] = frozenset({"kind", "values"})

# A field name is a frontmatter key, so it may not carry whitespace.
_FIELD_NAME = re.compile(r"[^\s]+")

# The edges a project may write when it declares none of its own (GAPS U14).
#
# Three, and deliberately few: a relation type is read by an agent choosing how
# to connect two records, and a long menu there produces a corpus where the same
# link is spelled four ways. `supersedes` is already schema vocabulary
# (`glossary.SUPERSEDES_RELATION`) and is listed so `--rel supersedes:<id>` and
# `--supersedes <id>` are the same write. `derived_from` carries provenance —
# this finding came out of that source record — and `relates_to` is the honest
# unlabelled edge, which is better written than left in prose.
#
# It is a *default*, not a floor: a project's `relations:` key replaces this list
# outright, the way `types:` and `statuses:` do.
DEFAULT_RELATIONS: tuple[str, ...] = ("relates_to", "derived_from", "supersedes")


class VocabularyError(ValueError):
    """A vocabulary file is malformed.

    Raised rather than degraded: a project with an unreadable vocabulary must not
    silently become ungoverned, because "ungoverned" and "governed by a file with
    a typo in it" are the same state to every later check (GAPS W4).
    """


@dataclass(frozen=True, slots=True)
class DeclaredField:
    """One optional extra a project declares in its vocabulary."""

    name: str
    kind: FieldKind
    # Non-empty only for ``kind == "enum"``; the parser enforces both directions.
    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """What one project allows: its types, statuses, areas, edges, declared extras."""

    types: tuple[str, ...]
    statuses: tuple[str, ...]
    areas: tuple[str, ...]
    review_months: int
    fields: Mapping[str, DeclaredField] = field(default_factory=lambda: MappingProxyType({}))
    # The relation types `bm new --rel` and `bm edit --rel` may write (GAPS U14).
    # Defaulted rather than positional: every vocabulary file written before U14
    # omits the key, and a project that says nothing about edges gets the same
    # three every other project starts with.
    relations: tuple[str, ...] = DEFAULT_RELATIONS


# What `bm project add --governed` WRITES into a project's first vocabulary file
# — explicitly NOT what an absent file means. An absent file means the project is
# not governed and the checker never runs (GAPS W4, decided 2026-08-10).
#
# `note` is the seventh and it is not one of the record types: it is MCP's
# `write_note` default, and a governed project that did not declare it refused the
# primary agent write path outright (GAPS D8). Declaring it costs nothing — it
# carries the four common fields and no rules of its own, exactly like a type a
# human added — and it keeps `--governed` from meaning "MCP stops working here".
# The record types are still the six: `note` has no picking question and no
# glossary summary, so `bm new` never offers it as a choice.
DEFAULT_VOCABULARY = Vocabulary(
    types=("task", "guide", "finding", "profile", "state", "inbox", "note"),
    # `shelved` sits between the open statuses and the closing ones because that
    # is what it means: parked, not dropped (GAPS U23).
    statuses=("open", "doing", "blocked", "shelved", "done", "dropped"),
    areas=(),
    review_months=12,
    fields=MappingProxyType({}),
    relations=DEFAULT_RELATIONS,
)


# The status names that close a task. A vocabulary declares its statuses but
# marks none of them terminal, so which names *mean* closed is type knowledge no
# project file can carry. It lives here, once, because every caller that asks
# "is this task still open" has to get the same answer — `bm brief` and the
# `bm done`/`bm mark` headline prompt disagreeing about it would read as a bug
# in whichever the reader checked second.
TERMINAL_STATUSES: frozenset[str] = frozenset({"done", "dropped"})


def terminal_statuses(vocabulary: Vocabulary | None = None) -> frozenset[str]:
    """Which status names close a task, for one project or in general.

    A governed project narrows the set to the terminal names it actually
    declares, so a vocabulary that dropped ``dropped`` stops matching it. A
    project that declares neither name says nothing about termination, and
    guessing nothing there would leave every task permanently open — so the
    defaults stand. Callers spanning several projects pass nothing and get them.
    """
    if vocabulary is None:
        return TERMINAL_STATUSES
    declared = TERMINAL_STATUSES & set(vocabulary.statuses)
    return frozenset(declared) if declared else TERMINAL_STATUSES


# The status names that park a task: it is not in the current set and it is not
# dropped either (GAPS U23, user decision 2026-08-18 — "not dropped forever but
# also not in our current set"). Kept apart from TERMINAL_STATUSES because the
# two answer different questions: a terminal task is finished with, a parked one
# is waiting, and `bm mark <id> open` revives it.
PARKED_STATUSES: frozenset[str] = frozenset({"shelved"})


def inactive_statuses(vocabulary: Vocabulary | None = None) -> frozenset[str]:
    """Which status names keep a task out of "what is open", terminal or parked.

    This — not `terminal_statuses` — is what every caller asking "is this task
    still open" wants, because a shelved task belongs in neither answer: it is
    not listed as open and it is not counted as closed.

    Narrowed to declared names the same way `terminal_statuses` is, and for the
    same reason: a project that never declared `shelved` cannot have a task in
    that state, so matching on it would filter nothing and mislead a reader of
    the query. Unlike the terminal set there is no fallback when nothing matches
    — an undeclared parked name means the project has no parked state, which is
    a real answer, where "no terminal names" would leave every task open forever.
    """
    return terminal_statuses(vocabulary) | (
        PARKED_STATUSES
        if vocabulary is None
        else frozenset(PARKED_STATUSES & set(vocabulary.statuses))
    )


def default_review_by(vocabulary: Vocabulary, today: date) -> str:
    """The day a record created on ``today`` falls due for review, as an ISO date.

    ``review_months`` calendar months out, never a fixed number of days: a
    review date is a human appointment, and "the same day of the month, N months
    on" is the only form that survives a year of leap days and short months.

    A day the target month does not have — the 31st of a 30-day month — clamps
    to that month's last day, because the alternative is not a real date.
    """
    months = today.month - 1 + vocabulary.review_months
    year = today.year + months // 12
    month = months % 12 + 1
    return date(year, month, min(today.day, monthrange(year, month)[1])).isoformat()


def vocabulary_path(external_id: str) -> Path:
    """Return the vocabulary file's path for a project's ``external_id``.

    The id is the project's UUID4 (``Project.external_id``). Taken as a plain
    ``str`` so this module never reaches the models or the DB.
    """
    return store_path() / external_id / VOCABULARY_FILENAME


def load_vocabulary(external_id: str) -> Vocabulary | None:
    """Load a project's vocabulary, or ``None`` when the project is not governed."""
    path = vocabulary_path(external_id)
    if not path.is_file():
        return None

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise VocabularyError(f"{path}: not valid YAML: {exc}") from exc

    # An empty file is a deliberate, present opt-in: it governs, with defaults.
    return parse_vocabulary({} if raw is None else raw, source=path)


def parse_vocabulary(raw: Mapping[str, Any], *, source: Path | str) -> Vocabulary:
    """Parse and validate vocabulary content. Pure: no filesystem, no defaults file.

    Every key is optional and falls back to ``DEFAULT_VOCABULARY``, but a key that
    is present must be well-formed. An unknown top-level key is an error: the file
    is small and hand-edited, and a typo'd key that silently does nothing is the
    failure mode ``.forked/decisions.md`` R5 records for beans.
    """
    if not isinstance(raw, Mapping):
        _fail(source, f"expected a mapping at the top level, got {type(raw).__name__}")

    if unknown := sorted(set(raw) - _ALLOWED_KEYS):
        allowed = ", ".join(sorted(_ALLOWED_KEYS))
        _fail(source, f"unknown key(s) {_quoted(unknown)}; allowed keys are {allowed}")

    return Vocabulary(
        types=_string_tuple(raw, "types", DEFAULT_VOCABULARY.types, source),
        statuses=_string_tuple(raw, "statuses", DEFAULT_VOCABULARY.statuses, source),
        areas=_string_tuple(raw, "areas", DEFAULT_VOCABULARY.areas, source),
        review_months=_review_months(raw, source),
        fields=_parse_fields(raw.get("fields", {}), source),
        # Absent means the default three, not "no edges allowed": every file
        # written before GAPS U14 omits the key, and reading that as a refusal
        # would turn `--rel` off for every project that already exists.
        relations=_string_tuple(raw, "relations", DEFAULT_VOCABULARY.relations, source),
    )


# --- Writing ---


def vocabulary_document(vocabulary: Vocabulary) -> dict[str, object]:
    """The YAML mapping one vocabulary serializes to (`.forked/schema.md` §3).

    Only ``enum`` fields carry ``values``; the parser rejects the key on the
    other two kinds, so emitting an empty list would produce a file this tree
    cannot read back.
    """
    return {
        "types": list(vocabulary.types),
        "statuses": list(vocabulary.statuses),
        "areas": list(vocabulary.areas),
        "relations": list(vocabulary.relations),
        "review_months": vocabulary.review_months,
        "fields": {
            name: (
                {"kind": declared.kind, "values": list(declared.values)}
                if declared.kind == "enum"
                else {"kind": declared.kind}
            )
            for name, declared in vocabulary.fields.items()
        },
    }


def write_default_vocabulary(external_id: str) -> Path | None:
    """Give a project the default vocabulary, and return where it landed.

    **Called only for `bm project add --governed`, and that is a reversal.**
    D8 originally had `project add` write this unconditionally. It cannot, and the
    reason it cannot is not the one that closed the item: an absent file means
    ungoverned (GAPS W4), so writing a vocabulary for every project would
    override that meaning rather than leave it to the human whose file it is.

    The *breakage* that forced the reversal is fixed. The default vocabulary now
    declares `note`, MCP's `write_note` default, so governing a project no longer
    refuses the primary agent write path — 7 integration tests and `just doctor`
    had failed on `Type 'note' is not in this project's vocabulary`, and every
    existing MCP caller would have failed the same way on its next write.
    Governed-by-default is still not this tree's decision to take.

    Returns None when the file is already there. A vocabulary is hand-edited
    (`.forked/schema.md` §3), so overwriting an existing one would discard a
    human's declarations — and re-adding a project by name must not cost them.
    """
    path = vocabulary_path(external_id)
    if path.exists():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(vocabulary_document(DEFAULT_VOCABULARY), sort_keys=False),
        encoding="utf-8",
    )

    # Committed here, not left for a later write to notice. The file sits in the
    # store's worktree, so an uncommitted one is reported as somebody else's
    # dirty work by every note write that follows it, forever (GAPS W3-B).
    # Trigger: the store repository is unusable — a stale lock, a broken config.
    # Why: the vocabulary is already on disk and the project is already in the
    #     registry, so failing the add would leave both behind anyway.
    # Outcome: log for the operator and keep the project, which is W3-A's rule
    #     for a create: nothing is lost, so a missing entry costs a warning.
    try:
        commit_paths(
            [f"{external_id}/{path.name}"],
            f"create {external_id}/{path.name}",
            actor="cli",
            session_id=session_id(),
        )
    except HistoryError as error:
        logger.warning(f"Could not record {path} in the note history: {error}")
    return path


# --- Parsing helpers ---


def _string_tuple(
    raw: Mapping[str, Any], key: str, default: tuple[str, ...], source: Path | str
) -> tuple[str, ...]:
    if key not in raw:
        return default

    values = raw[key]
    # A bare string is a list of characters to Python and a silent disaster here.
    if not isinstance(values, list):
        _fail(source, f"'{key}' must be a list, got {type(values).__name__}")
    for value in values:
        if not isinstance(value, str) or not value.strip():
            _fail(source, f"'{key}' must contain non-empty strings, got {value!r}")
    return tuple(values)


def _review_months(raw: Mapping[str, Any], source: Path | str) -> int:
    if "review_months" not in raw:
        return DEFAULT_VOCABULARY.review_months

    months = raw["review_months"]
    # bool is an int subclass, and `review_months: yes` parses as True in YAML.
    if isinstance(months, bool) or not isinstance(months, int) or months < 1:
        _fail(source, f"'review_months' must be a positive integer, got {months!r}")
    return months


def _parse_fields(raw: Any, source: Path | str) -> Mapping[str, DeclaredField]:
    if not isinstance(raw, Mapping):
        _fail(source, f"'fields' must be a mapping, got {type(raw).__name__}")

    declared: dict[str, DeclaredField] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not _FIELD_NAME.fullmatch(name):
            _fail(source, f"field name {name!r} must be a non-empty string without whitespace")
        declared[name] = _parse_field(name, spec, source)
    return MappingProxyType(declared)


def _parse_field(name: str, spec: Any, source: Path | str) -> DeclaredField:
    """Parse one declared field. A bare string is shorthand for ``{kind: <string>}``."""
    if isinstance(spec, str):
        spec = {"kind": spec}
    if not isinstance(spec, Mapping):
        _fail(source, f"field '{name}' must be a kind name or a mapping, got {spec!r}")

    if unknown := sorted(set(spec) - _ALLOWED_FIELD_KEYS):
        _fail(source, f"field '{name}' has unknown key(s) {_quoted(unknown)}")

    kind = spec.get("kind")
    if kind not in _FIELD_KINDS:
        kinds = ", ".join(sorted(_FIELD_KINDS))
        _fail(source, f"field '{name}' has kind {kind!r}; allowed kinds are {kinds}")

    values = spec.get("values")
    if kind != "enum":
        # Values on a string/date field are a misunderstanding, not a harmless
        # extra: nothing would ever read them, so they read as constraints that
        # are not enforced.
        if values is not None:
            _fail(source, f"field '{name}' is kind '{kind}' and must not declare 'values'")
        return DeclaredField(name=name, kind=kind, values=())

    if not isinstance(values, list) or not values:
        _fail(source, f"field '{name}' is an enum and must declare a non-empty 'values' list")
    for value in values:
        if not isinstance(value, str) or not value.strip():
            _fail(source, f"field '{name}' enum values must be non-empty strings, got {value!r}")
    return DeclaredField(name=name, kind="enum", values=tuple(values))


def _quoted(names: list[str]) -> str:
    return ", ".join(f"'{name}'" for name in names)


def _fail(source: Path | str, problem: str) -> NoReturn:
    """Raise with the file named first: the reader has to know which file to open."""
    raise VocabularyError(f"{source}: {problem}")
