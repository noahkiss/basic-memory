"""Tests for the per-project record vocabulary file (GAPS W4)."""

from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest

from basic_memory.vocabulary.model import (
    DEFAULT_VOCABULARY,
    DeclaredField,
    Vocabulary,
    VocabularyError,
    default_review_by,
    load_vocabulary,
    parse_vocabulary,
    vocabulary_path,
)

EXTERNAL_ID = "0d0b2f1e-6d3a-4a4e-9d2e-2f8a1b7c5e40"

FULL_FILE = """\
types:    [task, guide, finding, profile, state, inbox]
statuses: [open, doing, blocked, done, dropped]
areas:    [ops, life]
review_months: 6
fields:
  host-role: string
  commissioned: date
  tier: {kind: enum, values: [prod, staging, dev]}
"""


@pytest.fixture
def data_dir(monkeypatch, tmp_path: Path) -> Path:
    """Point the store at a temp data dir, as BASIC_MEMORY_CONFIG_DIR does in use."""
    data = tmp_path / "data"
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(data))
    return data


def write_vocabulary(text: str) -> Path:
    """Write a vocabulary file for EXTERNAL_ID and return its path."""
    path = vocabulary_path(EXTERNAL_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- Path and presence ---


def test_vocabulary_path_lives_in_the_store(data_dir: Path):
    assert vocabulary_path(EXTERNAL_ID) == data_dir / "store" / EXTERNAL_ID / "vocabulary.yml"


def test_absent_file_means_not_governed(data_dir: Path):
    assert load_vocabulary(EXTERNAL_ID) is None


def test_absent_store_directory_means_not_governed(data_dir: Path):
    # Nothing under the data dir exists yet; the loader must not create it.
    assert load_vocabulary(EXTERNAL_ID) is None
    assert not data_dir.exists()


# --- Loading ---


def test_full_file_loads_exactly(data_dir: Path):
    write_vocabulary(FULL_FILE)

    assert load_vocabulary(EXTERNAL_ID) == Vocabulary(
        types=("task", "guide", "finding", "profile", "state", "inbox"),
        statuses=("open", "doing", "blocked", "done", "dropped"),
        areas=("ops", "life"),
        review_months=6,
        fields={
            "host-role": DeclaredField(name="host-role", kind="string"),
            "commissioned": DeclaredField(name="commissioned", kind="date"),
            "tier": DeclaredField(name="tier", kind="enum", values=("prod", "staging", "dev")),
        },
    )


def test_empty_file_governs_with_defaults(data_dir: Path):
    # Presence is the opt-in, so an empty file is governed, not ungoverned.
    write_vocabulary("")

    assert load_vocabulary(EXTERNAL_ID) == DEFAULT_VOCABULARY


def test_missing_keys_fall_back_to_defaults(data_dir: Path):
    write_vocabulary("areas: [ops]\n")

    loaded = load_vocabulary(EXTERNAL_ID)

    assert loaded is not None
    assert loaded.areas == ("ops",)
    assert loaded.types == DEFAULT_VOCABULARY.types
    assert loaded.statuses == DEFAULT_VOCABULARY.statuses
    assert loaded.review_months == DEFAULT_VOCABULARY.review_months
    assert loaded.fields == {}


def test_unparseable_yaml_names_the_path(data_dir: Path):
    path = write_vocabulary("types: [task\n")

    with pytest.raises(VocabularyError) as exc:
        load_vocabulary(EXTERNAL_ID)

    assert str(path) in str(exc.value)
    assert "not valid YAML" in str(exc.value)


def test_invalid_content_names_the_path(data_dir: Path):
    path = write_vocabulary("review_months: 0\n")

    with pytest.raises(VocabularyError) as exc:
        load_vocabulary(EXTERNAL_ID)

    assert str(path) in str(exc.value)


# --- Declared fields ---


def test_shorthand_and_long_form_agree():
    shorthand = parse_vocabulary({"fields": {"host-role": "string"}}, source="v.yml")
    long_form = parse_vocabulary({"fields": {"host-role": {"kind": "string"}}}, source="v.yml")

    assert shorthand == long_form
    assert shorthand.fields["host-role"] == DeclaredField(name="host-role", kind="string")


def test_declared_date_field():
    parsed = parse_vocabulary({"fields": {"commissioned": "date"}}, source="v.yml")

    assert parsed.fields["commissioned"] == DeclaredField(name="commissioned", kind="date")


def test_enum_field_keeps_declared_order():
    parsed = parse_vocabulary(
        {"fields": {"tier": {"kind": "enum", "values": ["prod", "staging", "dev"]}}},
        source="v.yml",
    )

    assert parsed.fields["tier"].values == ("prod", "staging", "dev")


# --- Rejections ---


def test_top_level_type_must_be_a_mapping():
    # A YAML file whose root is a list, not a mapping. The cast is the point of
    # the test: the guard exists for input the type annotation already forbids.
    not_a_mapping = cast(Mapping[str, Any], ["task"])
    with pytest.raises(VocabularyError, match="expected a mapping"):
        parse_vocabulary(not_a_mapping, source="v.yml")


def test_unknown_top_level_key_is_an_error():
    # A typo'd key that silently does nothing is beans' failure mode (R5).
    with pytest.raises(VocabularyError, match="unknown key"):
        parse_vocabulary({"typez": ["task"]}, source="v.yml")


def test_types_must_be_a_list():
    with pytest.raises(VocabularyError, match="'types' must be a list"):
        parse_vocabulary({"types": "task"}, source="v.yml")


@pytest.mark.parametrize("bad", [[""], ["  "], [3], [None]])
def test_types_must_be_non_empty_strings(bad: list):
    with pytest.raises(VocabularyError, match="non-empty strings"):
        parse_vocabulary({"types": bad}, source="v.yml")


def test_statuses_must_be_a_list():
    with pytest.raises(VocabularyError, match="'statuses' must be a list"):
        parse_vocabulary({"statuses": {"open": True}}, source="v.yml")


@pytest.mark.parametrize("bad", [0, -1, "12", 1.5, True])
def test_review_months_must_be_a_positive_int(bad: object):
    with pytest.raises(VocabularyError, match="positive integer"):
        parse_vocabulary({"review_months": bad}, source="v.yml")


def test_fields_must_be_a_mapping():
    with pytest.raises(VocabularyError, match="'fields' must be a mapping"):
        parse_vocabulary({"fields": ["host-role"]}, source="v.yml")


def test_field_name_must_be_a_bare_string():
    with pytest.raises(VocabularyError, match="without whitespace"):
        parse_vocabulary({"fields": {"host role": "string"}}, source="v.yml")


def test_field_spec_must_be_a_kind_or_mapping():
    with pytest.raises(VocabularyError, match="kind name or a mapping"):
        parse_vocabulary({"fields": {"tier": ["prod"]}}, source="v.yml")


def test_field_spec_rejects_unknown_keys():
    with pytest.raises(VocabularyError, match="unknown key"):
        parse_vocabulary({"fields": {"tier": {"kind": "string", "required": True}}}, source="v.yml")


@pytest.mark.parametrize("kind", [None, "int", 3])
def test_field_kind_must_be_one_of_three(kind: object):
    with pytest.raises(VocabularyError, match="allowed kinds are"):
        parse_vocabulary({"fields": {"tier": {"kind": kind}}}, source="v.yml")


@pytest.mark.parametrize("values", [None, [], "prod", {}])
def test_enum_requires_non_empty_values(values: object):
    spec = {"kind": "enum"} if values is None else {"kind": "enum", "values": values}
    with pytest.raises(VocabularyError, match="non-empty 'values' list"):
        parse_vocabulary({"fields": {"tier": spec}}, source="v.yml")


def test_enum_values_must_be_non_empty_strings():
    with pytest.raises(VocabularyError, match="enum values must be non-empty strings"):
        parse_vocabulary({"fields": {"tier": {"kind": "enum", "values": ["prod", ""]}}}, source="v")


@pytest.mark.parametrize("kind", ["string", "date"])
def test_non_enum_field_must_not_declare_values(kind: str):
    with pytest.raises(VocabularyError, match="must not declare 'values'"):
        parse_vocabulary({"fields": {"x": {"kind": kind, "values": ["a"]}}}, source="v.yml")


def test_error_message_names_the_source():
    with pytest.raises(VocabularyError) as exc:
        parse_vocabulary({"review_months": 0}, source=Path("/store/abc/vocabulary.yml"))

    assert str(exc.value).startswith("/store/abc/vocabulary.yml:")


# --- The default block ---


def test_default_vocabulary_matches_the_schema_block():
    assert DEFAULT_VOCABULARY.types == ("task", "guide", "finding", "profile", "state", "inbox")
    assert DEFAULT_VOCABULARY.statuses == ("open", "doing", "blocked", "done", "dropped")
    assert DEFAULT_VOCABULARY.areas == ()
    assert DEFAULT_VOCABULARY.review_months == 12
    assert DEFAULT_VOCABULARY.fields == {}


# --- default_review_by ---


def test_the_default_review_date_crosses_a_year_boundary():
    """Twelve months on from July 2026 is July 2027, not month 19."""
    vocabulary = parse_vocabulary({"review_months": 12}, source="v.yml")

    assert default_review_by(vocabulary, date(2026, 7, 26)) == "2027-07-26"


def test_review_months_is_honoured():
    """The project's number decides, not a hardcoded year."""
    vocabulary = parse_vocabulary({"review_months": 3}, source="v.yml")

    assert default_review_by(vocabulary, date(2026, 11, 30)) == "2027-02-28"


def test_a_day_the_target_month_lacks_clamps_to_its_last():
    """The 31st of a 30-day month is not a date; the last day of it is."""
    vocabulary = parse_vocabulary({"review_months": 1}, source="v.yml")

    assert default_review_by(vocabulary, date(2026, 8, 31)) == "2026-09-30"


def test_february_29_survives_a_four_year_review():
    """A leap day plus 48 months is a leap day; the clamp must not fire."""
    vocabulary = parse_vocabulary({"review_months": 48}, source="v.yml")

    assert default_review_by(vocabulary, date(2024, 2, 29)) == "2028-02-29"
