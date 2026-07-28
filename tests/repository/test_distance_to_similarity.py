"""Unit tests for the SQLite distance-to-similarity conversion."""

import pytest

from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository


def test_sqlite_distance_to_similarity_formula():
    """SQLite converts L2 distance to cosine similarity for normalized vectors."""
    repo = SQLiteSearchRepository.__new__(SQLiteSearchRepository)

    assert repo._distance_to_similarity(0.0) == 1.0
    assert repo._distance_to_similarity(1.0) == pytest.approx(0.5)
    assert repo._distance_to_similarity(2.0) == 0.0
