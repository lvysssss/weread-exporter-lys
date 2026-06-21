from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(".weread-exporter-lys.json")
SUPPORTED_FORMATS = ("md", "txt", "pdf", "epub", "mobi", "azw3")
SUPPORTED_CRAWL_METHODS = ("xhtml", "canvas")


@dataclass(frozen=True)
class AppConfig:
    default_platform: str = "weread"
    default_format: str = "epub"
    cache_dir: Path = Path("cache")
    output_dir: Path = Path("output")
    delay: float = 1.0
    headless: bool = False
    auth_state_path: Path | None = None
    crawl_method: str = "xhtml"

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "AppConfig":
        config = cls()
        updates: dict[str, Any] = {}

        for key in cls.__dataclass_fields__:
            if key in data:
                updates[key] = data[key]

        for path_key in ("cache_dir", "output_dir", "auth_state_path"):
            if path_key in updates:
                updates[path_key] = Path(str(updates[path_key]))

        if "delay" in updates:
            updates["delay"] = float(updates["delay"])

        if "headless" in updates:
            updates["headless"] = bool(updates["headless"])

        return replace(config, **updates).validate()

    def validate(self) -> "AppConfig":
        if self.default_format not in SUPPORTED_FORMATS:
            formats = ", ".join(SUPPORTED_FORMATS)
            raise ValueError(f"Unsupported default format: {self.default_format}. Use one of: {formats}")

        if self.crawl_method not in SUPPORTED_CRAWL_METHODS:
            methods = ", ".join(SUPPORTED_CRAWL_METHODS)
            raise ValueError(f"Unsupported crawl method: {self.crawl_method}. Use one of: {methods}")

        if self.delay < 0:
            raise ValueError("Delay must be greater than or equal to 0")

        return self

    def with_overrides(
        self,
        *,
        default_platform: str | None = None,
        default_format: str | None = None,
        cache_dir: Path | None = None,
        output_dir: Path | None = None,
        delay: float | None = None,
        headless: bool | None = None,
        auth_state_path: Path | None = None,
        crawl_method: str | None = None,
    ) -> "AppConfig":
        updates: dict[str, Any] = {}
        if default_platform is not None:
            updates["default_platform"] = default_platform
        if default_format is not None:
            updates["default_format"] = default_format
        if cache_dir is not None:
            updates["cache_dir"] = cache_dir
        if output_dir is not None:
            updates["output_dir"] = output_dir
        if delay is not None:
            updates["delay"] = delay
        if headless is not None:
            updates["headless"] = headless
        if auth_state_path is not None:
            updates["auth_state_path"] = auth_state_path
        if crawl_method is not None:
            updates["crawl_method"] = crawl_method

        return replace(self, **updates).validate()


def load_config(path: Path | str | None = None) -> AppConfig:
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return AppConfig()

    with config_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise ValueError(f"Config file must contain a JSON object: {config_path}")

    return AppConfig.from_mapping(data)
