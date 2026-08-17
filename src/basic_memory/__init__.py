"""basic-memory - Local-first knowledge management combining Zettelkasten with knowledge graphs"""

from importlib.metadata import PackageNotFoundError, version as _distribution_version

_DISTRIBUTION_NAME = "basic-memory"

# Constraint: a bare source tree has no distribution metadata, so there is no version to report.
# We report this placeholder rather than a hardcoded release number, which would name a build that
# is not the one running. Callers pair it with `__version_from_metadata__` to say so out loud.
_UNINSTALLED_VERSION = "0.0.0"


def _resolve_version() -> tuple[str, bool]:
    """Return the version to report, and whether it came from installed package metadata.

    Metadata is derived from git by uv-dynamic-versioning, so it names the build actually
    installed (e.g. `0.1.0` for a tagged release, `0.1.0.dev4+79dc916e` between tags). Callers
    that report the version to a human must surface the second element: `0.0.0` is not a version.
    """
    try:
        return _distribution_version(_DISTRIBUTION_NAME), True
    except PackageNotFoundError:
        return _UNINSTALLED_VERSION, False


__version__, __version_from_metadata__ = _resolve_version()

# API version for FastAPI - independent of package version
__api_version__ = "v2"
