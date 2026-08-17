"""Matching a term across transcripts, carrying neighbouring turns as context.

No index, deliberately. GAPS W1 measured plain search over a 106 MB / 77-session
corpus at ~20 ms, worst case 0.47 s; an index would add a staleness problem in
exchange for nothing. This is a straight streaming read.

Pure Python rather than `rg --json`: the gap entry's four search-path
constraints are all about *not* letting a subprocess mangle the data — never
`--max-columns`, never a bare `split(':')`, always the `*.jsonl` allowlist. A
`json.loads` per line satisfies all four by construction, adds no dependency,
and cannot be defeated by a stray `--max-columns` in someone's ripgrep config.

Context turns are drawn from **every** speaker, not just the selected one. The
turn before a human decision is usually the assistant proposal it answers, and
dropping it would leave the quote unreadable.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from basic_memory.mine.turns import BadLine, Speaker, Turn, read_turns


@dataclass(frozen=True, slots=True)
class Hit:
    """A matching turn and the turns around it.

    ``context`` holds neighbouring turns in file order, excluding the hit
    itself. Each carries its own line number, so which side of the hit a
    context turn sits on is derivable rather than encoded in two fields.
    """

    turn: Turn
    context: tuple[Turn, ...]

    @property
    def ref(self) -> str:
        return self.turn.ref


@dataclass(frozen=True, slots=True)
class ScanReport:
    """What a scan found, and what it could not read.

    Damage travels beside the hits rather than as an exception, so a corpus
    with one torn line stays mineable and the caller can still fail the run
    (GAPS O10). An empty `damage` is the normal case: 12 lines of the measured
    tree are damaged and every other line reads.
    """

    hits: tuple[Hit, ...]
    damage: tuple[BadLine, ...]


def matches(turn: Turn, term: str) -> bool:
    """Case-insensitive substring match against the turn's own text.

    Matching is against the *extracted* text, never the raw JSON line, so a
    search for `decided` cannot hit a tool name, a file path in an attachment
    envelope, or a base64 image blob that happens to contain the letters.
    """
    return term.casefold() in turn.text.casefold()


def _hits_in_file(
    path: Path,
    term: str,
    speakers: frozenset[Speaker],
    context: int,
    damage: list[BadLine],
) -> Iterator[Hit]:
    """Yield hits from one transcript in line order, with a bounded window.

    The window is why this is not a list comprehension: a hit is not complete
    until ``context`` further turns have gone past, so open hits queue up and
    are released from the front once filled. Memory stays bounded by
    ``context``, which matters because a single transcript line has been
    measured at 1.2 MB.

    A damaged line lands in ``damage`` and the read continues. It contributes
    no context turn, because the turn it would have contributed is the one that
    was lost.
    """
    # maxlen 0 would make the deque permanently empty, which is what a caller
    # asking for no context means, so no branch is needed here.
    before: deque[Turn] = deque(maxlen=context)
    open_hits: deque[_OpenHit] = deque()

    for turn in read_turns(path):
        if isinstance(turn, BadLine):
            damage.append(turn)
            continue

        for open_hit in open_hits:
            open_hit.follow(turn, context)
        # Hits complete in the order they opened, so the front is always the
        # next one to release.
        while open_hits and open_hits[0].complete(context):
            yield open_hits.popleft().finish()

        if turn.speaker in speakers and matches(turn, term):
            open_hits.append(_OpenHit(turn=turn, before=tuple(before)))

        before.append(turn)

    # End of file: whatever context a trailing hit got is what it gets.
    while open_hits:
        yield open_hits.popleft().finish()


@dataclass(slots=True)
class _OpenHit:
    """A match whose trailing context is still being gathered.

    Leading and trailing context are held apart because they fill from opposite
    ends. Merging them into one list — the first shape this took — silently
    starved a hit of trailing context whenever its leading context was already
    full.
    """

    turn: Turn
    before: tuple[Turn, ...]
    after: list[Turn] = field(default_factory=list)

    def follow(self, turn: Turn, context: int) -> None:
        if len(self.after) < context:
            self.after.append(turn)

    def complete(self, context: int) -> bool:
        return len(self.after) >= context

    def finish(self) -> Hit:
        return Hit(turn=self.turn, context=(*self.before, *self.after))


def scan(
    paths: Iterable[Path],
    term: str,
    *,
    speakers: frozenset[Speaker],
    context: int = 0,
) -> ScanReport:
    """Every matching turn across ``paths`` in path then line order, plus the damage.

    This returns rather than yields because damage is only fully known once the
    last file has been read, and a caller that has to print the payload *and*
    then name every unreadable line needs both in hand.
    """
    if context < 0:
        raise ValueError("context must not be negative")

    hits: list[Hit] = []
    damage: list[BadLine] = []
    for path in paths:
        hits.extend(_hits_in_file(path, term, speakers, context, damage))
    return ScanReport(hits=tuple(hits), damage=tuple(damage))
