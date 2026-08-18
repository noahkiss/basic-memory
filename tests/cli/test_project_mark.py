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
from basic_memory.project_marker import read_marker_id, read_marker_project

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
