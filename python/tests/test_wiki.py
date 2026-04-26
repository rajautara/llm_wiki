"""
Targeted unit tests for wiki.py.

Run from the repo root:

    python -m pytest tests/

These tests avoid network calls and only exercise pure helpers and validators.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make `wiki.py` importable when running from the repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import wiki  # noqa: E402


@pytest.fixture
def isolated_wiki(tmp_path, monkeypatch):
    """Run each test in a temp wiki directory with no .env interference."""
    raw = tmp_path / "raw"
    out = tmp_path / "wiki"
    raw.mkdir()
    out.mkdir()

    monkeypatch.setattr(wiki, "RAW_DIR", raw)
    monkeypatch.setattr(wiki, "WIKI_DIR", out)
    monkeypatch.setattr(wiki, "BACKUP_DIR", out / ".backups")

    yield tmp_path


# -----------------------------
# sanitize_wiki_path
# -----------------------------

def test_sanitize_wiki_path_strips_wiki_prefix(isolated_wiki):
    p = wiki.sanitize_wiki_path("wiki/concepts/Foo.md")
    assert p.name == "Foo.md"
    assert p.is_relative_to(wiki.wiki_root_resolved())


def test_sanitize_wiki_path_accepts_bare_filename(isolated_wiki):
    p = wiki.sanitize_wiki_path("Foo.md")
    assert p.name == "Foo.md"


def test_sanitize_wiki_path_rejects_absolute(isolated_wiki):
    with pytest.raises(ValueError):
        wiki.sanitize_wiki_path("C:/evil.md" if os.name == "nt" else "/evil.md")


def test_sanitize_wiki_path_rejects_traversal(isolated_wiki):
    with pytest.raises(ValueError):
        wiki.sanitize_wiki_path("../evil.md")


def test_sanitize_wiki_path_rejects_non_markdown(isolated_wiki):
    with pytest.raises(ValueError):
        wiki.sanitize_wiki_path("concepts/Foo.txt")


def test_sanitize_wiki_path_rejects_index(isolated_wiki):
    with pytest.raises(ValueError):
        wiki.sanitize_wiki_path("wiki/index.md")


def test_sanitize_wiki_path_rejects_hidden(isolated_wiki):
    with pytest.raises(ValueError):
        wiki.sanitize_wiki_path("wiki/.backups/Foo.md")


# -----------------------------
# validate_frontmatter
# -----------------------------

def test_validate_frontmatter_rejects_index_type():
    with pytest.raises(ValueError):
        wiki.validate_frontmatter(
            {
                "type": "index",
                "sources": [],
                "created": "2026-01-01",
                "updated": "2026-01-01",
                "tags": [],
            }
        )


def test_validate_frontmatter_appends_source_filename():
    fm = wiki.validate_frontmatter(
        {
            "type": "concept",
            "sources": [],
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "tags": [],
        },
        source_filename="paper.pdf",
    )
    assert fm["sources"] == ["paper.pdf"]


def test_validate_frontmatter_rejects_non_list_sources():
    with pytest.raises(ValueError):
        wiki.validate_frontmatter(
            {
                "type": "concept",
                "sources": "paper.pdf",
                "created": "2026-01-01",
                "updated": "2026-01-01",
                "tags": [],
            }
        )


def test_validate_frontmatter_accepts_all_legal_types():
    for t in ("entity", "concept", "source", "note"):
        wiki.validate_frontmatter(
            {
                "type": t,
                "sources": [],
                "created": "2026-01-01",
                "updated": "2026-01-01",
                "tags": [],
            }
        )


# -----------------------------
# validate_llm_page
# -----------------------------

def _good_concept_page(stem="Self-Attention"):
    content = (
        f"# {stem}\n\n"
        "## Definition\nshort def.\n\n"
        "## Intuition\nidea.\n\n"
        "## How It Works\nsteps.\n\n"
        "## Trade-offs\n| Pros | Cons |\n|------|------|\n| a | b |\n\n"
        "## Related Concepts\n- [[Other]]\n\n"
        "## Source Notes\n> From `paper.pdf`: note.\n"
    )
    return {
        "path": f"wiki/concepts/{stem}.md",
        "frontmatter": {
            "type": "concept",
            "sources": ["paper.pdf"],
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "tags": [],
        },
        "content": content,
    }


def test_validate_llm_page_happy_path(isolated_wiki):
    path, fm, content = wiki.validate_llm_page(_good_concept_page(), "paper.pdf")
    assert path.stem == "Self-Attention"
    assert fm["type"] == "concept"
    assert content.startswith("# Self-Attention")


def test_validate_llm_page_rejects_h1_filename_mismatch(isolated_wiki):
    page = _good_concept_page()
    page["content"] = page["content"].replace("# Self-Attention", "# Different-Title")
    with pytest.raises(ValueError, match="H1 title must match filename"):
        wiki.validate_llm_page(page, "paper.pdf")


def test_validate_llm_page_rejects_missing_section(isolated_wiki):
    page = _good_concept_page()
    page["content"] = page["content"].replace("## How It Works\nsteps.\n\n", "")
    with pytest.raises(ValueError, match="missing required sections"):
        wiki.validate_llm_page(page, "paper.pdf")


# -----------------------------
# find_duplicate_like_titles
# -----------------------------

def test_find_duplicate_like_titles_pairs_all_members():
    dups = wiki.find_duplicate_like_titles(["Self-Attention", "self_attention", "SelfAttention"])
    # 3 normalized-equal titles -> 3 pairs.
    assert len(dups) == 3
    flat = {t for pair in dups for t in pair}
    assert flat == {"Self-Attention", "self_attention", "SelfAttention"}


def test_find_duplicate_like_titles_ignores_unique():
    assert wiki.find_duplicate_like_titles(["Foo", "Bar", "Baz"]) == []


# -----------------------------
# is_archived_page
# -----------------------------

def test_is_archived_page_by_frontmatter(isolated_wiki):
    page = wiki.WikiPage(
        path=isolated_wiki / "wiki" / "concepts" / "Foo.md",
        title="Foo",
        frontmatter={"type": "concept", "archived": True},
        content="",
    )
    assert wiki.is_archived_page(page) is True


def test_is_archived_page_by_path(isolated_wiki):
    p = isolated_wiki / "wiki" / "archive" / "concepts" / "Foo.md"
    p.parent.mkdir(parents=True)
    p.write_text("x", encoding="utf-8")
    page = wiki.WikiPage(
        path=p, title="Foo", frontmatter={"type": "concept"}, content=""
    )
    assert wiki.is_archived_page(page) is True


def test_is_archived_page_negative(isolated_wiki):
    page = wiki.WikiPage(
        path=isolated_wiki / "wiki" / "concepts" / "Foo.md",
        title="Foo",
        frontmatter={"type": "concept"},
        content="",
    )
    assert wiki.is_archived_page(page) is False


# -----------------------------
# parse_date
# -----------------------------

def test_parse_date_returns_utc_aware():
    d = wiki.parse_date("2026-04-26")
    assert d is not None
    assert d.tzinfo is not None


def test_parse_date_handles_unparseable():
    assert wiki.parse_date("not-a-date") is None
