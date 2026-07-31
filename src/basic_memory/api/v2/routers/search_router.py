"""V2 router for search operations.

This router uses external_id UUIDs for stable, API-friendly routing.
V1 uses string-based project names which are less efficient and less stable.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Path

from basic_memory.api.v2.utils import to_search_results
from basic_memory.repository.semantic_errors import (
    SemanticDependenciesMissingError,
    SemanticSearchDisabledError,
)
from basic_memory.schemas.search import SearchQuery, SearchResponse, SearchRetrievalMode
from basic_memory.deps import (
    SearchServiceV2ExternalDep,
    EntityServiceV2ExternalDep,
    SearchReindexSchedulerDep,
    ProjectExternalIdPathDep,
)

# Note: No prefix here - it's added during registration as /v2/{project_id}/search
router = APIRouter(tags=["search"])


@router.post("/search/", response_model=SearchResponse)
async def search(
    query: SearchQuery,
    search_service: SearchServiceV2ExternalDep,
    entity_service: EntityServiceV2ExternalDep,
    project_id: str = Path(..., description="Project external UUID"),
    page: int = 1,
    page_size: int = 10,
):
    """Search across all knowledge and documents in a project.

    V2 uses external_id UUIDs for stable API references.

    Args:
        project_id: Project external UUID from URL path
        query: Search query parameters (text, filters, etc.)
        search_service: Search service scoped to project
        entity_service: Entity service scoped to project
        page: Page number for pagination
        page_size: Number of results per page

    Returns:
        SearchResponse with paginated search results
    """
    offset = (page - 1) * page_size
    exact_count_available = query.retrieval_mode == SearchRetrievalMode.FTS
    total: int | None
    try:
        if exact_count_available:
            results, total = await asyncio.gather(
                search_service.search(query, limit=page_size, offset=offset),
                search_service.count(query),
            )
        else:
            results = await search_service.search(query, limit=page_size + 1, offset=offset)
            total = None
    except SemanticSearchDisabledError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SemanticDependenciesMissingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if total is not None:
        has_more = offset + len(results) < total
    else:
        # Trigger: semantic modes would need another vector/hybrid retrieval to count.
        # Why: search requests should not pay for a second semantic pass.
        # Outcome: preserve probe pagination and report the total as unknown (null),
        #   never a sentinel (docs/OUTPUT_CONTRACT.md).
        has_more = len(results) > page_size
        if has_more:
            results = results[:page_size]

    search_results = await to_search_results(entity_service, results)
    return SearchResponse(
        results=search_results,
        current_page=page,
        page_size=page_size,
        total=total,
        has_more=has_more,
    )


@router.post("/search/reindex")
async def reindex(
    search_reindex_scheduler: SearchReindexSchedulerDep,
    project_id: ProjectExternalIdPathDep,
):
    """Recreate and populate the search index for a project.

    This is a background operation that rebuilds the search index
    from scratch. Useful after bulk updates or if the index becomes
    corrupted.

    Args:
        project_id: Project external UUID from URL path
        search_reindex_scheduler: Search reindex scheduler for background work

    Returns:
        Status message indicating reindex has been initiated
    """
    search_reindex_scheduler.schedule_search_reindex(project_id=project_id)
    return {"status": "ok", "message": "Reindex initiated"}
