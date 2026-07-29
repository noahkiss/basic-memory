"""Service for building rich context from the knowledge graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple, TYPE_CHECKING


from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from basic_memory import db
from basic_memory.repository.entity_repository import EntityRepository
from basic_memory.repository.observation_repository import ObservationRepository
from basic_memory.repository.search_repository import SearchRepository, SearchIndexRow
from basic_memory.schemas.memory import MemoryUrl, memory_url_path
from basic_memory.schemas.search import SearchItemType
from basic_memory.utils import generate_permalink

if TYPE_CHECKING:
    from basic_memory.services.link_resolver import LinkResolver


@dataclass
class ContextResultRow:
    type: str
    id: int
    title: str
    permalink: str
    file_path: str
    depth: int
    root_id: int
    created_at: datetime
    from_id: Optional[int] = None
    to_id: Optional[int] = None
    relation_type: Optional[str] = None
    to_name: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    entity_id: Optional[int] = None


@dataclass
class ContextResultItem:
    """A hierarchical result containing a primary item with its observations and related items."""

    primary_result: ContextResultRow | SearchIndexRow
    observations: List[ContextResultRow] = field(default_factory=list)
    related_results: List[ContextResultRow] = field(default_factory=list)


@dataclass
class ContextMetadata:
    """Metadata about a context result."""

    uri: Optional[str] = None
    types: Optional[List[SearchItemType]] = None
    depth: int = 1
    timeframe: Optional[str] = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    primary_count: int = 0
    related_count: int = 0
    total_observations: int = 0
    total_relations: int = 0
    has_more: bool = False


@dataclass
class ContextResult:
    """Complete context result with metadata."""

    results: List[ContextResultItem] = field(default_factory=list)
    metadata: ContextMetadata = field(default_factory=ContextMetadata)


class ContextService:
    """Service for building rich context from memory:// URIs.

    Handles three types of context building:
    1. Direct permalink lookup - exact match on path
    2. Pattern matching - using * wildcards
    3. Special modes via params (e.g., 'related')
    """

    def __init__(
        self,
        search_repository: SearchRepository,
        entity_repository: EntityRepository,
        observation_repository: ObservationRepository,
        link_resolver: Optional[LinkResolver] = None,
        session_maker: async_sessionmaker[AsyncSession] | None = None,
    ):
        self.search_repository = search_repository
        self.entity_repository = entity_repository
        self.observation_repository = observation_repository
        self.link_resolver = link_resolver
        self.session_maker = session_maker

    def _require_session_maker(self) -> async_sessionmaker[AsyncSession]:
        """Fail fast when a session-opening path runs without a session maker."""
        if self.session_maker is None:  # pragma: no cover
            raise ValueError("session_maker is required for ContextService")
        return self.session_maker

    async def build_context(
        self,
        memory_url: Optional[MemoryUrl] = None,
        types: Optional[List[SearchItemType]] = None,
        depth: int = 1,
        since: Optional[datetime] = None,
        limit=10,
        offset=0,
        max_related: int = 10,
        include_observations: bool = True,
    ) -> ContextResult:
        """Build rich context from a memory:// URI."""
        logger.debug(
            f"Building context for URI: '{memory_url}' depth: '{depth}' since: '{since}' limit: '{limit}' offset: '{offset}'  max_related: '{max_related}'"
        )

        fetch_limit = limit + 1

        normalized_path: Optional[str] = None
        if memory_url:
            path = memory_url_path(memory_url)
            has_wildcard = "*" in path

            if has_wildcard:
                parts = path.split("*")
                normalized_parts = [
                    generate_permalink(part, split_extension=False) if part else ""
                    for part in parts
                ]
                normalized_path = "*".join(normalized_parts)
                logger.debug(f"Pattern search for '{normalized_path}'")
                primary = await self.search_repository.search(
                    permalink_match=normalized_path, limit=fetch_limit, offset=offset
                )
            else:
                normalized_path = generate_permalink(path, split_extension=False)
                logger.debug(f"Direct lookup for '{normalized_path}'")
                primary = await self.search_repository.search(
                    permalink=normalized_path, limit=fetch_limit, offset=offset
                )

                # Trigger: an exact permalink lookup matched nothing.
                # Why: a memory:// URI is also allowed to name a note by title or
                #   file path, so one more *exact* resolution pass is warranted.
                # Outcome: strict resolution only. A non-strict resolve falls through
                #   to a relaxed FTS retry (`root* OR not* OR exist*`) and returns
                #   results[0], so a miss came back as a real note with exit 0 and the
                #   requested URI rewritten to whatever matched — a hit and a miss were
                #   indistinguishable to the caller, and which note came back was
                #   arbitrary. Every tend verb doing reverse traversal by id calls this
                #   path, where that shape blesses fabricated links. See GAPS.md T10.
                if not primary and self.link_resolver:
                    async with db.scoped_session(self._require_session_maker()) as session:
                        entity = await self.link_resolver.resolve_link(
                            path,
                            use_search=False,
                            strict=True,
                            session=session,
                        )
                    if entity:
                        logger.debug(
                            f"LinkResolver resolved '{path}' to permalink '{entity.permalink}'"
                        )
                        normalized_path = entity.permalink
                        primary = await self.search_repository.search(
                            permalink=entity.permalink,
                            limit=fetch_limit,
                            offset=offset,
                        )
        else:
            logger.debug(f"Build context for '{types}'")
            primary = await self.search_repository.search(
                search_item_types=types,
                after_date=since,
                limit=fetch_limit,
                offset=offset,
            )

        has_more = len(primary) > limit
        if has_more:
            primary = primary[:limit]

        type_id_pairs = [(r.type, r.id) for r in primary] if primary else []
        logger.debug(f"found primary type_id_pairs: {len(type_id_pairs)}")

        related = await self.find_related(
            type_id_pairs, max_depth=depth, since=since, max_results=max_related
        )
        logger.debug(f"Found {len(related)} related results")

        entity_ids = []
        for result in primary:
            if result.type == SearchItemType.ENTITY.value:
                entity_ids.append(result.id)

        for result in related:
            if result.type == SearchItemType.ENTITY.value:
                entity_ids.append(result.id)

        observations_by_entity = {}
        if include_observations and entity_ids:
            async with db.scoped_session(self._require_session_maker()) as session:
                observations_by_entity = await self.observation_repository.find_by_entities(
                    session, entity_ids
                )
            logger.debug(f"Found observations for {len(observations_by_entity)} entities")

        metadata = ContextMetadata(
            uri=normalized_path if memory_url else None,
            types=types,
            depth=depth,
            timeframe=since.isoformat() if since else None,
            primary_count=len(primary),
            related_count=len(related),
            total_observations=sum(len(obs) for obs in observations_by_entity.values()),
            total_relations=sum(1 for r in related if r.type == SearchItemType.RELATION),
            has_more=has_more,
        )

        context_results = []
        for primary_item in primary:
            related_to_primary = [r for r in related if r.root_id == primary_item.id]

            item_observations = []
            if primary_item.type == SearchItemType.ENTITY.value and include_observations:
                for obs in observations_by_entity.get(primary_item.id, []):
                    item_observations.append(
                        ContextResultRow(
                            type="observation",
                            id=obs.id,
                            title=f"{obs.category}: {obs.content[:50]}...",
                            # Observation.permalink is the single definition of the
                            # synthetic permalink format (200-char truncation plus
                            # content digest); rebuilding it inline diverged from the
                            # search index for long observations (#929). The parent
                            # entity is eager-loaded by ObservationRepository.
                            permalink=obs.permalink,
                            file_path=primary_item.file_path,
                            content=obs.content,
                            category=obs.category,
                            entity_id=primary_item.id,
                            depth=0,
                            root_id=primary_item.id,
                            created_at=primary_item.created_at,
                        )
                    )

            context_results.append(
                ContextResultItem(
                    primary_result=primary_item,
                    observations=item_observations,
                    related_results=related_to_primary,
                )
            )

        return ContextResult(results=context_results, metadata=metadata)

    async def find_related(
        self,
        type_id_pairs: List[Tuple[str, int]],
        max_depth: int = 1,
        since: Optional[datetime] = None,
        max_results: int = 10,
    ) -> List[ContextResultRow]:
        """Find items connected through relations.

        Uses recursive CTE to find:
        - Connected entities
        - Relations that connect them

        Note on depth:
        Each traversal step requires two depth levels - one to find the relation,
        and another to follow that relation to an entity. So a max_depth of 4 allows
        traversal through two entities (relation->entity->relation->entity), while reaching
        an entity three steps away requires max_depth=6 (relation->entity->relation->entity->relation->entity).
        """
        max_depth = max_depth * 2

        if not type_id_pairs:
            return []

        # Extract entity IDs from type_id_pairs for the optimized query
        entity_ids = [i for t, i in type_id_pairs if t == "entity"]

        if not entity_ids:
            logger.debug("No entity IDs found in type_id_pairs")
            return []

        logger.debug(
            f"Finding connected items for {len(entity_ids)} entities with depth {max_depth}"
        )

        # Build the VALUES clause for entity IDs
        entity_id_values = ", ".join([str(i) for i in entity_ids])

        # Parameters for bindings - include project_id for security filtering
        params: dict[str, Any] = {
            "max_depth": max_depth,
            "max_results": max_results,
            "project_id": self.search_repository.project_id,
        }

        # Build date and timeframe filters conditionally based on since parameter
        if since:
            # SQLite compares datetimes as ISO-8601 text.
            params["since_date"] = since.isoformat()
            date_filter = "AND e.created_at >= :since_date"
            relation_date_filter = "AND e_from.created_at >= :since_date"
            timeframe_condition = "AND eg.relation_date >= :since_date"
        else:
            date_filter = ""
            relation_date_filter = ""
            timeframe_condition = ""

        # Trigger: build_context starts from a project-scoped search result.
        # Why: the seed entity must belong to the requested project, but an
        # explicit relation edge may point at another project.
        # Outcome: traversal follows only project-owned edges from reached
        # entities, instead of forcing every reached entity into the seed project.
        seed_project_filter = "AND e.project_id = :project_id"
        connected_entity_project_filter = ""
        relation_project_filter = "AND e_from.project_id = r.project_id"

        # Use a CTE that operates directly on entity and relation tables
        # This avoids the overhead of the search_index virtual table
        query = self._build_query(
            entity_id_values,
            date_filter,
            seed_project_filter,
            connected_entity_project_filter,
            relation_date_filter,
            relation_project_filter,
            timeframe_condition,
        )

        result = await self.search_repository.execute_query(query, params=params)
        rows = result.all()

        context_rows = [
            ContextResultRow(
                type=row.type,
                id=row.id,
                title=row.title,
                permalink=row.permalink,
                file_path=row.file_path,
                from_id=row.from_id,
                to_id=row.to_id,
                relation_type=row.relation_type,
                to_name=row.to_name,
                content=row.content,
                category=row.category,
                entity_id=row.entity_id,
                depth=row.depth,
                root_id=row.root_id,
                created_at=row.created_at,
            )
            for row in rows
        ]
        return context_rows

    def _build_query(
        self,
        entity_id_values: str,
        date_filter: str,
        seed_project_filter: str,
        connected_entity_project_filter: str,
        relation_date_filter: str,
        relation_project_filter: str,
        timeframe_condition: str,
    ):
        """Build the recursive CTE traversal query."""
        return text(f"""
        WITH RECURSIVE entity_graph AS (
            -- Base case: seed entities
            SELECT
                e.id,
                'entity' as type,
                e.title,
                e.permalink,
                e.file_path,
                NULL as from_id,
                NULL as to_id,
                NULL as relation_type,
                NULL as to_name,
                NULL as content,
                NULL as category,
                NULL as entity_id,
                0 as depth,
                e.id as root_id,
                e.created_at,
                e.created_at as relation_date,
                0 as is_incoming,
                e.project_id as project_id,
                ',' || e.id || ',' as entity_path
            FROM entity e
            WHERE e.id IN ({entity_id_values})
            {date_filter}
            {seed_project_filter}

            UNION ALL

            -- Get relations from current entities
            SELECT
                r.id,
                'relation' as type,
                r.relation_type || ': ' || r.to_name as title,
                '' as permalink,
                e_from.file_path,
                r.from_id,
                r.to_id,
                r.relation_type,
                r.to_name,
                NULL as content,
                NULL as category,
                NULL as entity_id,
                eg.depth + 1,
                eg.root_id,
                e_from.created_at,
                e_from.created_at as relation_date,
                CASE WHEN r.from_id = eg.id THEN 0 ELSE 1 END as is_incoming,
                eg.project_id as project_id,
                eg.entity_path as entity_path
            FROM entity_graph eg
            JOIN relation r ON (
                eg.type = 'entity' AND
                (r.from_id = eg.id OR r.to_id = eg.id) AND
                r.project_id = eg.project_id
            )
            JOIN entity e_from ON (
                r.from_id = e_from.id
                {relation_date_filter}
                {relation_project_filter}
            )
            WHERE eg.depth < :max_depth

            UNION ALL

            -- Get entities connected by relations
            SELECT
                e.id,
                'entity' as type,
                e.title,
                CASE
                    WHEN e.permalink IS NULL THEN ''
                    ELSE e.permalink
                END as permalink,
                e.file_path,
                NULL as from_id,
                NULL as to_id,
                NULL as relation_type,
                NULL as to_name,
                NULL as content,
                NULL as category,
                NULL as entity_id,
                eg.depth + 1,
                eg.root_id,
                e.created_at,
                eg.relation_date,
                eg.is_incoming,
                e.project_id as project_id,
                eg.entity_path || e.id || ',' as entity_path
            FROM entity_graph eg
            JOIN entity e ON (
                eg.type = 'relation' AND
                e.id = CASE
                    WHEN eg.is_incoming = 0 THEN eg.to_id
                    ELSE eg.from_id
                END
                {date_filter}
                {connected_entity_project_filter}
            )
            WHERE eg.depth < :max_depth
            AND instr(eg.entity_path, ',' || e.id || ',') = 0
            {timeframe_condition}
        )
        SELECT DISTINCT
            type,
            id,
            title,
            permalink,
            file_path,
            from_id,
            to_id,
            relation_type,
            to_name,
            content,
            category,
            entity_id,
            MIN(depth) as depth,
            root_id,
            created_at
        FROM entity_graph
        WHERE depth > 0
        GROUP BY type, id, title, permalink, file_path, from_id, to_id,
                 relation_type, to_name, content, category, entity_id, root_id, created_at
        ORDER BY depth, type, id
        LIMIT :max_results
       """)
