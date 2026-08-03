"""cwd → project resolution via the `.bm.yml` marker (GAPS B5).

A `.bm.yml` file maps "where I am" to "which BM project that is". The CLI
resolution chain is: explicit `--project` flag > nearest marker walking up from
cwd > configured default project. The marker is a CLI-boundary concept — the
MCP server and API never read cwd; marker resolution happens once at CLI entry
and flows down as an explicit project name.

The marker is a **pointer, not a container**: it sits at the root of a working
directory (usually a code repo you run `bm` from) and never has note content
beside it. Note content lives only in the central store at `store/<id>/`.

The marker's full schema (the store id that `bm history`/`bm undo` will key
off) is owned by the store design and not built yet. This module reads one
key — `project:` — and ignores everything else, so it stays forward-compatible
with whatever the marker grows into.

Parsing here is strict: a marker that exists but cannot be read, or whose
`project:` value is not a non-empty string, raises `MarkerError`. A silent
fallback to the default project would quietly point commands (including
writes) at the wrong project — the failure mode B5 exists to remove. Callers
that must never fail (`bm brief` on session start) catch `MarkerError` and
degrade themselves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

MARKER_FILENAME = ".bm.yml"


class MarkerError(ValueError):
    """A `.bm.yml` marker exists but cannot be used for resolution."""


def find_marker(start: Path) -> Optional[Path]:
    """Walk up from `start` looking for a `.bm.yml` project marker.

    Stops at the filesystem root. Returns the first marker found, so a nested
    project wins over the one above it.
    """
    for directory in (start, *start.parents):
        candidate = directory / MARKER_FILENAME
        if candidate.is_file():
            return candidate
    return None


def read_marker_project(marker: Path) -> Optional[str]:
    """Read the `project:` key out of a `.bm.yml`, strictly.

    Returns None when the key is absent (a marker may exist for other
    purposes). Raises MarkerError when the file is unreadable, is not a YAML
    mapping, or carries a `project:` value that is not a non-empty string.
    """
    # Deferred: PyYAML stays off the --version floor.
    import yaml

    try:
        data = yaml.safe_load(marker.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MarkerError(f"Unreadable project marker {marker}: {exc}") from exc

    if data is None:
        return None
    if not isinstance(data, dict):
        raise MarkerError(f"Invalid project marker {marker}: expected a YAML mapping")
    if "project" not in data:
        return None

    value = data["project"]
    if not isinstance(value, str) or not value.strip():
        raise MarkerError(f"Invalid project marker {marker}: 'project' must be a non-empty string")
    return value.strip()


def resolve_cli_project(explicit: Optional[str], cwd: Optional[Path] = None) -> Optional[str]:
    """CLI project chain: explicit flag > `.bm.yml` marker > registry default.

    A marker-supplied name is validated against the registered projects so a
    stale marker fails with its own path in the message instead of a bare
    "project not found" from three layers down.
    """
    # Deferred: the registry reader is only needed once a command actually
    # resolves a project, and it opens the SQLite file to do it.
    from basic_memory.project_registry import default_project_name, lookup_project

    if explicit:
        return explicit

    marker = find_marker(cwd if cwd is not None else Path.cwd())
    if marker is not None:
        name = read_marker_project(marker)
        if name:
            registered, _ = lookup_project(name)
            if not registered:
                raise MarkerError(
                    f"Project marker {marker} names '{name}', which is not a "
                    f"registered project (see 'bm project list')"
                )
            return registered

    return default_project_name()
