"""Tests for `bm project adopt` — arrival on a machine another VCS delivered notes to."""

from contextlib import asynccontextmanager

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.mcp.clients.project import ProjectClient
from basic_memory.schemas.project_info import ProjectAdoptResponse

# Importing registers project subcommands on the shared app instance.
import basic_memory.cli.commands.project  # noqa: F401

# `project` imports the MCP client graph inside the function body, not at
# module scope, so CLI startup stays off it (GAPS.md T30). Patch the source
# module, which the function-local import resolves against at call time.
import basic_memory.mcp.async_client as async_client_module
from basic_memory.project_marker import read_marker_id, read_marker_project


ADOPTED_ID = "12345678-1234-1234-1234-123456789012"


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def stub_adopt(monkeypatch):
    """Answer the two client calls `adopt` makes, and record what they received.

    Returns the recorder: ``recorder.adopted`` is the list of adopt payloads and
    ``recorder.indexed`` the list of ``(external_id, run_in_background)`` pairs.
    The action the response carries is settable, because the command's one output
    line is the thing that has to distinguish the four of them.
    """

    class Recorder:
        def __init__(self) -> None:
            self.adopted: list[dict] = []
            self.indexed: list[tuple[str, bool]] = []
            self.action = "registered"
            self.path = "/skills/example/.bm"

    recorder = Recorder()

    @asynccontextmanager
    async def fake_get_client():
        yield object()

    async def fake_adopt_project(self, adopt_data):
        recorder.adopted.append(adopt_data)
        return ProjectAdoptResponse.model_validate(
            {
                "action": recorder.action,
                "name": adopt_data["name"],
                "external_id": ADOPTED_ID,
                "path": recorder.path,
            }
        )

    async def fake_index(self, project_external_id, force_full=False, run_in_background=True):
        recorder.indexed.append((project_external_id, run_in_background))
        return {
            "total_files": 12,
            "enqueued_files": 12,
            "enqueued_batches": 1,
            "deleted_files": 0,
        }

    monkeypatch.setattr(async_client_module, "get_client", fake_get_client)
    monkeypatch.setattr(ProjectClient, "adopt_project", fake_adopt_project)
    monkeypatch.setattr(ProjectClient, "index", fake_index)
    return recorder


def test_project_adopt_registers_the_delivered_directory(
    runner, config_home, stub_adopt, monkeypatch, tmp_path
):
    """The payload names `<cwd>/.bm`, unresolved, and the marker lands above it.

    The directory here is yadm's link mode: `.bm` is a symlink to the class
    alternate `.bm##class.home`. The recorded path must stay the literal `.bm`,
    because the target's name differs on a machine of another class.
    """
    skill = tmp_path / "skills" / "example"
    (skill / "references").mkdir(parents=True)
    (skill / ".bm##class.home").mkdir()
    (skill / ".bm").symlink_to(skill / ".bm##class.home")
    monkeypatch.chdir(skill)
    stub_adopt.path = (skill / ".bm").as_posix()

    result = runner.invoke(app, ["project", "adopt", "example"])

    assert result.exit_code == 0, result.output
    assert stub_adopt.adopted == [{"name": "example", "path": (skill / ".bm").as_posix()}]
    assert "class.home" not in stub_adopt.adopted[0]["path"]
    # Indexed in the foreground: the delivered records have to be searchable
    # when the command returns, and the count is half of what it reports.
    assert stub_adopt.indexed == [(ADOPTED_ID, False)]
    # The marker sits at the skill root, not inside `.bm/`: a `bm` run from
    # `references/` has to mean the skill, which is what tree scope gives.
    marker = skill / ".bm.yml"
    assert read_marker_project(marker) == "example"
    assert read_marker_id(marker) == ADOPTED_ID
    assert "scope: here" not in marker.read_text()
    assert not (skill / ".bm" / ".bm.yml").exists()
    assert result.stdout.strip() == (
        f"registered 'example' at {(skill / '.bm').as_posix()}; indexed 12 of 12 files"
    )


def test_project_adopt_refuses_when_the_notes_have_not_arrived(
    runner, config_home, stub_adopt, monkeypatch, tmp_path
):
    """No `.bm/` means yadm has not delivered it — creating one would collide."""
    skill = tmp_path / "skills" / "example"
    skill.mkdir(parents=True)
    monkeypatch.chdir(skill)

    result = runner.invoke(app, ["project", "adopt", "example"])

    assert result.exit_code == 1, result.output
    assert "is not a directory" in result.output
    assert stub_adopt.adopted == []
    assert not (skill / ".bm.yml").exists()
    # Contract rule 6: nothing lands on stdout on the error path.
    assert result.stdout == ""


def test_project_adopt_refuses_a_bm_that_is_not_a_directory(
    runner, config_home, stub_adopt, monkeypatch, tmp_path
):
    """Positive control for the test above: a file called `.bm` is not delivery."""
    skill = tmp_path / "skills" / "example"
    skill.mkdir(parents=True)
    (skill / ".bm").write_text("not a directory\n")
    monkeypatch.chdir(skill)

    result = runner.invoke(app, ["project", "adopt", "example"])

    assert result.exit_code == 1, result.output
    assert "is not a directory" in result.output
    assert stub_adopt.adopted == []


def test_project_adopt_defaults_the_name_from_the_marker(
    runner, config_home, stub_adopt, monkeypatch, tmp_path
):
    """A re-run in a marked directory needs no argument — `mark` defaults it the same way."""
    skill = tmp_path / "skills" / "example"
    (skill / ".bm").mkdir(parents=True)
    (skill / ".bm.yml").write_text(f"project: example\nid: {ADOPTED_ID}\n")
    monkeypatch.chdir(skill)
    stub_adopt.action = "unchanged"
    stub_adopt.path = (skill / ".bm").as_posix()

    result = runner.invoke(app, ["project", "adopt"])

    assert result.exit_code == 0, result.output
    assert stub_adopt.adopted == [{"name": "example", "path": (skill / ".bm").as_posix()}]
    assert result.stdout.strip() == (
        f"'example' was already adopted at {(skill / '.bm').as_posix()}; indexed 12 of 12 files"
    )


def test_project_adopt_without_a_name_or_a_marker_asks_for_one(
    runner, config_home, stub_adopt, monkeypatch, tmp_path
):
    """Nothing here names a project, and adopt never guesses one from the directory."""
    skill = tmp_path / "skills" / "example"
    (skill / ".bm").mkdir(parents=True)
    monkeypatch.chdir(skill)

    result = runner.invoke(app, ["project", "adopt"])

    assert result.exit_code == 1, result.output
    assert "bm project adopt <name>" in result.output
    assert stub_adopt.adopted == []
    assert not (skill / ".bm.yml").exists()


def test_project_adopt_refuses_a_foreign_marker(
    runner, config_home, stub_adopt, monkeypatch, tmp_path
):
    """That tree belongs to something else, and the refusal comes before the adopt."""
    skill = tmp_path / "skills" / "example"
    (skill / ".bm").mkdir(parents=True)
    (skill / ".bm.yml").write_text("project: someone-else\n")
    monkeypatch.chdir(skill)

    result = runner.invoke(app, ["project", "adopt", "example"])

    assert result.exit_code == 1, result.output
    assert "already names project 'someone-else'" in result.output
    assert stub_adopt.adopted == []
    assert (skill / ".bm.yml").read_text() == "project: someone-else\n"
    assert result.stdout == ""


def test_project_adopt_reports_a_repointed_project(
    runner, config_home, stub_adopt, monkeypatch, tmp_path
):
    """The four actions are what the one output line has to tell apart."""
    skill = tmp_path / "skills" / "renamed"
    (skill / ".bm").mkdir(parents=True)
    monkeypatch.chdir(skill)
    stub_adopt.action = "repointed"
    stub_adopt.path = (skill / ".bm").as_posix()

    result = runner.invoke(app, ["project", "adopt", "example"])

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == (
        f"repointed 'example' to {(skill / '.bm').as_posix()}; indexed 12 of 12 files"
    )


def test_project_adopt_writes_no_marker_when_the_adopt_is_refused(
    runner, config_home, stub_adopt, monkeypatch, tmp_path
):
    """A refusal from the service — a store-homed project of that name — leaves nothing behind."""

    async def refuse(self, adopt_data):
        raise RuntimeError("Project 'example' is homed in the store at /store/abcd")

    monkeypatch.setattr(ProjectClient, "adopt_project", refuse)

    skill = tmp_path / "skills" / "example"
    (skill / ".bm").mkdir(parents=True)
    monkeypatch.chdir(skill)

    result = runner.invoke(app, ["project", "adopt", "example"])

    assert result.exit_code == 1, result.output
    assert "homed in the store" in result.output
    assert not (skill / ".bm.yml").exists()
    assert stub_adopt.indexed == []
    assert result.stdout == ""
