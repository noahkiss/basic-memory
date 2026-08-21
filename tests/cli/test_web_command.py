"""`bm web install` / `bm web uninstall` — the systemd user unit (GAPS U41).

The server itself is covered in `tests/web/`. What is proved here is the part an
operator runs once and then trusts: the exact bytes of the unit file, where it
is written, which systemctl calls follow it, and that none of it imports the
server — because `cli/main.py` imports this module on every `bm` invocation, and
an import here is a tax on `bm ls`.

No real `systemctl` is spoken to. `run_systemctl` is the one seam every call
goes through, and the fake below records what was asked for.
"""

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.cli.commands import web as web_command

runner = CliRunner()

# Placeholders, never this machine's paths: the unit's text is asserted byte for
# byte, and a real path in an assertion is a local detail in a public repo.
FAKE_HOME = Path("/home/tester")
FAKE_EXECUTABLE = Path("/opt/bin/bm")

EXPECTED_UNIT = (
    "[Unit]\n"
    "Description=bm web — Basic Memory board (localhost:2749)\n"
    "After=network.target\n"
    "\n"
    "[Service]\n"
    "Type=simple\n"
    "Environment=HOME=/home/tester\n"
    "ExecStart=/opt/bin/bm web --host 127.0.0.1 --port 2749\n"
    "Restart=on-failure\n"
    "RestartSec=5\n"
    "\n"
    "[Install]\n"
    "WantedBy=default.target\n"
)


@dataclass
class FakeSystemctl:
    """What `systemctl --user` was asked to do, and what it answered."""

    calls: list[list[str]] = field(default_factory=list)
    # `is-active` answers 3 — systemd's own "inactive" code — so the default
    # shape of a test is a fresh install rather than an upgrade.
    codes: dict[str, int] = field(default_factory=lambda: {"is-active": 3})
    stderr: str = ""

    @property
    def verbs(self) -> list[str]:
        return [call[0] for call in self.calls]


@pytest.fixture
def systemctl(monkeypatch) -> FakeSystemctl:
    fake = FakeSystemctl()

    def record(arguments) -> subprocess.CompletedProcess[str]:
        fake.calls.append(list(arguments))
        return subprocess.CompletedProcess(
            ["systemctl", "--user", *arguments],
            fake.codes.get(arguments[0], 0),
            "",
            fake.stderr,
        )

    monkeypatch.setattr(web_command, "run_systemctl", record)
    return fake


@pytest.fixture
def linux(monkeypatch) -> None:
    """Pin the platform, so these tests assert the same thing on any host."""
    monkeypatch.setattr(web_command.sys, "platform", "linux")


@pytest.fixture
def installed_bm(tmp_path, monkeypatch) -> Path:
    """A `bm` on PATH for `resolve_executable` to find."""
    executable = tmp_path / "bin" / "bm"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(web_command.shutil, "which", lambda name: str(executable))
    return executable


@pytest.fixture
def unit_root(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    return tmp_path / "config" / "systemd" / "user"


# --- The unit file ---


def test_render_unit_is_byte_exact() -> None:
    """The whole file, not a grep: a unit is read by systemd, not by a human."""
    assert web_command.render_unit(FAKE_EXECUTABLE, 2749, FAKE_HOME) == EXPECTED_UNIT


def test_the_unit_carries_home_so_the_server_reads_the_same_corpus() -> None:
    """A user unit inherits a minimal environment; every bm path hangs off HOME."""
    rendered = web_command.render_unit(FAKE_EXECUTABLE, 3000, Path("/home/other"))

    assert "Environment=HOME=/home/other\n" in rendered
    assert "--port 3000\n" in rendered


def test_unit_directory_honours_xdg_config_home(tmp_path, monkeypatch) -> None:
    """systemd honours it, so a tool that did not would write where nothing reads."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "elsewhere"))

    assert web_command.unit_directory() == tmp_path / "elsewhere" / "systemd" / "user"


def test_unit_directory_falls_back_to_dot_config(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    assert web_command.unit_directory() == Path.home() / ".config" / "systemd" / "user"


# --- install ---


def test_install_print_writes_nothing_and_speaks_to_no_systemd(
    linux, installed_bm, unit_root, systemctl
) -> None:
    result = runner.invoke(app, ["web", "install", "--print"])

    assert result.exit_code == 0, result.output
    # The whole file, start to finish — `render_unit` is what proves the bytes.
    assert result.stdout.startswith("[Unit]\n")
    assert result.stdout.endswith("WantedBy=default.target\n")
    assert f"Environment=HOME={Path.home()}\n" in result.stdout
    assert f"ExecStart={installed_bm} web --host 127.0.0.1 --port 2749\n" in result.stdout
    assert systemctl.calls == []
    assert not unit_root.exists()


def test_install_on_a_non_linux_platform_states_the_limit(monkeypatch, systemctl) -> None:
    """launchd is a later item; saying so beats a stack trace about `systemctl`."""
    monkeypatch.setattr(web_command.sys, "platform", "darwin")

    result = runner.invoke(app, ["web", "install"])

    assert result.exit_code == 1
    assert web_command.UNSUPPORTED_PLATFORM in result.stderr
    assert systemctl.calls == []


def test_install_writes_the_unit_at_the_xdg_path_and_enables_it(
    linux, installed_bm, unit_root, systemctl
) -> None:
    result = runner.invoke(app, ["web", "install"])

    assert result.exit_code == 0, result.output
    unit = unit_root / web_command.UNIT_NAME
    assert unit.is_file()
    assert f"ExecStart={installed_bm} web --host 127.0.0.1 --port 2749" in unit.read_text()
    assert systemctl.calls == [
        ["daemon-reload"],
        ["is-active", "--quiet", web_command.UNIT_NAME],
        ["enable", "--now", web_command.UNIT_NAME],
    ]
    assert str(unit) in result.stdout
    assert "http://127.0.0.1:2749/" in result.stdout


def test_install_bakes_the_chosen_port_into_the_unit(
    linux, installed_bm, unit_root, systemctl
) -> None:
    result = runner.invoke(app, ["web", "install", "--port", "3001"])

    assert result.exit_code == 0, result.output
    assert "--port 3001" in (unit_root / web_command.UNIT_NAME).read_text()
    assert "http://127.0.0.1:3001/" in result.stdout


def test_install_restarts_a_unit_that_is_already_running(
    linux, installed_bm, unit_root, systemctl
) -> None:
    """`enable --now` starts nothing that is already up, so an upgrade needs a restart."""
    systemctl.codes["is-active"] = 0

    result = runner.invoke(app, ["web", "install"])

    assert result.exit_code == 0, result.output
    assert systemctl.verbs == ["daemon-reload", "is-active", "restart"]


def test_a_failing_systemctl_is_one_error_line_and_exit_one(
    linux, installed_bm, unit_root, systemctl
) -> None:
    """Contract rule 6: one line on stderr, exit 1, nothing on stdout."""
    systemctl.codes["daemon-reload"] = 1
    systemctl.stderr = "Failed to connect to bus: No medium found\n"

    result = runner.invoke(app, ["web", "install"])

    assert result.exit_code == 1
    assert "systemctl --user daemon-reload failed" in result.stderr
    assert "No medium found" in result.stderr
    assert result.stdout == ""


# --- uninstall ---


def test_uninstall_disables_the_unit_and_removes_the_file(
    linux, installed_bm, unit_root, systemctl
) -> None:
    assert runner.invoke(app, ["web", "install"]).exit_code == 0
    systemctl.calls.clear()

    result = runner.invoke(app, ["web", "uninstall"])

    assert result.exit_code == 0, result.output
    assert not (unit_root / web_command.UNIT_NAME).exists()
    assert systemctl.calls == [
        ["disable", "--now", web_command.UNIT_NAME],
        ["daemon-reload"],
    ]


def test_uninstall_without_a_unit_states_what_it_found(linux, unit_root, systemctl) -> None:
    """Running it twice is a thing operators do; the second run is a fact, not an error."""
    result = runner.invoke(app, ["web", "uninstall"])

    assert result.exit_code == 0, result.output
    assert "no unit installed at" in result.stdout
    assert systemctl.calls == []


# --- The import boundary ---


def _loaded_modules(source: str) -> list[str]:
    """Import something in a fresh interpreter and report which heavy modules loaded."""
    probe = (
        "import json, sys\n"
        f"{source}\n"
        "watched = ('fastapi', 'uvicorn', 'jinja2', 'basic_memory.web.app')\n"
        "print(json.dumps([name for name in watched if name in sys.modules]))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_the_command_module_pulls_no_server_dependencies() -> None:
    """`cli/main.py` imports this module on every `bm` invocation (AGENTS.md baseline).

    A subprocess rather than a `sys.modules` check in-process: this process has
    already imported fastapi through other tests, so an in-process probe would
    report a crossing that never happened.
    """
    assert _loaded_modules("import basic_memory.cli.commands.web") == []


def test_the_probe_sees_the_server_graph_when_it_is_actually_imported() -> None:
    """Positive control: an empty result above must mean 'clean', not 'probe broken'."""
    loaded = _loaded_modules("import basic_memory.web.app")

    assert "fastapi" in loaded
    assert "jinja2" in loaded
    assert "basic_memory.web.app" in loaded


def test_install_keeps_the_symlink_spelling_of_a_package_managed_bm(
    linux, tmp_path, unit_root, systemctl, monkeypatch
) -> None:
    """The versioned target dies on the next upgrade; the `bin/bm` symlink survives it."""
    target = tmp_path / "cellar" / "0.1.9" / "bm"
    target.parent.mkdir(parents=True)
    target.write_text("#!/bin/sh\n", encoding="utf-8")
    link = tmp_path / "bin" / "bm"
    link.parent.mkdir()
    link.symlink_to(target)
    monkeypatch.setattr(web_command.shutil, "which", lambda name: str(link))

    result = runner.invoke(app, ["web", "install"])

    assert result.exit_code == 0, result.output
    unit_text = (unit_root / web_command.UNIT_NAME).read_text()
    assert f"ExecStart={link} web" in unit_text
    assert str(target) not in unit_text
