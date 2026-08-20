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

    async def fake_direct_project_refs(project_name: str | None) -> list[ProjectRef]:
        if project_name not in (None, PROJECT):
            raise ValueError(f"Project not found: '{project_name}'")
        return [ProjectRef(name=PROJECT, external_id=EXTERNAL_ID)]

    monkeypatch.setattr(types_cmd, "direct_project_refs", fake_direct_project_refs)


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


def test_relations_are_listed_with_what_each_one_claims(
    runner, config_home, stub_project, write_vocabulary
):
    """GAPS U14: `--rel` types are vocabulary, so the verb that teaches it lists them.

    `FULL_VOCABULARY` declares no `relations:` key, which is the state every file
    written before U14 is in — so this also proves the default three are what an
    older file means.
    """
    write_vocabulary(FULL_VOCABULARY)

    result = runner.invoke(app, ["types", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    assert "relations" in result.stdout
    assert "derived_from  This record came out of that one" in result.stdout
    assert "relates_to    These two belong together" in result.stdout
    # `part_of` joined the defaults with the plan type (GAPS U38).
    assert "part_of" in result.stdout
    assert "a stage task inside a plan" in result.stdout


def test_a_relation_the_glossary_does_not_know_still_appears(
    runner, config_home, stub_project, write_vocabulary
):
    """The anti-drift guarantee again: the live file decides what is listed."""
    write_vocabulary("types: [task]\nrelations: [blocks]\n")

    result = runner.invoke(app, ["types", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    assert "  blocks" in result.stdout
    assert "relates_to" not in result.stdout


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


# --- Scope C: an unmarked directory reports every project ---


@pytest.fixture
def stub_two_projects(monkeypatch):
    """Answer the project lookup with two projects, so a roll-up has sections."""
    second = "beta"
    second_id = "22222222-2222-2222-2222-222222222222"

    async def fake_direct_project_refs(project_name: str | None) -> list[ProjectRef]:
        refs = [
            ProjectRef(name=PROJECT, external_id=EXTERNAL_ID),
            ProjectRef(name=second, external_id=second_id),
        ]
        if project_name is None:
            return refs
        matched = [ref for ref in refs if ref.name == project_name]
        if not matched:
            raise ValueError(f"Project not found: '{project_name}'")
        return matched

    monkeypatch.setattr(types_cmd, "direct_project_refs", fake_direct_project_refs)
    return second_id


def test_unscoped_types_prints_one_section_per_project(
    runner, config_home, stub_two_projects, write_vocabulary, monkeypatch, tmp_path
):
    """No --project and no marker reports every project, not the default one.

    A vocabulary report that silently covered one of two projects would teach an
    agent the wrong rules for the other (GAPS W5-C).
    """
    monkeypatch.chdir(tmp_path)
    write_vocabulary(FULL_VOCABULARY)

    result = runner.invoke(app, ["types", "--quiet"])

    assert result.exit_code == 0, result.output
    assert f"Record types for project '{PROJECT}'" in result.stdout
    # The second project declares nothing, and that is a result, not an error.
    assert "Project 'beta' declares no record vocabulary" in result.stdout
    # Each section names the file it came from, because one trailing line cannot.
    assert "vocabulary.yml)" in result.stdout


def test_unscoped_types_names_no_single_file_to_edit(
    runner, config_home, stub_two_projects, write_vocabulary, monkeypatch, tmp_path
):
    """The affordance cannot name one path when the payload covered several."""
    monkeypatch.chdir(tmp_path)
    write_vocabulary(FULL_VOCABULARY)

    result = runner.invoke(app, ["types"])

    assert result.exit_code == 0, result.output
    assert "Edit a project's vocabulary.yml to add a type" in result.stdout


# --- Status prose (GAPS U23) ---


def test_plan_lists_with_its_summary_and_fields(
    runner, config_home, stub_project, write_vocabulary
):
    """The eighth type teaches itself the way the first seven do (GAPS U38)."""
    write_vocabulary("types: [task, plan]\n")

    result = runner.invoke(app, ["types", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    assert "plan — follow it" in result.stdout
    assert "A multi-stage effort." in result.stdout
    assert "status" in result.stdout


def test_shelved_gets_a_line_of_prose(runner, config_home, stub_project, write_vocabulary):
    """`shelved` is the one status whose name does not say what it means."""
    write_vocabulary("types: [task]\nstatuses: [open, shelved, done]\n")

    result = runner.invoke(app, ["types", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    assert "open, shelved, done" in result.stdout
    assert "Parked — not in the current set, not dropped." in result.stdout
    assert "bm mark <id> open` revives it." in result.stdout


def test_statuses_that_speak_for_themselves_get_no_prose(
    runner, config_home, stub_project, write_vocabulary
):
    """Positive control for the test above, and the reason there is no prose table.

    A line under every status would be five lines a reader learns to skip, so a
    project that declares none of the explained names prints the bare list alone.
    """
    write_vocabulary("types: [task]\nstatuses: [open, doing, done]\n")

    result = runner.invoke(app, ["types", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    statuses = result.stdout.split("statuses\n", 1)[1]
    assert statuses.startswith("  open, doing, done\n\n")


def test_aliases_are_listed_with_their_targets(runner, config_home, stub_project, write_vocabulary):
    """The aliases section mirrors `statuses`: aligned names, one target each (GAPS U25)."""
    write_vocabulary("types: [task, finding]\naliases: {decision: finding, todo: task}\n")

    result = runner.invoke(app, ["types", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    assert "aliases" in result.stdout
    assert "decision" in result.stdout
    assert "an alias for finding" in result.stdout
    assert "an alias for task" in result.stdout


def test_default_aliases_appear_when_the_file_declares_none(
    runner, config_home, stub_project, write_vocabulary
):
    """A file with no `aliases:` key still reports the narrowed defaults —
    that is what the write path will actually accept."""
    write_vocabulary(FULL_VOCABULARY)

    result = runner.invoke(app, ["types", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    assert "an alias for finding" in result.stdout


def test_no_aliases_says_none_declared(runner, config_home, stub_project, write_vocabulary):
    """An explicit empty mapping prints the same honest line the other sections use."""
    write_vocabulary("types: [task]\naliases: {}\n")

    result = runner.invoke(app, ["types", "--project", PROJECT])

    assert result.exit_code == 0, result.output
    aliases_section = result.stdout.split("aliases\n", 1)[1]
    assert aliases_section.lstrip().startswith("(this project declares none)")
