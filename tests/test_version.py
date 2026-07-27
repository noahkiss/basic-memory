"""Version reporting is pinned to the installed distribution, not a hardcoded string.

The hardcoded `__version__` in `src/basic_memory/__init__.py` only moves on release, so a
fork build and a stock upstream build both self-report the same number. Package metadata is
derived from git by uv-dynamic-versioning, so it identifies the build that is actually running.
"""

from importlib.metadata import PackageNotFoundError, version as distribution_version

import pytest
from typer.testing import CliRunner

import basic_memory
from basic_memory.cli.app import app


def test_version_matches_installed_distribution() -> None:
    """`basic_memory.__version__` reports the installed build, not the release fallback."""
    assert basic_memory.__version__ == distribution_version("basic-memory")
    assert basic_memory.__version_from_metadata__ is True


def test_resolve_version_falls_back_when_distribution_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running from a source tree with no install falls back to the hardcoded release."""

    def missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.setattr(basic_memory, "_distribution_version", missing)
    assert basic_memory._resolve_version("9.9.9") == ("9.9.9", False)


def test_resolve_version_prefers_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Metadata wins over the fallback whenever the distribution is installed."""
    monkeypatch.setattr(basic_memory, "_distribution_version", lambda name: "1.2.3.dev4+abc")
    assert basic_memory._resolve_version("9.9.9") == ("1.2.3.dev4+abc", True)


def test_cli_version_reports_distribution_version() -> None:
    """`bm --version` prints the installed distribution version."""
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert distribution_version("basic-memory") in result.stdout


def test_cli_version_flags_a_source_tree_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the version is only the fallback, say so rather than print a wrong number."""
    monkeypatch.setattr(basic_memory, "__version__", "0.0.0-fallback")
    monkeypatch.setattr(basic_memory, "__version_from_metadata__", False)

    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert "0.0.0-fallback" in result.stdout
    assert "not installed" in result.stdout
