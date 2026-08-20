"""Tests for read scope (GAPS W5 mechanism C).

The table under test, in full:

| cwd | scope |
|---|---|
| inside a `.bm.yml` tree | pinned to that project |
| anywhere else | all projects |
| `--project X` | overrides either |

The row that carries the decision is the middle one: an unmarked cwd used to
resolve to the registry default, and now reads everything. `resolve_cli_project`
keeps the old chain for writes, which the last test here is the positive control
for — the two functions must disagree on exactly that row.
"""

import pytest

from basic_memory.cli.scope import resolve_read_scope
from basic_memory.project_marker import MarkerError, resolve_cli_project


def test_explicit_project_pins(tmp_path, write_registry_file):
    """--project wins over a marker that names something else."""
    write_registry_file({"marked": str(tmp_path)}, default="marked")
    (tmp_path / ".bm.yml").write_text("project: marked\n")

    scope = resolve_read_scope("explicit", tmp_path)

    assert scope.project == "explicit"
    assert scope.is_pinned
    assert scope.origin == "flag"


def test_marker_pins(tmp_path, write_registry_file):
    # Nested below tmp_path — the registry fixture's fake $HOME, which the
    # marker walk never searches (GAPS U29).
    work = tmp_path / "work"
    work.mkdir()
    write_registry_file({"marked": str(work), "other": "/tmp/other"}, default="other")
    (work / ".bm.yml").write_text("project: marked\n")

    scope = resolve_read_scope(None, work)

    assert scope.project == "marked"
    assert scope.marker == work / ".bm.yml"
    assert scope.origin == "marker"


def test_nested_marker_beats_its_parent(tmp_path, write_registry_file):
    """The nearest marker wins — a project inside a project reads as itself."""
    write_registry_file({"outer": str(tmp_path), "inner": "/tmp/inner"}, default="outer")
    (tmp_path / ".bm.yml").write_text("project: outer\n")
    nested = tmp_path / "sub" / "deeper"
    nested.mkdir(parents=True)
    (nested.parent / ".bm.yml").write_text("project: inner\n")

    scope = resolve_read_scope(None, nested)

    assert scope.project == "inner"
    assert scope.marker == nested.parent / ".bm.yml"


def test_no_marker_reads_every_project(tmp_path, write_registry_file):
    """The decision: an unmarked cwd is unscoped, not the default project."""
    write_registry_file({"main": str(tmp_path)}, default="main")

    scope = resolve_read_scope(None, tmp_path)

    assert scope.project is None
    assert not scope.is_pinned
    assert scope.origin == "unscoped"


def test_marker_without_project_key_reads_every_project(tmp_path, write_registry_file):
    """A marker may exist for other purposes; only `project:` declares a scope."""
    write_registry_file({"main": str(tmp_path)}, default="main")
    (tmp_path / ".bm.yml").write_text("id: abc123\n")

    assert resolve_read_scope(None, tmp_path).project is None


def test_unregistered_marker_raises_rather_than_widening(tmp_path, write_registry_file):
    """A stale marker must not silently become "read everything".

    Degrading here would hand a marked tree the cross-project view the marker
    exists to exclude, and would do it precisely when the config is wrong.
    """
    work = tmp_path / "work"
    work.mkdir()
    write_registry_file({"other": "/tmp/other"}, default="other")
    (work / ".bm.yml").write_text("project: nope\n")

    with pytest.raises(MarkerError, match=r"names 'nope'") as exc_info:
        resolve_read_scope(None, work)
    assert str(work / ".bm.yml") in str(exc_info.value)


def test_writes_keep_the_default_project_tail(tmp_path, write_registry_file):
    """Positive control for the retirement: the write chain still ends at the default.

    Same cwd, same registry, two answers — reads unscoped, writes explicitly homed.
    """
    write_registry_file({"main": str(tmp_path)}, default="main")

    assert resolve_cli_project(None, tmp_path) == "main"
    assert resolve_read_scope(None, tmp_path).project is None


@pytest.mark.parametrize("pinned", [None, "main"])
def test_describe_names_the_origin(tmp_path, write_registry_file, pinned):
    """--verbose prints this line; it must name the scope and where it came from."""
    write_registry_file({"main": str(tmp_path)}, default="main")

    described = resolve_read_scope(pinned, tmp_path).describe()

    assert ("all projects" in described) is (pinned is None)
    # The unscoped line names `--project` too, as one of the things that was
    # absent, so match the origin clause rather than the bare flag name.
    assert ("(from --project)" in described) is (pinned is not None)
