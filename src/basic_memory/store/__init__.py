"""The note store: a local git repository holding every note bm writes."""

from basic_memory.store.history import (
    CommitResult,
    HistoryError,
    commit_paths,
    dirty_count,
    dirty_paths,
    ensure_store_repo,
    store_path,
    sweep_commit,
)
from basic_memory.store.write_hook import (
    OFF_STORE_NOTICE,
    HistoryOutcome,
    WriteOperation,
    check_can_record,
    project_store_prefix,
    record_note_write,
    store_relative_path,
)

__all__ = [
    "OFF_STORE_NOTICE",
    "CommitResult",
    "HistoryError",
    "HistoryOutcome",
    "WriteOperation",
    "check_can_record",
    "commit_paths",
    "dirty_count",
    "dirty_paths",
    "ensure_store_repo",
    "project_store_prefix",
    "record_note_write",
    "store_path",
    "store_relative_path",
    "sweep_commit",
]
