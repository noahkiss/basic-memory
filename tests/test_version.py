"""Version reporting is pinned to the installed distribution, not a hardcoded string.

There is no `__version__` literal to fall out of date: package metadata is derived from git by
uv-dynamic-versioning, so it identifies the build that is actually running. A source tree with no
install has no version at all, and reports `0.0.0` plus the flag that says so.
"""

from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path

import pytest
from typer.testing import CliRunner

import basic_memory
from basic_memory.cli.app import app


def test_version_matches_installed_distribution() -> None:
    """`basic_memory.__version__` reports the installed build."""
    assert basic_memory.__version__ == distribution_version("basic-memory")
    assert basic_memory.__version_from_metadata__ is True


def test_resolve_version_falls_back_when_distribution_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No install means the `0.0.0` placeholder, flagged as not coming from metadata."""

    def missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(basic_memory, "_distribution_version", missing)
    assert basic_memory._resolve_version() == ("0.0.0", False)


def test_resolve_version_prefers_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Metadata is used whenever the distribution is installed."""
    monkeypatch.setattr(basic_memory, "_distribution_version", lambda name: "1.2.3.dev4+abc")
    assert basic_memory._resolve_version() == ("1.2.3.dev4+abc", True)


def test_no_hardcoded_version_literal() -> None:
    """Guard the deletion: a reintroduced literal would silently outrank metadata again."""
    source = Path(basic_memory.__file__ or "").read_text()
    assert '__version__ = "' not in source


def test_cli_version_reports_distribution_version() -> None:
    """`bm --version` prints the installed distribution version."""
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert distribution_version("basic-memory") in result.stdout


def test_cli_version_flags_a_source_tree_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """When there is no installed build, say so rather than print a meaningless number."""
    monkeypatch.setattr(basic_memory, "__version__", "0.0.0")
    monkeypatch.setattr(basic_memory, "__version_from_metadata__", False)

    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "0.0.0" in result.stdout
    assert "not installed" in result.stdout
