"""Allocating the record id a governed write is filed under (GAPS U49).

`vocabulary/ids.py` owns the draw, the attempt count and the error, and stays
pure — it never asks a database whether an id is free. The collision check is a
query, so it lives here, and it lives *once*: two write paths need the same
answer and neither may import the other. `bm new` is one (`cli/record_notes.py`),
and the accepted-note create path every MCP and API write lands on is the other
(`indexing/accepted_note_write_runner.py`).

The predicate is injected rather than a repository, because the caller is the
only layer that holds both the project's repository and the session the question
must be asked on.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from basic_memory.vocabulary.ids import MAX_ID_ATTEMPTS, IdAllocationError, new_record_id

# True when this project already holds a note under that permalink. Async
# because every caller answers it with a query. The permalink column is what is
# asked: `permalink == id` byte-for-byte is the record schema's identity rule
# (`.design/schema.md` §2), so a taken permalink is a taken id.
type PermalinkTakenCheck = Callable[[str], Awaitable[bool]]


async def allocate_record_id(note_type: str, is_taken: PermalinkTakenCheck) -> str:
    """Draw a record id no note in this project already claims.

    ``note_type`` is the canonical type the write resolved — the id's prefix
    carries it (GAPS U30), so the hatch's inbox records draw `inbox-…` ids and an
    alias write draws the id of the type it stamped.

    Raises ``IdAllocationError`` after ``MAX_ID_ATTEMPTS`` collisions: at 36^8 a
    second collision means the check is wrong, not that the draw was unlucky.
    """
    for _ in range(MAX_ID_ATTEMPTS):
        candidate = new_record_id(note_type)
        if not await is_taken(candidate):
            return candidate
    raise IdAllocationError(
        f"could not allocate a free record id in {MAX_ID_ATTEMPTS} attempts; "
        "that many collisions means the collision check is wrong, not that the "
        "draw was unlucky"
    )
