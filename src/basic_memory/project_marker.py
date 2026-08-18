"""cwd → project resolution via the `.bm.yml` marker (GAPS B5).

A `.bm.yml` file maps "where I am" to "which BM project that is". The CLI
resolution chain is: explicit `--project` flag > nearest marker walking up from
cwd > configured default project. The marker is a CLI-boundary concept — the
MCP server and API never read cwd; marker resolution happens once at CLI entry
and flows down as an explicit project name.

The marker is a **pointer, not a container**: it sits at the root of a working
directory (usually a code repo you run `bm` from) and never has note content
beside it. Note content lives only in the central store at `store/<id>/`.

A marker carries two keys and ignores everything else, so it stays
forward-compatible with whatever it grows into:

- `project:` — the project name, and the key **resolution** uses.
- `id:` — the project's `external_id`, which is also its store directory name.

The id is written for consumers that cannot afford a `bm` call: a statusline
reads `store/<id>/headline.md` directly, and the 0.15 s floor for any `bm`
invocation is too slow for something that re-renders constantly (GAPS U21).

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


def _read_marker_mapping(marker: Path) -> dict:
    """Load a `.bm.yml` as a mapping, strictly. An empty file is an empty mapping."""
    # Deferred: PyYAML stays off the --version floor.
    import yaml

    try:
        data = yaml.safe_load(marker.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MarkerError(f"Unreadable project marker {marker}: {exc}") from exc

    if data is None:
        return {}
    if not isinstance(data, dict):
        raise MarkerError(f"Invalid project marker {marker}: expected a YAML mapping")
    return data


def _read_marker_key(marker: Path, key: str) -> Optional[str]:
    """Read one string key out of a marker, or None when it is absent."""
    data = _read_marker_mapping(marker)
    if key not in data:
        return None

    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise MarkerError(f"Invalid project marker {marker}: '{key}' must be a non-empty string")
    return value.strip()


def read_marker_project(marker: Path) -> Optional[str]:
    """Read the `project:` key out of a `.bm.yml`, strictly.

    Returns None when the key is absent (a marker may exist for other
    purposes). Raises MarkerError when the file is unreadable, is not a YAML
    mapping, or carries a `project:` value that is not a non-empty string.
    """
    return _read_marker_key(marker, "project")


def read_marker_id(marker: Path) -> Optional[str]:
    """Read the `id:` key — the project's `external_id` — out of a `.bm.yml`.

    Returns None for every marker written before GAPS U21, which carried the
    name alone; `bm project mark` is the retrofit that fills them in.

    **Resolution still keys off `project:`, not this.** The id is recorded for
    external consumers that must reach `store/<id>/` without paying for a `bm`
    invocation. Making it authoritative for resolution is a later step: it would
    need a decision about which key wins when a marker's two keys disagree, and
    that question has no answer until every marker carries both.
    """
    return _read_marker_key(marker, "id")


def render_marker(project: str, external_id: str) -> str:
    """The two-key marker document, hand-written rather than dumped by PyYAML.

    A marker is read by shell and JS consumers as often as by `bm`, and several
    of them grep it line by line. Emitting the two keys in a fixed order, one per
    line, unquoted, is what keeps `grep '^id:'` a valid way to read it — a YAML
    dump reserves the right to reorder or quote, and a working marker would then
    stop parsing for reasons nothing in this tree would explain.

    Neither value can need quoting: a project name is registry-validated and an
    external id is a UUID4.
    """
    return f"project: {project}\nid: {external_id}\n"


def marker_conflict(directory: Path, project: str) -> Optional[str]:
    """The name a marker in `directory` already carries, when it is not `project`.

    Returns None when there is no marker, when it names this same project, or
    when it carries no `project:` key at all — those three are all "safe to
    write". Exposed separately from `write_marker` so a caller that must refuse
    *before* it does other work can ask the question first.
    """
    marker = directory / MARKER_FILENAME
    if not marker.is_file():
        return None
    existing = read_marker_project(marker)
    return existing if existing is not None and existing != project else None


def write_marker(directory: Path, project: str, external_id: str) -> Path:
    """Write `<directory>/.bm.yml` for `project`, and return where it landed.

    Refuses when a marker is already there naming a *different* project: that
    directory is somebody else's working tree, and silently repointing it would
    send every later write in it to the wrong project. A marker naming this same
    project is rewritten, which is how a name-only marker gains its `id:`.
    """
    if conflict := marker_conflict(directory, project):
        raise MarkerError(
            f"{directory / MARKER_FILENAME} already names project '{conflict}'; "
            f"remove it first to mark this directory as '{project}'"
        )

    marker = directory / MARKER_FILENAME
    marker.write_text(render_marker(project, external_id), encoding="utf-8")
    return marker


def resolve_cli_project(explicit: Optional[str], cwd: Optional[Path] = None) -> Optional[str]:
    """CLI project chain for **writes**: explicit flag > `.bm.yml` marker > registry default.

    A marker-supplied name is validated against the registered projects so a
    stale marker fails with its own path in the message instead of a bare
    "project not found" from three layers down.

    **Reads do not use this chain.** The default-project tail retired for reads
    with GAPS W5 mechanism C: an unmarked cwd reads every project instead of
    one arbitrary configured project. Use `basic_memory.cli.scope.
    resolve_read_scope` for anything that reports rather than writes. The tail
    survives here because a write outside a marker still needs a home.
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
