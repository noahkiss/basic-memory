"""`bm status` under W5-C read scope.

Unscoped used to mean "the registry's default project", which reported one
project and said nothing about the rest. It now means every project, one plain
section each (OUTPUT_CONTRACT rule 1).
"""

import pytest
import typer

import basic_memory.cli.commands.status as status_module
from basic_memory.cli.scope import ReadScope
from basic_memory.project_marker import MarkerError
from basic_memory.schemas import ProjectIndexObservedFileResponse, ProjectIndexStatusResponse
from basic_memory.schemas.project_info import ProjectItem, ProjectList


def _status(total: int) -> ProjectIndexStatusResponse:
    return ProjectIndexStatusResponse(
        total_files=total,
        unindexed_file_count=0,
        observed_files=tuple(
            ProjectIndexObservedFileResponse(
                path=f"notes/note-{position}.md",
                checksum="0" * 64,
                size=12,
                indexed=True,
            )
            for position in range(total)
        ),
    )


def _project(position: int, name: str) -> ProjectItem:
    return ProjectItem(
        id=position,
        external_id=f"{position}" * 8 + "-1111-1111-1111-111111111111",
        name=name,
        path=f"/tmp/{name}",
        is_default=position == 1,
    )


class _FakeClientContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_run_status_unscoped_reports_every_project(monkeypatch):
    """No project names means every registered project, asked for once each."""
    projects = [_project(1, "alpha"), _project(2, "beta")]
    asked: list[str] = []

    class FakeProjectClient:
        def __init__(self, client):
            pass

        async def list_projects(self):
            return ProjectList(projects=projects, default_project="alpha")

        async def get_status(self, external_id):
            asked.append(external_id)
            return _status(len(asked))

    monkeypatch.setattr(status_module, "get_client", lambda **kwargs: _FakeClientContext())
    monkeypatch.setattr(status_module, "ProjectClient", FakeProjectClient)

    reports = await status_module.run_status(None)

    assert [name for name, _ in reports] == ["alpha", "beta"]
    assert asked == [projects[0].external_id, projects[1].external_id]


@pytest.mark.asyncio
async def test_run_status_pinned_asks_for_one_project(monkeypatch):
    """Positive control: a pinned read must not enumerate the registry at all."""
    listed = False

    class FakeProjectClient:
        def __init__(self, client):
            pass

        async def list_projects(self):  # pragma: no cover - must not be reached
            nonlocal listed
            listed = True
            return ProjectList(projects=[], default_project=None)

        async def get_status(self, external_id):
            return _status(1)

    async def fake_get_active_project(client, project, context):
        return _project(1, project)

    monkeypatch.setattr(status_module, "get_client", lambda **kwargs: _FakeClientContext())
    monkeypatch.setattr(status_module, "ProjectClient", FakeProjectClient)
    monkeypatch.setattr(status_module, "get_active_project", fake_get_active_project)

    reports = await status_module.run_status(["alpha"])

    assert [name for name, _ in reports] == ["alpha"]
    assert not listed


def _run_status_command(monkeypatch, scope: ReadScope, reports) -> None:
    """Drive the verb with scope and the fetch stubbed, so only rendering is under test."""
    monkeypatch.setattr(status_module, "resolve_read_scope", lambda explicit: scope)

    async def fake_run_status(projects, wait=False, timeout=30.0):
        assert projects == (None if scope.project is None else [scope.project])
        return reports

    monkeypatch.setattr(status_module, "run_status", fake_run_status)
    status_module.status(project=None, verbose=False, quiet=False, wait=False, timeout=30.0)


def test_status_unscoped_prints_one_section_per_project(monkeypatch, capsys):
    reports = [("alpha", _status(2)), ("beta", _status(3))]

    _run_status_command(monkeypatch, ReadScope(project=None, origin="unscoped"), reports)

    output = capsys.readouterr().out
    assert "project: alpha" in output
    assert "project: beta" in output
    assert "total files: 2" in output
    assert "total files: 3" in output
    # Sections are separated by a blank line, and the first one does not open with one.
    assert not output.startswith("\n")
    assert "\n\nproject: beta" in output


def test_status_pinned_prints_the_one_block_it_always_did(monkeypatch, capsys):
    _run_status_command(
        monkeypatch, ReadScope(project="alpha", origin="flag"), [("alpha", _status(2))]
    )

    output = capsys.readouterr().out
    assert output.splitlines() == [
        "project: alpha",
        "total files: 2",
        "unindexed files: 0",
    ]


def test_status_unscoped_states_an_empty_registry(monkeypatch, capsys):
    """Contract rule 5: "nothing there" is a result, and silence would read as health."""
    _run_status_command(monkeypatch, ReadScope(project=None, origin="unscoped"), [])

    assert capsys.readouterr().out == "no projects registered\n"


def test_status_exits_1_on_an_unusable_marker(monkeypatch, capsys):
    """Status is not brief: an unaddressable read is a failure (contract rules 5 and 6).

    `MarkerError` is a `ValueError`, which the verb already treats as addressing.
    """

    def raise_marker_error(explicit):
        raise MarkerError("Project marker /tmp/.bm.yml names 'nope', which is not registered")

    monkeypatch.setattr(status_module, "resolve_read_scope", raise_marker_error)

    with pytest.raises(typer.Exit) as exc_info:
        status_module.status(project=None, verbose=False, quiet=False, wait=False, timeout=30.0)

    assert exc_info.value.exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "nope" in captured.err


def test_status_exits_1_on_an_unknown_project(monkeypatch, capsys):
    """The pinned equivalent: the API cannot address the named project, so exit 1."""
    from mcp.server.fastmcp.exceptions import ToolError

    monkeypatch.setattr(
        status_module, "resolve_read_scope", lambda explicit: ReadScope("ghost", "flag")
    )

    async def fake_run_status(projects, wait=False, timeout=30.0):
        raise ToolError("Project 'ghost' not found")

    monkeypatch.setattr(status_module, "run_status", fake_run_status)

    with pytest.raises(typer.Exit) as exc_info:
        status_module.status(project="ghost", verbose=False, quiet=False, wait=False, timeout=30.0)

    assert exc_info.value.exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ghost" in captured.err
