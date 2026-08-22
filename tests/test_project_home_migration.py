"""Migration and schema guards for project.home (skill-homed projects).

The column records *intent*: a project whose notes live in a directory
something else already versions declares ``home = "external"``. Everything
else — store-homed, and legacy off-store — is NULL.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast, get_args

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError

from basic_memory import db
from basic_memory.project_registry import (
    PROJECT_HOME_EXTERNAL,
    externally_homed_project_names,
    lookup_project_home,
)
from basic_memory.schemas.project_info import ProjectInfoRequest


# The revision this migration builds on. Downgrading to it must leave a working
# database, not one that only Alembic can read.
REVISION_BEFORE_HOME = "p9k0l1m2n3o4"
EXTERNAL_ID = "3f1c9d2a-7b45-4c8e-9a10-2d6e5f0b3c71"
STORE_HOMED_ID = "8c2e4b60-1d39-4f7a-b5c2-9e0a7d13f846"


def sqlite_alembic_config(database_path: Path) -> Config:
    """Build an Alembic config that upgrades a temporary SQLite database."""
    alembic_dir = Path(db.__file__).parent / "alembic"
    config = Config()
    config.set_main_option("script_location", str(alembic_dir))
    config.set_main_option(
        "file_template",
        "%%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s",
    )
    config.set_main_option("timezone", "UTC")
    config.set_main_option("revision_environment", "false")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path}")
    return config


def insert_project(
    connection: sqlite3.Connection,
    *,
    row_id: int,
    external_id: str,
    path: str,
    home: str | None,
    name: str | None = None,
) -> None:
    """Insert one project row with the home the caller wants to read back.

    ``name`` defaults to a positional stand-in; pass it when the test asserts on
    the name itself, as the exclusion-line reader does.
    """
    now = datetime.now(timezone.utc).isoformat()
    label = name if name is not None else f"p{row_id}"
    connection.execute(
        "INSERT INTO project "
        "(id, external_id, name, permalink, path, is_active, home, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)",
        (row_id, external_id, label, label, path, home, now, now),
    )
    connection.commit()


def test_alembic_upgrade_adds_project_home(tmp_path, monkeypatch):
    """Running Alembic head adds the column, nullable and without a default."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "home-migration.db"
    command.upgrade(sqlite_alembic_config(database_path), "head")

    connection = sqlite3.connect(database_path)
    try:
        home_column = next(
            row
            for row in connection.execute("PRAGMA table_info(project)").fetchall()
            if row[1] == "home"
        )
        # Nullable, because every project that already exists declared nothing.
        assert home_column[3] == 0
        # No default: NULL is the value, not a placeholder standing in for one.
        assert home_column[4] is None
    finally:
        connection.close()


def test_lookup_project_home_reads_the_migrated_column(tmp_path, monkeypatch):
    """Stage 1's reader answers from a real migrated registry, not just a stub.

    `lookup_project_home` opens the registry file itself, so this is the only
    place the column and its reader meet — the service tests run against an
    in-memory database that file never sees.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(data_dir))

    database_path = data_dir / "memory.db"
    command.upgrade(sqlite_alembic_config(database_path), "head")

    connection = sqlite3.connect(database_path)
    try:
        insert_project(
            connection,
            row_id=1,
            external_id=EXTERNAL_ID,
            path="/skills/example/.bm",
            home=PROJECT_HOME_EXTERNAL,
        )
        # Positive control: an undeclared project in the same registry, so a
        # returned path proves the `home` filter and not just "any row wins".
        insert_project(
            connection, row_id=2, external_id=STORE_HOMED_ID, path="/store/other", home=None
        )
    finally:
        connection.close()

    assert lookup_project_home(EXTERNAL_ID) == Path("/skills/example/.bm")
    assert lookup_project_home(STORE_HOMED_ID) is None


def test_externally_homed_project_names_lists_only_the_declared_ones(tmp_path, monkeypatch):
    """The store-history verbs name what their repository excludes, by name.

    Sorted, because the line is read by a human and an order that follows insert
    id would reshuffle whenever a project is re-added.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(data_dir))

    database_path = data_dir / "memory.db"
    command.upgrade(sqlite_alembic_config(database_path), "head")

    connection = sqlite3.connect(database_path)
    try:
        insert_project(
            connection,
            row_id=1,
            external_id=EXTERNAL_ID,
            path="/skills/zebra/.bm",
            home=PROJECT_HOME_EXTERNAL,
            name="zebra-skill",
        )
        insert_project(
            connection,
            row_id=2,
            external_id="4a7b8c9d-0e1f-2a3b-4c5d-6e7f8a9b0c1d",
            path="/skills/alpha/.bm",
            home=PROJECT_HOME_EXTERNAL,
            name="alpha-skill",
        )
        # Positive control: a project in the same registry that declared
        # nothing. It must not appear, or the filter proves nothing.
        insert_project(
            connection,
            row_id=3,
            external_id=STORE_HOMED_ID,
            path="/store/other",
            home=None,
            name="store-homed",
        )
    finally:
        connection.close()

    assert externally_homed_project_names() == ["alpha-skill", "zebra-skill"]


def test_externally_homed_project_names_tolerates_a_pre_migration_registry(tmp_path, monkeypatch):
    """A registry older than the column reads as "nobody declared anything".

    The session hook calls this on every start, so a crash here would take the
    whole CLI down on a database that has simply not migrated yet.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(data_dir))

    connection = sqlite3.connect(data_dir / "memory.db")
    try:
        connection.execute("CREATE TABLE project (name TEXT, path TEXT, is_active INT)")
        connection.execute("INSERT INTO project (name, path, is_active) VALUES ('p', '/p', 1)")
        connection.commit()
    finally:
        connection.close()

    assert externally_homed_project_names() == []


def test_downgrade_and_reupgrade_on_a_populated_database(tmp_path, monkeypatch):
    """Downgrading drops only the new column, and upgrade re-adds it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "home-downgrade.db"
    config = sqlite_alembic_config(database_path)
    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        insert_project(
            connection,
            row_id=1,
            external_id=EXTERNAL_ID,
            path="/skills/example/.bm",
            home=PROJECT_HOME_EXTERNAL,
        )
    finally:
        connection.close()

    command.downgrade(config, REVISION_BEFORE_HOME)

    connection = sqlite3.connect(database_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(project)").fetchall()}
        assert "home" not in columns
        # The row the downgrade had no business touching is still there.
        assert connection.execute("SELECT path FROM project WHERE id = 1").fetchone()[0] == (
            "/skills/example/.bm"
        )
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(project)").fetchall()}
        assert "home" in columns
        # Re-upgrading adds the column back empty: the declaration was dropped
        # with it, so a re-homed project has to declare itself again.
        assert connection.execute("SELECT home FROM project WHERE id = 1").fetchone()[0] is None
    finally:
        connection.close()


def test_the_home_column_the_schema_and_the_constant_agree():
    """Schema guard: one legal value, spelled the same in all three places."""
    import basic_memory
    from basic_memory.models.project import Project

    column = Project.__table__.columns["home"]
    assert column.nullable is True

    # The request schema spells the value out because `Literal` takes literals,
    # not names. Assert the two still mean the same thing, by behaviour: the
    # constant validates, and nothing else does.
    accepted = ProjectInfoRequest(name="p", set_default=False, home=PROJECT_HOME_EXTERNAL)
    assert accepted.home == PROJECT_HOME_EXTERNAL
    assert ProjectInfoRequest(name="p", set_default=False).home is None
    with pytest.raises(ValidationError):
        ProjectInfoRequest(name="p", set_default=False, home=cast(Any, "elsewhere"))

    # And the Literal carries exactly that one value, so a second one cannot be
    # added to the schema without this test noticing.
    literal_type, none_type = get_args(ProjectInfoRequest.model_fields["home"].annotation)
    assert none_type is type(None)
    assert get_args(literal_type) == (PROJECT_HOME_EXTERNAL,)

    # `basic_memory.alembic` is a namespace package (no __init__), so the
    # versions directory is resolved from the parent package's file.
    versions = Path(basic_memory.__file__).parent / "alembic" / "versions"
    migration = versions / "q0l1m2n3o4p5_add_project_home.py"
    assert migration.is_file()
    text = migration.read_text(encoding="utf-8")
    assert f'down_revision: Union[str, None] = "{REVISION_BEFORE_HOME}"' in text
