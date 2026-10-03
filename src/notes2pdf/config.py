"""Loads and validates `bundles.yml`."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from notes2pdf.renderers.base import DocumentOptions


class ConfigError(ValueError):
    pass


@dataclass
class Include:
    page: str  # URL or id, as understood by the source
    depth: int | None = None  # levels of subpages (0 = only this page, None = all)
    exclude: list[str] = field(default_factory=list)  # title/path globs or ids to drop
    tags: list[str] = field(default_factory=list)  # keep only subpages with these tags


@dataclass
class Bundle:
    output: Path
    include: list[Include]
    options: DocumentOptions = field(default_factory=DocumentOptions)


@dataclass
class Config:
    source: dict[str, Any]  # {"type": "notion", ...source-specific options}
    bundles: list[Bundle] = field(default_factory=list)
    cache_dir: Path | None = None  # None disables the cache
    check_updates: bool = True  # False: use cached pages without asking the source


def _str_list(value: Any, where: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    raise ConfigError(f"{where}: expected a text or a list of texts")


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
    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ConfigError("`defaults` must be a mapping of bundle options")
    if {"output", "include"} & defaults.keys():
        raise ConfigError("`defaults` can't set `output` or `include`")
    for i, b in enumerate(raw.get("bundles") or []):
        b = {**defaults, **b}  # options set in the bundle win over the defaults
        where = f"bundles[{i}]"
        if "output" not in b:
            raise ConfigError(f"{where}: missing `output`")
        items = b.get("include") or []
        if not items:
            raise ConfigError(f"{where}: `include` is empty")
        bundle_exclude = _str_list(b.get("exclude"), f"{where}.exclude")
        includes = []
        for j, item in enumerate(items):
            iwhere = f"{where}.include[{j}]"
            if isinstance(item, str):
                item = {"page": item}
            if "page" not in item:
                raise ConfigError(f"{iwhere}: missing `page`")
            depth = item.get("depth")
            if item.get("children") is False:
                depth = 0
            if depth is not None and (not isinstance(depth, int) or depth < 0):
                raise ConfigError(f"{iwhere}: `depth` must be a number >= 0")
            includes.append(Include(
                page=str(item["page"]),
                depth=depth,
                exclude=bundle_exclude + _str_list(item.get("exclude"), f"{iwhere}.exclude"),
                tags=_str_list(item.get("tags"), f"{iwhere}.tags"),
            ))
        try:
            options = DocumentOptions(
                title=b.get("title"),
                subtitle=b.get("subtitle"),
                author=b.get("author"),
                cover=bool(b.get("cover", True)),
                toc=bool(b.get("toc", True)),
                toc_depth=int(b.get("toc_depth", 3)),
                embeds=str(b.get("embeds", "states")),
                embed_states=int(b.get("embed_states", 8)),
            )
        except (TypeError, ValueError) as e:
            raise ConfigError(f"{where}: {e}") from e
        if options.embeds not in ("states", "initial", "link"):
            raise ConfigError(f"{where}: `embeds` must be states, initial or link")
        bundles.append(Bundle(output=base / b["output"], include=includes, options=options))

    if not bundles:
        raise ConfigError("No bundles defined in `bundles`")
    cache = raw.get("cache", True)
    cache_dir = None if cache is False else base / (cache if isinstance(cache, str) else ".cache")
    check_updates = raw.get("check_updates", True)
    if not isinstance(check_updates, bool):
        raise ConfigError("`check_updates` must be true or false")
    return Config(source=source, bundles=bundles, cache_dir=cache_dir, check_updates=check_updates)
