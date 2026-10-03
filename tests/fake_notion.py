"""In-memory Notion client returning responses shaped like the real API."""

from __future__ import annotations

from collections import Counter

import httpx
from notion_client import APIErrorCode, APIResponseError


def rt(text, **ann):
    return {
        "type": "text",
        "plain_text": text,
        "href": ann.pop("href", None),
        "annotations": {"bold": False, "italic": False, "strikethrough": False,
                        "underline": False, "code": False, **ann},
    }


def eq(expr):
    return {"type": "equation", "plain_text": expr, "href": None,
            "equation": {"expression": expr}, "annotations": rt("")["annotations"]}


def block(id, kind, has_children=False, **data):
    return {"id": id, "type": kind, "has_children": has_children, kind: data}


def page(id, title, tags=()):
    return {
        "id": id,
        "url": f"https://www.notion.so/{id}",
        "created_time": "2026-01-01T00:00:00.000Z",
        "last_edited_time": "2026-01-02T00:00:00.000Z",
        "properties": {
            "title": {"type": "title", "title": [rt(title)]},
            "Tags": {"type": "multi_select", "multi_select": [{"name": t} for t in tags]},
        },
    }


ROOT = "a" * 32
CHILD = "b" * 32
NESTED = "c" * 32  # child page inside a toggle
DB = "d" * 32  # inline database inside CHILD
ROW = "e" * 32  # a row of DB
DS = "0f" * 16  # the data source behind DB
IMAGE_URL = "https://files.invalid/space/file-uuid/diagram.png?X-Amz-Signature=abc"
EMBED_URL = "https://files.invalid/space/file-uuid/how_a_network_learns.html?X-Amz-Signature=abc"

PAGES = {
    ROOT: page(ROOT, "Networks", tags=["uni"]),
    CHILD: page(CHILD, "Topic 1"),
    NESTED: page(NESTED, "Nested page"),
    ROW: page(ROW, "Row 1"),
}
DATABASES = {DB: {"title": [rt("Exercises")], "url": "https://www.notion.so/db",
                  "data_sources": [{"id": DS, "name": "Exercises"}]}}
DATA_SOURCES = {DS: [{"object": "page", "id": ROW}]}

CHILDREN = {
    ROOT: [
        block("h1", "heading_1", rich_text=[rt("Introduction")]),
        block("p1", "paragraph", rich_text=[rt("Some "), rt("bold", bold=True), rt(" and "),
                                             rt("link", href="https://example.com")]),
        block("li1", "bulleted_list_item", has_children=True, rich_text=[rt("One")]),
        block("li2", "bulleted_list_item", rich_text=[rt("Two")]),
        block("td1", "to_do", rich_text=[rt("Done")], checked=True),
        block("c1", "callout", rich_text=[rt("Careful")], icon={"type": "emoji", "emoji": "⚠️"}),
        block("code1", "code", rich_text=[rt("print('<hello>')")], language="python", caption=[]),
        block("t1", "table", has_children=True, has_column_header=True, has_row_header=False, table_width=2),
        block("x1", "ai_block"),
        block(CHILD, "child_page", title="Topic 1"),
        block("n1", "numbered_list_item", rich_text=[rt("Mean "), eq(r"\mu")]),
        block("e1", "equation", expression=r"\mu = \frac{1}{m} \sum_{i=1}^{m} x^{(i)}"),
        block("n2", "numbered_list_item", rich_text=[rt("Normalize")], list_start_index=2),
        block("e2", "equation", expression=r"\frac{"),
    ],
    "li1": [block("li1a", "bulleted_list_item", rich_text=[rt("Nested")])],
    "t1": [
        block("r1", "table_row", cells=[[rt("Layer")], [rt("Example")]]),
        block("r2", "table_row", cells=[[rt("Network")], [rt("IP")]]),
    ],
    CHILD: [
        block("p2", "paragraph", rich_text=[rt("Content of topic 1")]),
        block("h2", "heading_2", rich_text=[rt("Why "), eq(r"\gamma")]),
        block("h3", "heading_3", rich_text=[rt("Detail")]),
        block("tg1", "toggle", has_children=True, rich_text=[rt("More")]),
        block(DB, "child_database", title="Exercises"),
    ],
    "tg1": [block(NESTED, "child_page", title="Nested page")],
    NESTED: [
        block("img1", "image", type="file", file={"url": IMAGE_URL}, caption=[rt("Fig")]),
        block("emb1", "embed", url=EMBED_URL, caption=[rt("How it learns")]),
        block("emb2", "embed", url="https://www.youtube.com/watch?v=abc", caption=[]),
    ],
    ROW: [block("p3", "paragraph", rich_text=[rt("Row content")])],
}


def not_found(what):
    return APIResponseError(APIErrorCode.ObjectNotFound, 404, f"{what} not found", httpx.Headers(), "")


class _Children:
    def __init__(self, pages_per_call, calls):
        self.pages_per_call = pages_per_call
        self.calls = calls

    def list(self, block_id, start_cursor=None, page_size=100):
        self.calls["blocks.children.list"] += 1
        items = CHILDREN.get(block_id, [])
        start = int(start_cursor or 0)
        end = start + self.pages_per_call
        return {"results": items[start:end], "has_more": end < len(items),
                "next_cursor": str(end) if end < len(items) else None}


class _Blocks:
    def __init__(self, pages_per_call, calls):
        self.children = _Children(pages_per_call, calls)


class _Pages:
    def retrieve(self, page_id):
        if page_id not in PAGES:
            raise not_found("page")
        return PAGES[page_id]


class _Databases:
    def retrieve(self, database_id):
        if database_id not in DATABASES:
            raise not_found("database")
        return DATABASES[database_id]


class _DataSources:
    def retrieve(self, data_source_id):
        if data_source_id not in DATA_SOURCES:
            raise not_found("data source")
        return {"title": [rt("Exercises (source)")]}

    def query(self, data_source_id, start_cursor=None, page_size=100):
        return {"results": DATA_SOURCES[data_source_id], "has_more": False, "next_cursor": None}


class FakeNotion:
    # Small page size to check that cursors are followed.
    def __init__(self, pages_per_call=3):
        self.calls = Counter()
        self.blocks = _Blocks(pages_per_call, self.calls)
        self.pages = _Pages()
        self.databases = _Databases()
        self.data_sources = _DataSources()


class Unreachable:
    """A client that fails on any request, to prove that nothing is fetched."""

    def __getattr__(self, name):
        raise AssertionError(f"unexpected Notion request: {name}")
