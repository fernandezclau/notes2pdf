"""Loads and validates `bundles.yml`."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    pass


@dataclass
class Include:
    page: str  # URL or id, as understood by the source
    children: bool = True


@dataclass
class Bundle:
    output: Path
    include: list[Include]
    title: str | None = None


@dataclass
class Config:
    source: dict[str, Any]  # {"type": "notion", ...source-specific options}
    bundles: list[Bundle] = field(default_factory=list)


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"{path} not found (copy bundles.example.yml to get started)")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    base = path.parent

    source = raw.get("source", {"type": "notion"})
    if isinstance(source, str):
        source = {"type": source}
    if "type" not in source:
        raise ConfigError("`source` needs a `type` (e.g. notion)")

    bundles = []
    for i, b in enumerate(raw.get("bundles") or []):
        where = f"bundles[{i}]"
        if "output" not in b:
            raise ConfigError(f"{where}: missing `output`")
        items = b.get("include") or []
        if not items:
            raise ConfigError(f"{where}: `include` is empty")
        includes = []
        for j, item in enumerate(items):
            if isinstance(item, str):
                item = {"page": item}
            if "page" not in item:
                raise ConfigError(f"{where}.include[{j}]: missing `page`")
            includes.append(Include(page=str(item["page"]), children=bool(item.get("children", True))))
        bundles.append(Bundle(output=base / b["output"], include=includes, title=b.get("title")))

    if not bundles:
        raise ConfigError("No bundles defined in `bundles`")
    return Config(source=source, bundles=bundles)
