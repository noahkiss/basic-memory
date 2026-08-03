"""Pure project identifier parsing and canonical path construction."""

from __future__ import annotations

from typing import Optional

from basic_memory.project_registry import lookup_project
from basic_memory.schemas.memory import memory_url_path
from basic_memory.schemas.project_info import ProjectItem
from basic_memory.utils import (
    generate_permalink,
    normalize_project_reference,
)


class UnresolvedProjectRouteError(ValueError):
    """A mutating memory URL named a project prefix that could not be resolved."""

    def __init__(self, identifier: str, project_prefix: str):
        self.identifier = identifier
        self.project_prefix = project_prefix
        super().__init__(
            f"Memory URL project route '{project_prefix}' could not be resolved; "
            "refusing to treat the URL as a path in the active project."
        )


def canonicalize_project_name(project_name: Optional[str]) -> Optional[str]:
    """Return the registered name when an identifier matches by permalink."""
    if project_name is None:
        return None

    registered, _ = lookup_project(project_name)
    return registered or project_name


def project_matches_identifier(project_item: ProjectItem, identifier: Optional[str]) -> bool:
    """Return True when the identifier refers to the cached project."""
    if identifier is None:
        return True
    normalized_identifier = generate_permalink(identifier)
    return normalized_identifier in {
        generate_permalink(project_item.name),
        project_item.permalink,
    }


def canonical_memory_path_for_active_route(
    active_project: ProjectItem,
    path: str,
    *,
    include_project: bool,
) -> str:
    """Return the canonical permalink path for the active project."""
    if not include_project:
        return path
    project_prefix = active_project.permalink
    if path == project_prefix or path.startswith(f"{project_prefix}/"):
        return path
    return f"{project_prefix}/{path}"


def split_project_prefix(path: str) -> tuple[Optional[str], str]:
    """Split a possible project prefix from a memory URL path."""
    if "/" not in path:
        return None, path
    project_prefix, remainder = path.split("/", 1)
    if not project_prefix or not remainder or "*" in project_prefix:
        return None, path
    return project_prefix, remainder


def add_project_metadata(result: str, project_name: str) -> str:
    """Add project context as a metadata footer for session tracking."""
    return f"{result}\n\n[Session: Using project '{project_name}']"


def detect_project_from_url_prefix(identifier: str) -> Optional[str]:
    """Return the registered project matching a memory URL path prefix."""
    path = memory_url_path(identifier) if identifier.strip().startswith("memory://") else identifier
    normalized = normalize_project_reference(path)
    prefix, _ = split_project_prefix(normalized)
    if prefix is None:
        return None
    registered, _ = lookup_project(prefix)
    return registered
