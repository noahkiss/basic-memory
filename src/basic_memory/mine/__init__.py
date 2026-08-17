"""Reading Claude Code transcripts with correct speaker attribution (GAPS W1).

`bm mine` is a **parser, not a miner** (GAPS W1, decided 2026-08-06). It emits
turns whose speaker is correctly attributed and makes no claim that any of them
is a decision — an agent reads the output, judges, and writes any keeper through
the normal `bm` write path.

The package splits along the two jobs that actually differ:

- ``locate`` — which files are transcripts at all. The positive `*.jsonl`
  allowlist lives here because a Claude Code project directory is a transcript
  file *plus* a sidecar tree, and reading the sidecars is what produced the
  phantom 25% parse-failure rate diagnosed in GAPS R-O1.
- ``turns`` — who spoke. This is the real work: 83% of ``type: user`` lines in
  the measured corpus are tool results, not human speech.
- ``search`` — matching a term and carrying surrounding turns as context.

Nothing here imports the API, the MCP tool layer, or the database. `bm mine`
reads files and nothing else, so it is the cheapest verb in the tree.
"""

from basic_memory.mine.locate import (
    TranscriptError,
    project_slug,
    transcript_files,
    transcripts_dir_for_cwd,
)
from basic_memory.mine.search import Hit, ScanReport, scan
from basic_memory.mine.turns import SPEAKERS, BadLine, Speaker, Turn, read_turns

__all__ = [
    "SPEAKERS",
    "BadLine",
    "Hit",
    "ScanReport",
    "Speaker",
    "TranscriptError",
    "Turn",
    "project_slug",
    "read_turns",
    "scan",
    "transcript_files",
    "transcripts_dir_for_cwd",
]
