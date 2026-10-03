import pytest

from fake_notion import CHILD, ROOT, FakeNotion
from notes2pdf import model as m
from notes2pdf.renderers.base import DocumentOptions
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
                     m.CALLOUT, m.CODE, m.TABLE, m.UNSUPPORTED,
                     m.NUMBERED_ITEM, m.EQUATION, m.NUMBERED_ITEM, m.EQUATION]
    assert page.blocks[2].children[0].plain_text == "Nested"
    assert page.blocks[7].children[1].attrs["cells"][1][0].text == "IP"
    assert page.blocks[9].text[1].math and page.blocks[9].text[1].text == r"\mu"
    assert page.blocks[11].attrs == {"start": 2}

    [child] = page.children
    assert child.id == CHILD
    # The page inside a toggle and the database become subpages, in order.
    assert [c.title for c in child.children] == ["Nested page", "Exercises"]
    nested, db = child.children
    assert nested.path == ["Networks", "Topic 1", "Nested page"]
    assert [r.title for r in db.children] == ["Row 1"]
    assert db.children[0].blocks[0].plain_text == "Row content"
    assert child.path == ["Networks", "Topic 1"]
    assert child.blocks[0].plain_text == "Content of topic 1"


def test_fetch_without_children(source):
    assert source.fetch(ROOT, recursive=False).children == []


def test_tree_has_no_content(source):
    tree = source.tree(ROOT)
    assert tree.blocks == []
    assert [c.title for c in tree.children] == ["Topic 1"]
    assert all(not p.blocks for p in tree.walk())


def test_render_html(source):
    html = HtmlPdfRenderer().render_html([source.fetch(ROOT)])
    assert ">Introduction</h2>" in html
    assert "<strong>bold</strong>" in html
    assert '<a href="https://example.com">link</a>' in html
    assert "<ul><li>One<ul><li>Nested</li></ul></li><li>Two</li></ul>" in html
    assert "&lt;hello&gt;" in html and "<hello>" not in html  # code is escaped
    assert "<th>Layer</th>" in html and "<td>IP</td>" in html
    assert "☑" in html
    assert 'class="breadcrumb">Networks</p>' in html  # subpage breadcrumb
    assert 'class="breadcrumb">Networks › Topic 1</p>' in html


def test_render_pdf(source, tmp_path):
    out = HtmlPdfRenderer().render([source.fetch(ROOT)], tmp_path / "out.pdf")
    assert out.read_bytes().startswith(b"%PDF")


def test_render_math(source):
    html = HtmlPdfRenderer().render_html([source.fetch(ROOT)])
    assert '<div class="equation"><svg' in html  # block equation
    assert '<svg style="vertical-align' in html  # inline equation, baseline-aligned
    assert '<pre class="equation math-error">\\frac{</pre>' in html  # invalid LaTeX falls back
    assert "\\mu" not in html.replace("math-error", "")  # no raw LaTeX for valid input


def test_numbered_list_continues(source):
    html = HtmlPdfRenderer().render_html([source.fetch(ROOT)])
    assert '<ol start="2"><li>Normalize' in html


def test_cover_and_toc(source):
    renderer = HtmlPdfRenderer()
    html = renderer.render_html([source.fetch(ROOT)], DocumentOptions(subtitle="Sub", author="Me"))
    assert '<section class="cover">' in html and "Sub" in html and "Me" in html
    toc = html[html.index('<nav class="toc">'):html.index("</nav>")]
    # Pages and headings, linked to their anchors; topic 1 starts at heading_2.
    assert f'<li class="level-0"><a href="#p-{ROOT}">Networks</a>' in toc
    assert f'<li class="level-1"><a href="#p-{CHILD}">Topic 1</a>' in toc
    assert f'<li class="level-2"><a href="#h-{CHILD}-1">Why <svg' in toc
    assert f'<li class="level-3"><a href="#h-{CHILD}-2">Detail' not in toc  # beyond toc_depth=3
    assert f'id="h-{CHILD}-1"' in html and 'data-label="Why γ"' in html  # bookmark label

    deeper = renderer.render_html([source.fetch(ROOT)], DocumentOptions(toc_depth=4))
    assert f'<a href="#h-{CHILD}-2">Detail' in deeper

    bare = renderer.render_html([source.fetch(ROOT)], DocumentOptions(cover=False, toc=False))
    assert '<section class="cover">' not in bare and '<nav class="toc">' not in bare


def test_empty_parent_page_does_not_take_a_full_sheet(source):
    root = source.fetch(ROOT)
    root.blocks = [m.Block(m.PARAGRAPH)]  # only a blank line, like an index page in Notion
    html = HtmlPdfRenderer().render_html([root])
    assert '<section class="page depth-0 empty">' in html
    assert '<section class="page depth-1">' in html


def test_blank_heading_not_in_toc(source):
    root = source.fetch(ROOT, recursive=False)
    root.blocks = [m.Block(m.HEADING, [m.Span("  ")], attrs={"level": 1})]
    html = HtmlPdfRenderer().render_html([root])
    toc = html[html.index('<nav class="toc">'):html.index("</nav>")]
    assert "#h-" not in toc


def test_empty_embed_is_skipped(source):
    root = source.fetch(ROOT, recursive=False)
    root.blocks = [m.Block(m.LINK, attrs={"url": "", "caption": []})]
    assert 'class="link"' not in HtmlPdfRenderer().render_html([root])
