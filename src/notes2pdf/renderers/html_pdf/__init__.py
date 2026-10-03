"""Renderer: model → HTML (Jinja2 + CSS) → PDF (WeasyPrint)."""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from html import escape
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from notes2pdf import model as m
from notes2pdf.model import Block, Page, Span
from notes2pdf.renderers.base import DocumentOptions
from notes2pdf.renderers.html_pdf.embeds import EmbedSnapshotter
from notes2pdf.renderers.html_pdf.math import latex_to_plain, latex_to_svg

_HERE = Path(__file__).parent
_CODE_STYLE = "friendly"
_LIST_ITEMS = (m.BULLETED_ITEM, m.NUMBERED_ITEM, m.TODO)


def render_spans(spans: list[Span], links: bool = True) -> str:
    out = []
    for s in spans:
        if s.math:
            svg = latex_to_svg(s.text, inline=True)
            out.append(svg if svg is not None else f'<code class="math-error">{escape(s.text)}</code>')
            continue
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
        if s.href and links:
            html = f'<a href="{escape(s.href)}">{html}</a>'
        out.append(html)
    return "".join(out)


def plain_text(spans: list[Span]) -> str:
    return "".join(latex_to_plain(s.text) if s.math else s.text for s in spans)


def highlight_code(code: str, language: str) -> str:
    try:
        lexer = get_lexer_by_name(language.lower())
    except ClassNotFound:
        return escape(code)
    return highlight(code, lexer, HtmlFormatter(nowrap=True))


@dataclass
class TocEntry:
    level: int  # 0 = top-level page
    anchor: str
    html: str


@dataclass
class _PageWriter:
    """Renders one page's blocks; collects its headings for the TOC."""

    page: Page
    depth: int
    embed: Callable[[Block], str] = lambda b: embed_card(b)
    toc: list[TocEntry] = field(default_factory=list)
    _count: int = 0

    def __post_init__(self):
        levels = [b.attrs.get("level", 1) for b in _walk(self.page.blocks) if b.type == m.HEADING]
        # Pages often start at heading_2; make the shallowest heading level 1.
        self._min_level = min(levels, default=1)

    @property
    def anchor(self) -> str:
        return f"p-{self.page.id}"

    def title_html(self) -> str:
        self.toc.append(TocEntry(self.depth, self.anchor, escape(self.page.title)))
        label = escape(self.page.title)
        return (
            f'<h1 class="page-title" id="{self.anchor}" '
            f'style="bookmark-level: {self.depth + 1}" data-label="{label}">{label}</h1>'
        )

    def blocks(self, blocks: list[Block]) -> str:
        parts = []
        for item in _group_lists(blocks):
            if isinstance(item, list):
                parts.append(self._list(item))
            else:
                parts.append(self.block(item))
        return "\n".join(parts)

    def _list(self, items: list[Block]) -> str:
        kind = items[0].type
        if kind == m.NUMBERED_ITEM:
            start = items[0].attrs.get("start")
            open_tag, close = (f'<ol start="{int(start)}">' if start else "<ol>"), "</ol>"
        elif kind == m.TODO:
            open_tag, close = '<ul class="todo">', "</ul>"
        else:
            open_tag, close = "<ul>", "</ul>"
        return open_tag + "".join(self._item(b) for b in items) + close

    def _item(self, b: Block) -> str:
        children = self.blocks(b.children)
        if b.type == m.TODO:
            mark = "☑" if b.attrs.get("checked") else "☐"
            cls = ' class="done"' if b.attrs.get("checked") else ""
            return f'<li{cls}><span class="box">{mark}</span> {render_spans(b.text)}{children}</li>'
        return f"<li>{render_spans(b.text)}{children}</li>"

    def _heading(self, b: Block) -> str:
        rel = b.attrs.get("level", 1) - self._min_level + 1  # 1..3
        self._count += 1
        anchor = f"h-{self.page.id}-{self._count}"
        level = self.depth + rel
        if b.plain_text.strip():  # blank headings exist in Notion; keep them out of the TOC
            self.toc.append(TocEntry(level, anchor, render_spans(b.text, links=False)))
        tag = f"h{min(rel + 1, 6)}"
        label = escape(plain_text(b.text))
        return (
            f'<{tag} id="{anchor}" style="bookmark-level: {level + 1}" data-label="{label}">'
            f"{render_spans(b.text)}</{tag}>"
        )

    def block(self, b: Block) -> str:
        t = b.type
        if t == m.HEADING:
            return self._heading(b) + self.blocks(b.children)
        text = render_spans(b.text)
        children = self.blocks(b.children)
        if t == m.PARAGRAPH:
            return f"<p>{text}</p>{children}" if text or not children else children
        if t == m.QUOTE:
            return f"<blockquote>{text}{children}</blockquote>"
        if t == m.CALLOUT:
            icon = escape(b.attrs.get("icon", ""))
            return f'<div class="callout"><span class="icon">{icon}</span><div>{text}{children}</div></div>'
        if t == m.TOGGLE:
            # Paper can't collapse: bold summary and indented content.
            return f'<div class="toggle"><p class="summary">{text}</p><div class="body">{children}</div></div>'
        if t == m.CODE:
            lang = b.attrs.get("language", "")
            caption = render_spans(b.attrs.get("caption", []))
            cap = f"<figcaption>{caption}</figcaption>" if caption else ""
            code = highlight_code(b.plain_text, lang)
            return f'<figure class="code"><pre data-lang="{escape(lang)}"><code>{code}</code></pre>{cap}</figure>'
        if t == m.DIVIDER:
            return "<hr>"
        if t == m.EQUATION:
            expr = b.attrs.get("expression", "")
            svg = latex_to_svg(expr, inline=False)
            if svg is None:
                return f'<pre class="equation math-error">{escape(expr)}</pre>'
            return f'<div class="equation">{svg}</div>'
        if t == m.IMAGE:
            caption = render_spans(b.attrs.get("caption", []))
            cap = f"<figcaption>{caption}</figcaption>" if caption else ""
            url = escape(b.attrs.get("url") or "")
            return f'<figure class="image"><img src="{url}">{cap}</figure>'
        if t == m.LINK:
            if not b.attrs.get("url"):
                return ""  # embed left empty in the source
            url = escape(b.attrs["url"])
            label = render_spans(b.attrs.get("caption", [])) or url
            return f'<p class="link">🔗 <a href="{url}">{label}</a></p>'
        if t == m.EMBED:
            return self.embed(b)
        if t == m.TABLE:
            return _table(b)
        if t == m.GROUP:
            return f'<div class="group">{children}</div>'
        if t == m.UNSUPPORTED:
            return f"<!-- unsupported block: {escape(str(b.attrs.get('source_type')))} -->"
        return children


def embed_card(b: Block) -> str:
    """Fallback for interactive embeds that can't be captured."""
    caption = render_spans(b.attrs.get("caption", []))
    name = escape(b.attrs.get("name") or "embed")
    detail = f"<br>{caption}" if caption else ""
    return f'<div class="embed-card">🧩 <strong>Interactive content:</strong> {name}{detail}</div>'


def embed_figure(b: Block, shots) -> str:
    """The first snapshot full width, the other states in a two-column grid."""
    caption = render_spans(b.attrs.get("caption", [])) or escape(b.attrs.get("name") or "")
    first, rest = shots[0], shots[1:]
    states = "".join(
        f'<figure class="state"><img src="{s.image.resolve().as_uri()}">'
        f"<figcaption>{escape(s.label)}</figcaption></figure>"
        for s in rest
    )
    count = f" · {len(shots)} states" if rest else ""
    return (
        f'<figure class="embed"><img class="main" src="{first.image.resolve().as_uri()}">'
        f'<figcaption>🧩 {caption}<span class="count">{count}</span></figcaption>'
        + (f'<div class="states">{states}</div>' if states else "")
        + "</figure>"
    )


def _walk(blocks: list[Block]):
    for b in blocks:
        yield b
        yield from _walk(b.children)


def _is_empty(page: Page) -> bool:
    """True if the page has no real content of its own (only blank paragraphs)."""
    return all(b.type == m.PARAGRAPH and not b.plain_text.strip() and not b.children for b in page.blocks)


def _group_lists(blocks: list[Block]) -> list[Block | list[Block]]:
    """Group consecutive list items so they can be wrapped in <ul>/<ol>."""
    grouped: list[Block | list[Block]] = []
    for b in blocks:
        if b.type in _LIST_ITEMS:
            prev = grouped[-1] if grouped else None
            # An explicit start index means a new list even right after another one.
            if isinstance(prev, list) and prev[0].type == b.type and not b.attrs.get("start"):
                prev.append(b)
            else:
                grouped.append([b])
        else:
            grouped.append(b)
    return grouped


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
    return "<table>" + "".join(rows) + "</table>"


class HtmlPdfRenderer:
    def __init__(
        self, css_path: Path | None = None, template_dir: Path | None = None, cache_dir: Path | None = None
    ):
        self.css_path = css_path or _HERE / "style.css"
        self._cache_dir = cache_dir  # embed snapshots; a temp dir if None
        self._snapshotter: EmbedSnapshotter | None = None
        self.env = Environment(
            loader=FileSystemLoader(template_dir or _HERE / "templates"),
            autoescape=select_autoescape(["html"]),
        )

    def render_html(self, pages: list[Page], options: DocumentOptions | None = None) -> str:
        options = options or DocumentOptions()
        sections, toc = [], []
        for root in pages:
            for p in root.walk():
                writer = _PageWriter(
                    p, depth=len(p.path) - len(root.path), embed=lambda b: self._embed(b, options)
                )
                title = writer.title_html()
                body = writer.blocks(p.blocks)
                sections.append({
                    "page": p, "depth": writer.depth, "empty": _is_empty(p),
                    "title": Markup(title), "body": Markup(body),
                })
                toc.extend(writer.toc)

        title = options.title or (pages[0].title if len(pages) == 1 else "Notes")
        css = self.css_path.read_text(encoding="utf-8")
        css += "\n" + HtmlFormatter(style=_CODE_STYLE).get_style_defs("figure.code pre")
        return self.env.get_template("document.html").render(
            title=title,
            options=options,
            date=date.today().strftime("%d %B %Y"),
            toc=[e for e in toc if e.level < options.toc_depth] if options.toc else [],
            sections=sections,
            css=Markup(css),
        )

    def _embed(self, b: Block, options: DocumentOptions) -> str:
        url = b.attrs.get("url")
        if options.embeds == "link" or not url:
            return embed_card(b)
        if self._snapshotter is None:
            self._snapshotter = EmbedSnapshotter(self._cache_dir or Path(tempfile.mkdtemp(prefix="notes2pdf-")))
        limit = 1 if options.embeds == "initial" else options.embed_states
        shots = self._snapshotter.snapshots(url, max_states=limit)
        return embed_figure(b, shots) if shots else embed_card(b)

    def close(self) -> None:
        """Release the headless browser used for embeds, if it was started."""
        if self._snapshotter:
            self._snapshotter.close()

    def render(self, pages: list[Page], output: Path, options: DocumentOptions | None = None) -> Path:
        from weasyprint import HTML  # deferred import: slow to load

        output.parent.mkdir(parents=True, exist_ok=True)
        HTML(string=self.render_html(pages, options), base_url=str(Path.cwd())).write_pdf(output)
        return output
