"""Fixtures for semantic search benchmark tests.

Provides a SQLite engine factory and a ``SearchCombo`` descriptor used to
parameterize benchmarks over the embedding providers the suite covers.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from sqlalchemy import text

from basic_memory import db
from basic_memory.config import BasicMemoryConfig
from basic_memory.db import DatabaseType, engine_session_factory
from basic_memory.markdown import EntityParser
from basic_memory.markdown.markdown_processor import MarkdownProcessor
from basic_memory.models.base import Base
from basic_memory.models.search import CREATE_SEARCH_INDEX
from basic_memory.repository.embedding_provider import EmbeddingProvider
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.project_repository import ProjectRepository
from basic_memory.services.file_service import FileService
from basic_memory.services.search_service import SearchService

# Load .env so OPENAI_API_KEY (and other keys) are available to providers
load_dotenv()


# --- Combo descriptor ---


@dataclass(frozen=True)
class SearchCombo:
    """Describes a provider configuration for benchmark parameterization."""

    name: str
    provider_name: str | None  # None = FTS-only
    dimensions: int | None


# All combinations the suite covers
ALL_COMBOS = [
    SearchCombo("sqlite-fts", None, None),
    SearchCombo("sqlite-fastembed", "fastembed", 384),
]

# Benchmark queries compare ranking quality across providers rather than enforcing
# the stricter production retrieval cutoff. OpenAI paraphrase matches cluster near
# ~0.37 in this corpus, so the default 0.55 filter hides otherwise-correct results.
BENCHMARK_MIN_SIMILARITY = 0.3


# --- Skip guards ---


def _fastembed_available() -> bool:
    try:
        import fastembed  # noqa: F401

        return True
    except ImportError:
        return False


def skip_if_needed(combo: SearchCombo) -> None:
    """Skip the current test if the combo's requirements aren't met."""
    if combo.provider_name == "fastembed" and not _fastembed_available():
        pytest.skip("fastembed not installed (install/update basic-memory)")


# --- Engine factory ---


@pytest_asyncio.fixture
async def sqlite_engine_factory(tmp_path):
    """Create a SQLite engine + session factory for benchmark use."""
    db_path = tmp_path / "bench.db"

    async with engine_session_factory(db_path, DatabaseType.FILESYSTEM) as (
        engine,
        session_maker,
    ):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with db.scoped_session(session_maker) as session:
            await session.execute(text("DROP TABLE IF EXISTS search_index"))
            await session.execute(CREATE_SEARCH_INDEX)
            await session.commit()

        yield engine, session_maker


# --- Embedding provider factory ---


def _create_fastembed_provider() -> EmbeddingProvider:
    from basic_memory.repository.fastembed_provider import FastEmbedEmbeddingProvider

    return FastEmbedEmbeddingProvider(model_name="bge-small-en-v1.5", batch_size=64)


# --- Search service factory ---


async def create_search_service(
    engine_factory_result,
    combo: SearchCombo,
    tmp_path: Path,
    embedding_provider: EmbeddingProvider | None = None,
) -> SearchService:
    """Build a fully wired SearchService for a given combo."""
    engine, session_maker = engine_factory_result

    # Create test project
    project_repo = ProjectRepository()
    async with db.scoped_session(session_maker) as session:
        project = await project_repo.create(
            session,
            {
                "name": "bench-project",
                "description": "Semantic benchmark project",
                "path": str(tmp_path),
                "is_active": True,
                "is_default": True,
            },
        )

    # Build app config
    semantic_enabled = combo.provider_name is not None
    app_config = BasicMemoryConfig(
        env="test",
        projects={"bench-project": str(tmp_path)},
        default_project="bench-project",
        semantic_search_enabled=semantic_enabled,
        semantic_min_similarity=BENCHMARK_MIN_SIMILARITY,
    )

    from basic_memory.repository.sqlite_search_repository import SQLiteSearchRepository

    search_repo = SQLiteSearchRepository(
        session_maker,
        project_id=project.id,
        app_config=app_config,
    )
    # Benchmarks pick the provider explicitly rather than letting config resolution choose one.
    if embedding_provider is not None:
        search_repo._semantic_enabled = True
        search_repo._embedding_provider = embedding_provider
        search_repo._vector_dimensions = embedding_provider.dimensions
        search_repo._vector_tables_initialized = False

    entity_repo = EntityRepository(project_id=project.id)
    entity_parser = EntityParser(tmp_path)
    markdown_processor = MarkdownProcessor(entity_parser)
    file_service = FileService(tmp_path, markdown_processor)

    service = SearchService(
        search_repo,
        entity_repo,
        file_service,
        session_maker=session_maker,
    )
    await service.init_search_index()
    return service
