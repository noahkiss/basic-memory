"""The no-alembic head-stamp check that keeps migrations off the warm path (GAPS B4).

_single_alembic_head parses the migration files instead of importing alembic,
so its one correctness risk is drift from what alembic itself would compute.
The parity test pins that. The stamp tests cover the three DB states: fresh
(no table), current, and stale.
"""

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from basic_memory.db import _single_alembic_head, _stamped_at_single_head


def test_single_head_matches_alembic_own_answer():
    """Parity: the regex parse must agree with alembic's ScriptDirectory."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    import basic_memory.db as db_module

    config = Config()
    config.set_main_option("script_location", str(Path(db_module.__file__).parent / "alembic"))
    (alembic_head,) = ScriptDirectory.from_config(config).get_heads()

    assert _single_alembic_head() == alembic_head


@pytest_asyncio.fixture
async def session_maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/stamp.db")
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.asyncio
async def test_fresh_db_is_not_stamped(session_maker):
    assert await _stamped_at_single_head(session_maker) is False


@pytest.mark.asyncio
async def test_current_stamp_is_recognized(session_maker):
    head = _single_alembic_head()
    async with session_maker() as session:
        await session.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
        await session.execute(text("INSERT INTO alembic_version VALUES (:v)").bindparams(v=head))
        await session.commit()
    assert await _stamped_at_single_head(session_maker) is True


@pytest.mark.asyncio
async def test_stale_stamp_is_not_recognized(session_maker):
    async with session_maker() as session:
        await session.execute(text("CREATE TABLE alembic_version (version_num TEXT)"))
        await session.execute(text("INSERT INTO alembic_version VALUES ('0000deadbeef')"))
        await session.commit()
    assert await _stamped_at_single_head(session_maker) is False
