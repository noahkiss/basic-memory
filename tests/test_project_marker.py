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
    read_marker_only_here,
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


# --- find_marker boundaries (GAPS U29) ---


def test_find_marker_stops_at_a_git_boundary(tmp_path):
    """A marker above a repo must not capture the repo (tnd-b4f4eu4m)."""
    (tmp_path / ".bm.yml").write_text("project: workspace\n")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "src" / "deep"
    nested.mkdir(parents=True)
    assert find_marker(nested) is None


def test_find_marker_searches_the_repo_root_itself(tmp_path):
    """The boundary is inclusive: the repo's own marker is the normal case."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".bm.yml").write_text("project: repo\n")
    nested = repo / "src"
    nested.mkdir()
    assert find_marker(nested) == repo / ".bm.yml"


def test_find_marker_treats_a_git_file_as_a_boundary(tmp_path):
    """Worktrees and submodules leave a `.git` *file*; they bound the walk too."""
    (tmp_path / ".bm.yml").write_text("project: workspace\n")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /elsewhere\n")
    assert find_marker(worktree) is None


def test_find_marker_never_searches_home_or_above(tmp_path, monkeypatch):
    """$HOME is not a project, and a marker there would capture every repo."""
    home = tmp_path / "home"
    (home / "develop" / "sub").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (home / ".bm.yml").write_text("project: dotfiles\n")
    assert find_marker(home / "develop" / "sub") is None
    assert find_marker(home) is None


def test_find_marker_home_git_is_a_ceiling_not_a_boundary(tmp_path, monkeypatch):
    """A dotfiles-style ~/.git never makes $HOME a searchable repo root."""
    home = tmp_path / "home"
    develop = home / "develop"
    develop.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (home / ".git").mkdir()
    (home / ".bm.yml").write_text("project: dotfiles\n")
    (develop / ".bm.yml").write_text("project: workspace\n")
    nested = develop / "notes"
    nested.mkdir()
    # Below $HOME the walk still works; at $HOME it stops without looking.
    assert find_marker(nested) == develop / ".bm.yml"


def test_find_marker_without_git_still_walks_a_plain_tree(tmp_path):
    """No repo boundary in the chain: the walk climbs as it always has."""
    (tmp_path / ".bm.yml").write_text("project: research\n")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert find_marker(nested) == tmp_path / ".bm.yml"


# --- find_marker and `scope: here` (GAPS U40) ---


def test_find_marker_scope_here_applies_in_its_own_directory(tmp_path):
    """The narrowed marker is a normal marker where it sits — that is the point."""
    workspace = tmp_path / "develop"
    workspace.mkdir()
    (workspace / ".bm.yml").write_text("project: workspace\nid: abc-123\nscope: here\n")
    assert find_marker(workspace) == workspace / ".bm.yml"


def test_find_marker_scope_here_is_invisible_from_a_subdirectory(tmp_path):
    """A scratch folder under the workspace has no repo of its own to stop the walk."""
    workspace = tmp_path / "develop"
    scratch = workspace / "scratch"
    scratch.mkdir(parents=True)
    (workspace / ".bm.yml").write_text("project: workspace\nid: abc-123\nscope: here\n")
    assert find_marker(scratch) is None


def test_find_marker_scope_here_is_transparent_not_a_stop(tmp_path):
    """Skipping the marker means climbing past it, not ending the walk."""
    workspace = tmp_path / "ws"
    middle = workspace / "mid"
    leaf = middle / "leaf"
    leaf.mkdir(parents=True)
    (workspace / ".bm.yml").write_text("project: workspace\n")
    (middle / ".bm.yml").write_text("project: middle\nid: abc-123\nscope: here\n")
    assert find_marker(leaf) == workspace / ".bm.yml"


def test_find_marker_scope_here_still_stops_at_its_own_git(tmp_path):
    """The repo boundary is checked after the marker, skipped marker or not.

    Positive control for the transparency test above: the outer marker *would*
    be found if the skip also skipped the `.git` in the same directory.
    """
    workspace = tmp_path / "ws"
    repo = workspace / "repo"
    nested = repo / "src"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()
    (workspace / ".bm.yml").write_text("project: workspace\n")
    (repo / ".bm.yml").write_text("project: repo\nid: abc-123\nscope: here\n")
    assert find_marker(nested) is None


# --- read_marker_only_here (GAPS U40) ---


@pytest.mark.parametrize(
    "content",
    ["project: research\n", "project: research\nscope: tree\n"],
)
def test_read_marker_only_here_is_false_by_default(tmp_path, content):
    """An absent key means `tree`, which is what every pre-U40 marker means."""
    marker = tmp_path / ".bm.yml"
    marker.write_text(content)
    assert read_marker_only_here(marker) is False


def test_read_marker_only_here_reads_the_narrowed_scope(tmp_path):
    marker = tmp_path / ".bm.yml"
    marker.write_text("project: research\nscope: here\n")
    assert read_marker_only_here(marker) is True


def test_find_marker_unknown_scope_raises_in_own_directory(tmp_path):
    """Strictness is position-independent: the start directory's marker is parsed too."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".bm.yml").write_text("project: research\nscope: everywhere\n")
    with pytest.raises(MarkerError, match="everywhere"):
        find_marker(workspace)


def test_read_marker_unknown_scope_raises(tmp_path):
    """A misspelled scope must not silently widen the marker back to the tree."""
    marker = tmp_path / ".bm.yml"
    marker.write_text("project: research\nscope: everywhere\n")
    with pytest.raises(MarkerError, match="everywhere") as exc_info:
        read_marker_only_here(marker)
    assert str(marker) in str(exc_info.value)


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
    # Nested below tmp_path: the registry fixture makes tmp_path the fake $HOME,
    # which the walk never searches (GAPS U29).
    work = tmp_path / "work"
    work.mkdir()
    write_registry_file({"named": str(work)}, default="named")
    (work / ".bm.yml").write_text("project: named\nid: some-other-projects-id\n")
    assert resolve_cli_project(None, work) == "named"


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


def test_render_marker_only_here_adds_a_third_line():
    """The narrowed shape is three fixed lines, still greppable one at a time."""
    assert render_marker("research", "abc-123", only_here=True) == (
        "project: research\nid: abc-123\nscope: here\n"
    )


def test_write_marker_preserves_an_existing_scope(tmp_path):
    """The retrofit must not widen a marker as a side effect of filling in the id."""
    (tmp_path / ".bm.yml").write_text("project: research\nscope: here\n")
    marker = write_marker(tmp_path, "research", "abc-123")
    assert read_marker_id(marker) == "abc-123"
    assert read_marker_only_here(marker) is True


def test_write_marker_widens_a_narrowed_marker_only_when_told(tmp_path):
    """Positive control for the preserve path: False is how the scope comes off."""
    (tmp_path / ".bm.yml").write_text("project: research\nscope: here\n")
    marker = write_marker(tmp_path, "research", "abc-123", only_here=False)
    assert marker.read_text() == "project: research\nid: abc-123\n"


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
    # Nested below tmp_path — the fake $HOME — which the walk never searches.
    work = tmp_path / "work"
    work.mkdir()
    write_registry_file(
        {"marker-project": str(work), "other-default": "/tmp/other"},
        default="other-default",
    )
    (work / ".bm.yml").write_text("project: marker-project\n")
    assert resolve_cli_project(None, work) == "marker-project"


def test_resolve_unregistered_marker_raises(tmp_path, write_registry_file):
    work = tmp_path / "work"
    work.mkdir()
    write_registry_file({"other": "/tmp/other"}, default="other")
    (work / ".bm.yml").write_text("project: nope\n")
    with pytest.raises(MarkerError, match=r"names 'nope'") as exc_info:
        resolve_cli_project(None, work)
    assert str(work / ".bm.yml") in str(exc_info.value)


def test_resolve_no_marker_falls_back_to_default(tmp_path, write_registry_file):
    write_registry_file({"main": str(tmp_path)}, default="main")
    assert resolve_cli_project(None, tmp_path) == "main"


def test_resolve_scope_here_marker_covers_its_own_directory_only(tmp_path, write_registry_file):
    """The write chain reads the same walk: narrowed here, default one level down.

    A write in the workspace directory itself lands in the workspace project; a
    write in a scratch folder under it falls to the registry default, exactly as
    it did before the workspace was ever marked (GAPS U40).
    """
    workspace = tmp_path / "develop"
    scratch = workspace / "scratch"
    scratch.mkdir(parents=True)
    write_registry_file(
        {"workspace": str(tmp_path / "store"), "main": str(tmp_path / "other")}, default="main"
    )
    (workspace / ".bm.yml").write_text("project: workspace\nscope: here\n")

    assert resolve_cli_project(None, workspace) == "workspace"
    assert resolve_cli_project(None, scratch) == "main"


def test_resolve_marker_without_project_key_falls_back(tmp_path, write_registry_file):
    write_registry_file({"main": str(tmp_path)}, default="main")
    (tmp_path / ".bm.yml").write_text("id: abc123\n")
    assert resolve_cli_project(None, tmp_path) == "main"


# --- repo_identity (GAPS U36) ---


def _git(*args: str) -> None:
    import subprocess

    subprocess.run(["git", *args], check=True, capture_output=True)


def test_repo_identity_reads_origin_and_strips_dot_git(tmp_path):
    from basic_memory.project_marker import repo_identity

    _git("init", "-q", str(tmp_path))
    _git("-C", str(tmp_path), "remote", "add", "origin", "https://example.com/owner/repo.git")

    assert repo_identity(tmp_path) == "https://example.com/owner/repo"


def test_repo_identity_without_a_remote_is_none(tmp_path):
    """A repo with no origin has no cross-machine identity — None, not an error."""
    from basic_memory.project_marker import repo_identity

    _git("init", "-q", str(tmp_path))

    assert repo_identity(tmp_path) is None


def test_repo_identity_outside_a_repo_is_none(tmp_path):
    from basic_memory.project_marker import repo_identity

    assert repo_identity(tmp_path) is None
