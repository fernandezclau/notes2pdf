"""In-memory Notion client returning responses shaped like the real API."""

from __future__ import annotations


def rt(text, **ann):
    return {
        "type": "text",
        "plain_text": text,
        "href": ann.pop("href", None),
        "annotations": {"bold": False, "italic": False, "strikethrough": False,
                        "underline": False, "code": False, **ann},
    }


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

PAGES = {ROOT: page(ROOT, "Networks", tags=["uni"]), CHILD: page(CHILD, "Topic 1")}

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
    ],
    "li1": [block("li1a", "bulleted_list_item", rich_text=[rt("Nested")])],
    "t1": [
        block("r1", "table_row", cells=[[rt("Layer")], [rt("Example")]]),
        block("r2", "table_row", cells=[[rt("Network")], [rt("IP")]]),
    ],
    CHILD: [block("p2", "paragraph", rich_text=[rt("Content of topic 1")])],
}


class _Children:
    def __init__(self, pages_per_call):
        self.pages_per_call = pages_per_call

    def list(self, block_id, start_cursor=None, page_size=100):
        items = CHILDREN.get(block_id, [])
        start = int(start_cursor or 0)
        end = start + self.pages_per_call
        return {"results": items[start:end], "has_more": end < len(items),
                "next_cursor": str(end) if end < len(items) else None}


class _Blocks:
    def __init__(self, pages_per_call):
        self.children = _Children(pages_per_call)


class _Pages:
    def retrieve(self, page_id):
        return PAGES[page_id]


class FakeNotion:
    # Small page size to check that cursors are followed.
    def __init__(self, pages_per_call=3):
        self.blocks = _Blocks(pages_per_call)
        self.pages = _Pages()
