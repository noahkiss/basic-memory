"""Synchronous, read-only reads of the project registry (GAPS B2).

The database owns the project registry: which projects exist, where they live,
and which one is the default. Everything that mutates the registry goes through
``ProjectRepository`` on the async service path.

This module exists for the *other* direction: a handful of callers need to read
the registry from synchronous code at the CLI boundary — `.bm.yml` marker
resolution, `bm brief`, `bm format`'s ``get_project_config()`` — before any
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


# --- Columns a registry may predate ------------------------------------------


def _optional_column_query(sql: str, parameters: tuple = ()) -> list[tuple]:
    """A `_query` that also treats a missing column as an empty result.

    A registry migrated before a column was added has the table but not the
    column. That is the same pre-bootstrap state the "no such table" guard
    covers, and the session hook calls these lookups on every start — a crash
    here would take `bm brief` down with it. Used by the `repo` lookups (U36)
    and by `lookup_project_home` (`home`).
    """
    try:
        return _query(sql, parameters)
    except sqlite3.OperationalError as error:
        if "no such column" not in str(error):
            raise
        return []


# --- Repo identity (GAPS U36) -------------------------------------------------
# The three functions below are the registry side of repo↔project matching.
# The two lookups follow this module's read-only rule. `record_project_repo`
# is a deliberate, narrow exception to it: the capture happens inside
# `bm project mark`, a native verb that reads the registry through this module
# precisely to stay off the SQLAlchemy import — and routing one idempotent,
# single-column provenance stamp through the async service path would put that
# entire stack back on the marker verb. The write fills NULL only and never
# changes a stored value, so the service layer's invariants cannot be violated
# from here.


def lookup_project_repo(identifier: str) -> Optional[str]:
    """The repo URL recorded for a project, or None when none was captured."""
    rows = _optional_column_query(
        "SELECT repo FROM project WHERE is_active = 1 AND (name = ? OR permalink = ?)",
        (identifier, generate_permalink(identifier)),
    )
    return rows[0][0] if rows and rows[0][0] else None


def lookup_projects_by_repo(repo: str) -> list[tuple[str, str]]:
    """Projects whose recorded repo equals `repo`, as ``(name, external_id)`` rows.

    Exact string equality by design — see `project_marker.repo_identity` for
    why ssh and https spellings of one repo deliberately do not match.
    """
    rows = _optional_column_query(
        "SELECT name, external_id FROM project WHERE is_active = 1 AND repo = ? ORDER BY name",
        (repo,),
    )
    return [(name, external_id) for name, external_id in rows]


def record_project_repo(identifier: str, repo: str) -> bool:
    """Fill a project's repo column when it is empty; never overwrite.

    Returns True when the row was stamped. A project whose repo is already
    recorded — equal or different — is left untouched: on a mismatch the
    caller warns and the human decides, because two directories claiming one
    project is evidence of something this module must not paper over.
    """
    path = registry_database_path()
    if not path.is_file():
        return False

    connection = sqlite3.connect(path)
    try:
        cursor = connection.execute(
            "UPDATE project SET repo = ? WHERE is_active = 1 "
            "AND (name = ? OR permalink = ?) AND (repo IS NULL OR repo = '')",
            (repo, identifier, generate_permalink(identifier)),
        )
        connection.commit()
        return cursor.rowcount > 0
    except sqlite3.OperationalError as error:
        # Pre-U36 registry: the column arrives with the next migration run.
        # The stamp is retried on every later `bm project mark`, so skipping
        # it here loses nothing.
        if "no such column" not in str(error) and "no such table" not in str(error):
            raise
        return False
    finally:
        connection.close()


# --- Declared home (skill-homed projects) -------------------------------------
# The one value `project.home` may carry: this project's notes live outside the
# store, in a directory something else already versions (a Claude Code skill
# yadm transports, for instance). NULL means the default — store-homed, or a
# legacy off-store project that declared nothing.
#
# The constant lives here rather than beside the column in `models/project.py`
# because that module imports SQLAlchemy at import time, and this one exists to
# keep that ~1.1 s import off the fast CLI path (see the module docstring). The
# model imports the name from here.
PROJECT_HOME_EXTERNAL = "external"


def lookup_project_home(external_id: str) -> Optional[Path]:
    """The directory a project declares as its own home, or None.

    Keyed on `external_id`, not on a name, because the caller is
    `vocabulary.model.vocabulary_path` and the id is what it holds.

    None covers every case that is not an explicit declaration: no such
    project, a NULL `home`, a legacy off-store project, and a registry migrated
    before the column existed.

    The recorded path is returned verbatim, never resolved. An external home is
    often a yadm symlink (`.bm` → `.bm##class.home`), and a resolved path
    recorded on one machine would not match the literal one on the next.
    """
    rows = _optional_column_query(
        "SELECT path FROM project WHERE is_active = 1 AND external_id = ? AND home = ?",
        (external_id, PROJECT_HOME_EXTERNAL),
    )
    return Path(rows[0][0]) if rows and rows[0][0] else None


def externally_homed_project_names() -> list[str]:
    """Every active project declaring an external home, by name, sorted.

    Names rather than ids because the reader is a user-facing line: the store
    history verbs (`bm history dirty`, `bm history commit`, `bm undo`) say which
    projects their repository does not cover, and a name is what the reader
    types back. Empty when no project declares one — including a registry
    migrated before the column existed, which is the same "declared nothing".

    Uncached: one call per invocation of a verb that is already reading a git
    repository, so there is nothing to amortize. `vocabulary.model` caches its
    home lookup because that one sits on the write path.
    """
    rows = _optional_column_query(
        "SELECT name FROM project WHERE is_active = 1 AND home = ? ORDER BY name",
        (PROJECT_HOME_EXTERNAL,),
    )
    return [name for (name,) in rows]
