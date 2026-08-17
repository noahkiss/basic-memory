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
#
# mcp.async_client and mcp.clients are the client graph every verb used to pay
# for through `command_utils.run_with_cleanup` and through `project`, `status`
# and `orphans`' module-level imports (GAPS.md T30). They are the line that
# keeps that fix in place: the runner lives in `cli/runner.py` now, and nothing
# a native verb imports may reach them.
BANNED_MODULES = (
    "basic_memory.api.app",
    "basic_memory.mcp.tools",
    "basic_memory.mcp.async_client",
    "basic_memory.mcp.clients",
    "fastapi",
    "dateparser",
)

# Banned only once the DB is migrated: a fresh database legitimately runs
# alembic, but a warm run must skip it via the head-stamp check in db.py —
# alembic costs ~0.17 s of import beyond SQLAlchemy (GAPS.md B4).
WARM_ONLY_BANNED = BANNED_MODULES + ("alembic",)

# The record the ls/show/path probes read. The body's last line is what the
# `show` probe matches on, and the file path is what the `path` probe matches.
RECORD_ID = "tnd-guard0001"
RECORD_FILE = "tasks/tnd-guard0001--seed.md"
RECORD_BODY = f"---\nid: {RECORD_ID}\ntype: task\n---\n\nseeded for the import guard\n"

# The placeholder a write-verb probe carries where the seeded record's id goes.
# The id is drawn at random by `bm new`, so it cannot be baked into the table.
SEEDED = "<seeded>"

# The constants are prepended rather than interpolated: the probe body is full
# of dict literals, and an f-string would need every brace in it doubled.
PROBE_SOURCE = (
    f"RECORD_ID = {RECORD_ID!r}\nRECORD_FILE = {RECORD_FILE!r}\n"
    f"RECORD_BODY = {RECORD_BODY!r}\nSEEDED = {SEEDED!r}\n"
) + textwrap.dedent(
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

    if command[0] in ("ls", "show", "path"):
        # The record verbs need a record: `show` and `path` exit 1 without one,
        # so an unseeded probe would prove nothing about their import path. The
        # row is written through the same repository the verbs read, because a
        # hand-rolled INSERT would drift from the model.
        import asyncio
        from datetime import datetime, timezone
        from pathlib import Path

        async def seed_one_record():
            from basic_memory import db
            from basic_memory.config import ConfigManager
            from basic_memory.repository.entity_repository import EntityRepository
            from basic_memory.repository.project_repository import ProjectRepository

            config = ConfigManager().config
            _, session_maker = await db.get_or_create_db(config.database_path, config=config)
            try:
                async with db.scoped_session(session_maker) as session:
                    project = sorted(
                        await ProjectRepository().find_all(session), key=lambda row: row.id
                    )[0]
                    entities = EntityRepository(project_id=project.id)
                    stamped = datetime.now(timezone.utc)
                    # The cold and warm runs share one config directory, so the
                    # second pass finds the row the first one wrote.
                    if await entities.get_by_permalink(session, RECORD_ID) is None:
                        await entities.create(
                            session,
                            {
                                "project_id": project.id,
                                "title": "Seeded record",
                                "note_type": "task",
                                "permalink": RECORD_ID,
                                "file_path": RECORD_FILE,
                                "content_type": "text/markdown",
                                "entity_metadata": {
                                    "id": RECORD_ID,
                                    "permalink": RECORD_ID,
                                    "type": "task",
                                    "title": "Seeded record",
                                    "status": "open",
                                },
                                "created_at": stamped,
                                "updated_at": stamped,
                            },
                        )
                    target = Path(project.path) / RECORD_FILE
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(RECORD_BODY, encoding="utf-8")
            finally:
                await db.shutdown_db()

        asyncio.run(seed_one_record())

    if command[0] in ("new", "edit", "done", "mark", "undo"):
        # A write is recorded in the store's git history only when the project's
        # files sit in the store's worktree (VERBS_PLAN D3), and the bootstrap
        # project points at BASIC_MEMORY_HOME, which does not. Move it to the
        # store-derived home `bm project add` gives every new project, so these
        # probes run the real path rather than its off-store degradation.
        import asyncio

        from basic_memory.store.history import store_path

        async def move_project_into_the_store():
            from basic_memory import db
            from basic_memory.config import ConfigManager
            from basic_memory.repository.project_repository import ProjectRepository

            config = ConfigManager().config
            _, session_maker = await db.get_or_create_db(config.database_path, config=config)
            try:
                async with db.scoped_session(session_maker) as session:
                    repository = ProjectRepository()
                    project = sorted(await repository.find_all(session), key=lambda r: r.id)[0]
                    home = store_path() / project.external_id
                    home.mkdir(parents=True, exist_ok=True)
                    await repository.update(session, project.id, {"path": str(home)})
            finally:
                await db.shutdown_db()

        asyncio.run(move_project_into_the_store())

    if command[0] in ("edit", "done", "mark", "undo"):
        # `bm new` is the only way to produce what these three change and what
        # undo reverses, and it is itself a native verb — so seeding through it
        # adds no import the probe would not otherwise measure.
        seed_type = "guide" if command[0] == "edit" else "task"
        created = runner.invoke(
            app, ["new", seed_type, "Seeded record", "--body", "seeded", "--quiet"]
        )
        assert created.exit_code == 0, created.output
        record_id = created.stdout.strip().splitlines()[0].split()[0]
        # The id leads the payload row. Asserting its shape here means a stray
        # line above it fails the probe instead of silently probing a bad id.
        assert record_id.startswith("tnd-"), created.stdout
        command = [record_id if part == SEEDED else part for part in command]

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
# payload to match against. `show` matches the seeded file's last body line and
# `path` its file path, because neither verb prints a count (VERBS_PLAN D9).
NATIVE_COMMANDS = (
    (["project", "list"], " projects"),
    (["types", "--quiet"], " types"),
    (["mine", "sqlite", "--quiet"], " turns"),
    (["doctor", "--quiet"], "No issues"),
    (["brief"], ""),
    (["ls", "--quiet"], " records"),
    (["show", RECORD_ID, "--quiet"], "seeded for the import guard"),
    (["path", RECORD_ID], RECORD_FILE),
    (["new", "task", "Guard record", "--body", "seeded", "--quiet"], "1 record"),
    (["edit", SEEDED, "--title", "Renamed", "--quiet"], "1 record"),
    (["mark", SEEDED, "doing", "--quiet"], "1 record"),
    (["done", SEEDED, "--quiet"], "1 record"),
    (["undo", "--quiet"], " files restored"),
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
    "command,tail",
    NATIVE_COMMANDS,
    ids=[
        "project-list",
        "types",
        "mine",
        "doctor",
        "brief",
        "ls",
        "show",
        "path",
        "new",
        "edit",
        "mark",
        "done",
        "undo",
    ],
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


@pytest.mark.parametrize("banned_module", ["fastapi", "basic_memory.mcp.async_client"])
def test_guard_probe_detects_a_crossing(tmp_path, banned_module):
    """Positive control: the probe must report a banned module that IS loaded.

    Without this, an empty result could mean "probe broken" rather than
    "boundary held" — the class of false negative the evidence rules exist for.
    Both ban families are controlled: the seconds-scale one the guard was built
    for, and the MCP client graph T30 added.
    """
    env = os.environ.copy()
    env.pop("BASIC_MEMORY_ENV", None)
    env["HOME"] = str(tmp_path)
    env["BASIC_MEMORY_HOME"] = str(tmp_path / "notes")
    env["BASIC_MEMORY_CONFIG_DIR"] = str(tmp_path / ".basic-memory")

    # Force one banned module in, then run the same probe body.
    source = f"import {banned_module}\n" + PROBE_SOURCE
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
    assert banned_module in loaded
