"""Intermediate model, independent of the note-taking app.

Sources (Notion, Obsidian, ...) convert their format into these classes and
renderers only work with them. Supporting a new app = writing a new source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Span:
    """Text fragment with inline formatting."""

    text: str
    bold: bool = False
    italic: bool = False
    strike: bool = False
    underline: bool = False
    code: bool = False
    href: str | None = None


# Block types understood by renderers. A source must map its own types to
# these; anything that does not fit is emitted as UNSUPPORTED.
PARAGRAPH = "paragraph"
HEADING = "heading"  # attrs: level (1-3)
BULLETED_ITEM = "bulleted_item"
NUMBERED_ITEM = "numbered_item"
TODO = "todo"  # attrs: checked
TOGGLE = "toggle"
QUOTE = "quote"
CALLOUT = "callout"  # attrs: icon
CODE = "code"  # attrs: language
DIVIDER = "divider"
EQUATION = "equation"  # attrs: expression
IMAGE = "image"  # attrs: url, caption (list[Span])
LINK = "link"  # attrs: url, caption (list[Span])
TABLE = "table"  # attrs: has_column_header, has_row_header; children: TABLE_ROW
TABLE_ROW = "table_row"  # attrs: cells (list[list[Span]])
GROUP = "group"  # container with no semantics of its own (columns, synced blocks)
UNSUPPORTED = "unsupported"  # attrs: source_type


@dataclass
class Block:
    type: str
    text: list[Span] = field(default_factory=list)
    children: list[Block] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def plain_text(self) -> str:
        return "".join(s.text for s in self.text)


@dataclass
class Page:
    id: str
    title: str
    path: list[str] = field(default_factory=list)  # ancestor titles + its own
    tags: list[str] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    children: list[Page] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def walk(self):
        """Iterate over this page and all its subpages in order."""
        yield self
        for child in self.children:
            yield from child.walk()
