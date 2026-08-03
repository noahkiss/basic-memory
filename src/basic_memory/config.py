"""Configuration persistence and the stable public configuration facade."""

import importlib as importlib
import importlib.util  # noqa: F401 - preserves the historical config.importlib.util seam
import json
import os
import shutil
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from loguru import logger

from basic_memory import config_logging as _config_logging
from basic_memory.config_migrations import RETIRED_TOP_LEVEL_KEYS, normalize_legacy_projects
from basic_memory.config_models import (
    APP_DATABASE_NAME as APP_DATABASE_NAME,
    CONFIG_DIR_MODE as CONFIG_DIR_MODE,
    CONFIG_FILE_MODE as CONFIG_FILE_MODE,
    CONFIG_FILE_NAME as CONFIG_FILE_NAME,
    DATABASE_NAME as DATABASE_NAME,
    DATA_DIR_NAME as DATA_DIR_NAME,
    WATCH_STATUS_JSON as WATCH_STATUS_JSON,
    BasicMemoryConfig as BasicMemoryConfig,
    Environment as Environment,
    ProjectConfig as ProjectConfig,
    _secure_config_dir,
    _secure_config_file,
    bootstrap_project_home as bootstrap_project_home,
    default_fastembed_cache_dir as default_fastembed_cache_dir,
    is_locally_syncable as is_locally_syncable,
    resolve_data_dir as resolve_data_dir,
    shared_fastembed_cache_dir as shared_fastembed_cache_dir,
)
from basic_memory.utils import setup_logging


# Cache state remains on the public module because long-lived callers and test
# fixtures deliberately reset these names between isolated config directories.
_CONFIG_CACHE: Optional[BasicMemoryConfig] = None
_CONFIG_MTIME: Optional[float] = None
_CONFIG_SIZE: Optional[int] = None

# The registry keys a pre-B2 config.json may still carry, captured verbatim the
# last time the file was read. ``ensure_project_registry()`` imports them into
# an empty database registry once; nothing writes them back. Captured at load
# because a format migration resave (see ``load_config``) drops keys the model
# no longer declares, which would otherwise destroy them before the import runs.
_LEGACY_PROJECTS: Dict[str, str] = {}
_LEGACY_DEFAULT_PROJECT: Optional[str] = None


class ConfigManager:
    """Manage Basic Memory's persisted global configuration."""

    def __init__(self) -> None:
        self.config_dir = resolve_data_dir()
        self.config_file = self.config_dir / CONFIG_FILE_NAME
        self.config_dir.mkdir(parents=True, exist_ok=True)
        _secure_config_dir(self.config_dir)

    @property
    def config(self) -> BasicMemoryConfig:
        """Get configuration, loading it lazily if needed."""
        return self.load_config()

    def load_config(self) -> BasicMemoryConfig:
        """Load configuration with environment values taking file precedence."""
        global _CONFIG_CACHE, _CONFIG_MTIME, _CONFIG_SIZE

        if _CONFIG_CACHE is not None:
            try:
                stat_result = self.config_file.stat()
                current_mtime = stat_result.st_mtime
                current_size = stat_result.st_size
            except OSError:
                current_mtime = None
                current_size = None

            if (
                current_mtime is not None
                and current_mtime == _CONFIG_MTIME
                and current_size == _CONFIG_SIZE
            ):
                return _CONFIG_CACHE

            _CONFIG_CACHE = None
            _CONFIG_MTIME = None
            _CONFIG_SIZE = None

        if self.config_file.exists():
            try:
                file_data = json.loads(self.config_file.read_text(encoding="utf-8"))
                # Resaving rewrites config.json in the current shape. It is
                # triggered by anything the before-validators will strip or
                # rewrite, so the on-disk file stops carrying retired keys.
                needs_resave = bool(RETIRED_TOP_LEVEL_KEYS & file_data.keys())

                _capture_legacy_registry(file_data)

                merged_data = file_data.copy()
                for field_name in BasicMemoryConfig.model_fields:
                    env_var_name = f"BASIC_MEMORY_{field_name.upper()}"
                    if env_var_name in os.environ:
                        # BaseSettings only applies env precedence when the field
                        # is absent from constructor data.
                        merged_data.pop(field_name, None)

                _CONFIG_CACHE = BasicMemoryConfig(**merged_data)

                try:
                    stat_result = self.config_file.stat()
                    _CONFIG_MTIME = stat_result.st_mtime
                    _CONFIG_SIZE = stat_result.st_size
                except OSError:
                    _CONFIG_MTIME = None
                    _CONFIG_SIZE = None

                if needs_resave:
                    backup_path = self.config_file.with_suffix(".json.bak")
                    shutil.copy2(self.config_file, backup_path)
                    _secure_config_file(backup_path)
                    logger.info(f"Migrating config to current format (backup: {backup_path})")
                    save_basic_memory_config(self.config_file, _CONFIG_CACHE)

                return _CONFIG_CACHE
            except json.JSONDecodeError as error:  # pragma: no cover
                logger.error(f"Invalid JSON in config file {self.config_file}: {error}")
                raise SystemExit(
                    f"Error: config file is not valid JSON: {self.config_file}\n"
                    f"  {error}\n"
                    f"Fix or delete the file and re-run."
                )
            except Exception as error:  # pragma: no cover
                logger.error(f"Failed to load config from {self.config_file}: {error}")
                raise SystemExit(
                    f"Error: failed to load config from {self.config_file}\n"
                    f"  {error}\n"
                    f"Fix or delete the file and re-run."
                )

        config = BasicMemoryConfig()
        self.save_config(config)
        return config

    def save_config(self, config: BasicMemoryConfig) -> None:
        """Save configuration to file and invalidate the process cache."""
        global _CONFIG_CACHE, _CONFIG_MTIME, _CONFIG_SIZE
        save_basic_memory_config(self.config_file, config)
        _CONFIG_CACHE = None
        _CONFIG_MTIME = None
        _CONFIG_SIZE = None


def _capture_legacy_registry(file_data: dict) -> None:
    """Remember a pre-B2 registry found in config.json for the one-time import."""
    global _LEGACY_PROJECTS, _LEGACY_DEFAULT_PROJECT
    # Always reassign, including to empty: the capture must describe the file
    # that was just read, or a later config dir inherits the previous one's
    # registry.
    _LEGACY_PROJECTS = normalize_legacy_projects(file_data)
    legacy_default = file_data.get("default_project")
    _LEGACY_DEFAULT_PROJECT = legacy_default if isinstance(legacy_default, str) else None


def legacy_config_registry() -> Tuple[Dict[str, str], Optional[str]]:
    """Return the ``(projects, default_project)`` a legacy config.json declares.

    Reads the file directly so the caller does not depend on load ordering, and
    falls back to what the last load captured — a format-migration resave will
    have already dropped these keys from disk, and losing them would strand a
    pre-B2 install with an empty registry.
    """
    config_file = resolve_data_dir() / CONFIG_FILE_NAME
    if config_file.is_file():
        try:
            file_data = json.loads(config_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:  # pragma: no cover - load_config already exits on this
            file_data = {}
        legacy_projects = normalize_legacy_projects(file_data)
        if legacy_projects:
            legacy_default = file_data.get("default_project")
            return legacy_projects, legacy_default if isinstance(legacy_default, str) else None

    return dict(_LEGACY_PROJECTS), _LEGACY_DEFAULT_PROJECT


def get_project_config(project_name: Optional[str] = None) -> ProjectConfig:
    """Get the requested or default project configuration from the registry."""
    from basic_memory.project_registry import default_project_name, lookup_project

    actual_project_name = project_name or default_project_name()
    if actual_project_name is None:
        raise ValueError("No project specified and no default project is set")

    name, path = lookup_project(actual_project_name)
    if name is None or path is None:
        raise ValueError(f"Project '{actual_project_name}' not found")
    return ProjectConfig(name=name, home=Path(path))


def save_basic_memory_config(file_path: Path, config: BasicMemoryConfig) -> None:
    """Atomically save configuration so concurrent readers see complete JSON."""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        _secure_config_dir(file_path.parent)
        config_dict = config.model_dump(mode="json")
        temp_path = file_path.parent / f"{file_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            temp_path.write_text(json.dumps(config_dict, indent=2))
            _secure_config_file(temp_path)
            os.replace(temp_path, file_path)
        finally:
            temp_path.unlink(missing_ok=True)
    except Exception as error:  # pragma: no cover
        logger.error(f"Failed to save config: {error}")


def init_cli_logging() -> None:
    """Initialize CLI logging without writing protocol output to stdout."""
    log_level = os.getenv("BASIC_MEMORY_LOG_LEVEL", "INFO")
    _config_logging.initialize_file_logging(
        log_level=log_level,
        setup_logging=setup_logging,
    )


def init_mcp_logging() -> None:
    """Initialize MCP logging without corrupting the JSON-RPC stream."""
    log_level = os.getenv("BASIC_MEMORY_LOG_LEVEL", "INFO")
    _config_logging.initialize_file_logging(
        log_level=log_level,
        setup_logging=setup_logging,
    )


def init_api_logging() -> None:
    """Initialize API file logging without writing to stdout."""
    log_level = os.getenv("BASIC_MEMORY_LOG_LEVEL", "INFO")
    _config_logging.initialize_file_logging(
        log_level=log_level,
        setup_logging=setup_logging,
    )
