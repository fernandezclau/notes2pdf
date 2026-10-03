from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from notes2pdf.model import Page


@dataclass
class DocumentOptions:
    title: str | None = None  # defaults to the first page's title
    subtitle: str | None = None
    author: str | None = None
    cover: bool = True
    toc: bool = True
    toc_depth: int = 3  # levels shown in the table of contents (pages + headings)
    embeds: str = "states"  # interactive HTML: "states" (one image per state), "initial" or "link"
    embed_states: int = 8  # max images per embed in "states" mode


class Renderer(Protocol):
    def render(self, pages: list[Page], output: Path, options: DocumentOptions | None = None) -> Path:
        """Write `pages` (with their subpages) into a single `output` file."""
        ...
