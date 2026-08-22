import asyncio
import os
import re
import sys
from contextlib import asynccontextmanager, suppress
from enum import Enum, auto
from pathlib import Path
from typing import AsyncGenerator, Optional

from basic_memory.config import BasicMemoryConfig, ConfigManager

from loguru import logger
from sqlalchemy import text, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine,
    async_scoped_session,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository

# -----------------------------------------------------------------------------
# Windows event loop policy
# -----------------------------------------------------------------------------
# On Windows, the default ProactorEventLoop has known rough edges with aiosqlite
# during shutdown/teardown (threads posting results to a loop that's closing),
# which can manifest as:
# - "RuntimeError: Event loop is closed"
# - "IndexError: pop from an empty deque"
#
# The SelectorEventLoop doesn't support subprocess operations, so code that uses
# asyncio.create_subprocess_shell() (like sync_service._quick_count_files) must
# detect Windows and use fallback implementations.
if sys.platform == "win32":  # pragma: no cover
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# Module level state
_engine: Optional[AsyncEngine] = None
_session_maker: Optional[async_sessionmaker[AsyncSession]] = None


class DatabaseType(Enum):
    """Types of supported databases."""

    MEMORY = auto()
    FILESYSTEM = auto()

    @classmethod
    def get_db_url(cls, db_path: Path, db_type: "DatabaseType") -> str:
        """Get SQLAlchemy URL for database path.

        Args:
            db_path: Path to SQLite database file
            db_type: Type of database (MEMORY or FILESYSTEM)

        Returns:
            SQLAlchemy connection URL
        """
        if db_type == cls.MEMORY:
            logger.info("Using in-memory SQLite database")
            return "sqlite+aiosqlite://"

        return f"sqlite+aiosqlite:///{db_path}"  # pragma: no cover


def get_scoped_session_factory(
    session_maker: async_sessionmaker[AsyncSession],
) -> async_scoped_session:
    """Create a scoped session factory scoped to current task."""
    return async_scoped_session(session_maker, scopefunc=asyncio.current_task)


@asynccontextmanager
async def scoped_session(
    session_maker: async_sessionmaker[AsyncSession],
    session: AsyncSession | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Get a scoped session with proper lifecycle management.

    This is the one shared session-scope seam for services and indexing code.
    It covers both real usage variants:

    - ``session`` provided: the caller-owned session is yielded unchanged and
      the caller keeps commit/rollback ownership (composed multi-step writes).
    - ``session`` omitted: a fresh task-scoped session is opened that commits
      on success, rolls back on error, and always closes.

    Args:
        session_maker: Session maker to create scoped sessions from
        session: Optional caller-owned session to reuse instead of opening one
    """
    # Trigger: the caller already owns a transaction and passes its session in.
    # Why: nested scopes must not commit or roll back mid-way through the
    # caller's composed write; transaction ownership stays with the opener.
    # Outcome: yield the session untouched and let the outermost scope finish it.
    if session is not None:
        yield session
        return

    factory = get_scoped_session_factory(session_maker)
    owned_session = factory()
    try:
        # SQLite disables foreign-key enforcement per connection by default, so the
        # constraints the models declare only bind if the PRAGMA is set on each session.
        await owned_session.execute(text("PRAGMA foreign_keys=ON"))

        yield owned_session
        await owned_session.commit()
    except Exception:
        await owned_session.rollback()
        raise
    finally:
        await owned_session.close()
        await factory.remove()


_SQLITE_SYNCHRONOUS = {"OFF", "NORMAL", "FULL", "EXTRA"}


def _configure_sqlite_connection(
    dbapi_conn,
    enable_wal: bool = True,
    *,
    synchronous: str = "NORMAL",
    mmap_size: int = 0,
    wal_autocheckpoint: int = 1000,
    page_size: int = 0,
) -> None:
    """Configure a SQLite connection with WAL mode and tunable performance PRAGMAs.

    Args:
        dbapi_conn: Database API connection object
        enable_wal: Whether to enable WAL mode (should be False for in-memory databases)
        synchronous: PRAGMA synchronous level (OFF/NORMAL/FULL/EXTRA)
        mmap_size: PRAGMA mmap_size bytes (0 = disabled)
        wal_autocheckpoint: PRAGMA wal_autocheckpoint pages (0 = disabled; WAL only)
        page_size: PRAGMA page_size bytes (0 = leave default; only affects new DBs)
    """
    cursor = dbapi_conn.cursor()
    try:
        # page_size must be set before the database is written to take effect, so
        # do it first; it is a no-op on an already-populated DB.
        if page_size:
            cursor.execute(f"PRAGMA page_size={int(page_size)}")
        # Enable WAL mode for better concurrency (not supported for in-memory databases)
        if enable_wal:
            cursor.execute("PRAGMA journal_mode=WAL")
        # Set busy timeout to handle locked databases
        cursor.execute("PRAGMA busy_timeout=10000")  # 10 seconds
        # synchronous: OFF trades durability for write throughput. Safe here because
        # the markdown files are the source of truth and the index DB rebuilds from
        # them via sync — a crash means a re-sync, not data loss. Validate the token
        # since it is interpolated into the PRAGMA.
        sync = synchronous.upper() if synchronous.upper() in _SQLITE_SYNCHRONOUS else "NORMAL"
        cursor.execute(f"PRAGMA synchronous={sync}")
        cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
        cursor.execute("PRAGMA temp_store=MEMORY")
        # mmap_size: memory-map the DB for faster reads, including the lookups
        # inside writes (link/permalink resolution, FTS).
        if mmap_size:
            cursor.execute(f"PRAGMA mmap_size={int(mmap_size)}")
        # wal_autocheckpoint: checkpoint less often to avoid writer stalls under
        # sustained write bursts (WAL only).
        if enable_wal and wal_autocheckpoint:
            cursor.execute(f"PRAGMA wal_autocheckpoint={int(wal_autocheckpoint)}")
        # Windows-specific optimizations
        if os.name == "nt":
            cursor.execute("PRAGMA locking_mode=NORMAL")  # Ensure normal locking on Windows
    except Exception as e:
        # Log but don't fail - some PRAGMAs may not be supported
        logger.warning(f"Failed to configure SQLite connection: {e}")
    finally:
        cursor.close()


def _create_sqlite_engine(
    db_url: str, db_type: DatabaseType, config: Optional[BasicMemoryConfig] = None
) -> AsyncEngine:
    """Create SQLite async engine with appropriate configuration.

    Args:
        db_url: SQLite connection URL
        db_type: Database type (MEMORY or FILESYSTEM)
        config: Optional config supplying the tunable SQLite PRAGMAs

    Returns:
        Configured async engine for SQLite
    """
    # Configure connection args with Windows-specific settings
    connect_args: dict[str, bool | float] = {"check_same_thread": False}

    # Add Windows-specific parameters to improve reliability
    if os.name == "nt":  # Windows
        connect_args.update(
            {
                "timeout": 30.0,  # Increase timeout to 30 seconds for Windows
            }
        )

    if db_type == DatabaseType.MEMORY:
        # Trigger: an in-memory SQLite URL would default to StaticPool, which hands the
        # same DBAPI connection to every concurrently checked-out session.
        # Why: concurrent asyncio tasks then share one transaction scope — a rollback
        # issued by one session (scoped_session exception handling or the pool's
        # reset-on-return) silently destroys another session's uncommitted writes (#940).
        # Outcome: a single-connection blocking queue pool keeps the in-memory database
        # alive for the engine's lifetime while serializing sessions at transaction
        # granularity, restoring the isolation the repositories assume.
        engine = create_async_engine(
            db_url,
            connect_args=connect_args,
            poolclass=AsyncAdaptedQueuePool,
            pool_size=1,
            max_overflow=0,
        )
    elif os.name == "nt":
        # Use NullPool for Windows filesystem databases to avoid connection pooling issues
        engine = create_async_engine(
            db_url,
            connect_args=connect_args,
            poolclass=NullPool,  # Disable connection pooling on Windows
            echo=False,
        )
    else:
        engine = create_async_engine(db_url, connect_args=connect_args)

    # Enable WAL mode for better concurrency and reliability
    # Note: WAL mode is not supported for in-memory databases
    enable_wal = db_type != DatabaseType.MEMORY
    # Snapshot the tunable PRAGMAs once (config is process-stable) so the per-connect
    # listener doesn't re-read config on every pooled connection.
    synchronous = config.sqlite_synchronous if config else "NORMAL"
    mmap_size = config.sqlite_mmap_size if config else 0
    wal_autocheckpoint = config.sqlite_wal_autocheckpoint if config else 1000
    page_size = config.sqlite_page_size if config else 0

    @event.listens_for(engine.sync_engine, "connect")
    def enable_wal_mode(dbapi_conn, connection_record):
        """Apply WAL + tunable PRAGMAs on each connection."""
        _configure_sqlite_connection(
            dbapi_conn,
            enable_wal=enable_wal,
            synchronous=synchronous,
            mmap_size=mmap_size,
            wal_autocheckpoint=wal_autocheckpoint,
            page_size=page_size,
        )

    return engine


def _create_engine_and_session(
    db_path: Path,
    db_type: DatabaseType = DatabaseType.FILESYSTEM,
    config: Optional[BasicMemoryConfig] = None,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Internal helper to create engine and session maker.

    Args:
        db_path: Path to database file
        db_type: Type of database (MEMORY or FILESYSTEM)
        config: Optional explicit config. If not provided, reads from ConfigManager.
            Prefer passing explicitly from composition roots.

    Returns:
        Tuple of (engine, session_maker)
    """
    # Prefer explicit parameter; fall back to ConfigManager for backwards compatibility
    if config is None:
        config = ConfigManager().config
    db_url = DatabaseType.get_db_url(db_path, db_type)
    logger.debug(f"Creating engine for db_url: {db_url}")

    engine = _create_sqlite_engine(db_url, db_type, config)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_maker


class NewerSchemaError(RuntimeError):
    """The database was migrated by a newer build than the one running.

    Alembic cannot downgrade a schema it has never seen, so the only ways out
    are reinstalling the newer build or rebuilding the index. Raised before
    ``command.upgrade`` so the user gets this message instead of alembic's
    internal "Can't locate revision" error (GAPS.md T11).
    """

    def __init__(self, revision: str):
        super().__init__(
            f"This database was migrated by a newer Basic Memory build "
            f"(revision '{revision}' is not in this install). "
            f"Reinstall the newer build, or run `bm reset --reindex` to rebuild the index."
        )


def _scan_migration_files() -> tuple[set[str], set[str]] | None:
    """Parse the migration files for (revisions, parents), without importing alembic.

    Alembic costs ~0.17 s of import time beyond SQLAlchemy (GAPS.md B4), so
    schema-state questions on the warm path must be answered without it. The
    version files are the same source alembic itself reads: each assigns
    ``revision`` one quoted id and ``down_revision`` its parent id(s). Returns
    None on anything unparseable — callers then fall back to a real migration
    run, which is always safe.
    """
    versions_dir = Path(__file__).parent / "alembic" / "versions"
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in versions_dir.glob("*.py"):
        revision = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("revision") and revision is None:
                quoted = re.findall(r"['\"]([^'\"]+)['\"]", line.split("=", 1)[-1])
                if quoted:
                    revision = quoted[0]
            elif line.startswith("down_revision"):
                parents.update(re.findall(r"['\"]([^'\"]+)['\"]", line.split("=", 1)[-1]))
        if revision is None:
            return None
        revisions.add(revision)
    return revisions, parents


def _single_alembic_head() -> str | None:
    """Find the sole head revision: the one no other migration names as a parent.

    Returns None on anything unexpected (no files, several heads, unparseable)
    — the caller then falls back to a real migration run.
    """
    scanned = _scan_migration_files()
    if scanned is None:
        return None
    revisions, parents = scanned
    heads = revisions - parents
    if len(heads) != 1:
        return None
    return heads.pop()


async def _stamped_at_single_head(session_maker: async_sessionmaker[AsyncSession]) -> bool:
    """True if the database's alembic stamp already equals the sole head revision.

    Deciding this without importing alembic is the point (see
    _single_alembic_head). False on a missing alembic_version table (fresh DB)
    or any ambiguity — false only ever costs a redundant migration run, while a
    wrong true would silently skip one, so every doubtful case answers false.
    """
    head = _single_alembic_head()
    if head is None:
        return False
    try:
        async with scoped_session(session_maker) as session:
            result = await session.execute(text("SELECT version_num FROM alembic_version"))
            stamped = [row[0] for row in result]
    except OperationalError:
        # No alembic_version table: a fresh database that needs the real run.
        return False
    return stamped == [head]


async def _assert_no_newer_stamp(session_maker: async_sessionmaker[AsyncSession]) -> None:
    """Raise NewerSchemaError if the DB is stamped with a revision this tree has never seen.

    Here "upgrade" is `git pull` + reinstall, so rollback is a normal
    operation (GAPS.md T11) — an older build over a newer DB must die with an
    actionable message, not alembic's stack trace. Every doubtful case
    (unparseable files, no stamp table) returns silently: the real migration
    run that follows is the safe arbiter.
    """
    scanned = _scan_migration_files()
    if scanned is None:
        return
    known_revisions, _ = scanned
    try:
        async with scoped_session(session_maker) as session:
            result = await session.execute(text("SELECT version_num FROM alembic_version"))
            stamped = [row[0] for row in result]
    except OperationalError:
        # No alembic_version table: a fresh database, nothing to judge.
        return
    for revision in stamped:
        if revision not in known_revisions:
            raise NewerSchemaError(revision)


async def get_or_create_db(
    db_path: Path,
    db_type: DatabaseType = DatabaseType.FILESYSTEM,
    ensure_migrations: bool = True,
    config: Optional[BasicMemoryConfig] = None,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:  # pragma: no cover
    """Get or create database engine and session maker.

    Args:
        db_path: Path to database file
        db_type: Type of database
        ensure_migrations: Whether to run migrations
        config: Optional explicit config. If not provided, reads from ConfigManager.
            Prefer passing explicitly from composition roots.
    """
    global _engine, _session_maker

    # Prefer explicit parameter; fall back to ConfigManager for backwards compatibility
    if config is None:
        config = ConfigManager().config

    if _engine is None:
        _engine, _session_maker = _create_engine_and_session(db_path, db_type, config)

        # Run migrations automatically unless explicitly disabled. The stamp
        # check keeps alembic (~0.17 s of import) off the already-migrated
        # path — every doubtful case falls through to the real run (GAPS B4).
        if ensure_migrations and not await _stamped_at_single_head(_session_maker):
            # Not at head can mean behind (migrate) or *ahead* — a newer build's
            # stamp, which command.upgrade cannot resolve. Judge that first so
            # the user gets an actionable error, not alembic's (GAPS T11).
            await _assert_no_newer_stamp(_session_maker)
            await run_migrations(config, db_type)

    # These checks should never fail since we just created the engine and session maker
    # if they were None, but we'll check anyway for the type checker
    if _engine is None:
        logger.error("Failed to create database engine", db_path=str(db_path))
        raise RuntimeError("Database engine initialization failed")

    if _session_maker is None:
        logger.error("Failed to create session maker", db_path=str(db_path))
        raise RuntimeError("Session maker initialization failed")

    return _engine, _session_maker


def has_active_engine() -> bool:
    """True when a module-level engine is already open.

    A CLI verb that opens its own engine must dispose of it, and a verb that
    borrows one someone else opened must not: disposing a caller's engine kills
    an in-memory database outright and leaves a file-backed one bound to a loop
    that is about to close. Callers ask this before deciding (GAPS W5 item 6).
    """
    return _engine is not None


async def shutdown_db() -> None:  # pragma: no cover
    """Clean up database connections."""
    global _engine, _session_maker

    if _engine:
        # Trigger: teardown can run while the surrounding task is being cancelled
        # (e.g. lifespan shutdown, unshielded CLI cleanup).
        # Why: a cancellation landing mid-dispose surfaces the "IndexError: pop
        # from an empty deque" race in base_events._run_once (#831/#877) — the
        # same shutdown race the module header documents for Windows aiosqlite.
        # Shielding lets dispose finish atomically, and suppressing CancelledError
        # keeps a cancelled shutdown from re-raising the underlying race.
        # Outcome: connections always close cleanly even under cancellation.
        with suppress(asyncio.CancelledError):
            await asyncio.shield(_engine.dispose())
        _engine = None
        _session_maker = None


@asynccontextmanager
async def engine_session_factory(
    db_path: Path,
    db_type: DatabaseType = DatabaseType.MEMORY,
    config: Optional[BasicMemoryConfig] = None,
) -> AsyncGenerator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]], None]:
    """Create engine and session factory.

    Note: This is primarily used for testing where we want a fresh database
    for each test. For production use, use get_or_create_db() instead.

    Args:
        db_path: Path to database file
        db_type: Type of database
        config: Optional explicit config. If not provided, reads from ConfigManager.
    """

    global _engine, _session_maker

    # Use the same helper function as production code.
    #
    # Keep local references so teardown can deterministically dispose the
    # specific engine created by this context manager, even if other code calls
    # shutdown_db() and mutates module-level globals mid-test.
    created_engine, created_session_maker = _create_engine_and_session(db_path, db_type, config)
    _engine, _session_maker = created_engine, created_session_maker

    try:
        # Verify that engine and session maker are initialized
        if created_engine is None:  # pragma: no cover
            logger.error("Database engine is None in engine_session_factory")
            raise RuntimeError("Database engine initialization failed")

        if created_session_maker is None:  # pragma: no cover
            logger.error("Session maker is None in engine_session_factory")
            raise RuntimeError("Session maker initialization failed")

        yield created_engine, created_session_maker
    finally:
        # Trigger: context-manager teardown can run while the surrounding task is
        # being cancelled (e.g. a test aborting mid-fixture).
        # Why: a cancellation landing mid-dispose surfaces the "IndexError: pop
        # from an empty deque" shutdown race (#831/#877); shield the dispose and
        # suppress CancelledError to match the other dispose seams.
        # Outcome: the per-context engine always disposes cleanly under cancellation.
        with suppress(asyncio.CancelledError):
            await asyncio.shield(created_engine.dispose())

        # Only clear module-level globals if they still point to this context's
        # engine/session. This avoids clobbering newer globals from other callers.
        if _engine is created_engine:
            _engine = None
        if _session_maker is created_session_maker:
            _session_maker = None


async def run_migrations(
    app_config: BasicMemoryConfig, database_type=DatabaseType.FILESYSTEM
):  # pragma: no cover
    """Run any pending alembic migrations.

    Note: Alembic tracks which migrations have been applied via the alembic_version table,
    so it's safe to call this multiple times - it will only run pending migrations.
    """
    # Alembic costs ~0.17 s of import time beyond SQLAlchemy and is needed only
    # here, so it must stay off the read path (GAPS.md B4; guarded by
    # tests/cli/test_native_command_import_guard.py).
    from alembic import command
    from alembic.config import Config

    logger.info("Running database migrations...")
    temp_engine: AsyncEngine | None = None
    try:
        # Get the absolute path to the alembic directory relative to this file
        alembic_dir = Path(__file__).parent / "alembic"
        config = Config()

        # Set required Alembic config options programmatically
        config.set_main_option("script_location", str(alembic_dir))
        config.set_main_option(
            "file_template",
            "%%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s",
        )
        config.set_main_option("timezone", "UTC")
        config.set_main_option("revision_environment", "false")

        db_url = DatabaseType.get_db_url(app_config.database_path, database_type)
        config.set_main_option("sqlalchemy.url", db_url)

        command.upgrade(config, "head")
        logger.info("Migrations completed successfully")

        # Get session maker - ensure we don't trigger recursive migration calls
        if _session_maker is None:
            temp_engine, session_maker = _create_engine_and_session(
                app_config.database_path, database_type, app_config
            )
        else:
            session_maker = _session_maker

        # Initialize the search index schema (create the FTS5 virtual table).
        # The project_id is not used for init_search_index, so we pass a dummy value
        await SQLiteSearchRepository(session_maker, 1).init_search_index()

    except Exception as e:  # pragma: no cover
        logger.error(f"Error running migrations: {e}")
        raise
    finally:
        # Trigger: run_migrations() created a temporary engine while module-level
        # session maker was not initialized.
        # Why: temporary aiosqlite worker threads can outlive CLI command execution
        # and block process shutdown if the engine is not disposed. A cancellation
        # landing mid-dispose surfaces the same "IndexError: pop from an empty
        # deque" race as the other dispose seams (#831/#877), so shield the dispose
        # and suppress CancelledError to match them.
        # Outcome: always dispose temporary engines cleanly, even under cancellation.
        if temp_engine is not None:
            with suppress(asyncio.CancelledError):
                await asyncio.shield(temp_engine.dispose())
