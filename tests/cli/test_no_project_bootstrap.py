"""No `bm` command may invent a project at ``~/basic-memory`` (GAPS U15, U16).

Upstream's first-run bootstrap creates a project rooted at ``$HOME/basic-memory``
whenever the registry is empty. In this fork a project's path is store-derived
(AGENTS.md, D3), so that path is outside the history repo: a record written there
is committed nowhere and the only sign is one notice. A *read* verb creating it
is worse — nothing was asked for and a directory appeared. U15 covered the native
verbs, which reach the registry through ``cli/direct.py``; U16 covers the
client-routed ones, which reach it through the prepared-ASGI seam.

The probe has to be a subprocess. ``ConfigManager`` caches config at module
level, ``resolve_data_dir()`` reads the environment, and the bootstrap keys off
``Path.home()``; only a fresh process with its own ``HOME`` and
``BASIC_MEMORY_CONFIG_DIR`` reproduces a genuinely fresh install.

``BASIC_MEMORY_HOME`` is deliberately *unset* here, unlike in the import guard:
the whole question is where the bootstrap would have landed, and that is
``$HOME/basic-memory`` only when nothing redirects it.
"""

import json
import os
import subprocess
import sys
import textwrap

import pytest

# Runs one `bm` command in-process and reports what it did. Kept as source rather
# than a helper module because it has to execute in a subprocess that imports
# basic_memory for the first time.
PROBE_SOURCE = textwrap.dedent(
    """
    import json
    import sys
    from pathlib import Path

    from typer.testing import CliRunner

    from basic_memory.cli.main import app

    result = CliRunner().invoke(app, json.loads(sys.argv[1]))
    print(
        json.dumps(
            {
                "exit_code": result.exit_code,
                "output": result.output,
                "bootstrapped": (Path.home() / "basic-memory").exists(),
            }
        )
    )
    """
)


def fresh_install_env(tmp_path):
    """Build the environment of a genuinely fresh install, and its HOME."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)

    env = os.environ.copy()
    env.pop("BASIC_MEMORY_ENV", None)
    # Unset, not redirected: `bootstrap_project_home()` honours this, so leaving
    # it set would hide the very directory this test is looking for.
    env.pop("BASIC_MEMORY_HOME", None)
    env["HOME"] = str(home)
    env["BASIC_MEMORY_CONFIG_DIR"] = str(tmp_path / "config")
    return env, home


def run_probe(env, home, command):
    """Run one `bm` command in its own process under the given environment."""
    completed = subprocess.run(
        [sys.executable, "-c", PROBE_SOURCE, json.dumps(list(command))],
        capture_output=True,
        text=True,
        env=env,
        # cwd decides scope: run from a directory with no `.bm.yml` above it, or
        # the repo's own marker would resolve a project and the probe would
        # measure nothing.
        cwd=str(home),
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def run_on_empty_registry(tmp_path, command):
    """Run one command against a fresh install with no project registered."""
    env, home = fresh_install_env(tmp_path)
    return run_probe(env, home, command)


@pytest.mark.parametrize(
    "command",
    [
        ["new", "task", "Probe", "--body", "x"],
        ["edit", "tnd-probe001", "--title", "Renamed"],
        ["mark", "tnd-probe001", "doing"],
        ["done", "tnd-probe001"],
    ],
    ids=["new", "edit", "mark", "done"],
)
def test_write_verb_refuses_instead_of_bootstrapping(tmp_path, command):
    """A write with nothing to write to fails, names the fix, and creates nothing."""
    probe = run_on_empty_registry(tmp_path, command)

    assert probe["exit_code"] != 0, probe["output"]
    assert "no project — run 'bm project add <name>'" in probe["output"]
    assert not probe["bootstrapped"], probe["output"]


@pytest.mark.parametrize(
    "command",
    [["ls"], ["doctor"], ["types"], ["project", "list"]],
    ids=["ls", "doctor", "types", "project-list"],
)
def test_read_verb_reports_an_empty_registry_without_creating_one(tmp_path, command):
    """A read over no projects is an empty answer, never a new project."""
    probe = run_on_empty_registry(tmp_path, command)

    assert probe["exit_code"] == 0, probe["output"]
    assert not probe["bootstrapped"], probe["output"]


def test_project_add_leaves_exactly_the_project_asked_for(tmp_path):
    """`bm project add` on a fresh install creates one project, not two (GAPS U16).

    This is the client-routed half of U15. `project add` reaches the registry
    through the prepared-ASGI seam rather than `cli/direct.py`, and that seam
    bootstrapped a project `main` at ``$HOME/basic-memory`` before creating the
    one the caller asked for — so a clean install could not reach a state of
    exactly one project.

    Two processes, one HOME: the config manager caches config per process, so
    reading the registry back in the same process would not prove the row
    survived the first command's shutdown.
    """
    env, home = fresh_install_env(tmp_path)

    # Bare: the governed default is what a fresh install now gets (GAPS U49).
    added = run_probe(env, home, ["project", "add", "probe"])
    assert added["exit_code"] == 0, added["output"]
    assert not added["bootstrapped"], added["output"]
    # The service makes the first project the default (`ProjectService.add_project`),
    # and the command says so rather than moving the default silently.
    assert "is now the default project" in added["output"]

    listed = run_probe(env, home, ["project", "list"])
    assert listed["exit_code"] == 0, listed["output"]
    assert not listed["bootstrapped"], listed["output"]
    assert "1 projects" in listed["output"]
    assert "probe" in listed["output"]
    assert "(default)" in listed["output"]


def test_probe_would_see_a_bootstrap(tmp_path):
    """Positive control: the same probe reports the directory when it IS created.

    Without this, "not bootstrapped" could mean the command never ran far enough
    to reach the registry at all. ``ensure_project_registry`` with its default
    argument is the branch the verbs now opt out of, so running it directly
    proves both that the branch still works and that the probe can see it.
    """
    home = tmp_path / "home"
    home.mkdir()

    env = os.environ.copy()
    env.pop("BASIC_MEMORY_ENV", None)
    env.pop("BASIC_MEMORY_HOME", None)
    env["HOME"] = str(home)
    env["BASIC_MEMORY_CONFIG_DIR"] = str(tmp_path / "config")

    source = textwrap.dedent(
        """
        import asyncio
        import json
        from pathlib import Path

        from basic_memory import db
        from basic_memory.config import ConfigManager
        from basic_memory.services.initialization import ensure_project_registry

        async def main():
            try:
                await ensure_project_registry(ConfigManager().config)
            finally:
                await db.shutdown_db()

        asyncio.run(main())
        print(json.dumps({"bootstrapped": (Path.home() / "basic-memory").exists()}))
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(home),
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout.strip().splitlines()[-1])["bootstrapped"]
