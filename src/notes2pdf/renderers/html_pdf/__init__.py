"""Renderer: model → HTML (Jinja2 + CSS) → PDF (WeasyPrint)."""

from __future__ import annotations

from html import escape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from notes2pdf import model as m
from notes2pdf.model import Block, Page, Span

_HERE = Path(__file__).parent


def render_spans(spans: list[Span]) -> str:
    out = []
    for s in spans:
        html = escape(s.text).replace("\n", "<br>")
        if s.code:
            html = f"<code>{html}</code>"
        if s.bold:
            html = f"<strong>{html}</strong>"
        if s.italic:
            html = f"<em>{html}</em>"
        if s.strike:
            html = f"<s>{html}</s>"
        if s.underline:
            html = f"<u>{html}</u>"
        if s.href:
            html = f'<a href="{escape(s.href)}">{html}</a>'
        out.append(html)
    return "".join(out)


def _group_lists(blocks: list[Block]) -> list[Block | tuple[str, list[Block]]]:
    """Group consecutive list items so they can be wrapped in <ul>/<ol>."""
    grouped: list[Block | tuple[str, list[Block]]] = []
    for b in blocks:
        if b.type in (m.BULLETED_ITEM, m.NUMBERED_ITEM, m.TODO):
            if grouped and isinstance(grouped[-1], tuple) and grouped[-1][0] == b.type:
                grouped[-1][1].append(b)
            else:
                grouped.append((b.type, [b]))
        else:
            grouped.append(b)
    return grouped


_LIST_TAGS = {
    m.BULLETED_ITEM: '<ul>',
    m.NUMBERED_ITEM: '<ol>',
    m.TODO: '<ul class="todo">',
}


def render_blocks(blocks: list[Block], heading_offset: int = 1) -> str:
    parts = []
    for item in _group_lists(blocks):
        if isinstance(item, tuple):
            kind, items = item
            close = "</ol>" if kind == m.NUMBERED_ITEM else "</ul>"
            parts.append(_LIST_TAGS[kind] + "".join(_item(b, heading_offset) for b in items) + close)
        else:
            parts.append(render_block(item, heading_offset))
    return "\n".join(parts)


def _item(b: Block, offset: int) -> str:
    children = render_blocks(b.children, offset)
    if b.type == m.TODO:
        mark = "☑" if b.attrs.get("checked") else "☐"
        cls = ' class="done"' if b.attrs.get("checked") else ""
        return f'<li{cls}><span class="box">{mark}</span> {render_spans(b.text)}{children}</li>'
    return f"<li>{render_spans(b.text)}{children}</li>"


def render_block(b: Block, heading_offset: int = 1) -> str:
    text = render_spans(b.text)
    children = render_blocks(b.children, heading_offset)
    t = b.type
    if t == m.PARAGRAPH:
        return f"<p>{text}</p>{children}" if text or not children else children
    if t == m.HEADING:
        level = min(b.attrs.get("level", 1) + heading_offset, 6)
        return f"<h{level}>{text}</h{level}>{children}"
    if t == m.QUOTE:
        return f"<blockquote>{text}{children}</blockquote>"
    if t == m.CALLOUT:
        icon = escape(b.attrs.get("icon", ""))
        return f'<div class="callout"><span class="icon">{icon}</span><div>{text}{children}</div></div>'
    if t == m.TOGGLE:
        # Paper can't collapse: bold summary and indented content.
        return f'<div class="toggle"><p class="summary">{text}</p><div class="body">{children}</div></div>'
    if t == m.CODE:
        lang = escape(b.attrs.get("language", ""))
        caption = render_spans(b.attrs.get("caption", []))
        cap = f"<figcaption>{caption}</figcaption>" if caption else ""
        code = escape(b.plain_text)
        return f'<figure class="code"><pre data-lang="{lang}"><code>{code}</code></pre>{cap}</figure>'
    if t == m.DIVIDER:
        return "<hr>"
    if t == m.EQUATION:
        return f'<pre class="equation">{escape(b.attrs.get("expression", ""))}</pre>'
    if t == m.IMAGE:
        caption = render_spans(b.attrs.get("caption", []))
        cap = f"<figcaption>{caption}</figcaption>" if caption else ""
        url = escape(b.attrs.get("url") or "")
        return f'<figure class="image"><img src="{url}">{cap}</figure>'
    if t == m.LINK:
        url = escape(b.attrs.get("url") or "")
        label = render_spans(b.attrs.get("caption", [])) or url
        return f'<p class="link">🔗 <a href="{url}">{label}</a></p>'
    if t == m.TABLE:
        return _table(b)
    if t == m.GROUP:
        return f'<div class="group">{children}</div>'
    if t == m.UNSUPPORTED:
        return f"<!-- unsupported block: {escape(str(b.attrs.get('source_type')))} -->"
    return children


def _table(b: Block) -> str:
    col_header = b.attrs.get("has_column_header")
    row_header = b.attrs.get("has_row_header")
    rows = []
    for i, row in enumerate(r for r in b.children if r.type == m.TABLE_ROW):
        cells = []
        for j, cell in enumerate(row.attrs.get("cells", [])):
            tag = "th" if (col_header and i == 0) or (row_header and j == 0) else "td"
            cells.append(f"<{tag}>{render_spans(cell)}</{tag}>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return '<table>' + "".join(rows) + "</table>"


class HtmlPdfRenderer:
    def __init__(self, css_path: Path | None = None, template_dir: Path | None = None):
        self.css_path = css_path or _HERE / "style.css"
        self.env = Environment(
            loader=FileSystemLoader(template_dir or _HERE / "templates"),
            autoescape=select_autoescape(["html"]),
        )

    def render_html(self, pages: list[Page], title: str | None = None) -> str:
        sections = [
            {"page": p, "depth": len(p.path) - len(root.path), "body": render_blocks(p.blocks)}
            for root in pages
            for p in root.walk()
        ]
        return self.env.get_template("document.html").render(
            title=title or (pages[0].title if len(pages) == 1 else "Notes"),
            sections=sections,
            css=self.css_path.read_text(encoding="utf-8"),
        )

    def render(self, pages: list[Page], output: Path, title: str | None = None) -> Path:
        from weasyprint import HTML  # deferred import: slow to load

        output.parent.mkdir(parents=True, exist_ok=True)
        HTML(string=self.render_html(pages, title), base_url=str(Path.cwd())).write_pdf(output)
        return output
