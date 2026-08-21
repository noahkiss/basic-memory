"""Main CLI entry point for basic-memory."""  # pragma: no cover

import sys
import warnings

from basic_memory.cli.app import app  # pragma: no cover


def _version_only_invocation(argv: list[str]) -> bool:
    # Trigger: invocation is exactly `bm --version` or `bm -v`
    # Why: avoid importing command modules on the hot version path
    # Outcome: eager version callback exits quickly with minimal startup work
    return len(argv) == 1 and argv[0] in {"--version", "-v"}


if not _version_only_invocation(sys.argv[1:]):
    # Register commands only when not short-circuiting for --version
    from basic_memory.cli.commands import (  # noqa: F401  # pragma: no cover
        board,
        brief,
        bug,
        db,
        doctor,
        headline,
        history,
        import_chatgpt,
        import_claude_conversations,
        import_claude_projects,
        import_memory_json,
        man,
        mcp,
        mine,
        new,
        orphans,
        project,
        record_write,
        records,
        rm,
        status,
        tool,
        types,
        web,
    )

warnings.filterwarnings("ignore")  # pragma: no cover


def main() -> None:
    """The console-script entry: run the app inside the telemetry envelope.

    Every invocation — success, handled error, usage error, uncaught crash —
    lands one cmdlog line, and failures go through bug autocapture (GAPS U34).
    The envelope re-raises whatever the app raised: telemetry observes exits,
    it never changes them. Tests that drive the Typer app through CliRunner
    bypass this on purpose — in-process test invocations are not machine usage.
    """
    from basic_memory import bugs, cmdlog

    invocation = cmdlog.start(sys.argv[1:])
    try:
        app()
    except SystemExit as exc:
        # Typer/Click deliver every handled outcome this way: 0 for success,
        # 1 for verb errors, 2 for usage errors (typos — captured by decision).
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        cmdlog.finish(invocation, code)
        if code != 0:
            bugs.autocapture(invocation.command, code, None)
        raise
    except KeyboardInterrupt:
        cmdlog.finish(invocation, 130)  # observed, never captured — not a defect
        raise
    except BaseException as exc:
        cmdlog.finish(invocation, 1)
        bugs.autocapture(invocation.command, 1, exc)
        raise
    else:
        cmdlog.finish(invocation, 0)


if __name__ == "__main__":  # pragma: no cover
    main()
