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


# Keys that older releases wrote into config.json for the retired cloud/routing
# surface. They are dropped rather than translated: nothing reads them now, and
# leaving them in would fail validation or resurrect a routing concept that no
# longer exists.
RETIRED_TOP_LEVEL_KEYS = frozenset(
    {
        "default_project_mode",
        "cloud_mode",
        "project_modes",
        "cloud_projects",
    }
)

RETIRED_PROJECT_KEYS = frozenset(
    {
        "mode",
        "workspace_id",
        "local_sync_path",
        "cloud_sync_path",
        "bisync_initialized",
        "last_sync",
    }
)


def migrate_legacy_projects(data: Any) -> Any:
    """Convert legacy project dictionaries into unified project entries."""
    if not isinstance(data, dict):
        return data

    legacy_cloud_projects = data.get("cloud_projects", {})
    for key in RETIRED_TOP_LEVEL_KEYS:
        data.pop(key, None)

    projects = data.get("projects", {})
    if not projects:
        return data

    first_value = next(iter(projects.values()), None)
    if isinstance(first_value, str):
        data["projects"] = {name: {"path": path} for name, path in projects.items()}

    projects = data["projects"]
    for name, entry in projects.items():
        if not isinstance(entry, dict):
            continue
        # A remote-only project recorded a slug in ``path`` and the real
        # directory in the cloud entry's ``local_path``. Only that local
        # directory is meaningful now.
        legacy_entry = legacy_cloud_projects.get(name)
        if isinstance(legacy_entry, dict) and not os.path.isabs(entry.get("path", "")):
            entry["path"] = legacy_entry.get("local_path") or entry.get("path", "")
        for key in RETIRED_PROJECT_KEYS:
            entry.pop(key, None)

    return data
