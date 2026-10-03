import pytest

from fake_notion import NESTED, FakeNotion
from notes2pdf import model as m
from notes2pdf.renderers.base import DocumentOptions
from notes2pdf.renderers.html_pdf import HtmlPdfRenderer
from notes2pdf.renderers.html_pdf.embeds import EmbedSnapshotter, Snapshot
from notes2pdf.sources.notion_api import NotionApiSource

# A small interactive page with the patterns found in real embeds: a section
# with a step-through button, a section with an option group and a slider.
DEMO = """<!DOCTYPE html><html><body style="margin:0;font:16px sans-serif">
<h2>Training loop</h2>
<p id="stage">Stage 1</p>
<button id="next">Next step</button><button id="reset">Reset</button>
<h2>Activation</h2>
<div><span>Function:</span>
  <button class="fn on">Sigmoid</button><button class="fn">tanh</button></div>
<p id="fn">Sigmoid</p>
<label for="lr">Learning rate</label>
<input id="lr" type="range" min="0" max="1" step="0.5" value="0.5">
<p id="lrv">0.5</p>
<script>
  let s = 1;
  next.onclick = () => { if (s < 3) stage.textContent = 'Stage ' + (++s); next.disabled = s === 3; };
  document.querySelectorAll('.fn').forEach(b => b.onclick = () => {
    document.querySelectorAll('.fn').forEach(x => x.classList.toggle('on', x === b));
    fn.textContent = b.textContent; });
  lr.oninput = () => lrv.textContent = lr.value;
</script></body></html>"""


def embed_block():
    return m.Block(m.EMBED, attrs={"url": "file:///x.html", "name": "x.html", "caption": [m.Span("Demo")]})


def page_with(block):
    return m.Page(id="p", title="P", path=["P"], blocks=[block])


def test_notion_html_embed_becomes_embed_block():
    page = NotionApiSource(client=FakeNotion()).fetch(NESTED)
    html_embed, video = page.blocks[1], page.blocks[2]
    assert html_embed.type == m.EMBED
    assert html_embed.attrs["name"] == "how_a_network_learns.html"
    assert html_embed.attrs["caption"][0].text == "How it learns"
    assert video.type == m.LINK  # other embeds stay links


def test_link_mode_renders_a_card():
    html = HtmlPdfRenderer().render_html([page_with(embed_block())], DocumentOptions(embeds="link"))
    assert 'class="embed-card"' in html and "x.html" in html and "Demo" in html


def test_states_are_rendered_as_figure(tmp_path, monkeypatch):
    shots = [Snapshot(label, tmp_path / f"{i}.png") for i, label in enumerate(["Initial state", "Step 2", "tanh"])]
    calls = []

    def fake(self, url, max_states=8):
        calls.append(max_states)
        return shots[:max_states]

    monkeypatch.setattr(EmbedSnapshotter, "snapshots", fake)
    renderer = HtmlPdfRenderer(cache_dir=tmp_path)
    html = renderer.render_html([page_with(embed_block())], DocumentOptions(embed_states=5))
    assert '<figure class="embed">' in html and "3 states" in html
    assert html.count('<figure class="state">') == 2 and "<figcaption>tanh</figcaption>" in html

    renderer.render_html([page_with(embed_block())], DocumentOptions(embeds="initial"))
    assert calls == [5, 1]


def test_capture_failure_falls_back_to_card(tmp_path, monkeypatch):
    monkeypatch.setattr(EmbedSnapshotter, "snapshots", lambda self, url, max_states=8: None)
    html = HtmlPdfRenderer(cache_dir=tmp_path).render_html([page_with(embed_block())])
    assert 'class="embed-card"' in html


def _chromium_available():
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _chromium_available(), reason="headless Chromium not installed")
def test_capture_real_states(tmp_path):
    demo = tmp_path / "demo.html"
    demo.write_text(DEMO)
    snapshotter = EmbedSnapshotter(tmp_path / "cache")
    try:
        shots = snapshotter.snapshots(demo.as_uri())
    finally:
        snapshotter.close()
    labels = [s.label for s in shots]
    # Steps until the button is disabled, the other option, both slider ends;
    # "Reset" is skipped and duplicate states are dropped.
    assert labels == ["Initial state", "Step 2", "Step 3", "Function: tanh", "Learning rate = 0", "Learning rate = 1"]
    assert all(s.image.read_bytes().startswith(b"\x89PNG") for s in shots)

    # Cached: the same result without a browser.
    again = EmbedSnapshotter(tmp_path / "cache").snapshots(demo.as_uri())
    assert [s.label for s in again] == labels


def test_invalid_embeds_option(tmp_path):
    from notes2pdf.config import ConfigError, load_config

    path = tmp_path / "bundles.yml"
    path.write_text("bundles:\n  - output: a.pdf\n    embeds: video\n    include: [x]\n")
    with pytest.raises(ConfigError, match="`embeds` must be"):
        load_config(path)
