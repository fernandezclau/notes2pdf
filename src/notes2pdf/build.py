"""Builds every PDF defined in a config file."""

from __future__ import annotations

import logging
import os
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from notes2pdf import model as m
from notes2pdf.cache import Cache
from notes2pdf.config import Config, ConfigError, load_config
from notes2pdf.model import Block, Page
from notes2pdf.renderers.html_pdf import HtmlPdfRenderer
from notes2pdf.selection import select
from notes2pdf.sources.base import Source


def _notion(options: dict[str, Any], config: Config) -> Source:
    from notes2pdf.sources.notion_api import NotionApiSource

    token = options.get("token") or os.environ.get("NOTION_TOKEN")
    if not token:
        raise ConfigError("Set NOTION_TOKEN (in the environment or in a .env file)")
    cache = Cache(config.cache_dir) if config.cache_dir else None
    return NotionApiSource(token, cache=cache, check_updates=config.check_updates)


# Register new note-taking apps here: `source.type` in the config → factory.
SOURCES: dict[str, Callable[[dict[str, Any], Config], Source]] = {"notion": _notion}


def make_source(config: Config) -> Source:
    kind = config.source["type"]
    if kind not in SOURCES:
        raise ConfigError(f"Unknown source type {kind!r}; available: {', '.join(SOURCES)}")
    return SOURCES[kind](config.source, config)


def _report_unsupported(page: Page, log) -> None:
    for p in page.walk():
        found = Counter(b.attrs.get("source_type") for b in _walk(p.blocks) if b.type == m.UNSUPPORTED)
        if found:
            kinds = ", ".join(f"{k} ×{n}" if n > 1 else k for k, n in found.items())
            log(f"  ⚠ {p.title}: skipped unsupported blocks ({kinds})")


def _walk(blocks: list[Block]):
    for b in blocks:
        yield b
        yield from _walk(b.children)


def setup_logging() -> None:
    """Show our own warnings; hide WeasyPrint/fontTools/notion-client internals."""
    logging.basicConfig(level=logging.WARNING, format="  ⚠ %(message)s", stream=sys.stdout)
    for noisy in ("weasyprint", "fontTools", "notion_client"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


def _print(msg: str) -> None:
    print(msg, flush=True)


def build_all(config: Config | Path, source: Source | None = None, log=_print) -> list[Path]:
    if isinstance(config, Path):
        load_dotenv(config.parent / ".env")
        config = load_config(config)
    source = source or make_source(config)
    if not config.check_updates:
        log("check_updates is off: using cached pages without asking for changes")
    renderer = HtmlPdfRenderer(cache_dir=config.cache_dir)

    # The same page can appear in several bundles; fetch it only once.
    fetched: dict[tuple[str, bool], Page] = {}
    outputs = []
    try:
        for bundle in config.bundles:
            pages = []
            for inc in bundle.include:
                key = (inc.page, inc.depth != 0)
                if key not in fetched:
                    log(f"Fetching {inc.page}…")
                    fetched[key] = source.fetch(inc.page, recursive=inc.depth != 0)
                    _report_unsupported(fetched[key], log)
                pages.append(select(fetched[key], depth=inc.depth, exclude=inc.exclude, tags=inc.tags))
            out = renderer.render(pages, bundle.output, bundle.options)
            log(f"PDF written: {out}")
            outputs.append(out)
    finally:
        renderer.close()  # stops the headless browser used for embeds
    return outputs
