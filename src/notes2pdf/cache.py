"""On-disk cache of fetched pages and downloaded assets (images)."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from notes2pdf.model import Block, blocks_from_json, blocks_to_json

log = logging.getLogger(__name__)

_VERSION = 4  # bump when the cached format changes, to invalidate old entries


class Cache:
    def __init__(self, root: Path):
        self.root = root
        self.pages_dir = root / "pages"
        self.assets_dir = root / "assets"
        self._http: httpx.Client | None = None

    # --- Pages and databases -----------------------------------------------

    def load_page(self, page_id: str) -> dict[str, Any] | None:
        """Cached entry: kind ("page"/"database"), meta, last_edited_time, blocks, children."""
        path = self.pages_dir / f"{page_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if data.get("version") != _VERSION:
            return None
        data["blocks"] = blocks_from_json(data["blocks"])
        return data

    def save_page(
        self,
        page_id: str,
        edited: str,
        blocks: list[Block],
        children: list[dict[str, str]],
        meta: dict[str, Any] | None = None,
        kind: str = "page",
    ) -> None:
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "version": _VERSION,
            "kind": kind,
            "meta": meta or {},  # source data needed to rebuild the page offline (title, tags…)
            "last_edited_time": edited,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "blocks": blocks_to_json(blocks),
            "children": children,
        }
        (self.pages_dir / f"{page_id}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # --- Assets ------------------------------------------------------------

    def download(self, url: str, key: str) -> str:
        """Download `url` once and return a file:// URI; on failure return `url`."""
        suffix = Path(urlparse(url).path).suffix.lower()[:6] or ".bin"
        name = hashlib.sha1(key.encode()).hexdigest()[:16] + suffix
        target = self.assets_dir / name
        if not target.exists():
            try:
                self._http = self._http or httpx.Client(timeout=30, follow_redirects=True)
                resp = self._http.get(url)
                resp.raise_for_status()
            except httpx.HTTPError as e:
                log.warning("Could not download %s: %s", url, e)
                return url
            self.assets_dir.mkdir(parents=True, exist_ok=True)
            target.write_bytes(resp.content)
        return target.resolve().as_uri()
