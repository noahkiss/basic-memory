"""Telemetry coverage for CLI command boundaries."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import logfire

from basic_memory.cli import app as cli_app


class FakeContext:
    """Small Typer-like context for callback testing."""

    def __init__(self, invoked_subcommand: str | None) -> None:
        self.invoked_subcommand = invoked_subcommand
        self.resources: list[object] = []
        self.close_callbacks: list[object] = []

    def with_resource(self, resource: object) -> None:
        self.resources.append(resource)

    def call_on_close(self, callback) -> None:
        self.close_callbacks.append(callback)


def test_app_callback_registers_command_operation(monkeypatch) -> None:
    operations: list[tuple[str, dict]] = []
    resource = object()

    monkeypatch.setattr(cli_app, "init_cli_logging", lambda: None)
    monkeypatch.setattr(
        cli_app.CliContainer, "create", staticmethod(lambda: SimpleNamespace(config=object()))
    )
    monkeypatch.setattr(cli_app, "set_container", lambda container: None)

    def fake_span(name: str, **attrs):
        operations.append((name, attrs))
        return resource

    monkeypatch.setattr(logfire, "span", fake_span)

    ctx = FakeContext(invoked_subcommand="status")
    cli_app.app_callback(cast(Any, ctx), version=None)

    assert ctx.resources == [resource]
    assert operations == [
        (
            "cli.command.status",
            {"entrypoint": "cli", "command_name": "status"},
        )
    ]
