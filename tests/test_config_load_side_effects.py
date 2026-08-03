"""Regression tests for side effects while loading existing configuration."""

import json
import os
from pathlib import Path

import pytest

from basic_memory import config as config_module
from basic_memory.config import ConfigManager


def test_legacy_project_registry_is_tolerated_and_never_written_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-B2 config.json still parses, and its registry keys never round-trip.

    The database owns the registry now. The legacy keys are read once — by
    ``legacy_config_registry()``, for the one-time import into an empty
    registry — and are absent from the model, so nothing can write them back.
    Loading must also not materialize any project directory.
    """
    home = tmp_path / "home"
    config_dir = tmp_path / "config"
    project_dir = tmp_path / "custom-project"
    home.mkdir()
    config_dir.mkdir()
    project_dir.mkdir()

    monkeypatch.setenv("HOME", str(home))
    if os.name == "nt":
        monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(config_dir))
    monkeypatch.delenv("BASIC_MEMORY_HOME", raising=False)
    monkeypatch.setattr(config_module, "_CONFIG_CACHE", None)
    monkeypatch.setattr(config_module, "_CONFIG_MTIME", None)
    monkeypatch.setattr(config_module, "_CONFIG_SIZE", None)

    config_path = config_dir / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "projects": {
                    "custom": {
                        "path": str(project_dir),
                        "mode": "local",
                    }
                },
                "default_project": "custom",
            }
        ),
        encoding="utf-8",
    )

    loaded = ConfigManager().load_config()

    assert "projects" not in loaded.model_dump()
    assert "default_project" not in loaded.model_dump()
    assert not (home / "basic-memory").exists()

    # The legacy keys stay readable for the one-time import until something
    # rewrites the file in the current shape.
    legacy_projects, legacy_default = config_module.legacy_config_registry()
    assert legacy_projects == {"custom": str(project_dir)}
    assert legacy_default == "custom"


def test_existing_config_load_keeps_environment_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Removing overridden file fields still delegates environment parsing to BaseSettings."""
    config_dir = tmp_path / "config"
    project_dir = tmp_path / "custom-project"
    config_dir.mkdir()
    project_dir.mkdir()

    monkeypatch.setenv("BASIC_MEMORY_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("BASIC_MEMORY_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("BASIC_MEMORY_FORMATTERS", '{"md":"formatter --write {file}"}')
    monkeypatch.setattr(config_module, "_CONFIG_CACHE", None)
    monkeypatch.setattr(config_module, "_CONFIG_MTIME", None)
    monkeypatch.setattr(config_module, "_CONFIG_SIZE", None)

    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "projects": {"custom": {"path": str(project_dir)}},
                "default_project": "custom",
                "log_level": "INFO",
                "formatters": {"md": "old-formatter {file}"},
            }
        ),
        encoding="utf-8",
    )

    loaded = ConfigManager().load_config()

    assert loaded.log_level == "DEBUG"
    assert loaded.formatters == {"md": "formatter --write {file}"}
