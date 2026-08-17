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

from basic_memory.store.history import store_path

VOCABULARY_FILENAME = "vocabulary.yml"

# The three kinds a declared optional field may take. Bounded on purpose: no
# required-if, no cross-field rules, no defaults (GAPS W4 extension rules).
type FieldKind = Literal["string", "date", "enum"]

_FIELD_KINDS: frozenset[str] = frozenset({"string", "date", "enum"})
_ALLOWED_KEYS: frozenset[str] = frozenset({"types", "statuses", "areas", "review_months", "fields"})
_ALLOWED_FIELD_KEYS: frozenset[str] = frozenset({"kind", "values"})

# A field name is a frontmatter key, so it may not carry whitespace.
_FIELD_NAME = re.compile(r"[^\s]+")


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
    """What one project allows: its types, statuses, areas, and declared extras."""

    types: tuple[str, ...]
    statuses: tuple[str, ...]
    areas: tuple[str, ...]
    review_months: int
    fields: Mapping[str, DeclaredField] = field(default_factory=lambda: MappingProxyType({}))


# What `bm new` WRITES into a project's first vocabulary file — explicitly NOT
# what an absent file means. An absent file means the project is not governed and
# the checker never runs (GAPS W4, decided 2026-08-10).
DEFAULT_VOCABULARY = Vocabulary(
    types=("task", "guide", "finding", "profile", "state", "inbox"),
    statuses=("open", "doing", "blocked", "done", "dropped"),
    areas=(),
    review_months=12,
    fields=MappingProxyType({}),
)


# The status names that close a task. A vocabulary declares its statuses but
# marks none of them terminal, so which names *mean* closed is type knowledge no
# project file can carry. It lives here, once, because every caller that asks
# "is this task still open" has to get the same answer — `bm brief` and the
# headline file disagreeing about it would read as a bug in whichever the reader
# checked second.
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
    )


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
