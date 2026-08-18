"""Synchronous, read-only reads of the project registry (GAPS B2).

The database owns the project registry: which projects exist, where they live,
and which one is the default. Everything that mutates the registry goes through
``ProjectRepository`` on the async service path.

This module exists for the *other* direction: a handful of callers need to read
the registry from synchronous code at the CLI boundary — `.bm.yml` marker
resolution, `bm brief`, the importers' ``get_project_config()`` — before any
event loop exists and before any command has decided to pay for a database
stack. Routing those through SQLAlchemy would either force ``async`` through
every Typer command that calls them, or start a nested event loop inside code
that is sometimes already running in one.

So these reads go straight at the SQLite file with the stdlib driver. That is
sound because they are read-only single-row lookups against a two-column
projection of a stable table, and it keeps the ~1.1 s SQLAlchemy import off the
path entirely (AGENTS.md, "Measured baseline"). Writes never come through here.

An absent database file, or a database that has not been migrated yet, reads as
an empty registry — that is the genuine pre-bootstrap state, not an error, and
``ensure_project_registry()`` is what fills it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from basic_memory.config_models import DATABASE_NAME, resolve_data_dir
from basic_memory.utils import generate_permalink


def registry_database_path() -> Path:
    """Return the SQLite file that holds the project registry."""
    return resolve_data_dir() / DATABASE_NAME


def _query(sql: str, parameters: tuple = ()) -> list[tuple]:
    """Run one read-only query, treating an un-bootstrapped database as empty."""
    path = registry_database_path()
    if not path.is_file():
        return []

    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql, parameters).fetchall()
    except sqlite3.OperationalError as error:
        # Trigger: the database file exists but Alembic has not created `project`
        # yet (a touched-but-unmigrated file is what `app_database_path` leaves).
        # Why: that is the same state as "no registry", not a corrupt database.
        # Outcome: report an empty registry so bootstrap can populate it. Any
        # other OperationalError is a real fault and propagates.
        if "no such table" not in str(error):
            raise
        return []
    finally:
        connection.close()


def registry_projects() -> dict[str, str]:
    """Return active projects as a name → path mapping."""
    rows = _query("SELECT name, path FROM project WHERE is_active = 1 ORDER BY name")
    return {name: path for name, path in rows}


def default_project_name() -> Optional[str]:
    """Return the name of the project flagged ``is_default``, if there is one."""
    rows = _query("SELECT name FROM project WHERE is_default = 1 LIMIT 1")
    return rows[0][0] if rows else None


def lookup_project(identifier: str) -> tuple[str, str] | tuple[None, None]:
    """Resolve a project by name or permalink, returning ``(name, path)``.

    Returns ``(None, None)`` when nothing matches, so callers can distinguish
    "not registered" from a registered project without touching the service
    layer.
    """
    rows = _query(
        "SELECT name, path FROM project WHERE is_active = 1 AND (name = ? OR permalink = ?)",
        (identifier, generate_permalink(identifier)),
    )
    if rows:
        return rows[0][0], rows[0][1]
    return None, None


def lookup_project_external_id(identifier: str) -> tuple[str, str] | tuple[None, None]:
    """Resolve a project by name or permalink, returning ``(name, external_id)``.

    Separate from ``lookup_project`` rather than widening its return: the path
    and the external id answer different questions — where a project's files sit
    versus which store directory is its own — and every existing caller wants
    the path. `bm project mark` wants the id, and it is the reason this exists
    (GAPS U21): the marker it writes records the store directory name.
    """
    rows = _query(
        "SELECT name, external_id FROM project WHERE is_active = 1 AND (name = ? OR permalink = ?)",
        (identifier, generate_permalink(identifier)),
    )
    if rows:
        return rows[0][0], rows[0][1]
    return None, None
