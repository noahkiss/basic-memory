"""Record ids, title slugs, and the file path a record lands at.

The three rules this module holds are `.forked/schema.md` §2 and §8, and they
are decisions rather than conventions:

- **An id is `tnd-` + eight characters from a 36-symbol alphabet** (2.8e12
  values), drawn with ``secrets.choice``. Never a counter: `tnd-0001` needs a
  per-project allocator, and two machines writing records on separate branches
  would each allocate the same next number.
- **Hyphen, never underscore.** Relation targets are slugified even though
  explicit permalinks are not, so `tnd_aaaa1111` and `tnd-aaaa1111` collapse
  into one relation row, and `memory://` normalization makes an underscore id
  unreliable to address (`docs/IDENTITY.md` §2, `GAPS.md` T9).
- **The file is `<type-dir>/<id>--<slug>.md`.** The id comes first so a file is
  identity-addressable without parsing, and the separator is a double dash
  because a single one is ambiguous against the hyphens inside both halves.

Pure functions only: nothing here reads the database, the config, or a
vocabulary file. The caller that knows whether an id is taken passes the
predicate to ``allocate_record_id``.
"""

from __future__ import annotations

import re
import secrets
from types import MappingProxyType
from typing import Callable, Final

from unidecode import unidecode

ID_PREFIX: Final = "tnd-"

# 26 letters + 10 digits. No uppercase, because ids travel through permalinks
# and relation targets, both of which lowercase; a mixed-case id would not
# survive the round trip as the same string.
ID_ALPHABET: Final = "abcdefghijklmnopqrstuvwxyz0123456789"
ID_LENGTH: Final = 8

# How many ids ``allocate_record_id`` will draw before giving up. At 36^8 a
# second collision means the predicate is wrong, not that we were unlucky, so
# looping longer would hide a defect rather than route around one.
MAX_ID_ATTEMPTS: Final = 5

# Long enough to stay readable in `ls`, short enough that `<id>--<slug>.md`
# clears every filesystem's name limit with room to spare.
MAX_SLUG_LENGTH: Final = 60

# What a title that folds to nothing gets. A record's identity is its id, so an
# unusable slug is a cosmetic loss, never a reason to refuse the write.
FALLBACK_SLUG: Final = "untitled"

SEPARATOR: Final = "--"

_ID_PATTERN: Final = re.compile(rf"^{re.escape(ID_PREFIX)}[{ID_ALPHABET}]{{{ID_LENGTH}}}$")

# Plural directories, one per record type (schema.md §8). Type is set-once, so
# the directory a record lives in is stable for the record's whole life.
# ``inbox`` is a place rather than a count, so it does not take an `s`.
TYPE_DIRS: Final = MappingProxyType(
    {
        "task": "tasks",
        "guide": "guides",
        "finding": "findings",
        "profile": "profiles",
        "state": "states",
        "inbox": "inbox",
    }
)


class IdAllocationError(RuntimeError):
    """Every drawn id was already taken. Raised rather than looping forever."""


def new_record_id() -> str:
    """Draw one record id. Uniqueness is the caller's to check.

    ``secrets`` rather than ``random``: ids appear in file names and in commit
    messages, and a predictable sequence would let one project's ids be guessed
    from another's. The cost is nil at eight characters.
    """
    body = "".join(secrets.choice(ID_ALPHABET) for _ in range(ID_LENGTH))
    return f"{ID_PREFIX}{body}"


def is_record_id(value: str) -> bool:
    """True when ``value`` is exactly a record id — prefix, alphabet, and length."""
    return bool(_ID_PATTERN.match(value))


def allocate_record_id(is_taken: Callable[[str], bool], *, attempts: int = MAX_ID_ATTEMPTS) -> str:
    """Draw ids until one is free, then return it.

    ``is_taken`` is supplied by the caller because only the caller knows what
    "taken" means — a permalink query for one project, a set of ids already
    staged in this transaction. Keeping it here would drag the database into a
    module the fast CLI path imports.

    Raises ``IdAllocationError`` after ``attempts`` collisions.
    """
    for _ in range(attempts):
        candidate = new_record_id()
        if not is_taken(candidate):
            return candidate
    raise IdAllocationError(
        f"could not allocate a free record id in {attempts} attempts; "
        f"{attempts} collisions at {len(ID_ALPHABET)}^{ID_LENGTH} means the "
        "collision check is wrong, not that the draw was unlucky"
    )


def record_slug(title: str) -> str:
    """Fold a title into the `[a-z0-9-]` half of a record's file name.

    The folding is ``generate_permalink``'s ASCII branch (``docs/IDENTITY.md``
    §2.2): transliterate through ``unidecode``, split camelCase, lowercase,
    underscores to hyphens, drop apostrophes, everything else to a hyphen.

    **One deliberate divergence: periods do not survive here.** A permalink
    keeps them so `version-2.0.0` stays addressable; a file name that keeps them
    grows a `.0.md` tail that reads as a double extension. The slug is a human
    label — nothing resolves through it — so losing the period costs nothing.
    """
    folded = unidecode(title)
    folded = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", folded)
    lowered = folded.lower().replace("_", "-").replace("'", "")
    cleaned = re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]", "-", lowered)).strip("-")
    # Truncate first, then re-strip: cutting at 60 can leave a trailing hyphen
    # where a word boundary fell, and `tnd-x--long-title-.md` reads as a typo.
    return cleaned[:MAX_SLUG_LENGTH].rstrip("-") or FALLBACK_SLUG


def type_dir(record_type: str) -> str:
    """The directory records of ``record_type`` live in.

    Raises ValueError for a type outside the closed six. An unknown type is the
    W4 escape hatch and belongs in `inbox` with a ``proposed-type``, but that is
    a decision `bm new` makes and states; silently filing it here would make the
    escape hatch invisible.
    """
    directory = TYPE_DIRS.get(record_type)
    if directory is None:
        allowed = ", ".join(sorted(TYPE_DIRS))
        raise ValueError(f"unknown record type '{record_type}'; allowed types are {allowed}")
    return directory


def record_file_path(record_type: str, record_id: str, title: str) -> str:
    """Where a record lands: ``<type-dir>/<id>--<slug>.md`` (schema.md §8).

    A POSIX-style relative path, because that is how ``Entity.file_path`` is
    stored and compared everywhere in this tree.

    Raises ValueError for an unknown type, or for an ``record_id`` that is not a
    record id — a file whose name does not start with a real id is not
    identity-addressable, which is the whole reason for the naming rule.
    """
    if not is_record_id(record_id):
        raise ValueError(f"not a record id: '{record_id}' (expected {ID_PREFIX}<8 chars>)")
    return f"{type_dir(record_type)}/{record_id}{SEPARATOR}{record_slug(title)}.md"
