"""`bm doctor --only usage` (GAPS U35): informational, machine-wide, exit-neutral."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from basic_memory import cmdlog
from basic_memory.cli.app import app
from basic_memory.cli.commands import doctor as doctor_command  # noqa: F401 — registers the verb

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def seed_log(commands: list[tuple[str, int]]) -> None:
    for command, exit_code in commands:
        cmdlog.finish(cmdlog.start(command.split()), exit_code)


def test_usage_renders_counts_and_failures():
    seed_log([("ls", 0), ("ls", 0), ("new task X", 1)])

    result = runner.invoke(app, ["doctor", "--only", "usage"])

    assert result.exit_code == 0
    assert "this machine" in result.stdout
    assert "    2  ls" in result.stdout
    assert "    1  new  (1 failed)" in result.stdout


def test_usage_names_verbs_never_run():
    seed_log([("ls", 0)])
    result = runner.invoke(app, ["doctor", "--only", "usage"])
    assert result.exit_code == 0
    # `bug` is registered but absent from the seeded log; the list is computed
    # from the live registry, so it must name it without a maintained list.
    assert "never run:" in result.stdout
    assert "bug" in result.stdout.split("never run:")[1]


def test_usage_with_an_empty_log_says_so():
    result = runner.invoke(app, ["doctor", "--only", "usage"])
    assert result.exit_code == 0
    assert "No invocations logged yet" in result.stdout


def test_usage_never_gates_so_strict_is_refused():
    result = runner.invoke(app, ["doctor", "--only", "usage", "--strict"])
    assert result.exit_code == 1
    assert "informational" in result.stderr


def test_only_error_names_all_three_groups():
    result = runner.invoke(app, ["doctor", "--only", "nonsense"])
    assert result.exit_code == 1
    assert "integrity, hygiene, usage" in result.stderr
