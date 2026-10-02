import pytest

from fake_notion import CHILD, ROOT, FakeNotion
from notes2pdf import model as m
from notes2pdf.renderers.html_pdf import HtmlPdfRenderer
from notes2pdf.sources.notion_api import NotionApiSource, parse_page_id


@pytest.fixture
def source():
    return NotionApiSource(client=FakeNotion())


def test_parse_page_id():
    pid = "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d"
    assert parse_page_id(pid) == pid
    assert parse_page_id(f"https://www.notion.so/My-notes-{pid}?pvs=4") == pid
    assert parse_page_id("1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d") == pid
    with pytest.raises(ValueError):
        parse_page_id("not-an-id")


def test_fetch_converts_blocks_and_subpages(source):
    page = source.fetch(ROOT)
    assert page.title == "Networks"
    assert page.tags == ["uni"]
    types = [b.type for b in page.blocks]
    assert types == [m.HEADING, m.PARAGRAPH, m.BULLETED_ITEM, m.BULLETED_ITEM, m.TODO,
                     m.CALLOUT, m.CODE, m.TABLE, m.UNSUPPORTED]
    assert page.blocks[2].children[0].plain_text == "Nested"
    assert page.blocks[7].children[1].attrs["cells"][1][0].text == "IP"

    [child] = page.children
    assert child.id == CHILD
    assert child.path == ["Networks", "Topic 1"]
    assert child.blocks[0].plain_text == "Content of topic 1"


def test_fetch_without_children(source):
    assert source.fetch(ROOT, recursive=False).children == []


def test_tree_has_no_content(source):
    tree = source.tree(ROOT)
    assert tree.blocks == []
    assert [c.title for c in tree.children] == ["Topic 1"]


def test_render_html(source):
    html = HtmlPdfRenderer().render_html([source.fetch(ROOT)])
    assert "<h2>Introduction</h2>" in html
    assert "<strong>bold</strong>" in html
    assert '<a href="https://example.com">link</a>' in html
    assert "<ul><li>One<ul><li>Nested</li></ul></li><li>Two</li></ul>" in html
    assert "print(&#x27;&lt;hello&gt;&#x27;)" in html  # code is escaped
    assert "<th>Layer</th>" in html and "<td>IP</td>" in html
    assert "☑" in html
    assert "Networks › " not in html and "Networks</p>" in html  # subpage breadcrumb


def test_render_pdf(source, tmp_path):
    out = HtmlPdfRenderer().render([source.fetch(ROOT)], tmp_path / "out.pdf")
    assert out.read_bytes().startswith(b"%PDF")
