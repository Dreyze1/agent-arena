"""Shared application errors for user-facing Agent Arena failures."""

from pathlib import Path


class ArenaError(Exception):
    """An expected user-facing error while preparing or running an arena."""


class BenchmarkCancelled(KeyboardInterrupt):
    """A cancelled benchmark with enough context to reopen its partial results."""

    def __init__(self, benchmark_id: str, manifest_path: Path) -> None:
        self.benchmark_id = benchmark_id
        self.manifest_path = manifest_path
        super().__init__("Interrupted by user.")
