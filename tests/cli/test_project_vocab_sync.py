"""Tests for `bm project vocab-sync` — the explicit half of GAPS U39.

An untouched machine snapshot upgrades itself on the revalidation path; this
verb is the human act that brings a *hand-edited* vocabulary up to the current
defaults, additively. The verb is native: registry via the synchronous sqlite
path, file via the vocabulary module, no client anywhere.
"""

import pytest
import yaml
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.vocabulary.model import (
    DEFAULT_VOCABULARY,
    load_vocabulary,
    vocabulary_path,
)

# Importing registers project subcommands on the shared app instance.
import basic_memory.cli.commands.project  # noqa: F401


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def governed_dir(tmp_path, monkeypatch, write_registry_file, registry_external_id):
    """`research` registered and governed by a hand-edited, lagging vocabulary."""
    write_registry_file({"research": str(tmp_path / "store")}, default="research")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)

    path = vocabulary_path(registry_external_id("research"))
    path.parent.mkdir(parents=True, exist_ok=True)
    # One human declaration (`runbook`) keeps this out of snapshot territory;
    # the rest is the pre-plan generation, so plan and part_of are missing.
    path.write_text(
        yaml.safe_dump(
            {
                "types": ["runbook", "task", "guide", "finding", "profile", "state", "inbox"],
                "statuses": ["open", "doing", "blocked", "shelved", "done", "dropped"],
                "relations": ["relates_to", "derived_from", "supersedes"],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_sync_appends_the_missing_defaults(runner, governed_dir, registry_external_id):
    result = runner.invoke(app, ["project", "vocab-sync", "research", "--quiet"])

    assert result.exit_code == 0, result.output
    assert "added" in result.stdout
    assert "type plan" in result.stdout and "relation part_of" in result.stdout

    merged = load_vocabulary(registry_external_id("research"))
    assert merged is not None
    # The human's declaration keeps its place; the defaults arrive after.
    assert merged.types[0] == "runbook"
    assert "plan" in merged.types and "note" in merged.types
    assert "part_of" in merged.relations


def test_sync_twice_is_a_stated_no_op(runner, governed_dir):
    assert runner.invoke(app, ["project", "vocab-sync", "research", "--quiet"]).exit_code == 0

    result = runner.invoke(app, ["project", "vocab-sync", "research", "--quiet"])

    assert result.exit_code == 0, result.output
    assert "vocabulary already current" in result.stdout


def test_sync_on_a_snapshot_reaches_the_current_defaults(
    runner, governed_dir, registry_external_id
):
    """Running it on an untouched snapshot performs the same upgrade."""
    external_id = registry_external_id("research")
    vocabulary_path(external_id).write_text(
        yaml.safe_dump(
            {
                "types": ["task", "guide", "finding", "profile", "state", "inbox", "note"],
                "statuses": ["open", "doing", "blocked", "shelved", "done", "dropped"],
                "areas": [],
                "relations": ["relates_to", "derived_from", "supersedes"],
                "review_months": 12,
                "fields": {},
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["project", "vocab-sync", "research", "--quiet"])

    assert result.exit_code == 0, result.output
    assert load_vocabulary(external_id) == DEFAULT_VOCABULARY


def test_sync_refuses_an_ungoverned_project(runner, governed_dir, registry_external_id):
    vocabulary_path(registry_external_id("research")).unlink()

    result = runner.invoke(app, ["project", "vocab-sync", "research", "--quiet"])

    assert result.exit_code == 1
    assert "not governed" in result.stderr


def test_sync_names_an_unknown_project(runner, governed_dir):
    result = runner.invoke(app, ["project", "vocab-sync", "nonesuch", "--quiet"])

    assert result.exit_code == 1
    assert "not a registered project" in result.stderr


def test_sync_resolves_the_project_from_the_marker(runner, governed_dir, tmp_path):
    """No name argument: the `.bm.yml` above cwd decides, like every write."""
    (tmp_path / "work" / ".bm.yml").write_text("project: research\n")

    result = runner.invoke(app, ["project", "vocab-sync", "--quiet"])

    assert result.exit_code == 0, result.output
    assert "added" in result.stdout
