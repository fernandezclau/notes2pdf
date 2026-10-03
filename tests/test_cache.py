import json
from datetime import datetime, timezone

import httpx
import pytest

from fake_notion import CHILD, DB, DS, EMBED_URL, IMAGE_URL, NESTED, PAGES, ROOT, FakeNotion, Unreachable
from notes2pdf import model as m
from notes2pdf.cache import Cache
from notes2pdf.sources.notion_api import NotionApiSource


@pytest.fixture
def downloads(monkeypatch):
    """Fake HTTP: record downloaded URLs and return dummy bytes."""
    seen = []

    def get(self, url):
        seen.append(url)
        return httpx.Response(200, content=b"PNG", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", get)
    return seen


def test_roundtrip_serialization():
    blocks = [m.Block(m.TABLE, children=[m.Block(m.TABLE_ROW, attrs={"cells": [[m.Span("a", bold=True)]]})]),
              m.Block(m.PARAGRAPH, [m.Span("x", math=True)], attrs={"caption": [m.Span("c")]})]
    data = json.loads(json.dumps(m.blocks_to_json(blocks)))
    assert m.blocks_from_json(data) == blocks


def run(client, tmp_path):
    """A new source per call, like a separate build.py run."""
    return NotionApiSource(client=client, cache=Cache(tmp_path))


def test_second_run_uses_cache(tmp_path, downloads):
    client = FakeNotion()
    first = run(client, tmp_path).fetch(ROOT)
    calls = client.calls["blocks.children.list"]
    assert calls > 0

    second = run(client, tmp_path).fetch(ROOT)
    assert client.calls["blocks.children.list"] == calls  # no block requests
    assert [p.blocks for p in second.walk()] == [p.blocks for p in first.walk()]
    assert [p.title for p in second.walk()] == [p.title for p in first.walk()]


def test_each_page_is_requested_once_per_run():
    # Without a disk cache: CHILD is fetched as ROOT's subpage, then reused
    # (with its own path) when included on its own.
    client = FakeNotion()
    source = NotionApiSource(client=client)
    source.fetch(ROOT)
    calls = client.calls["blocks.children.list"]
    child = source.fetch(CHILD)
    assert client.calls["blocks.children.list"] == calls
    assert child.path == ["Topic 1"]


def test_edited_page_is_refetched(tmp_path, downloads, monkeypatch):
    client = FakeNotion()
    run(client, tmp_path).fetch(ROOT, recursive=False)
    calls = client.calls["blocks.children.list"]

    edited = dict(PAGES[ROOT], last_edited_time="2026-06-01T00:00:00.000Z")
    monkeypatch.setitem(PAGES, ROOT, edited)
    run(client, tmp_path).fetch(ROOT, recursive=False)
    assert client.calls["blocks.children.list"] > calls


def test_recent_edit_is_not_trusted(tmp_path, downloads, monkeypatch):
    # Edited "now": Notion rounds to the minute, so a fetch in that same minute
    # may have missed later changes.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:00.000Z")
    monkeypatch.setitem(PAGES, ROOT, dict(PAGES[ROOT], last_edited_time=now))
    client = FakeNotion()
    run(client, tmp_path).fetch(ROOT, recursive=False)
    calls = client.calls["blocks.children.list"]
    run(client, tmp_path).fetch(ROOT, recursive=False)
    assert client.calls["blocks.children.list"] > calls


def test_images_are_downloaded_once(tmp_path, downloads):
    source = NotionApiSource(client=FakeNotion(), cache=Cache(tmp_path))
    page = source.fetch(NESTED)
    image, embed = page.blocks[0].attrs["url"], page.blocks[1].attrs["url"]
    assert image.startswith("file://") and image.endswith(".png")
    assert embed.startswith("file://") and embed.endswith(".html")  # HTML embeds too
    assert downloads == [IMAGE_URL, EMBED_URL]

    # Same file with a new signed URL: not downloaded again.
    Cache(tmp_path).download(IMAGE_URL.replace("abc", "xyz"), key=IMAGE_URL.split("?")[0])
    assert downloads == [IMAGE_URL, EMBED_URL]


def test_failed_download_keeps_remote_url(tmp_path, monkeypatch):
    def fail(self, url):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.Client, "get", fail)
    assert Cache(tmp_path).download(IMAGE_URL, key="k") == IMAGE_URL


def test_fetch_database_by_id():
    db = NotionApiSource(client=FakeNotion()).fetch(DB)
    assert db.title == "Exercises"
    assert [r.title for r in db.children] == ["Row 1"]


def test_fetch_data_source_by_id():
    ds = NotionApiSource(client=FakeNotion()).fetch(DS)
    assert [r.title for r in ds.children] == ["Row 1"]
    assert ds.title == "Exercises (source)"


def test_unknown_id_reports_page_error():
    with pytest.raises(Exception, match="page not found"):
        NotionApiSource(client=FakeNotion()).fetch("f" * 32)


def test_without_update_checks_nothing_is_requested(tmp_path, downloads):
    first = run(FakeNotion(), tmp_path).fetch(ROOT)
    offline = NotionApiSource(client=Unreachable(), cache=Cache(tmp_path), check_updates=False)
    second = offline.fetch(ROOT)
    assert [(p.title, p.path, p.tags, p.blocks) for p in second.walk()] == \
        [(p.title, p.path, p.tags, p.blocks) for p in first.walk()]
    # Databases included directly by id work offline too.
    assert [r.title for r in offline.fetch(DB).children] == ["Row 1"]


def test_without_update_checks_missing_pages_are_fetched(tmp_path):
    client = FakeNotion()
    source = NotionApiSource(client=client, cache=Cache(tmp_path), check_updates=False)
    assert source.fetch(CHILD, recursive=False).title == "Topic 1"
    assert client.calls["blocks.children.list"] > 0
