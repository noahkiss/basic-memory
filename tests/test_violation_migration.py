"""Migration tests for the violation table and project.vocabulary_stamp (GAPS W5)."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from basic_memory import db


# The revision this migration builds on. Downgrading to it must leave a working
# database, not one that only Alembic can read.
REVISION_BEFORE_VIOLATION = "n7i8j9k0l1m2"


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


def populate(connection: sqlite3.Connection) -> None:
    """Insert one project, one entity, and one violation on that entity."""
    now = datetime.now(timezone.utc).isoformat()
    connection.execute(
        "INSERT INTO project "
        "(id, external_id, name, permalink, path, is_active, created_at, updated_at) "
        "VALUES (1, 'project-uuid', 'migration-project', 'migration-project', '/tmp/p', 1, ?, ?)",
        (now, now),
    )
    connection.execute(
        "INSERT INTO entity "
        "(id, external_id, title, note_type, content_type, permalink, file_path, "
        "project_id, created_at, updated_at) "
        "VALUES (1, 'entity-uuid', 'Note', 'task', 'text/markdown', 'notes/note', "
        "'notes/note.md', 1, ?, ?)",
        (now, now),
    )
    connection.execute(
        "INSERT INTO violation "
        "(entity_id, project_id, rule, field, message, severity, detected_at) "
        "VALUES (1, 1, 'unknown-type', 'type', 'type is not in the vocabulary', 'error', ?)",
        (now,),
    )
    connection.commit()


def test_alembic_upgrade_creates_violation_table(tmp_path, monkeypatch):
    """Running Alembic head should create violation with its expected contract."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "violation-migration.db"
    command.upgrade(sqlite_alembic_config(database_path), "head")

    connection = sqlite3.connect(database_path)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(violation)").fetchall()}
        assert columns == {
            "id",
            "entity_id",
            "project_id",
            "rule",
            "field",
            "message",
            "severity",
            "detected_at",
        }

        foreign_keys = connection.execute("PRAGMA foreign_key_list(violation)").fetchall()
        entity_fk = next(row for row in foreign_keys if row[3] == "entity_id")
        project_fk = next(row for row in foreign_keys if row[3] == "project_id")
        assert entity_fk[2] == "entity"
        assert entity_fk[4] == "id"
        assert entity_fk[6].upper() == "CASCADE"
        assert project_fk[2] == "project"
        assert project_fk[4] == "id"
        assert project_fk[6].upper() == "CASCADE"

        indexes = {row[1] for row in connection.execute("PRAGMA index_list(violation)").fetchall()}
        assert "ix_violation_project_severity" in indexes
        assert "ix_violation_entity_id" in indexes

        table_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'violation'"
        ).fetchone()[0]
        assert "ck_violation_severity" in table_sql
        # SQLite names an inline UNIQUE constraint's index `sqlite_autoindex_...`,
        # so the constraint name survives only in the table SQL. Assert behaviour
        # rather than the index name, which no SQLite database will ever report.
        assert "uix_violation_entity_rule_field" in table_sql

        populate(connection)
        # Positive control: a different (rule, field) on the same entity lands.
        connection.execute(
            "INSERT INTO violation "
            "(entity_id, project_id, rule, field, message, severity, detected_at) "
            "VALUES (1, 1, 'unknown-type', 'note_type', 'a different field', 'error', ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO violation "
                "(entity_id, project_id, rule, field, message, severity, detected_at) "
                "VALUES (1, 1, 'unknown-type', 'type', 'the same rule and field', 'error', ?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
    finally:
        connection.close()


def test_alembic_upgrade_adds_project_vocabulary_stamp(tmp_path, monkeypatch):
    """The same migration stamps projects with the vocabulary they were checked against."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "vocabulary-stamp-migration.db"
    command.upgrade(sqlite_alembic_config(database_path), "head")

    connection = sqlite3.connect(database_path)
    try:
        stamp_column = next(
            row
            for row in connection.execute("PRAGMA table_info(project)").fetchall()
            if row[1] == "vocabulary_stamp"
        )
        # Nullable, because every project that already exists has never been validated.
        assert stamp_column[3] == 0
    finally:
        connection.close()


def test_severity_check_constraint_rejects_an_unknown_severity(tmp_path, monkeypatch):
    """Only the two severities the checker emits may reach the table."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "violation-severity.db"
    command.upgrade(sqlite_alembic_config(database_path), "head")

    connection = sqlite3.connect(database_path)
    try:
        populate(connection)
        # Positive control: the same insert with a legal severity lands.
        connection.execute(
            "INSERT INTO violation "
            "(entity_id, project_id, rule, field, message, severity, detected_at) "
            "VALUES (1, 1, 'unknown-key', 'owner', 'owner is not a schema key', 'advisory', ?)",
            (datetime.now(timezone.utc).isoformat(),),
        )

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO violation "
                "(entity_id, project_id, rule, field, message, severity, detected_at) "
                "VALUES (1, 1, 'unknown-key', 'other', 'bad severity', 'warning', ?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
    finally:
        connection.close()


def test_downgrade_and_reupgrade_on_a_populated_database(tmp_path, monkeypatch):
    """Downgrading a populated database drops only the new schema, and upgrade re-adds it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BASIC_MEMORY_HOME", str(tmp_path / "basic-memory"))

    database_path = tmp_path / "violation-downgrade.db"
    config = sqlite_alembic_config(database_path)
    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        populate(connection)
        connection.execute("UPDATE project SET vocabulary_stamp = 'abc123' WHERE id = 1")
        connection.commit()
    finally:
        connection.close()

    command.downgrade(config, REVISION_BEFORE_VIOLATION)

    connection = sqlite3.connect(database_path)
    try:
        table_query = "SELECT name FROM sqlite_master WHERE type='table'"
        tables = {row[0] for row in connection.execute(table_query)}
        assert "violation" not in tables

        project_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(project)").fetchall()
        }
        assert "vocabulary_stamp" not in project_columns

        # The rows the downgrade had no business touching are still there.
        assert connection.execute("SELECT COUNT(*) FROM entity").fetchone()[0] == 1
        project_name = connection.execute("SELECT name FROM project WHERE id = 1").fetchone()[0]
        assert project_name == "migration-project"
    finally:
        connection.close()

    command.upgrade(config, "head")

    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM violation").fetchone()[0] == 0
        project_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(project)").fetchall()
        }
        assert "vocabulary_stamp" in project_columns
        assert connection.execute("SELECT COUNT(*) FROM entity").fetchone()[0] == 1
    finally:
        connection.close()
