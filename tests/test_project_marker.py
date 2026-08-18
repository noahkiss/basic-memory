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
    marker_conflict,
    read_marker_id,
    read_marker_project,
    render_marker,
    resolve_cli_project,
    write_marker,
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


# --- read_marker_id (GAPS U21) ---


def test_read_marker_id(tmp_path):
    marker = tmp_path / ".bm.yml"
    marker.write_text("project: research\nid: 12345678-1234-1234-1234-123456789012\n")
    assert read_marker_id(marker) == "12345678-1234-1234-1234-123456789012"


def test_read_marker_id_absent_returns_none(tmp_path):
    """Every marker written before U21 carries the name alone."""
    marker = tmp_path / ".bm.yml"
    marker.write_text("project: research\n")
    assert read_marker_id(marker) is None


def test_read_marker_id_bad_value_raises(tmp_path):
    marker = tmp_path / ".bm.yml"
    marker.write_text("project: research\nid: []\n")
    with pytest.raises(MarkerError, match="non-empty string"):
        read_marker_id(marker)


def test_marker_id_does_not_change_resolution(tmp_path, write_registry_file):
    """Resolution keys off `project:`. The id is recorded, not yet authoritative."""
    write_registry_file({"named": str(tmp_path)}, default="named")
    (tmp_path / ".bm.yml").write_text("project: named\nid: some-other-projects-id\n")
    assert resolve_cli_project(None, tmp_path) == "named"


# --- render_marker / write_marker / marker_conflict (GAPS U21) ---


def test_render_marker_is_two_plain_lines():
    """Shell consumers grep it line by line, so the shape is part of the contract."""
    assert render_marker("research", "abc-123") == "project: research\nid: abc-123\n"


def test_write_marker_writes_both_keys(tmp_path):
    marker = write_marker(tmp_path, "research", "abc-123")
    assert marker == tmp_path / ".bm.yml"
    assert read_marker_project(marker) == "research"
    assert read_marker_id(marker) == "abc-123"


def test_write_marker_retrofits_a_name_only_marker(tmp_path):
    """The U21 retrofit path: same project, so the file is rewritten with its id."""
    (tmp_path / ".bm.yml").write_text("project: research\n")
    marker = write_marker(tmp_path, "research", "abc-123")
    assert read_marker_id(marker) == "abc-123"


def test_write_marker_refuses_a_foreign_marker(tmp_path):
    (tmp_path / ".bm.yml").write_text("project: someone-else\n")
    with pytest.raises(MarkerError, match="already names project 'someone-else'"):
        write_marker(tmp_path, "research", "abc-123")
    # Refusing means refusing to write, not writing and then complaining.
    assert (tmp_path / ".bm.yml").read_text() == "project: someone-else\n"


def test_marker_conflict_reports_only_a_different_name(tmp_path):
    assert marker_conflict(tmp_path, "research") is None

    (tmp_path / ".bm.yml").write_text("project: research\n")
    assert marker_conflict(tmp_path, "research") is None

    (tmp_path / ".bm.yml").write_text("project: other\n")
    assert marker_conflict(tmp_path, "research") == "other"


def test_marker_conflict_ignores_a_marker_without_a_project_key(tmp_path):
    """A marker kept for other purposes claims no project, so nothing conflicts."""
    (tmp_path / ".bm.yml").write_text("id: abc-123\n")
    assert marker_conflict(tmp_path, "research") is None


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
