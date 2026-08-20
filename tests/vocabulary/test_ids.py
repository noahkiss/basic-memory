"""Record ids, slugs, and file paths (schema.md §2, §8; VERBS_PLAN D1 and D2; GAPS U30).

The claims worth guarding are the ones a later change could break silently: the
alphabet (an uppercase or underscore id survives every test that only checks
length, and then collides in a relation row), the truncation boundary, the fact
that collision retry gives up loudly instead of looping — and, since U30, that
the legacy `tnd-` shape stays accepted forever while new draws carry the type.
"""

import re

import pytest

from basic_memory.vocabulary.ids import (
    FALLBACK_TYPE_PREFIX,
    ID_ALPHABET,
    ID_LENGTH,
    LEGACY_ID_PREFIX,
    MAX_SLUG_LENGTH,
    IdAllocationError,
    allocate_record_id,
    is_record_id,
    new_record_id,
    record_file_path,
    record_slug,
    type_dir,
    type_prefix,
)


# --- Type prefixes (U30) ---


def test_a_closed_type_passes_through_as_its_own_prefix() -> None:
    """The whole point of U30: the prefix is the type word, unabbreviated."""
    for name in ("task", "guide", "finding", "profile", "state", "inbox", "note"):
        assert type_prefix(name) == name


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        ("Run Book", "run-book"),  # declared with a space and case
        ("host_profile", "host-profile"),  # underscore folds to hyphen (T9)
        ("café", "cafe"),  # transliterated like a slug
    ],
)
def test_a_custom_type_slugifies_into_its_prefix(declared: str, expected: str) -> None:
    assert type_prefix(declared) == expected


@pytest.mark.parametrize("declared", ["", "###", "3d-print"])
def test_an_unusable_type_name_takes_the_fallback_prefix(declared: str) -> None:
    """Folds to nothing, or opens with a digit the id pattern cannot carry."""
    assert type_prefix(declared) == FALLBACK_TYPE_PREFIX


# --- Ids ---


def test_a_new_id_is_the_type_word_then_the_declared_alphabet_and_length() -> None:
    """D1's shape as amended by U30, over enough draws to catch a stray symbol."""
    for record_type in ("task", "finding", "inbox"):
        for _ in range(60):
            record_id = new_record_id(record_type)
            assert record_id.startswith(f"{record_type}-")
            body = record_id[len(record_type) + 1 :]
            assert len(body) == ID_LENGTH
            assert set(body) <= set(ID_ALPHABET)
            assert is_record_id(record_id)


def test_an_alias_never_reaches_the_id() -> None:
    """`bm new todo` stamps `task` before the draw, so the id carries the
    canonical word — this guards the module contract that the caller passes the
    resolved type, with the resolution itself covered in test_new_command."""
    assert new_record_id("task").startswith("task-")


def test_ids_carry_a_hyphen_and_never_an_underscore() -> None:
    """One character, load-bearing: relation targets slugify `_` into `-` (T9)."""
    record_id = new_record_id("task")

    assert record_id[4] == "-"
    assert "_" not in record_id


def test_a_custom_type_id_never_contains_the_file_separator() -> None:
    """`--` is the file name's id/slug boundary, so an id must never hold one —
    ``type_prefix`` collapses runs precisely so this stays true."""
    assert "--" not in new_record_id("weird -- type")


def test_two_draws_differ() -> None:
    """A positive control for the draw: a constant would pass every check above."""
    assert len({new_record_id("task") for _ in range(50)}) == 50


@pytest.mark.parametrize(
    "value",
    # The first row is the legacy shape (pre-U30 records): permanent names,
    # accepted forever even though nothing draws them anymore.
    ["tnd-aaaa1111", "tnd-00000000", "tnd-zzzzzzzz"],
)
def test_is_record_id_accepts_a_legacy_id(value: str) -> None:
    assert is_record_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "task-yke6e8dz",
        "finding-8xk3p2q1",
        "inbox-a1b2c3d4",
        "note-a1b2c3d4",
        "run-book-a1b2c3d4",  # hyphenated custom type prefix
    ],
)
def test_is_record_id_accepts_the_type_word_shape(value: str) -> None:
    assert is_record_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "tnd_aaaa1111",  # underscore separator
        "tnd-AAAA1111",  # uppercase body
        "tnd-aaaa111",  # seven characters
        "tnd-aaaa11111",  # nine characters
        "tnd-aaaa-111",  # hyphen inside the body
        "Task-aaaa1111",  # uppercase type prefix
        "3d-aaaa1111",  # prefix opening with a digit
        "-aaaa1111",  # empty prefix
        "aaaa1111",  # no prefix at all
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

    allocated = allocate_record_id("task", taken)

    assert len(seen) == 2
    assert allocated == seen[1]


def test_allocation_raises_after_the_attempt_limit() -> None:
    """Exhaustion is a defect in the collision check, so it must be loud."""
    attempts: list[str] = []

    def always_taken(candidate: str) -> bool:
        attempts.append(candidate)
        return True

    with pytest.raises(IdAllocationError):
        allocate_record_id("task", always_taken, attempts=5)

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
    """schema.md §8, byte for byte, in the U30 shape."""
    path = record_file_path("finding", "finding-q8w3e1r5", "In-container backup cannot work")

    assert path == "findings/finding-q8w3e1r5--in-container-backup-cannot-work.md"


def test_record_file_path_still_accepts_a_legacy_id() -> None:
    """Pre-U30 records keep their names, so the path builder keeps taking them."""
    path = record_file_path("finding", "tnd-q8w3e1r5", "In-container backup cannot work")

    assert path == "findings/tnd-q8w3e1r5--in-container-backup-cannot-work.md"


def test_record_file_path_refuses_an_argument_that_is_not_a_record_id() -> None:
    """A file whose name does not open with a real id is not identity-addressable."""
    with pytest.raises(ValueError, match="not a record id"):
        record_file_path("task", "tnd_q8w3e1r5", "Anything")


def test_legacy_prefix_is_a_well_formed_type_slug() -> None:
    """The single-pattern design leans on `tnd` parsing like a type word; if the
    pattern ever tightens, this is the line that says why legacy ids still pass."""
    assert is_record_id(f"{LEGACY_ID_PREFIX}aaaa1111")
