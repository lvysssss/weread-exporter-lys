from __future__ import annotations

from .base import BookPlatform, ExportRequest, ExportResult
from .weread import WeReadPlatform

_PLATFORMS: dict[str, BookPlatform] = {
    WeReadPlatform.name: WeReadPlatform(),
}


def available_platforms() -> list[BookPlatform]:
    return list(_PLATFORMS.values())


def get_platform(name: str) -> BookPlatform:
    try:
        return _PLATFORMS[name]
    except KeyError as error:
        supported = ", ".join(sorted(_PLATFORMS))
        raise ValueError(f"Unsupported platform: {name}. Use one of: {supported}") from error


__all__ = [
    "BookPlatform",
    "ExportRequest",
    "ExportResult",
    "WeReadPlatform",
    "available_platforms",
    "get_platform",
]
