"""Shared loader for the bundled discovery markdown resources."""

from pathlib import Path


def load_discovery_resource(filename: str) -> str:
    """Read a bundled discovery markdown file."""
    return (Path(__file__).parent / filename).read_text(encoding="utf-8")
