"""Tests for canonical permalink utility helpers."""

from basic_memory.utils import (
    build_permalink_resolution_candidates,
    build_qualified_permalink_reference,
)


def test_project_prefixed_candidate_includes_short_legacy_when_project_prefix_disabled():
    candidates = build_permalink_resolution_candidates(
        "main/notes/example",
        "main",
        include_project=False,
    )

    assert candidates == [
        "main/notes/example",
        "notes/example",
    ]


def test_short_permalink_candidates_include_project_form():
    candidates = build_permalink_resolution_candidates(
        "notes/example",
        "main",
    )

    assert candidates == [
        "notes/example",
        "main/notes/example",
    ]


def test_qualified_permalink_reference_preserves_lookup_syntax():
    assert (
        build_qualified_permalink_reference(
            "main",
            "notes/roadmap.md",
            include_project=False,
        )
        == "notes/roadmap.md"
    )
    assert (
        build_qualified_permalink_reference(
            "main",
            "patterns/*",
            include_project=True,
        )
        == "main/patterns/*"
    )
