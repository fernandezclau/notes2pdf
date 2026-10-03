"""Prunes fetched page trees according to a bundle's selectors.

Works on the intermediate model only, so it applies to every source.
"""

from __future__ import annotations

from copy import copy
from fnmatch import fnmatchcase

from notes2pdf.model import Page


def _matches(page: Page, patterns: list[str]) -> bool:
    """A pattern matches the page id, its title, or its path ("A/B/C"), with * and ? wildcards."""
    path = "/".join(page.path)
    title = page.title.lower()
    for pat in patterns:
        p = pat.lower()
        if p == page.id or fnmatchcase(title, p) or fnmatchcase(path.lower(), p):
            return True
    return False


def select(
    page: Page,
    depth: int | None = None,
    exclude: list[str] | None = None,
    tags: list[str] | None = None,
) -> Page:
    """Copy of `page` keeping only the selected subpages.

    depth   -- levels of subpages to keep (0 = only this page, None = all)
    exclude -- patterns of subpages to drop (with all their subpages)
    tags    -- if given, subpages must have at least one of these tags
               (subpages without them are dropped, but their own subpages are kept)
    """
    exclude = exclude or []
    wanted = {t.lower() for t in tags or []}

    def visit(p: Page, level: int) -> list[Page]:
        if level > 0 and _matches(p, exclude):
            return []
        children = []
        if depth is None or level < depth:
            for child in p.children:
                children.extend(visit(child, level + 1))
        if level > 0 and wanted and not wanted & {t.lower() for t in p.tags}:
            return children  # skip this page but keep matching descendants
        kept = copy(p)
        kept.children = children
        return [kept]

    return visit(page, 0)[0]
