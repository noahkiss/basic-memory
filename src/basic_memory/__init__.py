"""basic-memory - Local-first knowledge management combining Zettelkasten with knowledge graphs"""

from importlib.metadata import PackageNotFoundError, version as _distribution_version

_DISTRIBUTION_NAME = "basic-memory"

# Release fallback - updated by release automation (scripts/update_versions.py). It only moves on
# release, so every build between two releases carries the same number and it cannot identify the
# code that is running. Used only when no installed distribution exists (a bare source tree).
__version__ = "0.22.1"


def _resolve_version(fallback: str) -> tuple[str, bool]:
    """Return the version to report, and whether it came from installed package metadata.

    Metadata is derived from git by uv-dynamic-versioning, so it names the build actually
    installed (e.g. `0.22.2.dev120+79dc916e`). Callers that report the version to a human must
    surface the second element: a fallback number is not the running code's version.
    """
    try:
        return _distribution_version(_DISTRIBUTION_NAME), True
    except PackageNotFoundError:
        return fallback, False


__version__, __version_from_metadata__ = _resolve_version(__version__)

# API version for FastAPI - independent of package version
__api_version__ = "v2"
