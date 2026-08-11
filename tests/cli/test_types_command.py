"""Tests for `bm types` — the record-type explainer (GAPS W19 item 4).

The behaviour under test is that the *list* of types comes from the project's
live `vocabulary.yml` while the *prose* comes from the glossary. Two tests carry
that guarantee in both directions: a type the glossary does not know still
appears, and a type the glossary knows but the project omits does not.

CliRunner folds loguru's stderr into `result.output`, so these assert on
containment and on `result.stdout` rather than on whole-output equality.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

import basic_memory.cli.commands.types as types_cmd
from basic_memory.cli.app import app
from basic_memory.cli.direct import ProjectRef

EXTERNAL_ID = "11111111-1111-1111-1111-111111111111"
PROJECT = "alpha"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def stub_project(monkeypatch) -> None:
    """Answer the project lookup without a database.

    The direct helper is the only thing `bm types` needs the DB for, so stubbing
    it here keeps these tests on the rendering behaviour they are about. The
    import guard covers the real path end to end.
    """

    async def fake_direct_project_ref(project_name: str | None) -> ProjectRef:
        if project_name not in (None, PROJECT):
            raise ValueError(f"Project not found: '{project_name}'")
        return ProjectRef(name=PROJECT, external_id=EXTERNAL_ID)

    monkeypatch.setattr(types_cmd, "direct_project_ref", fake_direct_project_ref)


@pytest.fixture
def write_vocabulary(config_home: Path):
    """Write a `vocabulary.yml` into the store dir the stubbed project owns."""

    def _write(content: str) -> Path:
        from basic_memory.vocabulary.model import vocabulary_path

        path = vocabulary_path(EXTERNAL_ID)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    return _write


FULL_VOCABULARY = """
types:    [task, guide, finding, profile, state, inbox]
statuses: [open, doing, blocked, done, dropped]
areas:    [ops]
review_months: 12
"""


def test_ungoverned_project_is_a_result(runner, config_home, stub_project):
    """No vocabulary file means "not governed" — a result, not an error."""
    result = runner.invoke(app, ["types", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    assert f"Project '{PROJECT}' declares no record vocabulary." in result.stdout
    # An absent file must never be rendered as the default six.
    assert "task" not in result.stdout
    # Affordance: name the file that would change the answer.
    assert "vocabulary.yml to declare one." in result.stdout


def test_governed_project_lists_every_declared_type(
    runner, config_home, stub_project, write_vocabulary
):
    """Every type in the file appears, each with its picking question."""
    write_vocabulary(FULL_VOCABULARY)

    result = runner.invoke(app, ["types", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    for heading in (
        "task — do it",
        "guide — consult it",
        "finding — learned it",
        "profile — refer to it",
        "state — how things are",
        "inbox — can't tell",
    ):
        assert heading in result.stdout
    assert "6 types" in result.stdout


def test_type_the_glossary_does_not_know_still_appears(
    runner, config_home, stub_project, write_vocabulary
):
    """The anti-drift guarantee: the live file decides what is listed.

    A human adding `runbook` to their vocabulary must see it here under its bare
    name. Dropping it would make `bm types` disagree with the file that governs
    the project — the exact failure W19 item 4 names.
    """
    write_vocabulary("types: [task, runbook]\n")

    result = runner.invoke(app, ["types", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    assert "runbook" in result.stdout
    assert "bm carries no description for it." in result.stdout
    # No invented picking question and no invented field list.
    assert "runbook —" not in result.stdout
    assert "2 types" in result.stdout


def test_type_the_vocabulary_omits_does_not_appear(
    runner, config_home, stub_project, write_vocabulary
):
    """The same guarantee in the other direction: the glossary never adds types."""
    write_vocabulary("types: [task, guide, finding, profile, state]\n")

    result = runner.invoke(app, ["types", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    assert "inbox" not in result.stdout
    assert "can't tell" not in result.stdout
    assert "5 types" in result.stdout


def test_declared_fields_show_their_kind_and_enum_values(
    runner, config_home, stub_project, write_vocabulary
):
    """A declared extra prints its kind, and an enum prints the values it allows."""
    write_vocabulary(
        "types: [profile]\n"
        "fields:\n"
        "  host-role:\n"
        "    kind: enum\n"
        "    values: [docker, vm, bare-metal]\n"
        "  owner: string\n"
    )

    result = runner.invoke(app, ["types", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    assert "host-role  enum: docker, vm, bare-metal" in result.stdout
    assert "owner      string" in result.stdout


def test_quiet_drops_the_affordance_and_keeps_the_payload(
    runner, config_home, stub_project, write_vocabulary
):
    """Contract rule 7: --quiet leaves the payload alone."""
    write_vocabulary(FULL_VOCABULARY)

    result = runner.invoke(app, ["types", "--project", PROJECT, "--quiet"])

    assert result.exit_code == 0, result.output
    assert "task — do it" in result.stdout
    assert result.stdout.strip().splitlines()[-1] == "6 types"
    assert "Edit " not in result.stdout


def test_unknown_project_fails_on_stderr(runner, config_home, stub_project):
    """An unscopeable request is a failure: one line on stderr, nothing on stdout."""
    result = runner.invoke(app, ["types", "--project", "missing"])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Project not found: 'missing'" in result.stderr
