"""Snapshots of interactive HTML embeds (JavaScript visualizations).

WeasyPrint can't run JavaScript, so each embed is opened in headless Chromium
(Playwright) and its interactive states are captured as images:

1. the initial state;
2. step-through buttons ("Next", "Siguiente"…), clicked until nothing changes;
3. option groups (buttons where one is marked on/active): each option;
4. other action buttons ("Run 30 steps", "Apply bias correction"…): clicked once;
5. sliders: minimum and maximum value;
6. dropdowns: each option.

Every state starts from a fresh page except step sequences, duplicates are
dropped and the result is cached on disk.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_VERSION = 1  # bump when the capture strategy changes
VIEWPORT = {"width": 760, "height": 600}
SETTLE_MS = 400  # after load or a click, for transitions
ACTION_MS = 1800  # after "run"-like buttons, for animations
MAX_STEPS = 12

_STEP = re.compile(r"^\s*(next|siguiente|step|paso)\b|^\s*[→›>]+\s*$", re.I)
_SKIP = re.compile(r"\b(reset|reiniciar|restart|clear|borrar|prev|previous|anterior|back|atrás)\b|^\s*[←‹<]+\s*$", re.I)

# Collected in the page: a description of every control we know how to drive.
_DISCOVER = r"""
() => {
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const label = el => (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
  const isOn = b => b.classList.contains('on') || b.classList.contains('active')
                 || b.classList.contains('selected') || b.getAttribute('aria-pressed') === 'true';
  const buttons = [...document.querySelectorAll('button, [role=button]')].filter(visible);
  const STATE = ['on', 'active', 'selected', 'primary'];
  const sig = b => (b.getAttribute('class') || '').split(/\s+/).filter(c => c && !STATE.includes(c)).sort().join('.');
  // Buttons with the same parent and the same (non-state) classes form a group.
  const groups = new Map();
  buttons.forEach((b, i) => {
    if (!groups.has(b.parentElement)) groups.set(b.parentElement, new Map());
    const bySig = groups.get(b.parentElement), k = sig(b);
    if (!bySig.has(k)) bySig.set(k, []);
    bySig.get(k).push(i);
  });
  // Text right before the first button of a group ("Batch size m:"), if short.
  const groupName = first => {
    const parts = [];
    for (let n = first.previousSibling; n; n = n.previousSibling) {
      if (n.nodeType === 1 && n.matches('button, [role=button], input, select')) break;
      parts.unshift((n.textContent || '').trim());
    }
    let text = parts.join(' ').trim();
    if (!text && first.parentElement.previousElementSibling) text = label(first.parentElement.previousElementSibling);
    text = text.replace(/\s+/g, ' ');
    return text && text.length <= 40 ? (/[:=]$/.test(text) ? text : text + ':') : '';
  };
  const inGroup = new Set();
  const optionGroups = [];
  for (const bySig of groups.values()) for (const idx of bySig.values()) {
    if (idx.length > 1 && idx.some(i => isOn(buttons[i]))) {
      idx.forEach(i => inGroup.add(i));
      const name = groupName(buttons[idx[0]]);
      optionGroups.push(idx.map(i => ({
        index: i, label: (name ? name + ' ' : '') + label(buttons[i]), on: isOn(buttons[i]) })));
    }
  }
  const singles = buttons.map((b, i) => ({ index: i, label: label(b) })).filter(b => !inGroup.has(b.index));
  const nameOf = el => {
    if (el.labels && el.labels[0]) return label(el.labels[0]);
    const prev = el.previousElementSibling; if (prev && label(prev)) return label(prev).slice(0, 40);
    return el.id || el.name || 'value';
  };
  const ranges = [...document.querySelectorAll('input[type=range]')].filter(visible)
    .map((r, i) => ({ index: i, name: nameOf(r), min: r.min || '0', max: r.max || '100', value: r.value }));
  const selects = [...document.querySelectorAll('select')].filter(visible)
    .map((s, i) => ({ index: i, name: nameOf(s), options: [...s.options].map(o => ({ value: o.value, label: o.text })) , value: s.value }));
  return { singles, optionGroups, ranges, selects };
}
"""

# Area of the section containing a control: from the previous heading to the next
# one (most embeds are a sequence of titled sections). Null if it is the whole page.
_CLIP = r"""
([kind, i]) => {
  const visible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const sel = { button: 'button, [role=button]', range: 'input[type=range]', select: 'select' }[kind];
  const el = [...document.querySelectorAll(sel)].filter(visible)[i];
  const heads = [...document.querySelectorAll('h1, h2, h3')].filter(visible);
  if (!el || heads.length < 2) return null;
  const before = heads.filter(h => h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
  const after = heads.filter(h => h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_PRECEDING);
  const doc = document.documentElement;
  const top = before.length ? before[before.length - 1].getBoundingClientRect().top + scrollY - 8 : 0;
  const bottom = after.length ? after[0].getBoundingClientRect().top + scrollY - 12 : doc.scrollHeight;
  if (top <= 0 && bottom >= doc.scrollHeight) return null;
  return { x: 0, y: Math.max(0, top), width: doc.clientWidth, height: Math.max(40, bottom - Math.max(0, top)) };
}
"""

_SET_RANGE = """
([i, v]) => {
  const r = [...document.querySelectorAll('input[type=range]')].filter(
    el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length))[i];
  r.value = v;
  r.dispatchEvent(new Event('input', { bubbles: true }));
  r.dispatchEvent(new Event('change', { bubbles: true }));
}
"""


@dataclass
class Snapshot:
    label: str
    image: Path


class EmbedSnapshotter:
    def __init__(self, cache_dir: Path):
        self.dir = cache_dir / "embeds"
        self._browser = None
        self._playwright = None
        self._unavailable = False

    def snapshots(self, url: str, max_states: int = 8) -> list[Snapshot] | None:
        """Up to `max_states` snapshots of the HTML at `url` (file:// or http),
        or None if it can't be captured."""
        key = hashlib.sha1(f"{_VERSION}:{max_states}:{url}".encode()).hexdigest()[:16]
        target = self.dir / key
        index = target / "index.json"
        if index.exists():
            return [Snapshot(s["label"], target / s["file"]) for s in json.loads(index.read_text())]
        browser = self._launch()
        if browser is None:
            return None
        try:
            shots = self._capture(browser, url, target, max_states)
        except Exception as e:  # a broken embed must not break the whole PDF
            log.warning("Could not capture embed %s: %s", url.split("?")[0], e)
            return None
        index.write_text(json.dumps([{"label": s.label, "file": s.image.name} for s in shots]))
        return shots

    def close(self) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        self._browser = self._playwright = None

    # --- Internals -----------------------------------------------------------

    def _launch(self):
        if self._browser or self._unavailable:
            return self._browser
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch()
        except Exception as e:
            self._unavailable = True
            log.warning(
                "Interactive embeds are shown as links: headless Chromium is not available (%s). "
                "Install it with `uv run playwright install chromium`.", str(e).splitlines()[0]
            )
            self.close()
        return self._browser

    def _capture(self, browser, url: str, target: Path, max_states: int) -> list[Snapshot]:
        target.mkdir(parents=True, exist_ok=True)
        context = browser.new_context(viewport=VIEWPORT, device_scale_factor=2, color_scheme="light")
        page = context.new_page()
        shots: list[Snapshot] = []
        seen: set[str] = set()

        def open_fresh():
            page.goto(url, wait_until="load")
            page.wait_for_timeout(SETTLE_MS)

        def shoot(label: str, control: tuple[str, int] | None = None) -> bool:
            """Capture the section of `control` (or the whole page); False if it
            looks like a state we already have."""
            clip = page.evaluate(_CLIP, list(control)) if control else None
            png = page.screenshot(full_page=True, clip=clip) if clip else page.screenshot(full_page=True)
            digest = hashlib.sha1(png).hexdigest()
            if digest in seen:
                return False
            seen.add(digest)
            if len(shots) < max_states:
                path = target / f"{len(shots):02d}.png"
                path.write_bytes(png)
                shots.append(Snapshot(label, path))
            return True

        def full() -> bool:
            return len(shots) >= max_states

        try:
            open_fresh()
            controls = page.evaluate(_DISCOVER)
            shoot("Initial state")
            buttons = page.locator("button, [role=button]")

            def button(i):
                return buttons.filter(visible=True).nth(i)

            singles = [b for b in controls["singles"] if b["label"] and not _SKIP.search(b["label"])]
            steps = [b for b in singles if _STEP.search(b["label"])]
            actions = [b for b in singles if b not in steps]

            # 2. Step sequences: keep clicking from the initial state.
            for b in steps:
                open_fresh()
                for n in range(2, MAX_STEPS + 2):
                    if full():
                        break
                    btn = button(b["index"])
                    if btn.is_disabled():
                        break
                    btn.click()
                    page.wait_for_timeout(SETTLE_MS)
                    if not shoot(f"Step {n}", ("button", b["index"])):
                        break

            # 3. Option groups: every option that is not the default.
            for group in controls["optionGroups"]:
                for opt in group:
                    if full():
                        break
                    if opt["on"]:
                        continue
                    open_fresh()
                    button(opt["index"]).click()
                    page.wait_for_timeout(SETTLE_MS)
                    shoot(opt["label"], ("button", opt["index"]))

            # 4. Actions: run once and let animations finish.
            for b in actions:
                if full():
                    break
                open_fresh()
                button(b["index"]).click()
                page.wait_for_timeout(ACTION_MS)
                shoot(b["label"], ("button", b["index"]))

            # 5. Sliders: both ends.
            for r in controls["ranges"]:
                for value in (r["min"], r["max"]):
                    if full() or value == r["value"]:
                        continue
                    open_fresh()
                    page.evaluate(_SET_RANGE, [r["index"], value])
                    page.wait_for_timeout(SETTLE_MS)
                    shoot(f"{r['name']} = {value}", ("range", r["index"]))

            # 6. Dropdowns: every other option.
            for s in controls["selects"]:
                for opt in s["options"]:
                    if full() or opt["value"] == s["value"]:
                        continue
                    open_fresh()
                    page.locator("select").nth(s["index"]).select_option(opt["value"])
                    page.wait_for_timeout(SETTLE_MS)
                    shoot(f"{s['name']}: {opt['label']}", ("select", s["index"]))
        finally:
            context.close()
        return shots
