"""Source that reads pages through the official Notion API."""

from __future__ import annotations

import re
import time
from typing import Any

from notion_client import APIErrorCode, APIResponseError, Client

from notes2pdf import model as m
from notes2pdf.model import Block, Page, Span

_ID_RE = re.compile(r"([0-9a-f]{32})|([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")

# Notion blocks that map 1:1 to a model type using only rich_text.
_SIMPLE_TYPES = {
    "paragraph": m.PARAGRAPH,
    "bulleted_list_item": m.BULLETED_ITEM,
    "numbered_list_item": m.NUMBERED_ITEM,
    "toggle": m.TOGGLE,
    "quote": m.QUOTE,
}
_CONTAINER_TYPES = {"column_list", "column", "synced_block"}
_LINK_TYPES = {"bookmark", "embed", "link_preview", "video", "file", "pdf"}


def parse_page_id(value: str) -> str:
    """Accept an id (with or without dashes) or a Notion URL and return the id."""
    match = _ID_RE.search(value.lower().rsplit("?", 1)[0])
    if not match:
        raise ValueError(f"No Notion id found in: {value!r}")
    return match.group(0).replace("-", "")


def spans(rich_text: list[dict[str, Any]]) -> list[Span]:
    out = []
    for rt in rich_text:
        ann = rt.get("annotations", {})
        if rt.get("type") == "equation":
            out.append(Span(rt["equation"]["expression"], code=True))
            continue
        out.append(
            Span(
                text=rt.get("plain_text", ""),
                bold=ann.get("bold", False),
                italic=ann.get("italic", False),
                strike=ann.get("strikethrough", False),
                underline=ann.get("underline", False),
                code=ann.get("code", False),
                href=rt.get("href"),
            )
        )
    return out


def _file_url(data: dict[str, Any]) -> str | None:
    kind = data.get("type")
    return data.get(kind, {}).get("url") if kind else data.get("url")


class NotionApiSource:
    def __init__(self, token: str | None = None, client: Client | None = None, max_retries: int = 5):
        if client is None and not token:
            raise ValueError("Missing Notion token (NOTION_TOKEN variable)")
        self.client = client or Client(auth=token)
        self.max_retries = max_retries

    # --- Public API ----------------------------------------------------------

    def tree(self, root: str) -> Page:
        return self._page(parse_page_id(root), parents=[], with_content=False, recursive=True)

    def fetch(self, page_id: str, recursive: bool = True) -> Page:
        return self._page(parse_page_id(page_id), parents=[], with_content=True, recursive=recursive)

    # --- Internals -----------------------------------------------------------

    def _call(self, fn, **kwargs):
        """Call the API, retrying with backoff when Notion rate-limits."""
        for attempt in range(self.max_retries):
            try:
                return fn(**kwargs)
            except APIResponseError as e:
                if e.code != APIErrorCode.RateLimited or attempt == self.max_retries - 1:
                    raise
                time.sleep(2**attempt)

    def _children(self, block_id: str) -> list[dict[str, Any]]:
        results, cursor = [], None
        while True:
            kwargs = {"block_id": block_id, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = self._call(self.client.blocks.children.list, **kwargs)
            results.extend(resp["results"])
            if not resp.get("has_more"):
                return results
            cursor = resp["next_cursor"]

    def _page(self, page_id: str, parents: list[str], with_content: bool, recursive: bool) -> Page:
        raw = self._call(self.client.pages.retrieve, page_id=page_id)
        title = _page_title(raw)
        page = Page(
            id=page_id,
            title=title,
            path=[*parents, title],
            tags=_page_tags(raw),
            metadata={
                "url": raw.get("url"),
                "created_time": raw.get("created_time"),
                "last_edited_time": raw.get("last_edited_time"),
            },
        )
        for child in self._children(page_id):
            if child["type"] == "child_page":
                if recursive:
                    page.children.append(
                        self._page(child["id"].replace("-", ""), page.path, with_content, recursive)
                    )
            elif with_content:
                block = self._block(child)
                if block:
                    page.blocks.append(block)
        return page

    def _block(self, raw: dict[str, Any]) -> Block | None:
        kind = raw["type"]
        data = raw.get(kind, {})
        block = _convert(kind, data)
        if block is None:
            return None
        if raw.get("has_children") and kind != "child_page":
            for child in self._children(raw["id"]):
                if child["type"] == "child_page":
                    # Subpage nested inside a toggle/column: not included
                    # as content; it is handled as a separate page.
                    continue
                converted = self._block(child)
                if converted:
                    block.children.append(converted)
        return block


def _convert(kind: str, data: dict[str, Any]) -> Block | None:
    text = spans(data.get("rich_text", []))
    if kind in _SIMPLE_TYPES:
        return Block(_SIMPLE_TYPES[kind], text)
    if kind in ("heading_1", "heading_2", "heading_3"):
        return Block(m.HEADING, text, attrs={"level": int(kind[-1])})
    if kind == "to_do":
        return Block(m.TODO, text, attrs={"checked": data.get("checked", False)})
    if kind == "callout":
        icon = data.get("icon") or {}
        return Block(m.CALLOUT, text, attrs={"icon": icon.get("emoji", "💡")})
    if kind == "code":
        return Block(
            m.CODE,
            text,
            attrs={"language": data.get("language", ""), "caption": spans(data.get("caption", []))},
        )
    if kind == "divider":
        return Block(m.DIVIDER)
    if kind == "equation":
        return Block(m.EQUATION, attrs={"expression": data.get("expression", "")})
    if kind == "image":
        return Block(m.IMAGE, attrs={"url": _file_url(data), "caption": spans(data.get("caption", []))})
    if kind in _LINK_TYPES:
        return Block(m.LINK, attrs={"url": _file_url(data), "caption": spans(data.get("caption", []))})
    if kind == "table":
        return Block(
            m.TABLE,
            attrs={
                "has_column_header": data.get("has_column_header", False),
                "has_row_header": data.get("has_row_header", False),
            },
        )
    if kind == "table_row":
        return Block(m.TABLE_ROW, attrs={"cells": [spans(c) for c in data.get("cells", [])]})
    if kind in _CONTAINER_TYPES:
        return Block(m.GROUP)
    if kind == "child_page":
        return None
    return Block(m.UNSUPPORTED, attrs={"source_type": kind})


def _page_title(raw: dict[str, Any]) -> str:
    for prop in raw.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(t.get("plain_text", "") for t in prop["title"]) or "Untitled"
    return "Untitled"


def _page_tags(raw: dict[str, Any]) -> list[str]:
    tags = []
    for prop in raw.get("properties", {}).values():
        if prop.get("type") == "multi_select":
            tags.extend(opt["name"] for opt in prop["multi_select"])
        elif prop.get("type") == "select" and prop.get("select"):
            tags.append(prop["select"]["name"])
    return tags
