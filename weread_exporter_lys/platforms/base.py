from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from ..progress import ProgressCallback


@dataclass(frozen=True)
class ExportRequest:
    platform: str
    book_id: str
    output_format: str
    cache_dir: Path
    output_dir: Path
    delay: float
    headless: bool
    auth_state_path: Path | None = None
    on_progress: ProgressCallback | None = None


@dataclass(frozen=True)
class ExportResult:
    ok: bool
    message: str
    output_path: Path | None = None


class BookPlatform(ABC):
    name: str
    display_name: str

    @abstractmethod
    def normalize_book_id(self, value: str) -> str:
        """Return a platform book id from a raw id or URL."""

    @abstractmethod
    def export(self, request: ExportRequest) -> ExportResult:
        """Start the platform export flow."""
