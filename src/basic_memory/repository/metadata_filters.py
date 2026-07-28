"""Helpers for parsing structured metadata filters for search."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Iterable, List, cast


_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$")
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
_COMPARISON_OPERATORS = {
    "$gt": "gt",
    "$gte": "gte",
    "$lt": "lt",
    "$lte": "lte",
}
_SUPPORTED_OPERATORS = "$in, $contains, $gt, $gte, $lt, $lte, $between"

# YAML 1.1 boolean literals PyYAML resolves to `bool` when parsing frontmatter.
_BOOLEAN_LITERALS = {
    "true": True,
    "false": False,
    "yes": True,
    "no": False,
    "on": True,
    "off": False,
}


@dataclass(frozen=True)
class ParsedMetadataFilter:
    """Normalized metadata filter for SQL generation."""

    path_parts: List[str]
    op: str
    value: Any
    comparison: str | None = None  # "numeric" or "text" for comparisons


def _is_numeric_value(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return bool(_NUMERIC_RE.match(value.strip()))
    return False


def _is_numeric_collection(values: Iterable[Any]) -> bool:
    return all(_is_numeric_value(v) for v in values)


def _normalize_scalar(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    return value


def _boolean_match_values(value: Any) -> List[str] | None:
    """Return the stored spellings a boolean-valued query must match, or None if not boolean.

    An unquoted YAML boolean is indexed as `str(bool)` ("True"/"False"), while a quoted one
    keeps whatever the author typed, and `--meta draft=true` arrives as the string "true".
    Matching only one spelling turns the other into a zero-result answer that reads as
    "no matches" -- the silent miss this exists to prevent.
    """
    if isinstance(value, bool):
        canonical = str(value)
        return [canonical, canonical.lower()]
    if isinstance(value, str):
        token = value.strip()
        literal = _BOOLEAN_LITERALS.get(token.lower())
        if literal is None:
            return None
        # dict.fromkeys preserves order and drops the duplicate when the caller typed "True"
        return list(dict.fromkeys([str(literal), token]))
    return None


def _normalize_numeric(value: object) -> float:
    """Normalize a value already proven numeric by _is_numeric_value."""
    return float(cast(str | int | float, value))


def parse_metadata_filters(filters: dict[str, Any]) -> List[ParsedMetadataFilter]:
    """Parse metadata filters into normalized clauses.

    Supported forms:
    - {"status": "in-progress"}
    - {"tags": ["security", "oauth"]}  # array contains all
    - {"tags": {"$contains": "security"}}  # array contains this element
    - {"priority": {"$in": ["high", "critical"]}}  # any of, element-wise on lists
    - {"schema.confidence": {"$gt": 0.7}}
    - {"schema.confidence": {"$between": [0.3, 0.6]}}
    """
    parsed: List[ParsedMetadataFilter] = []

    for raw_key, raw_value in (filters or {}).items():
        if not isinstance(raw_key, str) or not raw_key.strip():
            raise ValueError("metadata filter keys must be non-empty strings")
        key = raw_key.strip()
        if not _KEY_RE.match(key):
            raise ValueError(f"Unsupported metadata filter key: {raw_key}")

        path_parts = key.split(".")

        # Operator form
        if isinstance(raw_value, dict):
            if len(raw_value) != 1:
                raise ValueError(f"Invalid metadata filter for '{raw_key}': {raw_value}")
            raw_op, value = next(iter(raw_value.items()))
            if not isinstance(raw_op, str):
                raise ValueError(
                    f"Unsupported operator '{raw_op}' in metadata filter for '{raw_key}'"
                )
            op = raw_op

            if op == "$in":
                if not isinstance(value, list) or not value:
                    raise ValueError(f"$in requires a non-empty list for '{raw_key}'")
                parsed.append(
                    ParsedMetadataFilter(path_parts, "in", [_normalize_scalar(v) for v in value])
                )
                continue

            if op in _COMPARISON_OPERATORS:
                if _is_numeric_value(value):
                    normalized = _normalize_numeric(value)
                    comparison = "numeric"
                else:
                    normalized = _normalize_scalar(value)
                    comparison = "text"
                parsed.append(
                    ParsedMetadataFilter(
                        path_parts,
                        _COMPARISON_OPERATORS[op],
                        normalized,
                        comparison,
                    )
                )
                continue

            if op == "$between":
                if not isinstance(value, list) or len(value) != 2:
                    raise ValueError(f"$between requires [min, max] for '{raw_key}'")
                if _is_numeric_collection(value):
                    normalized = [_normalize_numeric(v) for v in value]
                    comparison = "numeric"
                else:
                    normalized = [_normalize_scalar(v) for v in value]
                    comparison = "text"
                parsed.append(ParsedMetadataFilter(path_parts, "between", normalized, comparison))
                continue

            # $contains states element-wise intent explicitly; a bare list means "all of".
            if op == "$contains":
                values = value if isinstance(value, list) else [value]
                if not values:
                    raise ValueError(f"$contains requires at least one value for '{raw_key}'")
                parsed.append(
                    ParsedMetadataFilter(
                        path_parts, "contains", [_normalize_scalar(v) for v in values]
                    )
                )
                continue

            raise ValueError(
                f"Unsupported operator '{op}' in metadata filter for '{raw_key}'. "
                f"Supported operators: {_SUPPORTED_OPERATORS}"
            )

        # Array contains (all)
        if isinstance(raw_value, list):
            if not raw_value:
                raise ValueError(f"Empty list not allowed for metadata filter '{raw_key}'")
            parsed.append(
                ParsedMetadataFilter(
                    path_parts, "contains", [_normalize_scalar(v) for v in raw_value]
                )
            )
            continue

        # Boolean equality has two possible stored spellings, so it becomes a set membership.
        boolean_values = _boolean_match_values(raw_value)
        if boolean_values is not None:
            parsed.append(ParsedMetadataFilter(path_parts, "in", boolean_values))
            continue

        # Simple equality
        parsed.append(ParsedMetadataFilter(path_parts, "eq", _normalize_scalar(raw_value)))

    return parsed


def build_sqlite_json_path(parts: List[str]) -> str:
    """Build a SQLite JSON path for json_extract/json_each."""
    path = "$"
    for part in parts:
        path += f'."{part}"'
    return path
