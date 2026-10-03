"""Source that reads pages through the official Notion API."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from notion_client import APIErrorCode, APIResponseError, Client

from notes2pdf import model as m
from notes2pdf.cache import Cache
from notes2pdf.model import Block, Page, Span

_ID_RE = re.compile(r"([0-9a-f]{32})|([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")

# Notion blocks that map 1:1 to a model type using only rich_text.
_SIMPLE_TYPES = {
    "paragraph": m.PARAGRAPH,
    "bulleted_list_item": m.BULLETED_ITEM,
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
            out.append(Span(rt["equation"]["expression"], math=True))
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
    def __init__(
        self,
        token: str | None = None,
        client: Client | None = None,
        cache: Cache | None = None,
        check_updates: bool = True,
        max_retries: int = 5,
    ):
        if client is None and not token:
            raise ValueError("Missing Notion token (NOTION_TOKEN variable)")
        # notion-client logs every failed request (e.g. probing whether an id is a
        # database); errors we don't handle are raised anyway.
        self.client = client or Client(auth=token, log_level=logging.CRITICAL)
        self.cache = cache
        # False: trust cached pages without asking Notion whether they changed
        # (only pages missing from the cache are fetched).
        self.check_updates = check_updates
        self.max_retries = max_retries
        # page id -> (raw page, blocks, subpages): each page is requested once per run,
        # even if it appears in several bundles.
        self._seen: dict[str, tuple[dict[str, Any], list[Block], list[dict[str, str]]]] = {}

    # --- Public API ----------------------------------------------------------

    def tree(self, root: str) -> Page:
        page = self.fetch(root)
        for p in page.walk():
            p.blocks = []
        return page

    def fetch(self, page_id: str, recursive: bool = True) -> Page:
        pid = parse_page_id(page_id)
        if self._offline_entry(pid, "database"):
            return self._database(pid, parents=[], recursive=recursive)
        try:
            return self._page(pid, parents=[], recursive=recursive)
        except APIResponseError as e:
            # The id may belong to a database rather than a page.
            if e.code not in (APIErrorCode.ObjectNotFound, APIErrorCode.ValidationError):
                raise
            for fallback in (self._database, self._data_source):
                try:
                    return fallback(pid, parents=[], recursive=recursive)
                except APIResponseError:
                    pass
            raise e from None  # not a database either: report the page error

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

    def _paginate(self, fn, **kwargs) -> list[dict[str, Any]]:
        results, cursor = [], None
        while True:
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = self._call(fn, page_size=100, **kwargs)
            results.extend(resp["results"])
            if not resp.get("has_more"):
                return results
            cursor = resp["next_cursor"]

    def _children(self, block_id: str) -> list[dict[str, Any]]:
        return self._paginate(self.client.blocks.children.list, block_id=block_id)

    def _offline_entry(self, item_id: str, kind: str) -> dict[str, Any] | None:
        """Cached entry to use without contacting Notion (only when check_updates is off)."""
        if self.check_updates or not self.cache:
            return None
        entry = self.cache.load_page(item_id)
        return entry if entry and entry.get("kind") == kind else None

    def _content(self, page_id: str) -> tuple[dict[str, Any], list[Block], list[dict[str, str]]]:
        if page_id in self._seen:
            return self._seen[page_id]
        if offline := self._offline_entry(page_id, "page"):
            self._seen[page_id] = (offline["meta"], offline["blocks"], offline["children"])
            return self._seen[page_id]
        raw = self._call(self.client.pages.retrieve, page_id=page_id)
        edited = raw.get("last_edited_time", "")
        cached = self.cache.load_page(page_id) if self.cache else None
        if cached and _is_fresh(cached, edited):
            blocks, subpages = cached["blocks"], cached["children"]
        else:
            blocks, subpages = [], []  # subpages: child pages/databases, in order, wherever nested
            for child in self._children(page_id):
                block = self._block(child, subpages)
                if block:
                    blocks.append(block)
            if self.cache:
                meta = {k: raw.get(k) for k in ("properties", "url", "created_time", "last_edited_time")}
                self.cache.save_page(page_id, edited, blocks, subpages, meta=meta)
        self._seen[page_id] = (raw, blocks, subpages)
        return self._seen[page_id]

    def _page(self, page_id: str, parents: list[str], recursive: bool) -> Page:
        raw, blocks, subpages = self._content(page_id)
        title = _page_title(raw)
        page = Page(
            id=page_id,
            title=title,
            path=[*parents, title],
            tags=_page_tags(raw),
            blocks=blocks,
            metadata={
                "url": raw.get("url"),
                "created_time": raw.get("created_time"),
                "last_edited_time": raw.get("last_edited_time"),
            },
        )

        if recursive:
            for sub in subpages:
                if sub["kind"] == "page":
                    page.children.append(self._page(sub["id"], page.path, recursive))
                else:
                    page.children.append(self._database(sub["id"], page.path, recursive))
        return page

    def _database(self, database_id: str, parents: list[str], recursive: bool) -> Page:
        """A database becomes a page with no content whose subpages are its rows."""
        if offline := self._offline_entry(database_id, "database"):
            meta, rows = offline["meta"], [r["id"] for r in offline["children"]]
        else:
            raw = self._call(self.client.databases.retrieve, database_id=database_id)
            meta = {"title": _plain(raw.get("title", [])), "url": raw.get("url")}
            rows = [r for source in raw.get("data_sources", []) for r in self._row_ids(source["id"])]
            self._save_database(database_id, meta, rows)
        return self._database_page(database_id, meta, rows, parents, recursive)

    def _data_source(self, data_source_id: str, parents: list[str], recursive: bool) -> Page:
        """Same as a database, for ids of a single data source (as returned by search)."""
        raw = self._call(self.client.data_sources.retrieve, data_source_id=data_source_id)
        meta = {"title": _plain(raw.get("title", [])), "url": raw.get("url")}
        rows = self._row_ids(data_source_id)
        self._save_database(data_source_id, meta, rows)
        return self._database_page(data_source_id, meta, rows, parents, recursive)

    def _row_ids(self, data_source_id: str) -> list[str]:
        rows = self._paginate(self.client.data_sources.query, data_source_id=data_source_id)
        return [r["id"].replace("-", "") for r in rows if r.get("object") == "page"]

    def _save_database(self, item_id: str, meta: dict[str, Any], rows: list[str]) -> None:
        if self.cache:
            children = [{"kind": "page", "id": r} for r in rows]
            self.cache.save_page(item_id, "", [], children, meta=meta, kind="database")

    def _database_page(
        self, item_id: str, meta: dict[str, Any], rows: list[str], parents: list[str], recursive: bool
    ) -> Page:
        title = meta.get("title") or "Untitled"
        page = Page(id=item_id, title=title, path=[*parents, title], metadata={"url": meta.get("url")})
        if recursive:
            page.children = [self._page(r, page.path, True) for r in rows]
        return page

    def _block(self, raw: dict[str, Any], subpages: list[dict[str, str]]) -> Block | None:
        kind = raw["type"]
        if kind in ("child_page", "child_database"):
            # Pages and databases become subpages, even when nested in toggles/columns.
            subpages.append({"kind": "page" if kind == "child_page" else "database",
                             "id": raw["id"].replace("-", "")})
            return None
        block = _convert(kind, raw.get(kind, {}))
        if block.type in (m.IMAGE, m.EMBED) and self.cache and block.attrs.get("url"):
            url = block.attrs["url"]
            # Notion file URLs are signed and expire; the path part is stable.
            block.attrs["url"] = self.cache.download(url, key=url.split("?", 1)[0])
        if raw.get("has_children"):
            for child in self._children(raw["id"]):
                converted = self._block(child, subpages)
                if converted:
                    block.children.append(converted)
        return block


def _is_fresh(cached: dict[str, Any], edited: str) -> bool:
    """Notion's last_edited_time has minute precision: only trust the cache if it
    was fetched more than a minute after the last edit."""
    if cached.get("last_edited_time") != edited or not edited:
        return False
    try:
        fetched = datetime.fromisoformat(cached["fetched_at"])
        edited_at = datetime.fromisoformat(edited.replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return False
    return fetched - edited_at > timedelta(minutes=1)


def _convert(kind: str, data: dict[str, Any]) -> Block:
    text = spans(data.get("rich_text", []))
    if kind in _SIMPLE_TYPES:
        return Block(_SIMPLE_TYPES[kind], text)
    if kind == "numbered_list_item":
        start = data.get("list_start_index")
        return Block(m.NUMBERED_ITEM, text, attrs={"start": start} if start else {})
    if kind.startswith("heading_") and kind[-1].isdigit():
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
        url = _file_url(data) or ""
        name = urlparse(url).path.rsplit("/", 1)[-1]
        block_type = m.EMBED if kind == "embed" and name.lower().endswith((".html", ".htm")) else m.LINK
        return Block(block_type, attrs={"url": url, "name": name, "caption": spans(data.get("caption", []))})
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
    return Block(m.UNSUPPORTED, attrs={"source_type": kind})


def _plain(rich_text: list[dict[str, Any]]) -> str:
    return "".join(t.get("plain_text", "") for t in rich_text)


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
