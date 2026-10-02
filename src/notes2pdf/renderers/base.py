from __future__ import annotations

from pathlib import Path
from typing import Protocol

from notes2pdf.model import Page


class Renderer(Protocol):
    def render(self, pages: list[Page], output: Path, title: str | None = None) -> Path:
        """Write `pages` (with their subpages) into a single `output` file."""
        ...
