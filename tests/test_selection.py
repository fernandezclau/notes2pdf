import pytest

from notes2pdf.config import ConfigError, load_config
from notes2pdf.model import Page
from notes2pdf.selection import select


def tree():
    def page(id, path, tags=(), children=()):
        return Page(id=id, title=path[-1], path=path, tags=list(tags), children=list(children))

    return page("root", ["Course"], children=[
        page("w1", ["Course", "Week 1"], tags=["exam"], children=[
            page("w1a", ["Course", "Week 1", "Draft notes"]),
            page("w1b", ["Course", "Week 1", "Exercises"], tags=["exam"]),
        ]),
        page("w2", ["Course", "Week 2"], children=[
            page("w2a", ["Course", "Week 2", "Lab"], tags=["exam"]),
        ]),
    ])


def titles(page):
    return [p.title for p in page.walk()]


def test_everything_by_default():
    assert titles(select(tree())) == ["Course", "Week 1", "Draft notes", "Exercises", "Week 2", "Lab"]


@pytest.mark.parametrize("depth, expected", [
    (0, ["Course"]),
    (1, ["Course", "Week 1", "Week 2"]),
])
def test_depth(depth, expected):
    assert titles(select(tree(), depth=depth)) == expected


def test_exclude_by_title_path_and_id():
    assert titles(select(tree(), exclude=["draft*"])) == ["Course", "Week 1", "Exercises", "Week 2", "Lab"]
    assert titles(select(tree(), exclude=["Course/Week 2"])) == ["Course", "Week 1", "Draft notes", "Exercises"]
    assert titles(select(tree(), exclude=["w1"])) == ["Course", "Week 2", "Lab"]


def test_tags_keep_matching_descendants():
    # Week 2 has no tag, but its Lab does: Lab is kept, Week 2 is skipped.
    assert titles(select(tree(), tags=["EXAM"])) == ["Course", "Week 1", "Exercises", "Lab"]


def test_select_does_not_modify_the_original():
    original = tree()
    select(original, depth=0)
    assert len(original.children) == 2


def test_config_selectors(tmp_path):
    path = tmp_path / "bundles.yml"
    path.write_text("""
bundles:
  - output: a.pdf
    exclude: "Draft*"
    include:
      - page: root
        depth: 2
        exclude: [Lab]
        tags: exam
      - page: other
        children: false
""")
    first, second = load_config(path).bundles[0].include
    assert (first.depth, first.exclude, first.tags) == (2, ["Draft*", "Lab"], ["exam"])
    assert (second.depth, second.exclude) == (0, ["Draft*"])


@pytest.mark.parametrize("item, error", [
    ("depth: -1", "`depth` must be a number"),
    ("depth: all", "`depth` must be a number"),
    ("tags: {a: 1}", "expected a text or a list"),
])
def test_invalid_selectors(tmp_path, item, error):
    path = tmp_path / "bundles.yml"
    path.write_text(f"bundles:\n  - output: a.pdf\n    include:\n      - page: x\n        {item}\n")
    with pytest.raises(ConfigError, match=error):
        load_config(path)
