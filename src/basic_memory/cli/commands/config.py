"""CLI commands for managing Basic Memory's config.json (issue #991).

Every user-facing config option previously required hand-editing ``config.json``
or knowing the ``BASIC_MEMORY_*`` env-var naming convention. This module exposes
``bm config list|get|set|unset`` for the scalar subset of ``BasicMemoryConfig``
fields, validating ``set`` through the model itself so invalid values fail with
the same Pydantic error a malformed config.json would produce.
"""

import json
import os
import types
import typing
from dataclasses import dataclass
from enum import Enum
from typing import Any

import typer
from pydantic import ValidationError

from basic_memory.cli.app import app
from basic_memory.config import BasicMemoryConfig, ConfigManager
from basic_memory.redaction import SECRET_FIELDS, URL_FIELDS, redact_url

config_app = typer.Typer(help="Manage Basic Memory's config.json settings")
app.add_typer(config_app, name="config")

# Display sentinels — kept as named constants so the CLI and its tests agree on the
# exact strings users see for masked secrets and unset values.
SECRET_MASK = "********"
NOT_SET = "(not set)"

_SCALAR_TYPES = (str, int, float, bool)
_UNION_ORIGINS = (typing.Union, types.UnionType)


# --- Configurable-field discovery ---


def _is_scalar_annotation(annotation: Any) -> bool:
    """Whether a field's type annotation is a plain scalar (or Optional/Literal/Enum of one).

    Excludes structured fields (dict, list, nested models, datetime) that need
    their own dedicated commands (e.g. `projects` -> `bm project add`) or richer
    input parsing than a single CLI string argument can offer.
    """
    origin = typing.get_origin(annotation)
    if origin in _UNION_ORIGINS:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return len(args) == 1 and _is_scalar_annotation(args[0])
    if origin is typing.Literal:
        return True
    if origin is not None:
        return False
    return isinstance(annotation, type) and issubclass(annotation, (*_SCALAR_TYPES, Enum))


def _configurable_fields() -> tuple[str, ...]:
    """Scalar BasicMemoryConfig fields settable via `bm config`, derived from the model."""
    return tuple(
        sorted(
            name
            for name, field in BasicMemoryConfig.model_fields.items()
            if _is_scalar_annotation(field.annotation)
        )
    )


# Derived at import time so the allowlist tracks BasicMemoryConfig automatically
# rather than drifting from a hand-maintained list.
CONFIGURABLE_FIELDS: tuple[str, ...] = _configurable_fields()


# --- Value resolution and display ---


@dataclass(frozen=True, slots=True)
class ConfigSetting:
    """One configurable setting resolved for display.

    ``value`` is already redacted and stringified for output; ``source`` is one of
    ``default`` (unset), ``file`` (present in config.json), or ``env (VAR)`` (an
    environment variable is overriding the file value, which always wins).
    """

    key: str
    value: str
    source: str


def _env_var_name(key: str) -> str:
    return f"BASIC_MEMORY_{key.upper()}"


def _redact_for_display(key: str, raw: str) -> str:
    """Mask secrets and URL credentials the same way `basic_memory_diagnostics` does (#963)."""
    if key in SECRET_FIELDS:
        return SECRET_MASK
    if key in URL_FIELDS:
        return redact_url(raw)
    return raw


def _render_value(key: str, value: Any) -> str:
    """Render an effective config value for display, masking secrets/URL credentials."""
    if value is None:
        return NOT_SET
    if isinstance(value, Enum):
        value = value.value
    # An empty string is a real value (e.g. bugs_followup unset-by-default), but
    # a blank cell breaks the three-column list shape every parser relies on.
    if value == "":
        return '""'
    return _redact_for_display(key, str(value))


def _file_keys(config_manager: ConfigManager) -> frozenset[str]:
    """Top-level keys actually present in config.json, to tell `file` from `default`.

    config.json was already parsed by ConfigManager to build the loaded config, so a
    read here does not swallow errors: if the file exists it is valid JSON, and a
    genuine read failure should surface rather than silently mislabel every setting.
    """
    config_file = config_manager.config_file
    if not config_file.exists():
        return frozenset()
    return frozenset(json.loads(config_file.read_text(encoding="utf-8")))


def _resolve_settings() -> list[ConfigSetting]:
    """Resolve every configurable setting to its effective value and source."""
    config_manager = ConfigManager()
    config = config_manager.config
    file_keys = _file_keys(config_manager)

    settings: list[ConfigSetting] = []
    for key in CONFIGURABLE_FIELDS:
        env_var = _env_var_name(key)
        if env_var in os.environ:
            source = f"env ({env_var})"
        elif key in file_keys:
            source = "file"
        else:
            source = "default"
        settings.append(
            ConfigSetting(key=key, value=_render_value(key, getattr(config, key)), source=source)
        )
    return settings


def _require_known_key(key: str) -> None:
    """Exit with guidance unless `key` is a configurable scalar setting."""
    if key not in CONFIGURABLE_FIELDS:
        # Rule 6: the error and the affordance that resolves it both belong on stderr,
        # so a caller redirecting stdout gets an empty payload, not half an error.
        typer.echo(f"Error: '{key}' is not a recognized setting.", err=True)
        typer.echo("Run 'bm config list' to see all available settings.", err=True)
        raise typer.Exit(1)


# --- Commands ---


@config_app.command("list")
def config_list() -> None:
    """List every configurable setting with its effective value and source."""
    settings = _resolve_settings()

    key_width = max((len(setting.key) for setting in settings), default=0)
    value_width = max((len(setting.value) for setting in settings), default=0)
    for setting in settings:
        typer.echo(f"{setting.key:<{key_width}}  {setting.value:<{value_width}}  {setting.source}")
    typer.echo(f"{len(settings)} settings")


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Config setting name (see 'bm config list')"),
    quiet: bool = typer.Option(False, "--quiet", help="Hide the status lines and next-step hints"),
) -> None:
    """Show the effective value of one config setting."""
    _require_known_key(key)

    config = ConfigManager().config
    typer.echo(f"{key} = {_render_value(key, getattr(config, key))}")

    env_var = _env_var_name(key)
    if env_var in os.environ and not quiet:
        env_value = _redact_for_display(key, os.environ[env_var])
        typer.echo(f"Overridden by ${env_var} = {env_value}")


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Config setting name (see 'bm config list')"),
    value: str = typer.Argument(..., help="New value"),
    quiet: bool = typer.Option(False, "--quiet", help="Hide the status lines and next-step hints"),
) -> None:
    """Set one config setting to a new value.

    If the value is not valid for the setting, the command reports why and
    writes nothing to config.json.
    """
    _require_known_key(key)

    config_manager = ConfigManager()
    config = config_manager.load_config()

    # Validate the whole config with the candidate applied, so `value` is coerced and
    # constrained by the same rules that guard a hand-edited config.json.
    candidate = config.model_dump(mode="json")
    candidate[key] = value
    try:
        validated = BasicMemoryConfig.model_validate(candidate)
    except ValidationError as e:
        # Pydantic's rendering is a multi-line block with a docs URL; rule 6 allows one
        # line, so take the first error's message and drop the rest.
        detail = e.errors()[0]["msg"]
        typer.echo(f"invalid value for {key}: {detail}", err=True)
        raise typer.Exit(1)

    setattr(config, key, getattr(validated, key))
    config_manager.save_config(config)

    typer.echo(f"{key} = {_render_value(key, getattr(config, key))}")

    env_var = _env_var_name(key)
    if env_var in os.environ and not quiet:
        typer.echo(
            f"Note: ${env_var} is set and will override this file value "
            "until the environment variable is unset."
        )


@config_app.command("unset")
def config_unset(
    key: str = typer.Argument(..., help="Config setting name (see 'bm config list')"),
) -> None:
    """Revert a config setting to its default value."""
    _require_known_key(key)

    config_manager = ConfigManager()
    config = config_manager.load_config()

    default_value = BasicMemoryConfig.model_fields[key].get_default(call_default_factory=True)
    setattr(config, key, default_value)
    config_manager.save_config(config)

    typer.echo(f"{key} reverted to default: {_render_value(key, default_value)}")
