# notes2pdf

Turn your notes into PDFs, choosing which pages are grouped into each document.
It currently reads from Notion; the design allows adding other note-taking apps.

```
Source (Notion, …) ──▶ intermediate model (Page/Block) ──▶ selection ──▶ Renderer (HTML → PDF)
```

- `src/notes2pdf/model.py`: app-independent model (`Page`, `Block`, `Span`).
- `src/notes2pdf/sources/`: one adapter per app. Each implements `tree()` and `fetch()`.
- `src/notes2pdf/selection.py`: `depth` / `exclude` / `tags` filters, on the model.
- `src/notes2pdf/renderers/html_pdf/`: Jinja2 template + `style.css` → WeasyPrint.
  Equations are rendered from LaTeX to SVG (`math.py`); code is highlighted with Pygments.
- `src/notes2pdf/renderers/html_pdf/embeds.py`: snapshots of interactive HTML embeds (Playwright).
- `src/notes2pdf/cache.py`: fetched pages and downloaded files, in `.cache/`.
- `src/notes2pdf/config.py` / `build.py`: read `bundles.yml` and build every PDF.

## Installation

```bash
uv sync
```

WeasyPrint needs the system Pango libraries (on Ubuntu: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`).

Interactive HTML embeds are captured with a headless Chromium, installed once with:

```bash
uv run playwright install chromium
```

Without it, embeds are shown as a placeholder card and the build carries on.

## Notion setup

1. Create an internal integration at <https://www.notion.so/my-integrations> and copy its token.
2. `cp .env.example .env` and paste the token into `NOTION_TOKEN`.
3. In Notion, open the root page of your notes → `···` → **Connections** → add your integration.
   Subpages inherit access.

## Usage

1. `cp bundles.example.yml bundles.yml`
2. Edit `bundles.yml`: each bundle is one PDF; list the pages it should contain, in order.
   Use the page URL from Notion (`···` → **Copy link**).
3. Run `build.py` (press Run in your editor, or `uv run build.py`).

`bundles.yml` is git-ignored because it points to your own pages.

### Keeping your config private

Keep `bundles.yml` in a separate private repository next to this one and link it:

```bash
ln -s ../notes2pdf-config/bundles.yml bundles.yml
```

### Bundle options

| Option | Default | |
|---|---|---|
| `output` | required | PDF path, relative to `bundles.yml` |
| `title`, `subtitle`, `author` | first page title | Shown on the cover and in the PDF metadata |
| `cover` | `true` | Cover page |
| `toc` | `true` | Table of contents with links and page numbers |
| `toc_depth` | `3` | Levels in the TOC (pages and headings) |
| `embeds` | `states` | Interactive HTML embeds: `states` (one image per state), `initial` (first state only) or `link` (a card) |
| `embed_states` | `8` | Max images per embed with `embeds: states` |
| `exclude` | — | Subpages to drop from every include |
| `include` | required | List of pages, in order (see below) |

Each `include` item:

| Option | Default | |
|---|---|---|
| `page` | required | Notion URL or id of a page or database |
| `depth` | all | Levels of subpages: `0` = only this page, `1` = direct subpages, … |
| `children: false` | — | Same as `depth: 0` |
| `exclude` | — | Patterns matching title, path (`Course/Week 2/*`) or id; `*` and `?` allowed |
| `tags` | — | Keep only subpages/database rows with one of these tags (select/multi-select properties) |

### What is converted

Text formatting, headings, lists (including numbering that continues after an
interruption), to-dos, toggles (shown expanded), quotes, callouts, code (highlighted),
equations (block and inline LaTeX), images, tables, interactive HTML embeds (as
snapshots, see below), other bookmarks/embeds (as links),
columns, synced blocks, subpages (also inside toggles/columns) and databases (each row
becomes a subpage). Unsupported blocks are skipped and reported when building.

### Interactive HTML embeds

HTML files embedded in Notion (JavaScript visualizations) can't be printed as they are,
so each one is opened in headless Chromium and its states are captured:

1. the initial state (whole page);
2. step-through buttons ("Next step"…), clicked until nothing changes;
3. option groups (buttons with one marked `on`/`active`), one image per option;
4. other action buttons ("Run 30 steps"…), clicked once;
5. sliders at their minimum and maximum, and every option of dropdowns.

Each state is cropped to its section (between the surrounding headings) and labeled
with the control that produced it ("Batch size m: 8"). Reset/Previous buttons and
repeated states are skipped. Snapshots are cached in `.cache/embeds/`.

### Cache

Pages are stored in `.cache/` and only fetched again when edited in Notion, so later
builds are much faster. Images are downloaded there too (Notion image links expire after
one hour). Delete `.cache/` to force a full refresh, or set `cache: false`.

Checking for changes still asks Notion about every page (about 3 requests per second).
While iterating on the selection or the styles, set `check_updates: false` to build only
from `.cache/` without contacting Notion; pages that are not cached yet are still fetched.
Remember to turn it back on to pick up your latest edits.

## Adding another note-taking app

Create `sources/<app>.py` with a class implementing `Source` (`sources/base.py`)
that converts the app's format into `Page`/`Block` using the types in `model.py`,
then register it in `SOURCES` in `src/notes2pdf/build.py` so `source: <app>` works in `bundles.yml`.
Selection, cover, TOC and rendering need no changes.

## Tests

```bash
uv run pytest
```

## Roadmap

- [x] Phase 1: model, Notion API source, HTML→PDF renderer, `bundles.yml`.
- [x] Phase 2: local cache and image downloading.
- [x] Phase 3: selectors (`depth`, `exclude`, `tags`), cover and linked table of contents.
- [x] Phase 4: LaTeX equations, code highlighting, Notion databases.
- [ ] Phase 5: second source (Notion export / Markdown folder / Obsidian).
