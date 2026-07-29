"""Project context utilities for Basic Memory MCP server.

Provides project lookup utilities for MCP tools.
Handles project validation and context management in one place.

Note: This module uses ProjectResolver for unified project resolution.
The resolve_project_parameter function is a thin wrapper for backwards
compatibility with existing MCP tools.
"""

# PEP 563 lazy annotations keep `Context` usable in signatures without importing
# fastmcp at module load — the fastmcp/mcp stack costs ~0.5s of CLI startup (#886).
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import (
    TYPE_CHECKING,
    AsyncIterator,
    List,
    Optional,
    Tuple,
)

from httpx import AsyncClient
from httpx._types import (
    HeaderTypes,
)
from loguru import logger

from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.project_resolver import ProjectResolver
from basic_memory.schemas.project_info import ProjectItem, ProjectList
from basic_memory.schemas.v2 import ProjectResolveResponse
from basic_memory.schemas.memory import memory_url_path
from basic_memory.utils import generate_permalink, normalize_project_reference
from basic_memory.mcp.project_context_identifiers import (
    UnresolvedProjectRouteError,
    add_project_metadata as _add_project_metadata,
    canonical_memory_path_for_active_route as _canonical_memory_path_for_active_route,
    canonicalize_project_name as _canonicalize_project_name,
    detect_project_from_url_prefix,
    project_matches_identifier as _project_matches_identifier,
    split_project_prefix as _split_project_prefix,
)

# Keep the original module's helper surface intact for callers and tests while
# the implementations live in focused, dependency-light modules.
add_project_metadata = _add_project_metadata

if TYPE_CHECKING:
    from fastmcp import Context


# --- Request-local project cache ---
# MCP context state is per-request, so caching the validated project there keeps
# repeated resolution inside one tool call from re-hitting /v2/projects/.


async def _get_cached_active_project(context: Optional[Context]) -> Optional[ProjectItem]:
    """Return the cached active project from context when available."""
    if not context:
        return None

    cached_raw = await context.get_state("active_project")
    if isinstance(cached_raw, dict):
        return ProjectItem.model_validate(cached_raw)
    return None


async def _set_cached_active_project(
    context: Optional[Context],
    active_project: ProjectItem,
) -> None:
    """Persist the active project and known default-project metadata in context."""
    if not context:
        return

    await context.set_state("active_project", active_project.model_dump())
    if active_project.is_default:
        await context.set_state("default_project_name", active_project.name)


async def _clear_cached_active_project(context: Optional[Context]) -> None:
    """Clear cached project metadata that may no longer match the active route."""
    if not context:
        return

    await context.set_state("active_project", None)
    await context.set_state("default_project_name", None)


async def _get_cached_default_project(context: Optional[Context]) -> Optional[str]:
    """Return the cached default project name from context when available."""
    if not context:
        return None

    cached_default = await context.get_state("default_project_name")
    if isinstance(cached_default, str):
        return cached_default
    return None


async def invalidate_project_caches(context: Optional[Context] = None) -> None:
    """Invalidate project identity caches after a project lifecycle change."""
    await _clear_cached_active_project(context)


async def _resolve_default_project_from_api() -> Optional[str]:
    """Query the projects API for the default project.

    Used as a fallback when the config file records no default project.
    """
    from basic_memory.mcp.async_client import get_client

    try:
        async with get_client() as client:
            response = await client.get("/v2/projects/")
            if response.status_code == 200:
                project_list = ProjectList.model_validate(response.json())
                if project_list.default_project:
                    return project_list.default_project
                # Fallback: find project with is_default=True
                for p in project_list.projects:
                    if p.is_default:
                        return p.name
    except Exception:
        pass
    return None


async def resolve_project_parameter(
    project: Optional[str] = None,
    allow_discovery: bool = False,
    default_project: Optional[str] = None,
    context: Optional[Context] = None,
) -> Optional[str]:
    """Resolve project parameter using unified linear priority chain.

    This is a thin wrapper around ProjectResolver for backwards compatibility.
    New code should consider using ProjectResolver directly for more detailed
    resolution information.

    Resolution order:
    1. ENV_CONSTRAINT: BASIC_MEMORY_MCP_PROJECT env var (highest priority)
    2. EXPLICIT: project parameter passed directly
    3. DEFAULT: default_project from config (if set)
    4. Fallback: discovery (if allowed) → NONE

    Args:
        project: Optional explicit project parameter
        allow_discovery: If True, allows returning None for discovery mode
            (used by tools like recent_activity that can operate across all projects)
        default_project: Optional explicit default project. If not provided, reads from ConfigManager.

    Returns:
        Resolved project name or None if no resolution possible
    """
    config = ConfigManager().config

    # Trigger: project already resolved earlier in the same MCP request
    # Why: the active project is request-constant, so re-discovering the
    #   default project via /v2/projects/ just repeats work
    # Outcome: reuse the cached project name as the explicit candidate
    if project is None:
        cached_project = await _get_cached_active_project(context)
        if cached_project is not None:
            project = cached_project.name

    # Trigger: there is no explicit project after env/context normalization
    # Why: default-project discovery is only needed as a fallback; doing it
    #   for explicit requests adds an avoidable /v2/projects/ round-trip
    # Outcome: skip default lookup when the active project is already known
    if default_project is None and project is None:
        # Load config for any values not explicitly provided. When config
        # records no default, fall back to the projects API is_default flag.
        default_project = config.default_project

        if default_project is None:
            default_project = await _get_cached_default_project(context)

        if default_project is None:
            default_project = await _resolve_default_project_from_api()
            if default_project and context:
                await context.set_state("default_project_name", default_project)

    # Create resolver with configuration and resolve
    resolver = ProjectResolver.from_env(
        default_project=default_project,
    )
    result = resolver.resolve(project=project, allow_discovery=allow_discovery)
    return _canonicalize_project_name(result.project, config)


async def get_project_names(client: AsyncClient, headers: HeaderTypes | None = None) -> List[str]:
    # Deferred import to avoid circular dependency with tools
    from basic_memory.mcp.tools.utils import call_get

    response = await call_get(client, "/v2/projects/", headers=headers)
    project_list = ProjectList.model_validate(response.json())
    return [project.name for project in project_list.projects]


async def get_active_project(
    client: AsyncClient,
    project: Optional[str] = None,
    context: Optional[Context] = None,
    headers: HeaderTypes | None = None,
) -> ProjectItem:
    """Get and validate project, setting it in context if available.

    Args:
        client: HTTP client for API calls
        project: Optional project name (resolved using hierarchy)
        context: Optional FastMCP context to cache the result

    Returns:
        The validated project item

    Raises:
        ValueError: If no project can be resolved
        HTTPError: If project doesn't exist or is inaccessible
    """
    # Deferred import to avoid circular dependency with tools
    from basic_memory.mcp.tools.utils import call_post

    cached_project = await _get_cached_active_project(context)
    if cached_project and _project_matches_identifier(cached_project, project):
        logger.debug(f"Using cached project from context: {cached_project.name}")
        return cached_project

    resolved_project = await resolve_project_parameter(project, context=context)
    if not resolved_project:
        project_names = await get_project_names(client, headers)
        raise ValueError(
            "No project specified. "
            "Either set 'default_project' in config, or use 'project' argument.\n"
            f"Available projects: {project_names}"
        )

    project = resolved_project

    if cached_project and _project_matches_identifier(cached_project, project):
        logger.debug(f"Using cached project from context: {cached_project.name}")
        return cached_project

    # Validate project exists by calling API
    logger.debug(f"Validating project: {project}")
    response = await call_post(
        client,
        "/v2/projects/resolve",
        json={"identifier": project},
        headers=headers,
    )
    resolved = ProjectResolveResponse.model_validate(response.json())
    active_project = ProjectItem(
        id=resolved.project_id,
        external_id=resolved.external_id,
        name=resolved.name,
        path=resolved.path,
        is_default=resolved.is_default,
    )

    # Cache in context if available
    await _set_cached_active_project(context, active_project)
    if context:
        logger.debug(f"Cached project in context: {project}")

    logger.debug(f"Validated project: {active_project.name}")
    return active_project


async def resolve_project_and_path(
    client: AsyncClient,
    identifier: str,
    project: Optional[str] = None,
    context: Optional[Context] = None,
    headers: HeaderTypes | None = None,
    *,
    strict_project_routing: bool = False,
    allow_missing_project_fallback: bool = False,
    cache_resolved_project: bool = True,
) -> tuple[ProjectItem, str, bool]:
    """Resolve project and normalized path for memory:// identifiers.

    Args:
        strict_project_routing: Reject a memory URL whose leading project-like
            segment cannot be resolved. Mutating tools use this to prevent a
            failed route from falling back to the active project.
        allow_missing_project_fallback: When strict routing is enabled, still
            allow a genuinely missing project prefix to be treated as an active-
            project path. This is safe only for mutations that require an existing
            target and cannot create content.
        cache_resolved_project: Persist a project resolved from the memory URL in
            MCP context. Set this to false for validation-only routing that may
            reject a resolved cross-project source.

    Returns:
        Tuple of (active_project, normalized_path, is_memory_url)

    Raises:
        UnresolvedProjectRouteError: If strict routing is enabled and the
            memory URL's leading project segment does not resolve.
    """
    is_memory_url = identifier.strip().startswith("memory://")
    config = ConfigManager().config
    include_project = config.permalinks_include_project if is_memory_url else None
    if not is_memory_url:
        active_project = await get_active_project(client, project, context, headers)
        return active_project, identifier, False

    normalized_path = normalize_project_reference(memory_url_path(identifier))
    cached_project = await _get_cached_active_project(context)

    project_prefix, remainder = _split_project_prefix(normalized_path)
    include_project = config.permalinks_include_project
    # Trigger: memory URL begins with a potential project segment
    # Why: allow project-scoped memory URLs without requiring a separate project parameter
    # Outcome: attempt to resolve the prefix as a project and route to it
    if project_prefix:
        # Deferred: ToolError lives in the mcp SDK, which must not load at CLI startup (#886).
        from mcp.server.fastmcp.exceptions import ToolError

        if cached_project and _project_matches_identifier(cached_project, project_prefix):
            resolved_project = await resolve_project_parameter(project_prefix, context=context)
            if resolved_project and generate_permalink(resolved_project) != generate_permalink(
                project_prefix
            ):
                raise ValueError(
                    f"Project is constrained to '{resolved_project}', cannot use '{project_prefix}'."
                )

            resolved_path = _canonical_memory_path_for_active_route(
                cached_project,
                remainder,
                include_project=include_project,
            )
            return cached_project, resolved_path, True

        try:
            from basic_memory.mcp.tools.utils import call_post

            response = await call_post(
                client,
                "/v2/projects/resolve",
                json={"identifier": project_prefix},
                headers=headers,
            )
            resolved = ProjectResolveResponse.model_validate(response.json())
        except ToolError as exc:
            if "project not found" not in str(exc).lower():
                raise
            if strict_project_routing and not allow_missing_project_fallback:
                # Mutations that can create content must not reinterpret a
                # missing project route as an active-project path (#1066).
                # Existing-target mutations may opt into that legacy path
                # fallback.
                raise UnresolvedProjectRouteError(identifier, project_prefix) from exc
        else:
            resolved_project = await resolve_project_parameter(project_prefix, context=context)
            if resolved_project and generate_permalink(resolved_project) != generate_permalink(
                project_prefix
            ):
                raise ValueError(
                    f"Project is constrained to '{resolved_project}', cannot use '{project_prefix}'."
                )

            active_project = ProjectItem(
                id=resolved.project_id,
                external_id=resolved.external_id,
                name=resolved.name,
                path=resolved.path,
                is_default=resolved.is_default,
            )
            if cache_resolved_project:
                await _set_cached_active_project(context, active_project)

            resolved_path = _canonical_memory_path_for_active_route(
                active_project,
                remainder,
                include_project=include_project,
            )
            return active_project, resolved_path, True

    # Trigger: memory URL has no resolvable project route segment
    # Why: preserve active-project behavior
    # Outcome: normalize the path against the already-selected project
    active_project = await get_active_project(client, project, context, headers)
    resolved_path = _canonical_memory_path_for_active_route(
        active_project,
        normalized_path,
        include_project=include_project,
    )
    return active_project, resolved_path, True


async def detect_project_from_memory_url_prefix(
    identifier: str,
    config: BasicMemoryConfig,
    context: Optional[Context] = None,
) -> Optional[str]:
    """Resolve a project from a memory URL prefix."""
    if not identifier.strip().startswith("memory://"):
        return None

    return detect_project_from_url_prefix(identifier, config)


async def detect_project_from_identifier_prefix(
    identifier: str,
    config: BasicMemoryConfig,
    context: Optional[Context] = None,
) -> Optional[str]:
    """Resolve a project from a plain permalink or memory URL route prefix."""
    return detect_project_from_url_prefix(identifier, config)


@asynccontextmanager
async def get_project_client(
    project: Optional[str] = None,
    context: Optional[Context] = None,
    project_id: Optional[str] = None,
) -> AsyncIterator[Tuple[AsyncClient, ProjectItem]]:
    """Resolve the project, open the local client, and validate the project.

    Args:
        project: Optional explicit project parameter (name or permalink)
        context: Optional FastMCP context for caching
        project_id: Optional project external_id (UUID). When provided, takes
            precedence over ``project``.

    Yields:
        Tuple of (client, active_project)

    Raises:
        ValueError: If no project can be resolved
    """
    # Deferred import to avoid circular dependency
    from basic_memory.mcp.async_client import get_client

    # When project_id (UUID) is provided, prefer it as the resolution identifier.
    # external_id is unambiguous; a project name can collide with another permalink.
    project_identifier = project_id if project_id else project

    resolved_project = await resolve_project_parameter(project_identifier, context=context)

    if not resolved_project:
        # Open the client anyway to discover projects and raise a helpful error
        async with get_client() as client:
            project_names = await get_project_names(client)
            raise ValueError(
                "No project specified. "
                "Either set 'default_project' in config, or use 'project' argument.\n"
                f"Available projects: {project_names}"
            )

    async with get_client() as client:
        active_project = await get_active_project(client, resolved_project, context)
        yield client, active_project
