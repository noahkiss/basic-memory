"""Structural guard for the native-command import boundary (GAPS.md T18).

A native CLI command must talk to the repository/service layer directly and
must never pull in the in-process FastAPI app, the MCP tool layer, or their
heavyweight leaf imports. sys.modules is process-global, so the only honest
probe is a fresh subprocess: run the command there and assert the banned
modules never loaded.
"""

import json
import os
import subprocess
import sys
import textwrap

import pytest

# Modules whose presence after a native command proves the boundary was
# crossed. api.app/mcp.tools are the seconds-scale offenders; fastapi and
# dateparser are the leaf imports that used to ride in through the service
# layer (search_service annotation, entity_parser.parse_date).
BANNED_MODULES = (
    "basic_memory.api.app",
    "basic_memory.mcp.tools",
    "fastapi",
    "dateparser",
)

# Banned only once the DB is migrated: a fresh database legitimately runs
# alembic, but a warm run must skip it via the head-stamp check in db.py —
# alembic costs ~0.17 s of import beyond SQLAlchemy (GAPS.md B4).
WARM_ONLY_BANNED = BANNED_MODULES + ("alembic",)

PROBE_SOURCE = textwrap.dedent(
    """
    import json
    import sys

    from typer.testing import CliRunner

    from basic_memory.cli.main import app

    banned = json.loads(sys.argv[1])
    command = json.loads(sys.argv[2])
    tail = sys.argv[3]

    runner = CliRunner()

    # Bootstrap: opening the database is what creates the project registry, and
    # a project-scoped verb needs one to resolve against. `project list` is a
    # native command itself, so it cannot smuggle a banned import in here.
    bootstrap = runner.invoke(app, ["project", "list"])
    assert bootstrap.exit_code == 0, bootstrap.output

    if command[0] == "types":
        # `bm types` renders nothing but the ungoverned line until a vocabulary
        # file exists, so give it one — the guard has to cover the full render.
        import sqlite3

        from basic_memory.config_models import DATABASE_NAME, resolve_data_dir
        from basic_memory.store.history import store_path

        connection = sqlite3.connect(resolve_data_dir() / DATABASE_NAME)
        external_id = connection.execute(
            "SELECT external_id FROM project WHERE is_default = 1"
        ).fetchone()[0]
        connection.close()

        directory = store_path() / external_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "vocabulary.yml").write_text("types: [task, guide]\\n")

    if command[0] == "mine":
        # `bm mine` reads transcripts off disk and nothing else, so the guard
        # needs one to read. The path is built here rather than baked into the
        # parametrization because it has to sit under this probe's temp HOME.
        from pathlib import Path

        transcripts = Path.home() / "transcripts"
        transcripts.mkdir(parents=True, exist_ok=True)
        (transcripts / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl").write_text(
            '{"type":"user","timestamp":"2026-08-16T09:00:00Z",'
            '"message":{"role":"user","content":"we chose sqlite"}}\\n'
        )
        command = [*command, "--dir", str(transcripts)]

    result = runner.invoke(app, command)
    assert result.exit_code == 0, result.output
    if tail:
        # The count line closes every record listing, so its presence proves the
        # command rendered a payload rather than exiting early on a swallowed error.
        assert result.stdout.strip().splitlines()[-1].endswith(tail), result.stdout
    else:
        # `bm brief` prints nothing when nothing is open, and it swallows every
        # failure to keep a session start alive, so there is no payload to match.
        # An empty stdout is the assertion: anything printed here would be a
        # traceback or a notice, and the import question is answered either way.
        assert result.stdout.strip() == "", result.stdout

    print(json.dumps([name for name in banned if name in sys.modules]))
    """
)

# Every native command the guard covers, with the tail of its own last output
# line — the assertion that proves the command rendered rather than exiting
# early. `types`, `mine` and `doctor` run --quiet so the count line is last, not
# the affordance. `doctor` checks a corpus with nothing in it, so its last line
# is the hygiene section's empty-result line rather than a count. `brief` has an
# empty tail because it prints nothing on an empty corpus by design — it is the
# most latency-sensitive verb in the tree, so it is guarded despite carrying no
# payload to match against.
NATIVE_COMMANDS = (
    (["project", "list"], " projects"),
    (["types", "--quiet"], " types"),
    (["mine", "sqlite", "--quiet"], " turns"),
    (["doctor", "--quiet"], "No issues"),
    (["brief"], ""),
)


def _probe(tmp_path, banned, command=("project", "list"), tail=" projects"):
    env = os.environ.copy()
    env.pop("BASIC_MEMORY_ENV", None)
    env["HOME"] = str(tmp_path)
    env["BASIC_MEMORY_HOME"] = str(tmp_path / "notes")
    env["BASIC_MEMORY_CONFIG_DIR"] = str(tmp_path / ".basic-memory")

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            PROBE_SOURCE,
            json.dumps(list(banned)),
            json.dumps(list(command)),
            tail,
        ],
        capture_output=True,
        text=True,
        env=env,
        # cwd decides scope for an unscoped verb: `bm doctor` walks up looking
        # for a `.bm.yml`, so running from the repo would make the result depend
        # on whether the checkout happens to carry a marker.
        cwd=str(tmp_path),
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    "command,tail", NATIVE_COMMANDS, ids=["project-list", "types", "mine", "doctor", "brief"]
)
def test_native_command_stays_off_api_and_mcp(tmp_path, command, tail):
    """A native command must not import the banned modules — alembic included once warm.

    Two runs against the same config: the first migrates a fresh database, so
    it may load alembic; the second finds the head stamp current and must not.
    """
    cold = _probe(tmp_path, BANNED_MODULES, command, tail)
    assert cold == [], f"native command imported banned modules: {cold}"

    warm = _probe(tmp_path, WARM_ONLY_BANNED, command, tail)
    assert warm == [], f"warm native command imported banned modules: {warm}"


def test_guard_probe_detects_a_crossing(tmp_path):
    """Positive control: the probe must report a banned module that IS loaded.

    Without this, an empty result could mean "probe broken" rather than
    "boundary held" — the class of false negative the evidence rules exist for.
    """
    env = os.environ.copy()
    env.pop("BASIC_MEMORY_ENV", None)
    env["HOME"] = str(tmp_path)
    env["BASIC_MEMORY_HOME"] = str(tmp_path / "notes")
    env["BASIC_MEMORY_CONFIG_DIR"] = str(tmp_path / ".basic-memory")

    # Force one banned module in, then run the same probe body.
    source = "import fastapi\n" + PROBE_SOURCE
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            source,
            json.dumps(list(BANNED_MODULES)),
            json.dumps(["project", "list"]),
            " projects",
        ],
        capture_output=True,
        text=True,
        env=env,
        # cwd decides scope for an unscoped verb: `bm doctor` walks up looking
        # for a `.bm.yml`, so running from the repo would make the result depend
        # on whether the checkout happens to carry a marker.
        cwd=str(tmp_path),
        timeout=120,
    )

    assert completed.returncode == 0, completed.stderr
    loaded = json.loads(completed.stdout.strip().splitlines()[-1])
    assert "fastapi" in loaded
