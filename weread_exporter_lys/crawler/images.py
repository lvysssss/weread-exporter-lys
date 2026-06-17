from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urljoin


@dataclass
class ImageFilter:
    seen: set[str] = field(default_factory=set)

    def keep(self, url: str | None, *, base_url: str | None = None) -> str | None:
        if not url:
            return None

        normalized = url.strip()
        if not normalized:
            return None
        if normalized.startswith("data:"):
            return None
        if "/loading_dark." in normalized:
            return None

        if base_url is not None:
            normalized = urljoin(base_url, normalized)

        if normalized in self.seen:
            return None

        self.seen.add(normalized)
        return normalized

    def markdown_lines(
        self,
        urls: list[str],
        *,
        base_url: str | None = None,
        exclude: set[str] | None = None,
    ) -> list[str]:
        excluded = exclude or set()
        lines: list[str] = []
        for url in urls:
            kept = self.keep(url, base_url=base_url)
            if kept is None:
                continue
            # Skip rare-char images already inlined into the text by coordinate merge.
            if any(kept == ex or ex in kept for ex in excluded):
                continue
            lines.append(f"![]({kept})")
        return lines
