"""The event loop every DB-touching CLI verb runs on.

This module exists to keep the MCP client graph off the native verbs. It sits
below `cli/commands/command_utils.py`, which imports `basic_memory.mcp.async_client`
and `basic_memory.mcp.clients` for the client-routed helpers — an import graph
that `project list`, `types`, `ls`, `doctor` and the rest never call (GAPS.md T30).

The saving is ~0.04 s, not the ~0.25 s T30 opened with; the measurement and its
correction are in that entry. The reason to keep the split is structural: every
new native verb would otherwise inherit a client graph it never uses.

**Nothing here may import `basic_memory.mcp` or `basic_memory.api`, at module
level or inside a function.** The native-command import guard
(`tests/cli/test_native_command_import_guard.py`) bans both graphs and is what
keeps that true.
"""

import asyncio
from typing import Any, Coroutine, TypeVar

import typer

T = TypeVar("T")


def run_with_cleanup(coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine with proper database cleanup.

    This helper ensures database connections are cleaned up before the
    event loop closes, preventing process hangs in CLI commands.

    Args:
        coro: The coroutine to run

    Returns:
        The result of the coroutine
    """
    # Deferred: basic_memory.db pulls SQLAlchemy + Alembic, which must not load
    # at CLI import time — only when a command actually runs (#886).
    from basic_memory import db
    from basic_memory.index.local_schedulers import drain_background_tasks

    async def _with_cleanup() -> T:
        try:
            return await coro
        finally:
            # Note writes materialize inline, but the follow-up work they
            # scheduled (vector sync, relation resolution) is still deferred:
            # cancelling it at loop close would leave semantic search and
            # inbound wikilinks stale until a later reindex.
            await drain_background_tasks()
            await db.shutdown_db()

    try:
        return asyncio.run(_with_cleanup())
    except db.NewerSchemaError as e:
        # Every DB-touching CLI verb funnels through here, so one catch turns
        # "older build over a newer DB" into the contract's error shape —
        # message on its own line, exit 1 (GAPS T11, W20 rule 6).
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
