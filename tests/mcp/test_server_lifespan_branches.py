import pytest

import basic_memory.mcp.server as server_module
from basic_memory import db
from basic_memory.mcp.server import lifespan, mcp


@pytest.mark.asyncio
async def test_mcp_lifespan_sync_disabled_branch(config_manager):
    cfg = config_manager.load_config()
    cfg.index_changes = False
    config_manager.save_config(cfg)

    async with lifespan(mcp):
        pass


@pytest.mark.asyncio
async def test_mcp_lifespan_sync_enabled_branch(config_manager):
    cfg = config_manager.load_config()
    cfg.index_changes = True
    config_manager.save_config(cfg)

    async with lifespan(mcp):
        pass


@pytest.mark.asyncio
async def test_mcp_lifespan_shuts_down_db_when_engine_was_none(config_manager):
    db._engine = None
    async with lifespan(mcp):
        pass


@pytest.mark.asyncio
async def test_mcp_lifespan_drains_pending_work_before_db_shutdown(config_manager, monkeypatch):
    """Shutdown must drain deferred background tasks (vector sync, relation
    resolution) before the DB closes — cancelling them at loop close would leave
    semantic search and inbound wikilinks stale until a later reindex."""
    calls: list[str] = []

    async def record_drain_background_tasks() -> None:
        calls.append("drain_background_tasks")

    real_shutdown_db = db.shutdown_db

    async def record_shutdown_db() -> None:
        calls.append("shutdown_db")
        await real_shutdown_db()

    monkeypatch.setattr(server_module, "drain_background_tasks", record_drain_background_tasks)
    monkeypatch.setattr(db, "shutdown_db", record_shutdown_db)

    db._engine = None
    async with lifespan(mcp):
        pass

    assert calls == ["drain_background_tasks", "shutdown_db"]
