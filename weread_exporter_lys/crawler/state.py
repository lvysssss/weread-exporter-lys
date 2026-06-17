from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CrawlState:
    book_id: str
    reader_url: str
    current_chapter_index: int = 0
    completed_chapters: list[str] = field(default_factory=list)
    toc_path: str | None = None
    last_error: str | None = None
    updated_at: str = field(default_factory=utc_now_iso)
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path, *, book_id: str, reader_url: str) -> "CrawlState":
        if not path.exists():
            return cls(book_id=book_id, reader_url=reader_url)

        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError(f"State file must contain a JSON object: {path}")

        return cls(
            book_id=str(data.get("book_id", book_id)),
            reader_url=str(data.get("reader_url", reader_url)),
            current_chapter_index=int(data.get("current_chapter_index", 0)),
            completed_chapters=list(data.get("completed_chapters", [])),
            toc_path=data.get("toc_path"),
            last_error=data.get("last_error"),
            updated_at=str(data.get("updated_at", utc_now_iso())),
            warnings=list(data.get("warnings", [])),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = utc_now_iso()
        with path.open("w", encoding="utf-8") as file:
            json.dump(asdict(self), file, ensure_ascii=False, indent=2)

    def add_warning(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def mark_completed(self, chapter_path: Path) -> None:
        value = str(chapter_path)
        if value not in self.completed_chapters:
            self.completed_chapters.append(value)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
