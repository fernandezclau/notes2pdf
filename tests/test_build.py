import pytest

from fake_notion import CHILD, ROOT, FakeNotion
from notes2pdf.build import build_all
from notes2pdf.config import ConfigError, load_config
from notes2pdf.sources.notion_api import NotionApiSource


def write(tmp_path, text):
    path = tmp_path / "bundles.yml"
    path.write_text(text)
    return path


def test_load_config(tmp_path):
    cfg = load_config(write(tmp_path, f"""
source: notion
bundles:
  - title: All
    output: out/all.pdf
    include:
      - page: {ROOT}
  - output: out/topic.pdf
    include:
      - {CHILD}
      - page: {ROOT}
        children: false
"""))
    assert cfg.source == {"type": "notion"}
    first, second = cfg.bundles
    assert first.title == "All"
    assert first.output == tmp_path / "out/all.pdf"  # relative to the config file
    assert [(i.page, i.children) for i in second.include] == [(CHILD, True), (ROOT, False)]


@pytest.mark.parametrize("text, error", [
    ("bundles: []", "No bundles"),
    ("bundles:\n  - include: [x]", "missing `output`"),
    ("bundles:\n  - output: a.pdf", "`include` is empty"),
    ("bundles:\n  - output: a.pdf\n    include:\n      - children: true", "missing `page`"),
])
def test_invalid_config(tmp_path, text, error):
    with pytest.raises(ConfigError, match=error):
        load_config(write(tmp_path, text))


def test_missing_config(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "bundles.yml")


def test_build_all(tmp_path):
    path = write(tmp_path, f"""
bundles:
  - output: out/a.pdf
    include: [{ROOT}]
  - output: out/b.pdf
    include: [{ROOT}, {CHILD}]
""")
    outputs = build_all(path, source=NotionApiSource(client=FakeNotion()), log=lambda _: None)
    assert outputs == [tmp_path / "out/a.pdf", tmp_path / "out/b.pdf"]
    assert all(p.read_bytes().startswith(b"%PDF") for p in outputs)
