"""Entrypoint-specific logging configuration."""

from typing import Protocol


class SetupLogging(Protocol):
    def __call__(
        self,
        log_level: str = "INFO",
        log_to_file: bool = False,
        log_to_stdout: bool = False,
    ) -> None: ...


def initialize_file_logging(
    *,
    log_level: str,
    setup_logging: SetupLogging,
) -> None:
    """Initialize an entrypoint that must keep stdout protocol-clean."""
    setup_logging(log_level=log_level, log_to_file=True)
