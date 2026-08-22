"""Legacy Basic Memory configuration migrations."""

import os
from typing import Any


def migrate_legacy_sync_fields(
    data: Any,
    *,
    legacy_fields: dict[str, str],
    env_prefix: str,
) -> Any:
    """Map legacy sync field names while preserving new-name precedence."""
    if not isinstance(data, dict):
        return data
    for new_field, legacy_key in legacy_fields.items():
        if new_field in data:
            continue
        legacy_env_value = os.getenv(f"{env_prefix}{legacy_key.upper()}")
        if legacy_env_value is not None:
            data[new_field] = legacy_env_value
        elif legacy_key in data:
            data[new_field] = data[legacy_key]
    return data


# Keys that older releases wrote into config.json for surfaces this project has
# retired: cloud/routing, and the multi-backend database settings left over from
# when Postgres was selectable. They are dropped rather than translated: nothing
# reads them now, and leaving them in would fail validation or resurrect a
# concept that no longer exists.
RETIRED_TOP_LEVEL_KEYS = frozenset(
    {
        "default_project_mode",
        "cloud_mode",
        "project_modes",
        "cloud_projects",
        "database_backend",
        "database_url",
        "db_pool_size",
        "db_pool_overflow",
        "db_pool_recycle",
        "semantic_postgres_prepare_concurrency",
    }
)


def drop_retired_config_keys(data: Any) -> Any:
    """Remove retired top-level keys before the config model validates."""
    if not isinstance(data, dict):
        return data
    for key in RETIRED_TOP_LEVEL_KEYS:
        data.pop(key, None)
    return data


def normalize_legacy_projects(raw_config: dict[str, Any]) -> dict[str, str]:
    """Read a legacy ``config.json`` project registry as a name → path mapping.

    Older releases wrote the registry into config.json in two shapes: a bare
    ``{"name": "/path"}`` map, and a ``{"name": {"path": "/path", ...}}`` map.
    Both are read here so a one-time import into the database registry can
    accept either (GAPS B2). Nothing writes these keys any more.
    """
    projects = raw_config.get("projects")
    if not isinstance(projects, dict):
        return {}

    legacy_cloud_projects = raw_config.get("cloud_projects", {})
    normalized: dict[str, str] = {}
    for name, entry in projects.items():
        if isinstance(entry, str):
            path = entry
        elif isinstance(entry, dict):
            path = entry.get("path", "")
            # A remote-only project recorded a slug in ``path`` and the real
            # directory in the cloud entry's ``local_path``. Only that local
            # directory is meaningful now.
            legacy_entry = legacy_cloud_projects.get(name)
            if isinstance(legacy_entry, dict) and not os.path.isabs(path):
                path = legacy_entry.get("local_path") or path
        else:
            continue

        if path:
            normalized[name] = path
    return normalized
