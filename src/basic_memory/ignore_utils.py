"""Utilities for handling .gitignore patterns and file filtering."""

import fnmatch
from pathlib import Path
from typing import Set

from basic_memory.config import resolve_data_dir


# Marker shared by the API ignored-path rejection detail and MCP-side error handling.
# The index-file endpoint embeds it in its 400 detail and edit_note's disk recovery
# matches on it, so "exists but ignored" stays distinguishable from generic rejections
# without duplicating message text across layers.
IGNORED_PATH_REJECTION_DETAIL = (
    "matches Basic Memory ignore rules (global .bmignore, project .bmignore, or project .gitignore)"
)


# Common directories and patterns to ignore by default
# These are used as fallback if .bmignore doesn't exist
DEFAULT_IGNORE_PATTERNS = {
    # Hidden files (files starting with dot)
    ".*",
    # Basic Memory internal files
    "*.db",
    "*.db-shm",
    "*.db-wal",
    "config.json",
    # Version control
    ".git",
    ".svn",
    # Python
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".pytest_cache",
    ".coverage",
    "*.egg-info",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    # Virtual environments
    ".venv",
    "venv",
    "env",
    ".env",
    # Node.js
    "node_modules",
    # Build artifacts
    "build",
    "dist",
    ".cache",
    # IDE
    ".idea",
    ".vscode",
    # OS files
    ".DS_Store",
    "Thumbs.db",
    "desktop.ini",
    # Obsidian
    ".obsidian",
    # Temporary files
    "*.tmp",
    "*.swp",
    "*.swo",
    "*~",
}


# Files bm derives or governs itself, never note content (GAPS U8, U9). The store holds
# three kinds of file — records, bm's own derived output, and the project's control file —
# and only records are indexable. `headline.md` is rewritten on every write, so indexing it
# leaves `bm status` reporting a permanently unindexed file on a healthy project; and
# `vocabulary.yml` is the file that *defines* the corpus, so indexing it puts the control
# file in the corpus it controls.
#
# Root-relative (leading `/`) so a record that happens to be named `headline.md` in a
# subdirectory still indexes: only the project root copies are bm's own.
# Names mirror `services.headline.HEADLINE_FILENAME` and
# `vocabulary.model.VOCABULARY_FILENAME`, kept as literals here because importing either
# module would pull the service/DB graph into every ignore-pattern load.
DERIVED_FILE_IGNORE_PATTERNS = frozenset({"/headline.md", "/vocabulary.yml"})


def get_bmignore_path() -> Path:
    """Get path to .bmignore file.

    Returns:
        Path to <basic-memory data dir>/.bmignore, honoring
        ``BASIC_MEMORY_CONFIG_DIR`` so isolated instances each keep their
        own ignore file.
    """
    return resolve_data_dir() / ".bmignore"


def create_default_bmignore() -> None:
    """Create default .bmignore file if it doesn't exist.

    This ensures users have a file they can customize for all Basic Memory operations.
    """
    bmignore_path = get_bmignore_path()

    if bmignore_path.exists():
        return

    bmignore_path.parent.mkdir(parents=True, exist_ok=True)
    bmignore_path.write_text("""# Basic Memory Ignore Patterns
# This file is used by file indexing
# Patterns use standard gitignore-style syntax

# Hidden files (files starting with dot)
.*

# Basic Memory internal files (includes test databases)
*.db
*.db-shm
*.db-wal
config.json

# Version control
.git
.svn

# Python
__pycache__
*.pyc
*.pyo
*.pyd
.pytest_cache
.coverage
*.egg-info
.tox
.mypy_cache
.ruff_cache

# Virtual environments
.venv
venv
env
.env

# Node.js
node_modules

# Build artifacts
build
dist
.cache

# IDE
.idea
.vscode

# OS files
.DS_Store
Thumbs.db
desktop.ini

# Obsidian
.obsidian

# Temporary files
*.tmp
*.swp
*.swo
*~
""")


def load_bmignore_patterns() -> Set[str]:
    """Load patterns from .bmignore file.

    Returns:
        Set of patterns from .bmignore, or DEFAULT_IGNORE_PATTERNS if file doesn't exist
    """
    bmignore_path = get_bmignore_path()

    # Create default file if it doesn't exist
    if not bmignore_path.exists():
        create_default_bmignore()

    patterns = set()

    try:
        with bmignore_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # Skip empty lines and comments
                if line and not line.startswith("#"):
                    patterns.add(line)
    except Exception:  # pragma: no cover
        # If we can't read .bmignore, fall back to defaults
        return set(DEFAULT_IGNORE_PATTERNS)  # pragma: no cover

    # If no patterns were loaded, use defaults
    if not patterns:  # pragma: no cover
        return set(DEFAULT_IGNORE_PATTERNS)  # pragma: no cover

    return patterns


def read_ignore_pattern_file(path: Path) -> Set[str]:
    """Read gitignore-style patterns from one file; missing or unreadable means none."""
    try:
        with path.open("r", encoding="utf-8") as f:
            return {
                stripped
                for line in f
                if (stripped := line.strip()) and not stripped.startswith("#")
            }
    except OSError:
        return set()


def load_gitignore_patterns(base_path: Path) -> Set[str]:
    """Load the ignore patterns that apply to files under base_path.

    Combines patterns from:
    1. DERIVED_FILE_IGNORE_PATTERNS (bm's own derived and control files; not
       user-editable, because indexing them is never correct)
    2. <basic-memory data dir>/.bmignore (user's global ignore patterns, honors
       BASIC_MEMORY_CONFIG_DIR)
    3. {base_path}/.bmignore (project-specific index exclusions — for files that
       stay committed in git but must not be indexed, e.g. verbatim `_import/`
       copies in a store repo, which .gitignore cannot express)
    4. {base_path}/.gitignore (project-specific patterns)

    Returns:
        Set of patterns to ignore
    """
    patterns = set(DERIVED_FILE_IGNORE_PATTERNS)
    patterns |= load_bmignore_patterns()
    patterns |= read_ignore_pattern_file(base_path / ".bmignore")
    patterns |= read_ignore_pattern_file(base_path / ".gitignore")
    return patterns


def should_ignore_path(file_path: Path, base_path: Path, ignore_patterns: Set[str]) -> bool:
    """Check if a file path should be ignored based on gitignore patterns.

    Args:
        file_path: The file path to check
        base_path: The base directory for relative path calculation
        ignore_patterns: Set of patterns to match against

    Returns:
        True if the path should be ignored, False otherwise
    """
    # Get the relative path from base
    try:
        relative_path = file_path.relative_to(base_path)
        relative_str = str(relative_path)
        relative_posix = relative_path.as_posix()  # Use forward slashes for matching

        # Check each pattern
        for pattern in ignore_patterns:
            # Handle patterns starting with / (root relative)
            if pattern.startswith("/"):
                root_pattern = pattern[1:]  # Remove leading /

                # For directory patterns ending with /
                if root_pattern.endswith("/"):
                    dir_name = root_pattern[:-1]  # Remove trailing /
                    # Check if the first part of the path matches the directory name
                    if len(relative_path.parts) > 0 and relative_path.parts[0] == dir_name:
                        return True
                else:
                    # Regular root-relative pattern
                    if fnmatch.fnmatch(relative_posix, root_pattern):
                        return True
                continue

            # Handle directory patterns (ending with /)
            if pattern.endswith("/"):
                dir_name = pattern[:-1]  # Remove trailing /
                # Check if any path part matches the directory name
                if dir_name in relative_path.parts:
                    return True
                continue

            # Direct name match (e.g., ".git", "node_modules")
            if pattern in relative_path.parts:
                return True

            # Check if any individual path part matches the glob pattern
            # This handles cases like ".*" matching ".hidden.md" in "concept/.hidden.md"
            for part in relative_path.parts:
                if fnmatch.fnmatch(part, pattern):
                    return True

            # Glob pattern match on full path
            if fnmatch.fnmatch(relative_posix, pattern) or fnmatch.fnmatch(relative_str, pattern):
                return True  # pragma: no cover

        return False
    except ValueError:
        # If we can't get relative path, don't ignore
        return False


def filter_files(
    files: list[Path], base_path: Path, ignore_patterns: Set[str] | None = None
) -> tuple[list[Path], int]:
    """Filter a list of files based on gitignore patterns.

    Args:
        files: List of file paths to filter
        base_path: The base directory for relative path calculation
        ignore_patterns: Set of patterns to ignore. If None, loads from .gitignore

    Returns:
        Tuple of (filtered_files, ignored_count)
    """
    if ignore_patterns is None:
        ignore_patterns = load_gitignore_patterns(base_path)

    filtered_files = []
    ignored_count = 0

    for file_path in files:
        if should_ignore_path(file_path, base_path, ignore_patterns):
            ignored_count += 1
        else:
            filtered_files.append(file_path)

    return filtered_files, ignored_count
