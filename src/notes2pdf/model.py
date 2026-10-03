"""Intermediate model, independent of the note-taking app.

Sources (Notion, Obsidian, ...) convert their format into these classes and
renderers only work with them. Supporting a new app = writing a new source.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    math: bool = False  # `text` is a LaTeX expression
    href: str | None = None


# Block types understood by renderers. A source must map its own types to
# these; anything that does not fit is emitted as UNSUPPORTED.
PARAGRAPH = "paragraph"
HEADING = "heading"  # attrs: level (1 = largest)
BULLETED_ITEM = "bulleted_item"
NUMBERED_ITEM = "numbered_item"  # attrs: start (optional, restarts numbering)
TODO = "todo"  # attrs: checked
TOGGLE = "toggle"
QUOTE = "quote"
CALLOUT = "callout"  # attrs: icon
CODE = "code"  # attrs: language
DIVIDER = "divider"
EQUATION = "equation"  # attrs: expression (LaTeX)
IMAGE = "image"  # attrs: url, caption (list[Span])
LINK = "link"  # attrs: url, caption (list[Span])
EMBED = "embed"  # interactive HTML page; attrs: url, name, caption (list[Span])
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


# --- JSON serialization (used by the cache) ---------------------------------
# Spans can appear nested inside Block.attrs (captions, table cells), so they
# are tagged to be restored wherever they occur.


def _encode(value: Any) -> Any:
    if isinstance(value, Span):
        return {"$span": asdict(value)}
    if isinstance(value, Block):
        return {
            "type": value.type,
            "text": _encode(value.text),
            "children": _encode(value.children),
            "attrs": _encode(value.attrs),
        }
    if isinstance(value, list):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode(v) for v in value]
    if isinstance(value, dict):
        if "$span" in value:
            return Span(**value["$span"])
        return {k: _decode(v) for k, v in value.items()}
    return value


def blocks_to_json(blocks: list[Block]) -> list[dict[str, Any]]:
    return _encode(blocks)


def blocks_from_json(data: list[dict[str, Any]]) -> list[Block]:
    return [
        Block(
            type=d["type"],
            text=_decode(d["text"]),
            children=blocks_from_json(d["children"]),
            attrs=_decode(d["attrs"]),
        )
        for d in data
    ]
