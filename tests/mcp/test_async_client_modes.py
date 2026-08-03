"""Tests for the local ASGI async client."""

from typing import AsyncIterator

import httpx
import pytest

from basic_memory.mcp import async_client as async_client_module
from basic_memory.mcp.async_client import get_client


@pytest.fixture(autouse=True)
def _reset_async_client_state():
    async_client_module._prepared_local_asgi_databases.clear()
    async_client_module._prepared_local_asgi_database_prepare_locks.clear()
    yield
    async_client_module._prepared_local_asgi_databases.clear()
    async_client_module._prepared_local_asgi_database_prepare_locks.clear()


@pytest.mark.asyncio
async def test_get_client_default_uses_local_asgi_transport(config_manager):
    cfg = config_manager.load_config()
    config_manager.save_config(cfg)

    async with get_client() as client:
        assert isinstance(client._transport, httpx.ASGITransport)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_get_client_preinitializes_local_asgi_database(config_manager, monkeypatch):
    """Local ASGI routing initializes DB state before request handling."""
    from basic_memory import db
    from basic_memory.api.app import app as fastapi_app

    cfg = config_manager.load_config()
    config_manager.save_config(cfg)

    previous_engine = getattr(fastapi_app.state, "engine", None)
    previous_session_maker = getattr(fastapi_app.state, "session_maker", None)
    fastapi_app.state._state.pop("engine", None)  # pyright: ignore[reportPrivateUsage]
    fastapi_app.state._state.pop("session_maker", None)  # pyright: ignore[reportPrivateUsage]

    engine = object()
    session_maker = object()
    calls = []

    async def fake_get_or_create_db(db_path):
        calls.append(db_path)
        return engine, session_maker

    monkeypatch.setattr(db, "get_or_create_db", fake_get_or_create_db)

    # These tests wire stub engine/session objects; the empty-registry project
    # sync (GAPS B2) needs a real DB and is covered in test_initialization.
    from basic_memory.services import initialization as init_mod

    async def fake_registry_sync(app_config):
        return None

    monkeypatch.setattr(init_mod, "ensure_project_registry", fake_registry_sync)

    try:
        async with get_client() as client:
            assert isinstance(client._transport, httpx.ASGITransport)  # pyright: ignore[reportPrivateUsage]
            assert calls == [cfg.database_path]
            assert fastapi_app.state.engine is engine
            assert fastapi_app.state.session_maker is session_maker
        assert not hasattr(fastapi_app.state, "engine")
        assert not hasattr(fastapi_app.state, "session_maker")
    finally:
        if previous_engine is None:
            fastapi_app.state._state.pop("engine", None)  # pyright: ignore[reportPrivateUsage]
        else:
            fastapi_app.state.engine = previous_engine
        if previous_session_maker is None:
            fastapi_app.state._state.pop("session_maker", None)  # pyright: ignore[reportPrivateUsage]
        else:
            fastapi_app.state.session_maker = previous_session_maker


@pytest.mark.parametrize("async_override", [False, True])
@pytest.mark.asyncio
async def test_get_client_uses_existing_local_asgi_database_override(
    config_manager,
    async_override,
):
    """Local ASGI routing honors FastAPI test dependency overrides."""
    from basic_memory.api.app import app as fastapi_app
    from basic_memory.deps import get_engine_factory

    cfg = config_manager.load_config()
    config_manager.save_config(cfg)

    previous_overrides = dict(fastapi_app.dependency_overrides)
    previous_engine = getattr(fastapi_app.state, "engine", None)
    previous_session_maker = getattr(fastapi_app.state, "session_maker", None)
    fastapi_app.state._state.pop("engine", None)  # pyright: ignore[reportPrivateUsage]
    fastapi_app.state._state.pop("session_maker", None)  # pyright: ignore[reportPrivateUsage]

    engine = object()
    session_maker = object()
    calls = []

    if async_override:

        async def override_engine_factory():
            calls.append("override")
            return engine, session_maker

    else:

        def override_engine_factory():
            calls.append("override")
            return engine, session_maker

    fastapi_app.dependency_overrides[get_engine_factory] = override_engine_factory

    try:
        async with get_client() as client:
            assert isinstance(client._transport, httpx.ASGITransport)  # pyright: ignore[reportPrivateUsage]
            assert calls == ["override"]
            assert fastapi_app.state.engine is engine
            assert fastapi_app.state.session_maker is session_maker
        assert not hasattr(fastapi_app.state, "engine")
        assert not hasattr(fastapi_app.state, "session_maker")
    finally:
        fastapi_app.dependency_overrides = previous_overrides
        if previous_engine is None:
            fastapi_app.state._state.pop("engine", None)  # pyright: ignore[reportPrivateUsage]
        else:
            fastapi_app.state.engine = previous_engine
        if previous_session_maker is None:
            fastapi_app.state._state.pop("session_maker", None)  # pyright: ignore[reportPrivateUsage]
        else:
            fastapi_app.state.session_maker = previous_session_maker


@pytest.mark.asyncio
async def test_get_client_resolves_local_asgi_database_override_with_fastapi_di(
    config_manager,
):
    """Local ASGI DB pre-init honors FastAPI dependency injection and cleanup."""
    from fastapi import Request

    from basic_memory.api.app import app as fastapi_app
    from basic_memory.deps import get_engine_factory

    cfg = config_manager.load_config()
    config_manager.save_config(cfg)

    previous_overrides = dict(fastapi_app.dependency_overrides)
    previous_engine = getattr(fastapi_app.state, "engine", None)
    previous_session_maker = getattr(fastapi_app.state, "session_maker", None)
    fastapi_app.state._state.pop("engine", None)  # pyright: ignore[reportPrivateUsage]
    fastapi_app.state._state.pop("session_maker", None)  # pyright: ignore[reportPrivateUsage]

    engine = object()
    session_maker = object()
    calls = []
    cleanup = []

    async def override_engine_factory(
        request: Request,
    ) -> AsyncIterator[tuple[object, object]]:
        calls.append(request.app)
        try:
            yield engine, session_maker
        finally:
            cleanup.append("closed")

    fastapi_app.dependency_overrides[get_engine_factory] = override_engine_factory

    try:
        async with get_client() as client:
            assert isinstance(client._transport, httpx.ASGITransport)  # pyright: ignore[reportPrivateUsage]
            assert calls == [fastapi_app]
            assert cleanup == []
            assert fastapi_app.state.engine is engine
            assert fastapi_app.state.session_maker is session_maker
        assert cleanup == ["closed"]
        assert not hasattr(fastapi_app.state, "engine")
        assert not hasattr(fastapi_app.state, "session_maker")
    finally:
        fastapi_app.dependency_overrides = previous_overrides
        if previous_engine is None:
            fastapi_app.state._state.pop("engine", None)  # pyright: ignore[reportPrivateUsage]
        else:
            fastapi_app.state.engine = previous_engine
        if previous_session_maker is None:
            fastapi_app.state._state.pop("session_maker", None)  # pyright: ignore[reportPrivateUsage]
        else:
            fastapi_app.state.session_maker = previous_session_maker


@pytest.mark.asyncio
async def test_get_client_keeps_local_asgi_database_during_overlapping_contexts(
    config_manager,
    monkeypatch,
):
    """Local ASGI database state stays installed until the last overlapping client exits."""
    from basic_memory import db
    from basic_memory.api.app import app as fastapi_app

    cfg = config_manager.load_config()
    config_manager.save_config(cfg)

    previous_engine = getattr(fastapi_app.state, "engine", None)
    previous_session_maker = getattr(fastapi_app.state, "session_maker", None)
    fastapi_app.state._state.pop("engine", None)  # pyright: ignore[reportPrivateUsage]
    fastapi_app.state._state.pop("session_maker", None)  # pyright: ignore[reportPrivateUsage]

    engine = object()
    session_maker = object()
    calls = []

    async def fake_get_or_create_db(db_path):
        calls.append(db_path)
        return engine, session_maker

    monkeypatch.setattr(db, "get_or_create_db", fake_get_or_create_db)

    # These tests wire stub engine/session objects; the empty-registry project
    # sync (GAPS B2) needs a real DB and is covered in test_initialization.
    from basic_memory.services import initialization as init_mod

    async def fake_registry_sync(app_config):
        return None

    monkeypatch.setattr(init_mod, "ensure_project_registry", fake_registry_sync)

    first_context = get_client()
    second_context = get_client()
    first_entered = False
    second_entered = False
    first_exited = False

    try:
        first_client = await first_context.__aenter__()
        first_entered = True
        second_client = await second_context.__aenter__()
        second_entered = True

        assert isinstance(first_client._transport, httpx.ASGITransport)  # pyright: ignore[reportPrivateUsage]
        assert isinstance(second_client._transport, httpx.ASGITransport)  # pyright: ignore[reportPrivateUsage]
        assert calls == [cfg.database_path]

        await first_context.__aexit__(None, None, None)
        first_exited = True

        assert fastapi_app.state.engine is engine
        assert fastapi_app.state.session_maker is session_maker

        await second_context.__aexit__(None, None, None)
        second_entered = False

        assert not hasattr(fastapi_app.state, "engine")
        assert not hasattr(fastapi_app.state, "session_maker")
    finally:
        if second_entered:
            await second_context.__aexit__(None, None, None)
        if first_entered and not first_exited:
            await first_context.__aexit__(None, None, None)

        if previous_engine is None:
            fastapi_app.state._state.pop("engine", None)  # pyright: ignore[reportPrivateUsage]
        else:
            fastapi_app.state.engine = previous_engine
        if previous_session_maker is None:
            fastapi_app.state._state.pop("session_maker", None)  # pyright: ignore[reportPrivateUsage]
        else:
            fastapi_app.state.session_maker = previous_session_maker
