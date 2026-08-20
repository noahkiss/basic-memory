"""Bug reports: `bm bug` and the autocapture hook (GAPS U34).

Reports are markdown files in a configurable directory (`bugs_dir`), one file
per *distinct* failure. The design is cross-machine by configuration, not by
code: the user points `bugs_dir` at a dotfiles-synced directory and wires
`bugs_followup` to their own sync command, so reports written on any machine
arrive wherever they get reviewed. bm itself never knows about the sync — the
follow-up is an opaque user command, run best-effort after a report lands.

Autocapture (`bugs_autocapture`, default on) files a report for every nonzero
exit and every uncaught exception — including usage errors, by decision:
nearly every caller is an agent, so a mistyped flag is a signal about the
tool's teaching surfaces, not noise. What keeps that sane is dedup: a repeat
of the same failure shape increments a counter in the existing report instead
of minting a file.

Like `cmdlog`, the capture path swallows its own failures (a bug in bug
reporting must never re-fire or mask the verb's real error) — the second
deliberate exception to the fail-fast rule, guarded by `_capturing`.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from basic_memory import cmdlog

# Harness fingerprints worth carrying in a report: which agent runtime ran the
# failing command. Values are not read, only presence.
_HARNESS_ENV_VARS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "OPENCODE", "PI_AGENT")

# Re-entrancy latch: a failure while writing a report must not try to write a
# report about itself.
_capturing = False

_SLUG_MAX = 40


@dataclass(frozen=True)
class BugConfig:
    """The three `bugs_*` config values, with the defaults used when the
    config itself cannot be loaded — a broken config being a prime moment to
    capture a bug."""

    dir: Path
    autocapture: bool
    followup: str


def load_bug_config() -> BugConfig:
    """The effective bug settings, degrading to defaults on any config failure."""
    try:
        from basic_memory.config import ConfigManager
        from basic_memory.config_models import resolve_data_dir

        config = ConfigManager().config
        # An empty bugs_dir means "the default under the data dir" — resolved
        # here, not stored, so BASIC_MEMORY_CONFIG_DIR isolation keeps working.
        bugs_dir = (
            Path(config.bugs_dir).expanduser() if config.bugs_dir else resolve_data_dir() / "bugs"
        )
        return BugConfig(
            dir=bugs_dir,
            autocapture=config.bugs_autocapture,
            followup=config.bugs_followup,
        )
    except Exception:  # noqa: BLE001 — module docstring: capture must survive a broken config
        from basic_memory.config_models import resolve_data_dir

        return BugConfig(dir=resolve_data_dir() / "bugs", autocapture=True, followup="")


def _slug(text: str) -> str:
    folded = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return folded[:_SLUG_MAX].rstrip("-") or "report"


def dedup_key(command: str, kind: str, message: str) -> str:
    """The identity of a failure shape: command + exception kind + first line.

    Timestamps, paths in later lines, and counts are deliberately excluded so a
    recurring failure keeps hashing to the same report.
    """
    first_line = message.strip().splitlines()[0] if message.strip() else ""
    raw = f"{command}\n{kind}\n{first_line}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _existing_report(bugs_dir: Path, key: str) -> Path | None:
    try:
        for path in sorted(bugs_dir.glob("*.md")):
            try:
                head = path.read_text(encoding="utf-8", errors="replace")[:600]
            except OSError:
                continue
            if f"dedup-key: {key}" in head:
                return path
    except OSError:
        pass
    return None


def _bump_existing(path: Path) -> None:
    """count+1 and last-seen on a repeat — never a second file for the same shape."""
    text = path.read_text(encoding="utf-8")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _bump(match: re.Match[str]) -> str:
        return f"count: {int(match.group(1)) + 1}"

    text = re.sub(r"count: (\d+)", _bump, text, count=1)
    text = re.sub(r"last-seen: .*", f"last-seen: {now}", text, count=1)
    path.write_text(text, encoding="utf-8")


def _harness_hints() -> str:
    present = [name for name in _HARNESS_ENV_VARS if os.getenv(name)]
    return ", ".join(present) if present else "(none detected)"


def _version() -> str:
    package = sys.modules.get("basic_memory")
    if package is None:
        import basic_memory as package  # noqa: PLC0415 — `bm bug` is allowed the import
    return str(getattr(package, "__version__", "unknown"))


def write_report(
    message: str,
    *,
    command: str,
    kind: str = "reported",
    config: BugConfig | None = None,
) -> tuple[Path, bool]:
    """Write (or bump) the report for one failure shape.

    Returns the report path and whether it is new. Raises on failure — the
    *callers* decide whether failure is fatal (`bm bug`: yes) or swallowed
    (autocapture: never).
    """
    settings = config or load_bug_config()
    key = dedup_key(command, kind, message)
    settings.dir.mkdir(parents=True, exist_ok=True)

    existing = _existing_report(settings.dir, key)
    if existing is not None:
        _bump_existing(existing)
        return existing, False

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    path = settings.dir / f"{stamp}-{_slug(message)}.md"
    log_tail = "\n".join(cmdlog.tail(20)) or "(no cmdlog)"
    body = (
        "---\n"
        f"dedup-key: {key}\n"
        f"kind: {kind}\n"
        f"command: {command}\n"
        f"count: 1\n"
        f"first-seen: {now.isoformat(timespec='seconds')}\n"
        f"last-seen: {now.isoformat(timespec='seconds')}\n"
        f"version: {_version()}\n"
        f"platform: {platform.platform()}\n"
        f"cwd: {Path.cwd()}\n"
        f"project: {cmdlog._marker_project(Path.cwd()) or '(none)'}\n"
        f"harness: {_harness_hints()}\n"
        "---\n\n"
        f"{message.strip()}\n\n"
        "## cmdlog tail\n\n"
        "```\n"
        f"{log_tail}\n"
        "```\n"
    )
    path.write_text(body, encoding="utf-8")
    return path, True


def run_followup(settings: BugConfig) -> None:
    """Run the user's post-report command, if any. Best-effort by design.

    `shell=True` is deliberate: the value is the user's own config (never
    agent- or record-supplied) and compound commands are its whole point.
    Output is suppressed unless the command fails, and failure costs one
    stderr line — a broken sync must not fail the report that just landed.
    """
    if not settings.followup:
        return
    try:
        result = subprocess.run(
            settings.followup,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=settings.dir,
        )
        if result.returncode != 0:
            print(
                f"bugs_followup exited {result.returncode}: {settings.followup}",
                file=sys.stderr,
            )
    except Exception:  # noqa: BLE001 — a broken followup must not fail the report
        print(f"bugs_followup failed to run: {settings.followup}", file=sys.stderr)


def autocapture(command: str, exit_code: int, exc: BaseException | None) -> None:
    """File a report for a failed invocation. Swallows everything (docstring)."""
    global _capturing
    if _capturing or command.startswith("bug"):
        return
    _capturing = True
    try:
        settings = load_bug_config()
        if not settings.autocapture:
            return
        if exc is not None:
            kind = type(exc).__name__
            message = str(exc) or kind
        else:
            kind = f"exit-{exit_code}"
            message = f"`bm {command}` exited {exit_code}"
        write_report(message, command=command, kind=kind, config=settings)
        run_followup(settings)
    except Exception:  # noqa: BLE001 — capture never masks or re-raises over the real error
        pass
    finally:
        _capturing = False
