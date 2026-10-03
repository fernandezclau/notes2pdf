"""Generate every PDF defined in bundles.yml.

Run it with `uv run build.py`, or open this file and press Run in your editor.
"""

from pathlib import Path

from notes2pdf.build import build_all, setup_logging

CONFIG = Path(__file__).parent / "bundles.yml"

if __name__ == "__main__":
    setup_logging()
    build_all(CONFIG)
