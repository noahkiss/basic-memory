"""cwd → project resolution via the `.bm.yml` marker (GAPS B5).

A `.bm.yml` file maps "where I am" to "which BM project that is". The CLI
resolution chain is: explicit `--project` flag > nearest marker walking up from
cwd > configured default project. The marker is a CLI-boundary concept — the
MCP server and API never read cwd; marker resolution happens once at CLI entry
and flows down as an explicit project name.

The marker is a **pointer, not a container**: it sits at the root of a working
directory (usually a code repo you run `bm` from) and never has note content
beside it. Note content lives only in the central store at `store/<id>/`.

A marker carries three keys and ignores everything else, so it stays
forward-compatible with whatever it grows into:

- `project:` — the project name, and the key **resolution** uses.
- `id:` — the project's `external_id`, which is also its store directory name.
- `scope:` — `tree` (the default, and the shape every marker had before GAPS
  U40) or `here`, which makes the marker apply to its own directory alone.

The id is written for consumers that cannot afford a `bm` call: a statusline
reads `store/<id>/headline.md` directly, and the 0.15 s floor for any `bm`
invocation is too slow for something that re-renders constantly (GAPS U21).

Parsing here is strict: a marker that exists but cannot be read, or whose
`project:` value is not a non-empty string, raises `MarkerError`. A silent
fallback to the default project would quietly point commands (including
writes) at the wrong project — the failure mode B5 exists to remove. Callers
that must never fail (`bm brief` on session start) catch `MarkerError` and
degrade themselves.

The marker *search* is bounded (GAPS U29): the walk up from cwd stops at the
first `.git` boundary (inclusive) and never consults `$HOME` or its ancestors,
and a `scope: here` marker bounds itself (GAPS U40). Both the write chain here
and the read scope in `cli/scope.py` resolve through the same `find_marker`, so
the boundaries hold for every verb at once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

MARKER_FILENAME = ".bm.yml"

# The two `scope:` values. `tree` is what every marker written before GAPS U40
# means, so it is also what an absent key means; `here` is the narrowed shape.
MARKER_SCOPE_TREE = "tree"
MARKER_SCOPE_HERE = "here"


class MarkerError(ValueError):
    """A `.bm.yml` marker exists but cannot be used for resolution."""


def find_marker(start: Path) -> Optional[Path]:
    """Walk up from `start` looking for a `.bm.yml` project marker.

    Returns the first marker found, so a nested project wins over the one above
    it. The walk honours three boundaries (GAPS U29, U40):

    - **A repo boundary is inclusive and final.** The first directory holding a
      `.git` (a directory, or the file a worktree or submodule leaves) is the
      last one searched. A marker *above* a repo must never capture the repo:
      one `.bm.yml` at a workspace root would silently claim every unmarked
      repo below it, writes included, which is the trap tnd-b4f4eu4m recorded.
    - **`$HOME` and its ancestors are never searched.** The home directory is
      not a project, and a marker there (or at `/home`, or `/`) could only be
      the workspace-capture trap in its widest form. This ceiling is also what
      keeps a dotfiles-style `~/.git` from reading as a repo boundary: the walk
      stops at `$HOME` before it would ever consult it.
    - **A `scope: here` marker is invisible from below.** It resolves only when
      the walk *starts* in its own directory; from a subdirectory the walk
      climbs past it as if the file were not there. That is what lets a
      catch-all workspace project sit at `~/develop` — so a discussion held in
      the bare directory has a home — without claiming the scratch folders
      under it, which have no repo boundary of their own to stop the walk
      (GAPS U40).

    Raises MarkerError when a marker on the walk carries an unusable `scope:`
    value; deciding whether the file applies means reading it.
    """
    home = Path.home()
    # $HOME itself and everything above it. Precomputed as a set: the walk
    # compares each visited directory against it, and `Path.__eq__` chains in a
    # loop read worse than one membership test.
    never_searched = {home, *home.parents}
    for directory in (start, *start.parents):
        if directory in never_searched:
            return None
        candidate = directory / MARKER_FILENAME
        # The scope is read wherever a marker exists, including the start
        # directory where it cannot change the answer: a malformed `scope:`
        # must fail the same way from every directory, not only from below.
        # The walk's usual step — a directory with no marker — stays a stat
        # with no YAML parse behind it.
        if candidate.is_file():
            only_here = read_marker_only_here(candidate)
            if directory == start or not only_here:
                return candidate
        # After the marker check, so the repo root itself is still searched.
        if (directory / ".git").exists():
            return None
    return None


def repo_identity(directory: Path) -> Optional[str]:
    """The working repo's identity: its git `origin` URL, lightly normalized.

    Captured into the registry at marking time (GAPS U36). Markers are
    gitignored, so a fresh clone arrives unmarked; the origin URL is the one
    machine-independent fact that says "this directory is that project's repo",
    which is what lets `bm project mark --if-repo-matches` re-mark a clone
    mechanically instead of a human answering a name-collision prompt.

    Normalization is deliberately light — trailing whitespace and a trailing
    `.git` — so ssh and https spellings of the same repo remain *distinct*.
    Exact-match semantics keep false positives impossible at the cost of a
    prompt when a machine clones over a different transport; unifying
    transports would need URL parsing this fact does not deserve.

    Never raises: no git binary, no repo, and no remote all read as None,
    because "this directory has no repo identity" is an answer, not a fault.
    """
    import subprocess

    try:
        completed = subprocess.run(
            ["git", "-C", str(directory), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None

    url = completed.stdout.strip()
    if url.endswith(".git"):
        url = url[: -len(".git")]
    return url or None


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


def read_marker_only_here(marker: Path) -> bool:
    """Whether this marker claims its own directory alone (`scope: here`).

    An absent key reads as `tree`, which is what every marker written before
    GAPS U40 means and what the walk has always done. Any other value raises:
    a misspelled scope silently widening a marker back to the whole tree is the
    exact failure U40 exists to prevent.
    """
    scope = _read_marker_key(marker, "scope")
    if scope is None or scope == MARKER_SCOPE_TREE:
        return False
    if scope != MARKER_SCOPE_HERE:
        raise MarkerError(
            f"Invalid project marker {marker}: 'scope' must be "
            f"'{MARKER_SCOPE_TREE}' or '{MARKER_SCOPE_HERE}', not '{scope}'"
        )
    return True


def render_marker(project: str, external_id: str, *, only_here: bool = False) -> str:
    """The marker document, hand-written rather than dumped by PyYAML.

    A marker is read by shell and JS consumers as often as by `bm`, and several
    of them grep it line by line. Emitting the keys in a fixed order, one per
    line, unquoted, is what keeps `grep '^id:'` a valid way to read it — a YAML
    dump reserves the right to reorder or quote, and a working marker would then
    stop parsing for reasons nothing in this tree would explain.

    `only_here` appends the third line, `scope: here`. It is omitted otherwise
    rather than written as `scope: tree`, so the file a plain marker produces is
    byte-identical to the one every earlier version wrote.

    No value can need quoting: a project name is registry-validated, an external
    id is a UUID4, and the scope is one of two literals.
    """
    scope_line = f"scope: {MARKER_SCOPE_HERE}\n" if only_here else ""
    return f"project: {project}\nid: {external_id}\n{scope_line}"


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


def write_marker(
    directory: Path, project: str, external_id: str, *, only_here: bool | None = None
) -> Path:
    """Write `<directory>/.bm.yml` for `project`, and return where it landed.

    Refuses when a marker is already there naming a *different* project: that
    directory is somebody else's working tree, and silently repointing it would
    send every later write in it to the wrong project. A marker naming this same
    project is rewritten, which is how a name-only marker gains its `id:`.

    `only_here` is three-state on purpose. True and False set the scope; None
    **preserves** whatever the existing marker declares, because the rewrite is
    also the id-retrofit path — a bare `bm project mark` must not widen a marker
    a human narrowed to one directory as a side effect of filling in its id
    (GAPS U40).
    """
    if conflict := marker_conflict(directory, project):
        raise MarkerError(
            f"{directory / MARKER_FILENAME} already names project '{conflict}'; "
            f"remove it first to mark this directory as '{project}'"
        )

    marker = directory / MARKER_FILENAME
    if only_here is None:
        only_here = marker.is_file() and read_marker_only_here(marker)
    marker.write_text(render_marker(project, external_id, only_here=only_here), encoding="utf-8")
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
