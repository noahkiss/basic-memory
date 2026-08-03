"""Tests for project context utilities (no standard-library mock usage).

These functions are config/env driven, so we use the real ConfigManager-backed
test config file and pytest monkeypatch for environment variables.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, cast

import pytest

from tests.mcp.conftest import ContextState, ctx


def _project(
    name: str,
    *,
    id: int = 1,
    external_id: str = "11111111-1111-1111-1111-111111111111",
    is_default: bool = False,
):
    from basic_memory.schemas.project_info import ProjectItem

    return ProjectItem(
        id=id,
        external_id=external_id,
        name=name,
        path=f"/{name}",
        is_default=is_default,
    )


@pytest.mark.asyncio
async def test_returns_none_when_no_default_and_no_project(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)

    # Prevent API fallback from returning a project via stale dependency overrides
    async def _no_api_fallback():
        return None

    monkeypatch.setattr(
        "basic_memory.mcp.project_context._resolve_default_project_from_api",
        _no_api_fallback,
    )
    assert await resolve_project_parameter(project=None, allow_discovery=False) is None


@pytest.mark.asyncio
async def test_allows_discovery_when_enabled(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    # Prevent API fallback from returning a project via stale dependency overrides
    async def _no_api_fallback():
        return None

    monkeypatch.setattr(
        "basic_memory.mcp.project_context._resolve_default_project_from_api",
        _no_api_fallback,
    )
    assert await resolve_project_parameter(project=None, allow_discovery=True) is None


@pytest.mark.asyncio
async def test_returns_project_when_specified(config_manager):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    config_manager.save_config(cfg)

    assert await resolve_project_parameter(project="my-project") == "my-project"


@pytest.mark.asyncio
async def test_uses_env_var_priority(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    config_manager.save_config(cfg)

    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "env-project")
    assert await resolve_project_parameter(project="explicit-project") == "env-project"


@pytest.mark.asyncio
async def test_uses_explicit_project_when_no_env(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    cfg = config_manager.load_config()
    config_manager.save_config(cfg)

    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)
    assert await resolve_project_parameter(project="explicit-project") == "explicit-project"


@pytest.mark.asyncio
async def test_canonicalizes_case_insensitive_project_reference(
    write_registry_file, config_home, monkeypatch
):
    from basic_memory.mcp.project_context import resolve_project_parameter

    project_name = "Personal-Project"
    project_path = config_home / "personal-project"
    project_path.mkdir(parents=True, exist_ok=True)
    write_registry_file({project_name: str(project_path)})

    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)

    assert await resolve_project_parameter(project="personal-project") == project_name
    assert await resolve_project_parameter(project="PERSONAL-PROJECT") == project_name


@pytest.mark.asyncio
async def test_uses_default_project(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    async def fake_default_lookup():
        return "default-project"

    monkeypatch.setattr(
        "basic_memory.mcp.project_context._resolve_default_project_from_api",
        fake_default_lookup,
    )

    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)
    assert await resolve_project_parameter(project=None) == "default-project"


@pytest.mark.asyncio
async def test_returns_none_when_no_default(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    monkeypatch.delenv("BASIC_MEMORY_MCP_PROJECT", raising=False)

    # Prevent API fallback from returning a project via stale dependency overrides
    async def _no_api_fallback():
        return None

    monkeypatch.setattr(
        "basic_memory.mcp.project_context._resolve_default_project_from_api",
        _no_api_fallback,
    )
    assert await resolve_project_parameter(project=None) is None


@pytest.mark.asyncio
async def test_env_constraint_overrides_default(config_manager, monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    async def fake_default_lookup():
        return "default-project"

    monkeypatch.setattr(
        "basic_memory.mcp.project_context._resolve_default_project_from_api",
        fake_default_lookup,
    )

    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "env-project")
    assert await resolve_project_parameter(project=None) == "env-project"


@pytest.mark.asyncio
async def test_detect_project_from_identifier_prefix_resolves_local_project(write_registry_file):
    """A leading segment naming a configured project resolves for both URL forms."""
    from basic_memory.mcp.project_context import detect_project_from_identifier_prefix

    write_registry_file({"main": "/tmp/main"})

    assert await detect_project_from_identifier_prefix("memory://main/notes/foo") == "main"
    assert await detect_project_from_identifier_prefix("main/notes/foo") == "main"
    assert await detect_project_from_identifier_prefix("notes/foo") is None


@pytest.mark.asyncio
async def test_get_project_client_with_project_id_validates_the_external_id(
    config_manager, monkeypatch
):
    """A UUID project_id is the identifier that gets resolved and validated."""
    import basic_memory.mcp.project_context as project_context

    captured: dict[str, object] = {}

    @asynccontextmanager
    async def fake_get_client(**kwargs) -> AsyncIterator[object]:
        captured["get_client_kwargs"] = kwargs
        yield object()

    async def fake_get_active_project(client, project, context=None, headers=None):
        captured["validated_project"] = project
        return _project("Local Project", id=99, external_id=str(project))

    monkeypatch.setattr("basic_memory.mcp.async_client.get_client", fake_get_client)
    monkeypatch.setattr(project_context, "get_active_project", fake_get_active_project)

    canonical_uuid = "55555555-5555-5555-5555-555555555555"
    async with project_context.get_project_client(project_id=canonical_uuid) as (_, active):
        assert active.external_id == canonical_uuid

    assert captured["get_client_kwargs"] == {}
    assert captured["validated_project"] == canonical_uuid


@pytest.mark.asyncio
async def test_get_project_client_with_project_id_respects_env_constraint(
    write_registry_file, config_home, monkeypatch
):
    """BASIC_MEMORY_MCP_PROJECT must remain authoritative when project_id is supplied."""
    import basic_memory.mcp.project_context as project_context

    write_registry_file(
        {
            "env-project": str(config_home / "env-project"),
            "other-project": str(config_home / "other-project"),
        }
    )

    monkeypatch.setenv("BASIC_MEMORY_MCP_PROJECT", "env-project")

    captured: dict[str, object] = {}

    @asynccontextmanager
    async def fake_get_client(**kwargs) -> AsyncIterator[object]:
        captured["get_client_kwargs"] = kwargs
        yield object()

    async def fake_get_active_project(client, project, context=None, headers=None):
        captured["validated_project"] = project
        return _project(str(project), id=99, external_id="env-project-id")

    monkeypatch.setattr("basic_memory.mcp.async_client.get_client", fake_get_client)
    monkeypatch.setattr(project_context, "get_active_project", fake_get_active_project)

    requested_uuid = "55555555-5555-5555-5555-555555555555"
    async with project_context.get_project_client(project_id=requested_uuid) as (_, active):
        assert active.name == "env-project"

    assert captured["get_client_kwargs"] == {}
    assert captured["validated_project"] == "env-project"


@pytest.mark.asyncio
async def test_get_project_client_prefers_project_id_over_project_name(monkeypatch):
    """When both project and project_id are passed, the UUID takes precedence."""
    import basic_memory.mcp.project_context as project_context

    # Capture which identifier flows into resolution, then short-circuit before
    # the rest of the routing chain runs (avoids real network calls).
    captured: dict[str, str | None] = {}
    sentinel = RuntimeError("stop after resolution")

    async def fake_resolve(project=None, *, allow_discovery=True, context=None):
        captured["project"] = project
        raise sentinel

    monkeypatch.setattr(project_context, "resolve_project_parameter", fake_resolve)

    canonical_uuid = "44444444-4444-4444-4444-444444444444"
    with pytest.raises(RuntimeError, match="stop after resolution"):
        async with project_context.get_project_client(
            project="ambiguous-name",
            project_id=canonical_uuid,
        ):
            pass

    assert captured["project"] == canonical_uuid


@pytest.mark.asyncio
async def test_resolve_project_parameter_uses_cached_active_project_before_api_default_lookup(
    monkeypatch,
):
    from basic_memory.mcp.project_context import resolve_project_parameter
    from basic_memory.schemas.project_info import ProjectItem

    context = ContextState()
    cached_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="Cached Project",
        path="/tmp/cached-project",
        is_default=True,
    )
    await context.set_state("active_project", cached_project.model_dump())

    async def fail_if_called():  # pragma: no cover
        raise AssertionError("Default project API lookup should not run when project is cached")

    monkeypatch.setattr(
        "basic_memory.mcp.project_context._resolve_default_project_from_api",
        fail_if_called,
    )

    resolved = await resolve_project_parameter(project=None, context=ctx(context))
    assert resolved == cached_project.name


@pytest.mark.asyncio
async def test_resolve_project_parameter_caches_api_default_project_name(monkeypatch):
    from basic_memory.mcp.project_context import resolve_project_parameter

    context = ContextState()
    api_calls = {"count": 0}

    async def fake_default_lookup():
        api_calls["count"] += 1
        return "cloud-default"

    monkeypatch.setattr(
        "basic_memory.mcp.project_context._resolve_default_project_from_api",
        fake_default_lookup,
    )

    first = await resolve_project_parameter(project=None, context=ctx(context))
    second = await resolve_project_parameter(project=None, context=ctx(context))

    assert first == "cloud-default"
    assert second == "cloud-default"
    assert api_calls["count"] == 1


@pytest.mark.asyncio
async def test_get_active_project_uses_cached_project_before_resolution(monkeypatch):
    from basic_memory.mcp.project_context import get_active_project
    from basic_memory.schemas.project_info import ProjectItem

    context = ContextState()
    cached_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="Cached Project",
        path="/tmp/cached-project",
        is_default=True,
    )
    await context.set_state("active_project", cached_project.model_dump())

    async def fail_if_called(*args, **kwargs):  # pragma: no cover
        raise AssertionError("Project resolution should not run when cache matches")

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.resolve_project_parameter",
        fail_if_called,
    )

    resolved = await get_active_project(client=cast(Any, None), context=ctx(context))
    assert resolved == cached_project


@pytest.mark.asyncio
async def test_get_active_project_uses_cached_project_for_explicit_permalink(monkeypatch):
    from basic_memory.mcp.project_context import get_active_project
    from basic_memory.schemas.project_info import ProjectItem

    context = ContextState()
    cached_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="My Research",
        path="/tmp/my-research",
        is_default=False,
    )
    await context.set_state("active_project", cached_project.model_dump())

    async def fail_if_called(*args, **kwargs):  # pragma: no cover
        raise AssertionError(
            "Project resolution should not run when explicit project matches cache"
        )

    monkeypatch.setattr(
        "basic_memory.mcp.project_context.resolve_project_parameter",
        fail_if_called,
    )

    resolved = await get_active_project(
        client=cast(Any, None), project="my-research", context=ctx(context)
    )
    assert resolved == cached_project


@pytest.mark.asyncio
async def test_resolve_project_and_path_uses_cached_project_for_memory_url_prefix(
    config_manager, monkeypatch
):
    from basic_memory.mcp.project_context import resolve_project_and_path
    from basic_memory.schemas.project_info import ProjectItem

    config = config_manager.load_config()
    config.permalinks_include_project = False
    config_manager.save_config(config)

    context = ContextState()
    cached_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="My Research",
        path="/tmp/my-research",
        is_default=False,
    )
    await context.set_state("active_project", cached_project.model_dump())

    async def fail_if_called(*args, **kwargs):  # pragma: no cover
        raise AssertionError("Project resolve API should not run when memory URL matches cache")

    async def fake_resolve_project_parameter(project=None, **kwargs):
        return cached_project.name if project else cached_project.name

    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", fail_if_called)
    monkeypatch.setattr(
        "basic_memory.mcp.project_context.resolve_project_parameter",
        fake_resolve_project_parameter,
    )

    active_project, resolved_path, is_memory_url = await resolve_project_and_path(
        client=cast(Any, None),
        identifier="memory://my-research/notes/roadmap.md",
        context=ctx(context),
    )

    assert active_project == cached_project
    assert resolved_path == "notes/roadmap.md"
    assert is_memory_url is True


@pytest.mark.asyncio
async def test_resolve_project_and_path_preserves_existing_project_prefixed_memory_url(
    config_manager,
):
    from basic_memory.mcp.project_context import resolve_project_and_path
    from basic_memory.schemas.project_info import ProjectItem

    config = config_manager.load_config()
    config.permalinks_include_project = True
    config_manager.save_config(config)

    context = ContextState()
    cached_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="main",
        path="/tmp/main",
        is_default=False,
    )
    await context.set_state("active_project", cached_project.model_dump())

    active_project, resolved_path, is_memory_url = await resolve_project_and_path(
        client=cast(Any, None),
        identifier="memory://main/notes/foo",
        context=ctx(context),
    )

    assert active_project == cached_project
    assert resolved_path == "main/notes/foo"
    assert is_memory_url is True


@pytest.mark.asyncio
async def test_resolve_project_and_path_uses_resolved_project_prefix(
    config_manager,
    monkeypatch,
):
    """A memory URL prefix that resolves through the API routes to that project."""
    import basic_memory.mcp.project_context as project_context
    from basic_memory.mcp.project_context import resolve_project_and_path

    config = config_manager.load_config()
    config.permalinks_include_project = True
    config_manager.save_config(config)

    context = ContextState()

    class FakeResponse:
        def json(self):
            return {
                "external_id": "22222222-2222-2222-2222-222222222222",
                "project_id": 2,
                "name": "Research",
                "permalink": "research",
                "path": "/tmp/research",
                "is_active": True,
                "is_default": False,
                "resolution_method": "permalink",
            }

    async def fake_call_post(*args, **kwargs):
        return FakeResponse()

    async def fake_resolve_project_parameter(project=None, **kwargs):
        return "Research"

    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", fake_call_post)
    monkeypatch.setattr(
        project_context,
        "resolve_project_parameter",
        fake_resolve_project_parameter,
    )

    active_project, resolved_path, is_memory_url = await resolve_project_and_path(
        client=cast(Any, None),
        identifier="memory://research/notes/foo",
        context=ctx(context),
    )

    assert active_project.name == "Research"
    assert resolved_path == "research/notes/foo"
    assert is_memory_url is True
    assert await context.get_state("active_project") == active_project.model_dump()


class TestDetectProjectFromUrlPrefix:
    """Test detect_project_from_url_prefix for URL-based project detection."""

    def test_detects_project_from_memory_url(self, write_registry_file, config_home):
        from basic_memory.mcp.project_context import detect_project_from_url_prefix

        write_registry_file({"test-project": str(config_home / "test-project")})
        result = detect_project_from_url_prefix("memory://test-project/some-note")
        assert result == "test-project"

    def test_detects_project_from_plain_path(self, write_registry_file, config_home):
        from basic_memory.mcp.project_context import detect_project_from_url_prefix

        write_registry_file({"test-project": str(config_home / "test-project")})
        result = detect_project_from_url_prefix("test-project/some-note")
        assert result == "test-project"

    def test_returns_none_for_unknown_prefix(self, write_registry_file, config_home):
        from basic_memory.mcp.project_context import detect_project_from_url_prefix

        write_registry_file({"test-project": str(config_home / "test-project")})
        result = detect_project_from_url_prefix("memory://unknown-project/note")
        assert result is None

    def test_returns_none_for_no_slash(self, write_registry_file, config_home):
        from basic_memory.mcp.project_context import detect_project_from_url_prefix

        write_registry_file({"test-project": str(config_home / "test-project")})
        result = detect_project_from_url_prefix("memory://single-segment")
        assert result is None

    def test_returns_none_for_wildcard_prefix(self, write_registry_file, config_home):
        from basic_memory.mcp.project_context import detect_project_from_url_prefix

        write_registry_file({"test-project": str(config_home / "test-project")})
        result = detect_project_from_url_prefix("memory://*/notes")
        assert result is None

    def test_matches_case_insensitive_via_permalink(self, write_registry_file, config_home):
        from basic_memory.mcp.project_context import detect_project_from_url_prefix

        (config_home / "My Research").mkdir(parents=True, exist_ok=True)
        write_registry_file({"My Research": str(config_home / "My Research")})

        result = detect_project_from_url_prefix("memory://my-research/notes")
        assert result == "My Research"


@pytest.mark.asyncio
async def test_detect_project_from_memory_url_prefix_ignores_plain_paths():
    """Only memory:// identifiers are treated as project-routed URLs."""
    from basic_memory.mcp.project_context import detect_project_from_memory_url_prefix

    resolved = await detect_project_from_memory_url_prefix("main/notes/foo")

    assert resolved is None


@pytest.mark.asyncio
async def test_resolve_project_and_path_keeps_patterns_project_qualified(
    config_manager,
    monkeypatch,
):
    """Glob patterns are qualified with the active project prefix (#957).

    The search index stores project-qualified permalinks (manual/man3/...), so a
    pattern that is not project-qualified can never match anything.
    """
    from mcp.server.fastmcp.exceptions import ToolError

    from basic_memory.mcp.project_context import resolve_project_and_path
    from basic_memory.schemas.project_info import ProjectItem

    config = config_manager.load_config()
    config.permalinks_include_project = True
    config_manager.save_config(config)

    context = ContextState()
    cached_project = ProjectItem(
        id=1,
        external_id="11111111-1111-1111-1111-111111111111",
        name="manual",
        path="/tmp/manual",
        is_default=False,
    )
    await context.set_state("active_project", cached_project.model_dump())

    async def fake_call_post(*args, **kwargs):
        raise ToolError("project not found")

    monkeypatch.setattr("basic_memory.mcp.tools.utils.call_post", fake_call_post)

    # A directory segment that is not a project stays part of the path.
    _, resolved_path, _ = await resolve_project_and_path(
        client=cast(Any, None),
        identifier="memory://man3/*",
        context=ctx(context),
    )
    assert resolved_path == "manual/man3/*"

    _, resolved_path, _ = await resolve_project_and_path(
        client=cast(Any, None),
        identifier="memory://man3/write-note-3",
        context=ctx(context),
    )
    assert resolved_path == "manual/man3/write-note-3"
