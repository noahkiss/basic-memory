"""Tests for structured metadata filter parsing helpers."""

from datetime import date

import pytest

from basic_memory.models.knowledge import Entity
from basic_memory.repository.metadata_filters import (
    _ENTITY_COLUMN_GUIDANCE,
    ParsedMetadataFilter,
    _is_numeric_collection,
    _is_numeric_value,
    build_sqlite_json_path,
    parse_metadata_filters,
    validate_metadata_filter_keys,
)


def test_parse_simple_equality():
    parsed = parse_metadata_filters({"status": "in-progress"})
    assert parsed == [ParsedMetadataFilter(["status"], "eq", "in-progress")]


def test_parse_contains_list():
    parsed = parse_metadata_filters({"tags": ["security", "oauth"]})
    assert parsed == [ParsedMetadataFilter(["tags"], "contains", ["security", "oauth"])]


def test_parse_in_operator():
    parsed = parse_metadata_filters({"priority": {"$in": ["high", "critical"]}})
    assert parsed == [ParsedMetadataFilter(["priority"], "in", ["high", "critical"])]


def test_parse_comparison_numeric():
    parsed = parse_metadata_filters({"schema.confidence": {"$gt": 0.7}})
    assert parsed == [ParsedMetadataFilter(["schema", "confidence"], "gt", 0.7, "numeric")]


def test_parse_between_numeric():
    parsed = parse_metadata_filters({"score": {"$between": [0.3, 0.6]}})
    assert parsed == [ParsedMetadataFilter(["score"], "between", [0.3, 0.6], "numeric")]


def test_parse_between_text():
    parsed = parse_metadata_filters({"window": {"$between": ["2024-01-01", "2024-12-31"]}})
    assert parsed == [
        ParsedMetadataFilter(["window"], "between", ["2024-01-01", "2024-12-31"], "text")
    ]


def test_parse_normalizes_scalar_types():
    parsed = parse_metadata_filters({"created": date(2025, 1, 10), "ratio": 0.5})
    values = {f.path_parts[0]: f.value for f in parsed}
    assert values["created"] == "2025-01-10"
    assert values["ratio"] == "0.5"


def test_parse_boolean_matches_both_stored_spellings():
    """Unquoted YAML booleans index as "True"; quoted ones keep the author's spelling."""
    assert parse_metadata_filters({"flag": True}) == [
        ParsedMetadataFilter(["flag"], "in", ["True", "true"])
    ]
    assert parse_metadata_filters({"flag": False}) == [
        ParsedMetadataFilter(["flag"], "in", ["False", "false"])
    ]


def test_parse_boolean_literal_strings():
    """`--meta draft=true` arrives as a string and must still mean the boolean."""
    assert parse_metadata_filters({"draft": "true"}) == [
        ParsedMetadataFilter(["draft"], "in", ["True", "true"])
    ]
    assert parse_metadata_filters({"draft": "yes"}) == [
        ParsedMetadataFilter(["draft"], "in", ["True", "yes"])
    ]
    assert parse_metadata_filters({"draft": "True"}) == [
        ParsedMetadataFilter(["draft"], "in", ["True"])
    ]


def test_parse_non_boolean_string_is_unaffected():
    assert parse_metadata_filters({"status": "truely"}) == [
        ParsedMetadataFilter(["status"], "eq", "truely")
    ]


def test_parse_contains_operator():
    assert parse_metadata_filters({"tags": {"$contains": "security"}}) == [
        ParsedMetadataFilter(["tags"], "contains", ["security"])
    ]
    assert parse_metadata_filters({"tags": {"$contains": ["security", "oauth"]}}) == [
        ParsedMetadataFilter(["tags"], "contains", ["security", "oauth"])
    ]


def test_parse_contains_rejects_empty_list():
    with pytest.raises(ValueError, match="at least one value"):
        parse_metadata_filters({"tags": {"$contains": []}})


def test_unsupported_operator_names_the_supported_ones():
    with pytest.raises(ValueError, match=r"Supported operators: .*\$contains"):
        parse_metadata_filters({"tags": {"contains": "security"}})


def test_invalid_filter_key():
    with pytest.raises(ValueError):
        parse_metadata_filters({"bad key": "value"})


def test_invalid_operator():
    with pytest.raises(ValueError):
        parse_metadata_filters({"priority": {"$nope": "high"}})


def test_empty_list_rejected():
    with pytest.raises(ValueError):
        parse_metadata_filters({"tags": []})


def test_numeric_helpers():
    assert _is_numeric_value("1.5")
    assert _is_numeric_value(2)
    assert not _is_numeric_value("not-a-number")
    assert _is_numeric_collection(["1", 2, 3.5])
    assert not _is_numeric_collection(["1", "nope"])


# --- Entity-column keys are refused, not answered wrong (GAPS T21) ---


@pytest.mark.parametrize("column", sorted(_ENTITY_COLUMN_GUIDANCE))
def test_entity_column_key_is_rejected(column):
    """Every column name in the table raises instead of matching nothing."""
    with pytest.raises(ValueError, match="names a database column"):
        parse_metadata_filters({column: {"$lt": "2026-08-15"}})


def test_updated_at_rejection_names_the_supported_date_path():
    with pytest.raises(ValueError, match="after_date"):
        parse_metadata_filters({"updated_at": {"$lt": "2026-08-15"}})


def test_column_key_is_rejected_case_insensitively():
    with pytest.raises(ValueError, match="names a database column"):
        parse_metadata_filters({"Updated_At": "2026-08-15"})


def test_frontmatter_keys_named_like_columns_still_filter():
    """Positive control: title, type and permalink are real frontmatter keys."""
    assert parse_metadata_filters({"title": "Release Plan"}) == [
        ParsedMetadataFilter(["title"], "eq", "Release Plan")
    ]
    assert parse_metadata_filters({"type": "decision"}) == [
        ParsedMetadataFilter(["type"], "eq", "decision")
    ]
    assert parse_metadata_filters({"permalink": "notes/release-plan"}) == [
        ParsedMetadataFilter(["permalink"], "eq", "notes/release-plan")
    ]


def test_nested_key_named_like_a_column_is_frontmatter():
    """`schema.updated_at` addresses nested frontmatter, not the entity column."""
    assert parse_metadata_filters({"schema.updated_at": "2026-08-15"}) == [
        ParsedMetadataFilter(["schema", "updated_at"], "eq", "2026-08-15")
    ]


def test_every_entity_column_is_covered_or_deliberately_allowed():
    """A new Entity column must be classified, not silently answered with zero rows."""
    frontmatter_backed = {"title", "permalink"}
    columns = set(Entity.__table__.columns.keys()) - frontmatter_backed
    assert columns <= set(_ENTITY_COLUMN_GUIDANCE)


def test_validate_ignores_non_string_keys():
    """Key typing is the parser's error to report, not this guard's."""
    # The non-str key is the point of the test: the guard must not raise on it.
    validate_metadata_filter_keys({1: "value"})  # ty: ignore[invalid-argument-type]


def test_build_json_paths():
    assert build_sqlite_json_path(["schema", "confidence"]) == '$."schema"."confidence"'
