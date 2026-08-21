"""What the board reads, as session-taking functions over the repository layer.

Every function here takes the caller's `AsyncSession`. That is not a style
preference: the engine's pool holds a single connection, so a query that opened
its own session inside the request's session would deadlock — the same
constraint `search_pointers` in `cli/commands/brief.py` carries.

No SQL lives here. Lane data comes from `list_records`
(`repository/entity_repository.py`), which already returns the record's declared
status and its superseded flag as separate values; record resolution comes from
`resolve_record` in `cli/direct.py`. The web server is a second reader of the
same corpus, never a second definition of what a record is.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from basic_memory.models import Project
    from basic_memory.vocabulary.model import Vocabulary

# The types that carry a lifecycle and therefore earn a column on the board
# (GAPS U38). Everything else is filed under "other records".
LIFECYCLE_TYPES: tuple[str, ...] = ("task", "plan")

# What a record with no declared status is bucketed under. The same reading
# `bm brief`, `bm board` and `bm show` already give it: hiding open work over a
# schema fault the notice already reports would be a second, silent penalty.
UNSET_STATUS = "open"

# How many cards one status column renders before it says how many are left.
# A `done` column on a project two years old is thousands of records, and a
# board that renders all of them is a page nobody can scroll. The cap is on the
# column, not on the query, so the count under it is always the true total.
MAX_COLUMN_CARDS = 25

# How many titles one "other records" group lists. The group's job is to say
# what kind of material is here, not to be `bm ls`.
MAX_OTHER_TITLES = 20


@dataclass(frozen=True, slots=True)
class Card:
    """One record on the board."""

    record_id: str
    title: str
    note_type: str
    superseded: bool


@dataclass(frozen=True, slots=True)
class Column:
    """One status and the lifecycle records sitting in it."""

    status: str
    cards: tuple[Card, ...]
    total: int

    @property
    def hidden(self) -> int:
        """How many records the card cap left out of this column."""
        return self.total - len(self.cards)


@dataclass(frozen=True, slots=True)
class OtherGroup:
    """The statusless records of one type: a count and the first few titles."""

    note_type: str
    cards: tuple[Card, ...]
    total: int

    @property
    def hidden(self) -> int:
        return self.total - len(self.cards)


@dataclass(frozen=True, slots=True)
class Lane:
    """Everything the board shows for one project.

    `live` and `inactive` are computed in `build_lane` rather than derived on
    read: both depend on the project's vocabulary, and a property would reread
    `vocabulary.yml` off disk every time a template touched it.
    """

    project: str
    external_id: str
    headline: str | None
    columns: tuple[Column, ...]
    other: tuple[OtherGroup, ...]
    # Lifecycle records in neither a terminal nor a parked status — the count
    # bare `bm` prints as "N open items".
    live: int
    shelved: int
    inbox: int
    # The status names this project treats as closed or parked. The page dims
    # those columns, so a reader's eye lands on what is still moving.
    inactive: frozenset[str]


def status_order(vocabulary: "Vocabulary | None", observed: Sequence[str]) -> tuple[str, ...]:
    """The board's columns, left to right.

    The project's declared statuses first, in the order its `vocabulary.yml`
    states them — that order is a human's judgment about the lifecycle, and the
    board is the one place it is visible as a shape rather than a list.

    Then any status a record actually carries that the vocabulary does not
    declare, sorted. Those records are a violation `bm doctor` reports, and
    dropping their column would hide the work while the violation counts it.

    A declared status nothing sits in still gets a column: an empty `blocked`
    column is the fact that nothing is blocked.
    """
    from basic_memory.vocabulary.model import DEFAULT_VOCABULARY

    declared = (DEFAULT_VOCABULARY if vocabulary is None else vocabulary).statuses
    undeclared = sorted(set(observed) - set(declared))
    return tuple(declared) + tuple(undeclared)


async def build_lane(session: "AsyncSession", project: "Project") -> Lane:
    """Read one project's whole board in a single records query.

    One query, not one per column: the columns are a partition of the same rows,
    and a query per status would multiply a page render by the size of the
    vocabulary for no new information.
    """
    from basic_memory.repository.entity_repository import list_records
    from basic_memory.services.headline import read_headline
    from basic_memory.vocabulary.model import (
        PARKED_STATUSES,
        VocabularyError,
        inactive_statuses,
        load_vocabulary,
    )

    rows = await list_records(session, [project.id])

    lifecycle = [row for row in rows if row.note_type in LIFECYCLE_TYPES]
    by_status: dict[str, list[Card]] = {}
    for row in lifecycle:
        bucket = by_status.setdefault(row.status or UNSET_STATUS, [])
        bucket.append(
            Card(
                record_id=row.permalink,
                title=row.title,
                note_type=row.note_type,
                superseded=row.superseded,
            )
        )

    # Trigger: this project's `vocabulary.yml` will not parse.
    # Why: W4 forbids reading that as "not governed", and `bm doctor` is what
    #     reports it. For the board it means only that the columns cannot be put
    #     in this project's declared order and the inactive set cannot be
    #     narrowed to its declared names.
    # Outcome: fall back to the cross-project defaults — the same reading an
    #     unscoped `bm brief` gives — and render the lane rather than losing it.
    try:
        vocabulary = load_vocabulary(project.external_id)
    except VocabularyError:
        vocabulary = None

    columns = tuple(
        Column(
            status=status,
            cards=tuple(by_status.get(status, ())[:MAX_COLUMN_CARDS]),
            total=len(by_status.get(status, ())),
        )
        for status in status_order(vocabulary, list(by_status))
    )

    others: dict[str, list[Card]] = {}
    for row in rows:
        if row.note_type in LIFECYCLE_TYPES:
            continue
        others.setdefault(row.note_type, []).append(
            Card(
                record_id=row.permalink,
                title=row.title,
                note_type=row.note_type,
                superseded=row.superseded,
            )
        )

    parked = PARKED_STATUSES if vocabulary is None else PARKED_STATUSES & set(vocabulary.statuses)
    inactive = inactive_statuses(vocabulary)
    return Lane(
        project=project.name,
        external_id=project.external_id,
        headline=read_headline(project.external_id),
        columns=columns,
        other=tuple(
            OtherGroup(note_type=note_type, cards=tuple(cards[:MAX_OTHER_TITLES]), total=len(cards))
            for note_type, cards in sorted(others.items())
        ),
        live=sum(column.total for column in columns if column.status not in inactive),
        shelved=sum(len(by_status.get(status, ())) for status in parked),
        inbox=len(others.get("inbox", ())),
        inactive=inactive,
    )


async def build_lanes(session: "AsyncSession", project_name: str | None) -> list[Lane]:
    """One lane per project in scope, ordered by name.

    `None` means every registered project — the board's default, because "what
    is stored where" is the question the server exists to answer. A name narrows
    it to one lane and raises `ValueError` when the registry does not hold it.
    """
    from basic_memory.cli.direct import projects_in_scope

    projects = sorted(await projects_in_scope(session, project_name), key=lambda row: row.name)
    return [await build_lane(session, project) for project in projects]
