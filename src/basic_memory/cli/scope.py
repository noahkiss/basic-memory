"""Read scope for `bm` verbs (GAPS W5 mechanism C).

A **read** is either pinned to one project or it covers every project. There is
no third answer, and in particular there is no fallback to the registry's
default project:

| cwd | scope |
|---|---|
| inside a `.bm.yml` tree | pinned to that project — you declared which one you mean |
| anywhere else | all projects — unscoped is the honest answer, not a fallback |
| `--project X` | overrides either |

Both directions are the requirement. An agent working inside a marked tree must
not be handed another project's problems, *and* an agent standing anywhere else
must be able to review every outstanding `bm` item without `cd`-ing first. Both
fall out of the table with no flag, which is why no `--all-projects` exists.

**Writes keep the old chain.** `project_marker.resolve_cli_project` still ends
at the default project, because a write outside a marker needs a home for the
note. Reads unscoped, writes explicitly homed.

Resolution here is **strict**: an unusable marker raises `MarkerError` rather
than degrading to "all projects". Degrading would hand a marked tree exactly
the cross-project view the marker exists to exclude. A verb that must not fail
(`bm brief` on session start) catches the error and degrades itself, in the one
direction it can afford: silence.

Enumerating "all projects" is deliberately **not** done here. `bm brief` reads
the `project` table it is already querying; `bm status` asks the API it is
already talking to. A shared enumerator would force one of them onto the other's
data source for no gain.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from basic_memory.project_marker import MarkerError, find_marker, read_marker_project


@dataclass(frozen=True)
class ReadScope:
    """Which projects a read covers, and where that decision came from.

    `project` is the pinned project name, or None for "every project". `origin`
    is carried for diagnostics only — `bm brief --verbose` states why it read
    what it read — and nothing branches on it.
    """

    project: Optional[str]
    origin: str
    marker: Optional[Path] = None

    @property
    def is_pinned(self) -> bool:
        return self.project is not None

    def describe(self) -> str:
        """One line naming the scope and its origin, for a diagnostic."""
        if self.project is None:
            return "scope: all projects (no --project, no .bm.yml above cwd)"
        if self.marker is not None:
            return f"scope: project '{self.project}' (from {self.marker})"
        return f"scope: project '{self.project}' (from --project)"


def resolve_read_scope(explicit_project: Optional[str], cwd: Optional[Path] = None) -> ReadScope:
    """Resolve a read's scope: `--project` > nearest `.bm.yml` > all projects.

    `find_marker` returns the nearest marker walking up, so a nested `.bm.yml`
    wins over the one above it. A marker that exists but carries no `project:`
    key is not a scope declaration — a marker may exist for other purposes — so
    resolution falls through to all projects.

    Raises MarkerError when a marker is unreadable, malformed, or names a
    project the registry does not know.
    """
    # Deferred: the registry reader opens the SQLite file, which a caller that
    # passes --project never needs.
    from basic_memory.project_registry import lookup_project

    if explicit_project:
        return ReadScope(project=explicit_project, origin="flag")

    marker = find_marker(cwd if cwd is not None else Path.cwd())
    if marker is None:
        return ReadScope(project=None, origin="unscoped")

    name = read_marker_project(marker)
    if not name:
        return ReadScope(project=None, origin="unscoped")

    registered, _ = lookup_project(name)
    if not registered:
        raise MarkerError(
            f"Project marker {marker} names '{name}', which is not a "
            f"registered project (see 'bm project list')"
        )
    return ReadScope(project=registered, origin="marker", marker=marker)
