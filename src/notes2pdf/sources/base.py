from __future__ import annotations

from typing import Protocol

from notes2pdf.model import Page


class Source(Protocol):
    """Interface implemented by each note-taking app."""

    def tree(self, root: str) -> Page:
        """Page structure under `root`, without content (for browsing/choosing)."""
        ...

    def fetch(self, page_id: str, recursive: bool = True) -> Page:
        """Full page; with `recursive`, its subpages too."""
        ...
