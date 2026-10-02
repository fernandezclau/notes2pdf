"""Builds every PDF defined in a config file."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from notes2pdf.config import Config, ConfigError, load_config
from notes2pdf.model import Page
from notes2pdf.renderers.html_pdf import HtmlPdfRenderer
from notes2pdf.sources.base import Source


def _notion(options: dict[str, Any]) -> Source:
    from notes2pdf.sources.notion_api import NotionApiSource

    token = options.get("token") or os.environ.get("NOTION_TOKEN")
    if not token:
        raise ConfigError("Set NOTION_TOKEN (in the environment or in a .env file)")
    return NotionApiSource(token)


# Register new note-taking apps here: `source.type` in the config → factory.
SOURCES: dict[str, Callable[[dict[str, Any]], Source]] = {"notion": _notion}


def make_source(options: dict[str, Any]) -> Source:
    kind = options["type"]
    if kind not in SOURCES:
        raise ConfigError(f"Unknown source type {kind!r}; available: {', '.join(SOURCES)}")
    return SOURCES[kind](options)


def build_all(config: Config | Path, source: Source | None = None, log=print) -> list[Path]:
    if isinstance(config, Path):
        load_dotenv(config.parent / ".env")
        config = load_config(config)
    source = source or make_source(config.source)
    renderer = HtmlPdfRenderer()

    # The same page can appear in several bundles; fetch it only once.
    cache: dict[tuple[str, bool], Page] = {}
    outputs = []
    for bundle in config.bundles:
        pages = []
        for inc in bundle.include:
            key = (inc.page, inc.children)
            if key not in cache:
                log(f"Fetching {inc.page}…")
                cache[key] = source.fetch(inc.page, recursive=inc.children)
            pages.append(cache[key])
        out = renderer.render(pages, bundle.output, title=bundle.title)
        log(f"PDF written: {out}")
        outputs.append(out)
    return outputs
