"""Tests for cwd → project resolution via the `.bm.yml` marker (GAPS B5).

The shared reader is strict — a marker that exists but cannot be used raises
MarkerError instead of silently falling back to the default project, which
would point commands (including writes) at the wrong project.

This chain is the **write** chain: it ends at the registry default. Reads use
`basic_memory.cli.scope.resolve_read_scope`, tested in tests/test_cli_scope.py,
which ends at "every project" instead (GAPS W5-C).
"""

import pytest

from basic_memory.project_marker import (
    MarkerError,
    find_marker,
    read_marker_project,
    resolve_cli_project,
)


# --- find_marker ---


def test_find_marker_walks_up(tmp_path):
    (tmp_path / ".bm.yml").write_text("project: research\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_marker(nested) == tmp_path / ".bm.yml"


def test_find_marker_prefers_nearest(tmp_path):
    (tmp_path / ".bm.yml").write_text("project: outer\n")
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / ".bm.yml").write_text("project: inner\n")
    assert find_marker(inner) == inner / ".bm.yml"


def test_find_marker_absent(tmp_path):
    assert find_marker(tmp_path) is None


# --- read_marker_project (strict) ---


def test_read_marker_project(tmp_path):
    marker = tmp_path / ".bm.yml"
    marker.write_text("project: research\nid: ignored-for-now\n")
    assert read_marker_project(marker) == "research"


def test_read_marker_missing_key_returns_none(tmp_path):
    """A marker may exist for other purposes (the future store id)."""
    marker = tmp_path / ".bm.yml"
    marker.write_text("id: abc123\n")
    assert read_marker_project(marker) is None


def test_read_marker_empty_file_returns_none(tmp_path):
    marker = tmp_path / ".bm.yml"
    marker.write_text("")
    assert read_marker_project(marker) is None


def test_read_marker_malformed_yaml_raises(tmp_path):
    marker = tmp_path / ".bm.yml"
    marker.write_text("project: [unclosed\n")
    with pytest.raises(MarkerError, match="Unreadable"):
        read_marker_project(marker)


def test_read_marker_non_mapping_raises(tmp_path):
    marker = tmp_path / ".bm.yml"
    marker.write_text("- a\n- b\n")
    with pytest.raises(MarkerError, match="mapping"):
        read_marker_project(marker)


@pytest.mark.parametrize("content", ["project:\n", "project: '   '\n", "project: [1, 2]\n"])
def test_read_marker_bad_project_value_raises(tmp_path, content):
    marker = tmp_path / ".bm.yml"
    marker.write_text(content)
    with pytest.raises(MarkerError, match="non-empty string"):
        read_marker_project(marker)


# --- resolve_cli_project chain ---


def test_resolve_explicit_wins(tmp_path):
    (tmp_path / ".bm.yml").write_text("project: from-marker\n")
    assert resolve_cli_project("explicit", tmp_path) == "explicit"


def test_resolve_marker_beats_default(tmp_path, write_registry_file):
    write_registry_file(
        {"marker-project": str(tmp_path), "other-default": "/tmp/other"},
        default="other-default",
    )
    (tmp_path / ".bm.yml").write_text("project: marker-project\n")
    assert resolve_cli_project(None, tmp_path) == "marker-project"


def test_resolve_unregistered_marker_raises(tmp_path, write_registry_file):
    write_registry_file({"other": "/tmp/other"}, default="other")
    (tmp_path / ".bm.yml").write_text("project: nope\n")
    with pytest.raises(MarkerError, match=r"names 'nope'") as exc_info:
        resolve_cli_project(None, tmp_path)
    assert str(tmp_path / ".bm.yml") in str(exc_info.value)


def test_resolve_no_marker_falls_back_to_default(tmp_path, write_registry_file):
    write_registry_file({"main": str(tmp_path)}, default="main")
    assert resolve_cli_project(None, tmp_path) == "main"


def test_resolve_marker_without_project_key_falls_back(tmp_path, write_registry_file):
    write_registry_file({"main": str(tmp_path)}, default="main")
    (tmp_path / ".bm.yml").write_text("id: abc123\n")
    assert resolve_cli_project(None, tmp_path) == "main"
