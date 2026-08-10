"""Tests for the `bm config` command group (issue #991)."""

import json
import re
from enum import Enum
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from typer.testing import CliRunner

from basic_memory.cli.app import app
from basic_memory.config import BasicMemoryConfig

# Importing registers the config subcommands on the shared app instance.
import basic_memory.cli.commands.config as config_cmd  # noqa: F401


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def write_config(tmp_path, monkeypatch):
    """Write config.json under a temporary HOME and return the file path."""

    def _write(config_data: dict) -> Path:
        from basic_memory import config as config_module

        config_module._CONFIG_CACHE = None
        config_module._CONFIG_MTIME = None
        config_module._CONFIG_SIZE = None

        config_dir = tmp_path / ".basic-memory"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.json"
        config_file.write_text(json.dumps(config_data, indent=2))
        monkeypatch.setenv("HOME", str(tmp_path))
        return config_file

    return _write


def _list_rows(output: str) -> dict[str, tuple[str, str]]:
    """Parse `config list` column output into {key: (value, source)}.

    Columns are padded with at least two spaces, and the final line is the count.
    """
    lines = output.strip().splitlines()
    assert lines[-1].endswith(" settings"), lines[-1]
    rows = {}
    for line in lines[:-1]:
        key, value, source = (field.strip() for field in re.split(r"\s{2,}", line.strip()))
        rows[key] = (value, source)
    return rows


def _base_config(**overrides) -> dict:
    data = {
        "env": "dev",
        # No retired project keys (`mode`, `local_sync_path`, …): their presence
        # triggers ConfigManager's migrate-and-resave path, which rewrites
        # config.json out from under these tests and logs to a closed stream.
        "projects": {"main": {"path": "/tmp/main"}},
        "default_project": "main",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# CONFIGURABLE_FIELDS derivation
# ---------------------------------------------------------------------------


def test_configurable_fields_excludes_structured_types():
    """Structured fields need dedicated commands or richer parsing, so they're excluded."""
    assert "projects" not in config_cmd.CONFIGURABLE_FIELDS
    assert "formatters" not in config_cmd.CONFIGURABLE_FIELDS


def test_configurable_fields_includes_scalar_settings():
    """Scalar settings (str/bool/int/float/Literal/Enum) are derived from the model."""
    for expected in ("log_level", "kebab_filenames", "sqlite_synchronous"):
        assert expected in config_cmd.CONFIGURABLE_FIELDS


def test_configurable_fields_matches_model_field_count():
    """Every configurable field name must be a real BasicMemoryConfig field."""
    assert set(config_cmd.CONFIGURABLE_FIELDS) <= set(BasicMemoryConfig.model_fields)


# ---------------------------------------------------------------------------
# get / list default behavior
# ---------------------------------------------------------------------------


def test_config_get_default_value(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "get", "log_level"])

    assert result.exit_code == 0, result.output
    assert "log_level = INFO" in result.output


def test_config_get_unknown_key(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "get", "not_a_real_setting"])

    assert result.exit_code == 1
    # The error and the affordance that resolves it both belong on stderr.
    assert result.stdout == ""
    assert "not a recognized setting" in result.stderr
    assert "bm config list" in result.stderr


def test_render_value_renders_enum_value_not_repr():
    """Enum-typed settings must display their value, not `Class.MEMBER`."""

    class Flavor(Enum):
        VANILLA = "vanilla"

    assert config_cmd._render_value("flavor", Flavor.VANILLA) == "vanilla"


def test_config_list_shows_default_source(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 0, result.output
    assert "kebab_filenames" in result.output
    assert "default" in result.output


def test_config_list_shows_file_source_for_set_field(runner, write_config):
    write_config(_base_config(log_level="DEBUG"))

    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 0, result.output
    assert _list_rows(result.stdout)["log_level"] == ("DEBUG", "file")


def test_config_list_counts_every_setting(runner, write_config):
    """The count line closes the listing and matches the rows above it."""
    write_config(_base_config())

    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 0, result.output
    rows = _list_rows(result.stdout)
    assert "log_level" in rows
    assert result.stdout.strip().splitlines()[-1] == f"{len(rows)} settings"


# ---------------------------------------------------------------------------
# set: validation through BasicMemoryConfig
# ---------------------------------------------------------------------------


def test_config_set_valid_value_round_trips(runner, write_config):
    config_file = write_config(_base_config())

    result = runner.invoke(app, ["config", "set", "log_level", "DEBUG"])
    assert result.exit_code == 0, result.output
    assert "log_level = DEBUG" in result.output

    on_disk = json.loads(config_file.read_text())
    assert on_disk["log_level"] == "DEBUG"

    get_result = runner.invoke(app, ["config", "get", "log_level"])
    assert "log_level = DEBUG" in get_result.output


def test_config_set_invalid_value_fails_with_pydantic_error(runner, write_config):
    config_file = write_config(_base_config())

    result = runner.invoke(app, ["config", "set", "kebab_filenames", "bogus"])

    assert result.exit_code == 1
    assert result.stdout == ""
    # One line on stderr, not pydantic's multi-line block with its docs URL.
    assert len(result.stderr.strip().splitlines()) == 1
    assert result.stderr.startswith("invalid value for kebab_filenames: ")

    # Config file must be untouched by a failed validation.
    on_disk = json.loads(config_file.read_text())
    assert "kebab_filenames" not in on_disk


def test_config_set_coerces_bool_from_string(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "set", "format_on_save", "true"])

    assert result.exit_code == 0, result.output
    assert "format_on_save = True" in result.output


def test_config_set_rejects_structured_field(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "set", "projects", '{"x": "/tmp/x"}'])

    assert result.exit_code == 1
    assert "not a recognized setting" in result.stderr


def test_config_set_unknown_key(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "set", "not_a_real_setting", "value"])

    assert result.exit_code == 1
    assert "not a recognized setting" in result.stderr


# ---------------------------------------------------------------------------
# unset: revert to default
# ---------------------------------------------------------------------------


def test_config_unset_reverts_to_default(runner, write_config):
    write_config(_base_config(log_level="DEBUG"))

    result = runner.invoke(app, ["config", "unset", "log_level"])

    assert result.exit_code == 0, result.output
    assert "reverted to default: INFO" in result.output

    get_result = runner.invoke(app, ["config", "get", "log_level"])
    assert "log_level = INFO" in get_result.output


def test_config_unset_unknown_key(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "unset", "not_a_real_setting"])

    assert result.exit_code == 1
    assert "not a recognized setting" in result.stderr


# ---------------------------------------------------------------------------
# Redaction: secrets must never print, URL credentials masked
# ---------------------------------------------------------------------------


def test_config_get_never_prints_secret_field(runner, write_config):
    write_config(_base_config(semantic_embedding_api_key="sk_super_secret_token"))

    result = runner.invoke(app, ["config", "get", "semantic_embedding_api_key"])

    assert result.exit_code == 0, result.output
    assert "sk_super_secret_token" not in result.output
    assert "semantic_embedding_api_key = ********" in result.output


def test_config_list_never_prints_secret_field(runner, write_config):
    write_config(_base_config(semantic_embedding_api_key="sk_super_secret_token"))

    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 0, result.output
    assert "sk_super_secret_token" not in result.output
    assert _list_rows(result.stdout)["semantic_embedding_api_key"][0] == "********"


def test_config_get_masks_url_field_credentials(runner, write_config):
    write_config(
        _base_config(semantic_embedding_api_base="https://apiuser:apipass@host.example.com:5432/v1")
    )

    result = runner.invoke(app, ["config", "get", "semantic_embedding_api_base"])

    assert result.exit_code == 0, result.output
    assert "apipass" not in result.output
    assert "apiuser" not in result.output
    # Parse the masked URL and compare the hostname exactly — a bare substring
    # check trips CodeQL's incomplete-URL-sanitization rule and proves less.
    masked_url = result.output.split("semantic_embedding_api_base = ", 1)[1].strip()
    assert urlsplit(masked_url).hostname == "host.example.com"


def test_config_get_shows_not_set_for_unset_secret(runner, write_config):
    write_config(_base_config())

    result = runner.invoke(app, ["config", "get", "semantic_embedding_api_key"])

    assert result.exit_code == 0, result.output
    assert "semantic_embedding_api_key = (not set)" in result.output


# ---------------------------------------------------------------------------
# Env var overrides
# ---------------------------------------------------------------------------


def test_config_get_shows_env_override(runner, write_config, monkeypatch):
    write_config(_base_config())
    monkeypatch.setenv("BASIC_MEMORY_LOG_LEVEL", "DEBUG")

    result = runner.invoke(app, ["config", "get", "log_level"])

    assert result.exit_code == 0, result.output
    assert "log_level = DEBUG" in result.output
    assert "BASIC_MEMORY_LOG_LEVEL" in result.output


def test_config_get_masks_secret_env_override(runner, write_config, monkeypatch):
    write_config(_base_config())
    monkeypatch.setenv("BASIC_MEMORY_SEMANTIC_EMBEDDING_API_KEY", "sk_env_secret_token")

    result = runner.invoke(app, ["config", "get", "semantic_embedding_api_key"])

    assert result.exit_code == 0, result.output
    assert "sk_env_secret_token" not in result.output
    assert "********" in result.output


def test_config_list_shows_env_source(runner, write_config, monkeypatch):
    write_config(_base_config())
    monkeypatch.setenv("BASIC_MEMORY_LOG_LEVEL", "DEBUG")

    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 0, result.output
    value, source = _list_rows(result.stdout)["log_level"]
    assert value == "DEBUG"
    assert source == "env (BASIC_MEMORY_LOG_LEVEL)"


def test_config_set_warns_when_env_var_overrides(runner, write_config, monkeypatch):
    write_config(_base_config())
    monkeypatch.setenv("BASIC_MEMORY_LOG_LEVEL", "DEBUG")

    result = runner.invoke(app, ["config", "set", "log_level", "INFO"])

    assert result.exit_code == 0, result.output
    assert "BASIC_MEMORY_LOG_LEVEL is set" in result.output


def test_config_quiet_drops_the_env_override_notice(runner, write_config, monkeypatch):
    """--quiet leaves the payload line and nothing else."""
    write_config(_base_config())
    monkeypatch.setenv("BASIC_MEMORY_LOG_LEVEL", "DEBUG")

    get_result = runner.invoke(app, ["config", "get", "log_level", "--quiet"])
    assert get_result.exit_code == 0, get_result.output
    assert get_result.stdout.splitlines() == ["log_level = DEBUG"]

    set_result = runner.invoke(app, ["config", "set", "log_level", "INFO", "--quiet"])
    assert set_result.exit_code == 0, set_result.output
    assert set_result.stdout.splitlines() == ["log_level = INFO"]
