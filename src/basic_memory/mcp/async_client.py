from asyncio import Lock
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Annotated, Any, AsyncIterator

from httpx import ASGITransport, AsyncClient, Timeout
from loguru import logger

if TYPE_CHECKING:
    # FastAPI is only needed when a request routes through the local ASGI
    # transport; importing it at module level costs ~0.1s on every CLI start (#886).
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

LocalDatabaseState = tuple["AsyncEngine", "async_sessionmaker[AsyncSession]"]
_MISSING_STATE_VALUE = object()


@dataclass
class _PreparedLocalAsgiDatabase:
    active_count: int
    previous_engine: object
    previous_session_maker: object
    dependency_context: AbstractAsyncContextManager[LocalDatabaseState]


_prepared_local_asgi_database_lock = RLock()
_prepared_local_asgi_database_prepare_locks: dict["FastAPI", Lock] = {}
_prepared_local_asgi_databases: dict["FastAPI", _PreparedLocalAsgiDatabase] = {}


def _build_timeout() -> Timeout:
    """Create a standard timeout config used across all clients."""
    return Timeout(
        connect=10.0,
        read=30.0,
        write=30.0,
        pool=30.0,
    )


def _build_asgi_client(app: "FastAPI", timeout: Timeout) -> AsyncClient:
    """Create a local ASGI client for an already-prepared FastAPI app."""
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=timeout,
    )


def _get_prepared_local_asgi_database_prepare_lock(app: "FastAPI") -> Lock:
    """Get the async lock that serializes first-time DB preparation for an app."""
    with _prepared_local_asgi_database_lock:
        prepare_lock = _prepared_local_asgi_database_prepare_locks.get(app)
        if prepare_lock is None:
            prepare_lock = Lock()
            _prepared_local_asgi_database_prepare_locks[app] = prepare_lock
        return prepare_lock


@asynccontextmanager
async def _resolve_local_asgi_database(app: "FastAPI") -> AsyncIterator[LocalDatabaseState]:
    """Resolve database state for a local ASGI request."""
    # Imported on first local-ASGI use so CLI startup never pays for FastAPI (#886).
    from fastapi import Depends, Request
    from fastapi.dependencies.utils import get_dependant, solve_dependencies

    from basic_memory.deps import get_engine_factory

    async def resolve_database_state(
        database_state: Annotated[LocalDatabaseState, Depends(get_engine_factory)],
    ) -> LocalDatabaseState:
        return database_state

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "app": app,
        "path_params": {},
    }

    async with AsyncExitStack() as request_stack, AsyncExitStack() as function_stack:
        scope["fastapi_inner_astack"] = request_stack
        scope["fastapi_function_astack"] = function_stack
        request = Request(scope)
        dependant = get_dependant(path="/", call=resolve_database_state)
        solved = await solve_dependencies(
            request=request,
            dependant=dependant,
            dependency_overrides_provider=app,
            async_exit_stack=request_stack,
            embed_body_fields=False,
        )
        if solved.errors:
            raise RuntimeError(f"Failed to resolve local ASGI database dependency: {solved.errors}")

        yield await resolve_database_state(**solved.values)


def _retain_prepared_local_asgi_database(app: "FastAPI") -> bool:
    """Retain an active local ASGI database preparation if one exists."""
    with _prepared_local_asgi_database_lock:
        active = _prepared_local_asgi_databases.get(app)
        if active is None:
            return False

        active.active_count += 1
        return True


def _install_prepared_local_asgi_database(
    app: "FastAPI",
    database_state: LocalDatabaseState,
    dependency_context: AbstractAsyncContextManager[LocalDatabaseState],
) -> None:
    """Install local ASGI database state after dependency resolution."""
    with _prepared_local_asgi_database_lock:
        active = _prepared_local_asgi_databases.get(app)
        if active is not None:
            raise RuntimeError("Local ASGI database state installed while another state is active")

        previous_engine = getattr(app.state, "engine", _MISSING_STATE_VALUE)
        previous_session_maker = getattr(app.state, "session_maker", _MISSING_STATE_VALUE)
        engine, session_maker = database_state

        app.state.engine = engine
        app.state.session_maker = session_maker
        _prepared_local_asgi_databases[app] = _PreparedLocalAsgiDatabase(
            active_count=1,
            previous_engine=previous_engine,
            previous_session_maker=previous_session_maker,
            dependency_context=dependency_context,
        )


def _restore_local_asgi_state_attribute(app: "FastAPI", name: str, previous_value: object) -> None:
    """Restore a FastAPI app.state attribute captured before local ASGI preparation."""
    if previous_value is _MISSING_STATE_VALUE:
        if hasattr(app.state, name):
            delattr(app.state, name)
    else:
        setattr(app.state, name, previous_value)


def _release_prepared_local_asgi_database(
    app: "FastAPI",
) -> AbstractAsyncContextManager[LocalDatabaseState] | None:
    """Release local ASGI database state after a client context exits."""
    with _prepared_local_asgi_database_lock:
        active = _prepared_local_asgi_databases.get(app)
        if active is None:
            raise RuntimeError("Local ASGI database state released without a matching retain")

        active.active_count -= 1
        if active.active_count > 0:
            return None

        del _prepared_local_asgi_databases[app]
        _restore_local_asgi_state_attribute(app, "engine", active.previous_engine)
        _restore_local_asgi_state_attribute(
            app,
            "session_maker",
            active.previous_session_maker,
        )
        return active.dependency_context


@asynccontextmanager
async def _prepared_local_asgi_database(app: "FastAPI") -> AsyncIterator[None]:
    """Initialize local ASGI database state before the first request."""
    prepare_lock = _get_prepared_local_asgi_database_prepare_lock(app)
    async with prepare_lock:
        if not _retain_prepared_local_asgi_database(app):
            database_context = _resolve_local_asgi_database(app)
            database_state = await database_context.__aenter__()
            try:
                _install_prepared_local_asgi_database(app, database_state, database_context)
            except Exception:
                await database_context.__aexit__(None, None, None)
                raise

    try:
        yield
    finally:
        database_context = _release_prepared_local_asgi_database(app)
        if database_context is not None:
            await database_context.__aexit__(None, None, None)


@asynccontextmanager
async def _asgi_client(timeout: Timeout) -> AsyncIterator[AsyncClient]:
    """Create a local ASGI client."""
    # Import on first local-client use so CLI help/version paths can import
    # routing helpers without constructing the full FastAPI router graph.
    from basic_memory.api.app import app as fastapi_app

    # Trigger: local ASGITransport does not execute FastAPI lifespan startup.
    # Why: letting request dependencies initialize Postgres can run asyncpg DDL
    # under Starlette's request loop and trigger CPython's empty-ready-queue race.
    # Outcome: request handling sees the same app.state database objects as API
    # lifespan startup would have provided.
    async with _prepared_local_asgi_database(fastapi_app):
        async with _build_asgi_client(fastapi_app, timeout) as client:
            yield client


@asynccontextmanager
async def get_client() -> AsyncIterator[AsyncClient]:
    """Get an AsyncClient as a context manager.

    Every request is served in-process by the local FastAPI app over the ASGI
    transport, so there is nothing to route on.
    """
    logger.debug("Using ASGI client for local Basic Memory API")
    async with _asgi_client(_build_timeout()) as client:
        yield client
