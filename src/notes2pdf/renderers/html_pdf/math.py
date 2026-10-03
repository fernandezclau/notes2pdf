"""LaTeX → inline SVG (WeasyPrint can't run MathJax/KaTeX, so we render in Python)."""

from __future__ import annotations

import itertools
import logging
import re
import unicodedata
from functools import lru_cache

import ziamath as zm

log = logging.getLogger(__name__)

INLINE_SIZE = 14  # px, matches the 10.5pt body text
BLOCK_SIZE = 16

_ids = itertools.count()
_ID_REF = re.compile(r'(id="|href="#)([^"]+)"')
_VIEWBOX = re.compile(r'viewBox="([-\d.]+) ([-\d.]+) ([-\d.]+) ([-\d.]+)"')
_COMMAND = re.compile(r"\\([A-Za-z]+)")


@lru_cache(maxsize=None)
def _svg(expr: str, inline: bool) -> str | None:
    try:
        return zm.Latex(expr, size=INLINE_SIZE if inline else BLOCK_SIZE, inline=inline).svg()
    except Exception as e:  # ziamath raises various errors on unsupported LaTeX
        log.warning("Could not render equation %r: %s", expr, e)
        return None


def latex_to_svg(expr: str, inline: bool) -> str | None:
    """SVG markup for `expr` ("" if empty), or None if it can't be rendered."""
    if not expr.strip():
        return ""
    svg = _svg(expr.strip(), inline)
    if svg is None:
        return None
    # ziamath reuses glyph ids across equations; make them unique per use.
    prefix = f"m{next(_ids)}-"
    svg = _ID_REF.sub(lambda mt: f'{mt.group(1)}{prefix}{mt.group(2)}"', svg)
    if inline and (vb := _VIEWBOX.search(svg)):
        # viewBox y is -ascent; y + height is the descent below the baseline.
        descent = float(vb.group(2)) + float(vb.group(4))
        svg = svg.replace("<svg ", f'<svg style="vertical-align: {-descent:.2f}px" ', 1)
    return svg


def latex_to_plain(expr: str) -> str:
    """Readable plain text for places that can't hold SVG (PDF bookmarks)."""

    def greek(mt: re.Match) -> str:
        name = mt.group(1)
        case = "CAPITAL" if name[0].isupper() else "SMALL"
        try:
            return unicodedata.lookup(f"GREEK {case} LETTER {name.upper()}")
        except KeyError:
            return name

    return _COMMAND.sub(greek, expr).replace("{", "").replace("}", "")
