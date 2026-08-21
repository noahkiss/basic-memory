"""Tests for `bm project mark` — the `.bm.yml` retrofit path (GAPS U21).

A marker carries the project's name and its store id. The id is what lets a
statusline read `store/<id>/headline.md` without paying the 0.15 s floor for a
`bm` invocation, and every marker written before U21 carries the name alone.
`bm project mark` is how those gain their id.

The verb is native: it reads two columns of one registry row through the
synchronous sqlite path, so nothing here mocks a client.
"""

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.project_marker import read_marker_id, read_marker_only_here, read_marker_project

# Importing registers project subcommands on the shared app instance.
import basic_memory.cli.commands.project  # noqa: F401


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def work_dir(tmp_path, monkeypatch, write_registry_file):
    """A working directory with `research` registered and cwd pointed at it."""
    write_registry_file({"research": str(tmp_path / "store")}, default="research")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


def test_mark_writes_both_keys(runner, work_dir, registry_external_id):
    result = runner.invoke(app, ["project", "mark", "research"])

    assert result.exit_code == 0, result.output
    marker = work_dir / ".bm.yml"
    assert read_marker_project(marker) == "research"
    assert read_marker_id(marker) == registry_external_id("research")
    assert f"id: {registry_external_id('research')}" in result.stdout


def test_mark_retrofits_a_name_only_marker(runner, work_dir, registry_external_id):
    """The reason the verb exists: no argument, and the id appears."""
    (work_dir / ".bm.yml").write_text("project: research\n")

    result = runner.invoke(app, ["project", "mark"])

    assert result.exit_code == 0, result.output
    assert read_marker_id(work_dir / ".bm.yml") == registry_external_id("research")


def test_mark_resolves_a_permalink_to_the_registered_name(
    runner, tmp_path, monkeypatch, write_registry_file
):
    """The marker records the registry's spelling, so resolution keeps working."""
    write_registry_file({"My Research": str(tmp_path / "store")}, default="My Research")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    result = runner.invoke(app, ["project", "mark", "my-research"])

    assert result.exit_code == 0, result.output
    assert read_marker_project(work / ".bm.yml") == "My Research"


def test_mark_refuses_a_foreign_marker(runner, work_dir):
    (work_dir / ".bm.yml").write_text("project: someone-else\n")

    result = runner.invoke(app, ["project", "mark", "research"])

    assert result.exit_code == 1
    assert "already names project 'someone-else'" in result.stderr
    assert (work_dir / ".bm.yml").read_text() == "project: someone-else\n"
    assert result.stdout == ""


def test_mark_on_an_unknown_project_errors(runner, work_dir):
    result = runner.invoke(app, ["project", "mark", "nope"])

    assert result.exit_code == 1
    assert "not a registered project" in result.stderr
    assert not (work_dir / ".bm.yml").exists()


def test_mark_without_a_name_or_a_marker_errors(runner, work_dir):
    """Nothing to default from, so the verb asks for the name instead of guessing."""
    result = runner.invoke(app, ["project", "mark"])

    assert result.exit_code == 1
    assert "bm project mark <name>" in result.stderr
    assert not (work_dir / ".bm.yml").exists()


def test_mark_does_not_default_from_a_parent_marker(runner, work_dir, monkeypatch):
    """A parent's marker is not this directory's to adopt (see `mark_project`).

    Defaulting from a walked-up marker would silently give a subdirectory a
    project the caller never named — and `find_marker` already makes the parent
    cover it, so the subdirectory marker would be a duplicate to keep in sync.
    """
    (work_dir / ".bm.yml").write_text("project: research\n")
    nested = work_dir / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["project", "mark"])

    assert result.exit_code == 1
    assert "bm project mark <name>" in result.stderr
    assert not (nested / ".bm.yml").exists()


# --- `--only-here`: the marker that claims one directory (GAPS U40) ---


def test_mark_only_here_writes_the_scope_and_says_so(runner, work_dir, registry_external_id):
    """The flag's whole job is visible in the file and in the output line."""
    result = runner.invoke(app, ["project", "mark", "research", "--only-here"])

    assert result.exit_code == 0, result.output
    marker = work_dir / ".bm.yml"
    assert marker.read_text() == (
        f"project: research\nid: {registry_external_id('research')}\nscope: here\n"
    )
    assert "(only here)" in result.stdout


def test_mark_without_the_flag_keeps_an_existing_scope(runner, work_dir, registry_external_id):
    """The retrofit path must not widen a marker the human narrowed."""
    (work_dir / ".bm.yml").write_text("project: research\nscope: here\n")

    result = runner.invoke(app, ["project", "mark"])

    assert result.exit_code == 0, result.output
    marker = work_dir / ".bm.yml"
    assert read_marker_id(marker) == registry_external_id("research")
    assert read_marker_only_here(marker) is True
    assert "(only here)" in result.stdout


def test_mark_without_the_flag_leaves_a_plain_marker_plain(runner, work_dir):
    """Positive control: the scope line is the flag's doing, not the verb's."""
    result = runner.invoke(app, ["project", "mark", "research"])

    assert result.exit_code == 0, result.output
    assert "scope:" not in (work_dir / ".bm.yml").read_text()
    assert "(only here)" not in result.stdout


# --- repo identity capture and --if-repo-matches (GAPS U36) ---


REPO_URL = "https://example.com/owner/research"


def _git(*args: str) -> None:
    import subprocess

    subprocess.run(["git", *args], check=True, capture_output=True)


def _make_repo(directory, remote: str | None = REPO_URL) -> None:
    _git("init", "-q", str(directory))
    if remote is not None:
        _git("-C", str(directory), "remote", "add", "origin", remote)


def test_mark_records_the_repo_and_prints_it(runner, work_dir):
    """Marking is the moment the registry learns which repo the project is."""
    from basic_memory.project_registry import lookup_project_repo

    _make_repo(work_dir)

    result = runner.invoke(app, ["project", "mark", "research"])

    assert result.exit_code == 0, result.output
    assert f"repo: {REPO_URL}" in result.stdout
    assert lookup_project_repo("research") == REPO_URL


def test_mark_without_a_remote_records_nothing(runner, work_dir):
    from basic_memory.project_registry import lookup_project_repo

    _make_repo(work_dir, remote=None)

    result = runner.invoke(app, ["project", "mark", "research"])

    assert result.exit_code == 0, result.output
    assert "repo:" not in result.stdout
    assert lookup_project_repo("research") is None


def test_mark_refreshes_a_null_repo_on_the_retrofit_path(runner, work_dir):
    """A bare re-mark fills the repo in, the same way it fills the id in."""
    from basic_memory.project_registry import lookup_project_repo

    _make_repo(work_dir)
    (work_dir / ".bm.yml").write_text("project: research\n")

    result = runner.invoke(app, ["project", "mark"])

    assert result.exit_code == 0, result.output
    assert lookup_project_repo("research") == REPO_URL


def test_mark_warns_on_a_repo_mismatch_and_keeps_the_recorded_value(
    runner, tmp_path, monkeypatch, write_registry_file
):
    """Two directories claiming one project is for the human, not an overwrite."""
    from basic_memory.project_registry import lookup_project_repo

    recorded = "https://example.com/owner/original"
    write_registry_file(
        {"research": str(tmp_path / "store")}, default="research", repos={"research": recorded}
    )
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    _make_repo(work)

    result = runner.invoke(app, ["project", "mark", "research"])

    assert result.exit_code == 0, result.output
    assert "not overwritten" in result.stderr
    assert lookup_project_repo("research") == recorded
    # The marker itself still lands: the mismatch is a warning, not a refusal.
    assert read_marker_project(work / ".bm.yml") == "research"


def test_mark_matching_repo_stays_silent(runner, tmp_path, monkeypatch, write_registry_file):
    """Re-marking the same clone is the common case and must not nag."""
    write_registry_file(
        {"research": str(tmp_path / "store")}, default="research", repos={"research": REPO_URL}
    )
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    _make_repo(work)

    result = runner.invoke(app, ["project", "mark", "research"])

    assert result.exit_code == 0, result.output
    assert result.stderr == ""


def test_if_repo_matches_marks_a_fresh_clone_without_a_name(
    runner, tmp_path, monkeypatch, write_registry_file, registry_external_id
):
    """The session hook's path: the match supplies the name."""
    write_registry_file(
        {"research": str(tmp_path / "store")}, default="research", repos={"research": REPO_URL}
    )
    clone = tmp_path / "clone"
    clone.mkdir()
    monkeypatch.chdir(clone)
    _make_repo(clone)

    result = runner.invoke(app, ["project", "mark", "--if-repo-matches"])

    assert result.exit_code == 0, result.output
    assert read_marker_project(clone / ".bm.yml") == "research"
    assert read_marker_id(clone / ".bm.yml") == registry_external_id("research")


def test_if_repo_matches_without_a_match_exits_3(runner, work_dir):
    """No side effects: the hook falls through to its prompt on 3."""
    _make_repo(work_dir, remote="https://example.com/owner/unregistered")

    result = runner.invoke(app, ["project", "mark", "--if-repo-matches"])

    assert result.exit_code == 3
    assert not (work_dir / ".bm.yml").exists()


def test_if_repo_matches_without_a_remote_exits_3(runner, work_dir):
    _make_repo(work_dir, remote=None)

    result = runner.invoke(app, ["project", "mark", "--if-repo-matches"])

    assert result.exit_code == 3
    assert "no origin remote" in result.stderr
    assert not (work_dir / ".bm.yml").exists()


def test_if_repo_matches_with_two_claimants_exits_4(
    runner, tmp_path, monkeypatch, write_registry_file
):
    """Ambiguity is the human's: both names are listed, nothing is written."""
    write_registry_file(
        {"research": str(tmp_path / "a"), "research-fork": str(tmp_path / "b")},
        default="research",
        repos={"research": REPO_URL, "research-fork": REPO_URL},
    )
    clone = tmp_path / "clone"
    clone.mkdir()
    monkeypatch.chdir(clone)
    _make_repo(clone)

    result = runner.invoke(app, ["project", "mark", "--if-repo-matches"])

    assert result.exit_code == 4
    assert "research" in result.stderr and "research-fork" in result.stderr
    assert not (clone / ".bm.yml").exists()


def test_if_repo_matches_with_a_name_filters_to_it(
    runner, tmp_path, monkeypatch, write_registry_file
):
    """A name plus the flag asserts both: this project, and this repo."""
    write_registry_file(
        {"research": str(tmp_path / "a"), "research-fork": str(tmp_path / "b")},
        default="research",
        repos={"research": REPO_URL, "research-fork": REPO_URL},
    )
    clone = tmp_path / "clone"
    clone.mkdir()
    monkeypatch.chdir(clone)
    _make_repo(clone)

    result = runner.invoke(app, ["project", "mark", "research-fork", "--if-repo-matches"])

    assert result.exit_code == 0, result.output
    assert read_marker_project(clone / ".bm.yml") == "research-fork"


def test_project_model_carries_the_repo_column():
    """Schema guard: the column exists, nullable, alongside its migration."""
    from pathlib import Path as _Path

    import basic_memory
    from basic_memory.models.project import Project

    column = Project.__table__.columns["repo"]
    assert column.nullable is True

    # `basic_memory.alembic` is a namespace package (no __init__), so the
    # versions directory is resolved from the parent package's file.
    versions = _Path(basic_memory.__file__).parent / "alembic" / "versions"
    migration = versions / "p9k0l1m2n3o4_add_project_repo.py"
    assert migration.is_file()
    text = migration.read_text(encoding="utf-8")
    assert 'down_revision: Union[str, None] = "o8j9k0l1m2n3"' in text
