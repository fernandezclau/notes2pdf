# notes2pdf

Turn your notes into PDFs, choosing which pages are grouped into each document.
It currently reads from Notion; the design allows adding other note-taking apps.

```
Source (Notion, …) ──▶ intermediate model (Page/Block) ──▶ Renderer (HTML → PDF)
```

- `src/notes2pdf/model.py`: app-independent model (`Page`, `Block`, `Span`).
- `src/notes2pdf/sources/`: one adapter per app. Each implements `tree()` and `fetch()`.
- `src/notes2pdf/renderers/html_pdf/`: Jinja2 template + `style.css` → WeasyPrint.
- `src/notes2pdf/config.py` / `build.py`: read `bundles.yml` and build every PDF.

## Installation

```bash
uv sync
```

WeasyPrint needs the system Pango libraries (on Ubuntu: `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`).

## Notion setup

1. Create an internal integration at <https://www.notion.so/my-integrations> and copy its token.
2. `cp .env.example .env` and paste the token into `NOTION_TOKEN`.
3. In Notion, open the root page of your notes → `···` → **Connections** → add your integration.
   Subpages inherit access.

## Usage

1. `cp bundles.example.yml bundles.yml`
2. Edit `bundles.yml`: each bundle is one PDF; list the pages it should contain, in order.
   Use the page URL from Notion (`···` → **Copy link**).

   ```yaml
   source: notion
   bundles:
     - title: Exam review
       output: out/review.pdf
       include:
         - page: https://www.notion.so/Topic-1-<id>
         - page: https://www.notion.so/Topic-3-<id>
           children: false   # only this page, without its subpages
   ```

3. Run `build.py` (press Run in your editor, or `uv run build.py`).

`bundles.yml` is git-ignored because it points to your own pages.

## Adding another note-taking app

Create `sources/<app>.py` with a class implementing `Source` (`sources/base.py`)
that converts the app's format into `Page`/`Block` using the types in `model.py`,
then register it in `SOURCES` in `src/notes2pdf/build.py` so `source: <app>` works in `bundles.yml`.
The renderer needs no changes.

## Tests

```bash
uv run pytest
```

## Roadmap

- [x] Phase 1: model, Notion API source, HTML→PDF renderer, `bundles.yml` with explicit page lists.
- [ ] Phase 2: local cache (`last_edited_time`) and image downloading (Notion URLs expire after 1 h).
- [ ] Phase 3: richer `bundles.yml` selectors (`path` glob, `tag`, `depth`, `exclude`) and a table of contents.
- [ ] Phase 4: code highlighting, equations, Notion databases.
- [ ] Phase 5: second source (Notion export / Markdown folder / Obsidian).
