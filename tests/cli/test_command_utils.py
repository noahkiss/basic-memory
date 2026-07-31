"""Tests for CLI command utilities."""

import basic_memory.db as db
import basic_memory.index.local_schedulers as local_schedulers
from basic_memory.cli.commands.command_utils import run_with_cleanup


def test_run_with_cleanup_drains_pending_work_before_db_shutdown(monkeypatch):
    """One-shot clients must drain the deferred follow-up work their writes
    scheduled (vector sync, relation resolution) before the DB is shut down and
    the event loop closes — otherwise semantic search is left stale."""
    calls: list[str] = []

    async def fake_drain_background() -> None:
        calls.append("drain-background")

    async def fake_shutdown() -> None:
        calls.append("shutdown")

    monkeypatch.setattr(local_schedulers, "drain_background_tasks", fake_drain_background)
    monkeypatch.setattr(db, "shutdown_db", fake_shutdown)

    async def work() -> int:
        calls.append("work")
        return 42

    result = run_with_cleanup(work())

    assert result == 42
    assert calls == ["work", "drain-background", "shutdown"]
