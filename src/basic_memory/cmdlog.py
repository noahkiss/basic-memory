"""The machine-wide invocation log every `bm` run appends to (GAPS U34).

One JSONL line per CLI invocation — command path, timestamp, exit code,
duration, project, version — written to the XDG *state* directory, not the data
directory: the log describes this machine's usage of the tool, not any
project's knowledge, so it sits outside the `BASIC_MEMORY_CONFIG_DIR`
isolation boundary on purpose (the same argument that moved the fastembed
cache out).

Two consumers, by decision (2026-08-20): `bm bug` attaches the tail of this log
to every report, and `bm doctor --only usage` aggregates it into per-command
counts. One log, two readers — there is no second telemetry file to drift.

**Everything here is best-effort and must stay that way.** This module is a
deliberate, documented exception to the fail-fast house rule: telemetry that
can break a verb is worse than no telemetry, so every public function swallows
every exception. The hot path imports stdlib only — the native-command import
guard counts this module's cost against every verb.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CMDLOG_FILENAME = "cmdlog.jsonl"

# Ring bounds. The trim check is by file size because counting lines on every
# append would read the whole file back; ~1000 lines of these records weigh
# roughly 150 KB, so the size gate and the line gate agree in practice.
RING_TRIM_BYTES = 150_000
RING_KEEP_LINES = 500

# Sub-command groups whose first argument is part of the command's name:
# "project list" is one command, not "project" with an argument.
_GROUPS = frozenset({"project", "import", "config", "tool", "db", "claude"})


def state_dir() -> Path:
    """Where machine-scoped state lives: ``$XDG_STATE_HOME``, else the XDG default."""
    if xdg_state := os.getenv("XDG_STATE_HOME"):
        return Path(xdg_state) / "basic-memory"
    return Path.home() / ".local" / "state" / "basic-memory"


def cmdlog_path() -> Path:
    """The one log file (module docstring: state, not data)."""
    return state_dir() / CMDLOG_FILENAME


def command_path(argv: list[str]) -> str:
    """The command's name from raw argv: positionals only, group-aware.

    ``bm --quiet project list`` and ``bm project list --quiet`` both name the
    command ``project list``. Flag *values* are not distinguishable from
    positionals without Typer's grammar, so a value can be mistaken for a
    sub-command name — acceptable for telemetry, never used for dispatch.
    """
    positionals = [token for token in argv if token and not token.startswith("-")]
    if not positionals:
        return "(none)"
    if positionals[0] in _GROUPS and len(positionals) > 1:
        return f"{positionals[0]} {positionals[1]}"
    return positionals[0]


def _marker_project(cwd: Path) -> str:
    """The project the cwd's own marker names, or ''. No walking — cheap only."""
    try:
        for line in (cwd / ".bm.yml").read_text(encoding="utf-8").splitlines():
            if line.startswith("project:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def _version() -> str:
    """The running build's version, but only if the package is already loaded.

    Importing `basic_memory` here would work, yet the log line is not worth a
    package import on paths that never touched it; `sys.modules` makes the
    common case free.
    """
    package = sys.modules.get("basic_memory")
    return str(getattr(package, "__version__", "")) if package else ""


@dataclass
class Invocation:
    """One run, opened by `start` and closed by `finish`."""

    command: str
    argv: list[str]
    started: str
    monotonic_start: float
    project: str


def start(argv: list[str]) -> Invocation:
    """Open the record for this run. Never raises."""
    try:
        return Invocation(
            command=command_path(argv),
            argv=list(argv),
            started=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            monotonic_start=time.monotonic(),
            project=_marker_project(Path.cwd()),
        )
    except Exception:  # noqa: BLE001 — module docstring: telemetry never breaks a verb
        return Invocation(
            command="(unknown)", argv=[], started="", monotonic_start=time.monotonic(), project=""
        )


def finish(invocation: Invocation, exit_code: int) -> None:
    """Append the closed record and keep the ring bounded. Never raises."""
    try:
        line = json.dumps(
            {
                "command": invocation.command,
                "ts": invocation.started,
                "exit": exit_code,
                "duration_ms": int((time.monotonic() - invocation.monotonic_start) * 1000),
                "project": invocation.project,
                "version": _version(),
            },
            ensure_ascii=False,
        )
        path = cmdlog_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if path.stat().st_size > RING_TRIM_BYTES:
            _trim(path)
    except Exception:  # noqa: BLE001 — module docstring: telemetry never breaks a verb
        pass


def _trim(path: Path) -> None:
    """Rewrite the log keeping the newest RING_KEEP_LINES lines, atomically."""
    lines = path.read_text(encoding="utf-8").splitlines()[-RING_KEEP_LINES:]
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)


def tail(count: int = 20) -> list[str]:
    """The newest `count` raw lines, oldest first, for a bug report. Never raises."""
    try:
        return cmdlog_path().read_text(encoding="utf-8").splitlines()[-count:]
    except Exception:  # noqa: BLE001 — module docstring: telemetry never breaks a verb
        return []


def entries() -> list[dict]:
    """Every parseable record, oldest first, for aggregation. Never raises."""
    records: list[dict] = []
    try:
        for line in cmdlog_path().read_text(encoding="utf-8").splitlines():
            try:
                parsed = json.loads(line)
            except ValueError:
                continue  # a torn line from a crashed writer is data loss, not an error
            if isinstance(parsed, dict):
                records.append(parsed)
    except Exception:  # noqa: BLE001 — module docstring: telemetry never breaks a verb
        return []
    return records
