"""Record ids, slugs, and file paths (schema.md §2, §8; VERBS_PLAN D1 and D2).

The claims worth guarding are the ones a later change could break silently: the
alphabet (an uppercase or underscore id survives every test that only checks
length, and then collides in a relation row), the truncation boundary, and the
fact that collision retry gives up loudly instead of looping.
"""

import re

import pytest

from basic_memory.vocabulary.ids import (
    ID_ALPHABET,
    ID_LENGTH,
    ID_PREFIX,
    MAX_SLUG_LENGTH,
    IdAllocationError,
    allocate_record_id,
    is_record_id,
    new_record_id,
    record_file_path,
    record_slug,
    type_dir,
)


# --- Ids ---


def test_a_new_id_uses_the_declared_prefix_alphabet_and_length() -> None:
    """D1's shape, checked over enough draws to catch a stray symbol."""
    for _ in range(200):
        record_id = new_record_id()
        assert record_id.startswith(ID_PREFIX)
        body = record_id[len(ID_PREFIX) :]
        assert len(body) == ID_LENGTH
        assert set(body) <= set(ID_ALPHABET)


def test_ids_carry_a_hyphen_and_never_an_underscore() -> None:
    """One character, load-bearing: relation targets slugify `_` into `-` (T9)."""
    record_id = new_record_id()

    assert record_id[3] == "-"
    assert "_" not in record_id


def test_two_draws_differ() -> None:
    """A positive control for the draw: a constant would pass every check above."""
    assert len({new_record_id() for _ in range(50)}) == 50


@pytest.mark.parametrize(
    "value",
    ["tnd-aaaa1111", "tnd-00000000", "tnd-zzzzzzzz"],
)
def test_is_record_id_accepts_a_well_formed_id(value: str) -> None:
    assert is_record_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "tnd_aaaa1111",  # underscore separator
        "tnd-AAAA1111",  # uppercase body
        "tnd-aaaa111",  # seven characters
        "tnd-aaaa11111",  # nine characters
        "tnd-aaaa-111",  # hyphen inside the body
        "notes/my-note",  # an ordinary permalink
        "",
    ],
)
def test_is_record_id_rejects_everything_else(value: str) -> None:
    assert not is_record_id(value)


# --- Collision retry ---


def test_allocation_returns_the_first_free_id() -> None:
    """The predicate decides, and one collision is not a failure."""
    seen: list[str] = []

    def taken(candidate: str) -> bool:
        seen.append(candidate)
        return len(seen) == 1

    allocated = allocate_record_id(taken)

    assert len(seen) == 2
    assert allocated == seen[1]


def test_allocation_raises_after_the_attempt_limit() -> None:
    """Exhaustion is a defect in the collision check, so it must be loud."""
    attempts: list[str] = []

    def always_taken(candidate: str) -> bool:
        attempts.append(candidate)
        return True

    with pytest.raises(IdAllocationError):
        allocate_record_id(always_taken, attempts=5)

    assert len(attempts) == 5


# --- Slugs ---


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Move backups off-container", "move-backups-off-container"),
        ("API (v2) & friends", "api-v2-friends"),
        ("don't panic", "dont-panic"),
        ("unified_model_refactor", "unified-model-refactor"),
        ("myFeature", "my-feature"),
        ("Café naïve", "cafe-naive"),
        ("Version 2.0.0", "version-2-0-0"),
        ("   ", "untitled"),
    ],
)
def test_record_slug_folds_a_title_into_the_file_name_alphabet(title: str, expected: str) -> None:
    """Every rule in one table: transliteration, camelCase, apostrophes, empties."""
    assert record_slug(title) == expected


def test_a_slug_holds_only_lowercase_letters_digits_and_hyphens() -> None:
    """The alphabet claim, stated once over a deliberately hostile title."""
    slug = record_slug("Ünicode: PATHS/with slashes, #hashes & 100% symbols!")

    assert re.fullmatch(r"[a-z0-9-]+", slug)


def test_a_long_title_truncates_without_a_trailing_hyphen() -> None:
    """Cutting at 60 can land on a word boundary; `...-.md` reads as a typo."""
    slug = record_slug("word " * 40)

    assert len(slug) <= MAX_SLUG_LENGTH
    assert not slug.endswith("-")


# --- Type directories and file paths ---


def test_type_dir_maps_every_closed_type_to_its_plural_directory() -> None:
    """D2's mapping. `inbox` is a place, not a count, so it keeps its name."""
    assert [
        type_dir(name) for name in ("task", "guide", "finding", "profile", "state", "inbox")
    ] == ["tasks", "guides", "findings", "profiles", "states", "inbox"]


def test_type_dir_refuses_a_type_outside_the_closed_six() -> None:
    """An unknown type files as `inbox` with a proposed-type — `bm new`'s call, said out loud."""
    with pytest.raises(ValueError, match="unknown record type 'runbook'"):
        type_dir("runbook")


def test_record_file_path_is_type_dir_id_double_dash_slug() -> None:
    """schema.md §8, byte for byte."""
    path = record_file_path("finding", "tnd-q8w3e1r5", "In-container backup cannot work")

    assert path == "findings/tnd-q8w3e1r5--in-container-backup-cannot-work.md"


def test_record_file_path_refuses_an_argument_that_is_not_a_record_id() -> None:
    """A file whose name does not open with a real id is not identity-addressable."""
    with pytest.raises(ValueError, match="not a record id"):
        record_file_path("task", "tnd_q8w3e1r5", "Anything")
