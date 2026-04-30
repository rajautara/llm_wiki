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
    monkeypatch.setattr(wiki, "OVERVIEW_FILE", out / "overview.md")
    monkeypatch.setattr(wiki, "LOG_FILE", out / "log.md")

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


def test_sanitize_wiki_path_rejects_log(isolated_wiki):
    with pytest.raises(ValueError):
        wiki.sanitize_wiki_path("wiki/log.md")


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

def test_normalize_title_from_filename_formats_display_title():
    assert wiki.normalize_title_from_filename(Path("machine-learning.md")) == "Machine Learning"
    assert wiki.normalize_title_from_filename(Path("deep_learning.md")) == "Deep Learning"


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


def test_validate_llm_page_accepts_h1_filename_mismatch(isolated_wiki):
    page = _good_concept_page()
    page["content"] = page["content"].replace("# Self-Attention", "# Different-Title")
    path, fm, content = wiki.validate_llm_page(page, "paper.pdf")
    assert path.stem == "Self-Attention"
    assert fm["type"] == "concept"
    assert content.startswith("# Different-Title")


def test_validate_llm_page_rejects_missing_section(isolated_wiki):
    page = _good_concept_page()
    page["content"] = page["content"].replace("## How It Works\nsteps.\n\n", "")
    with pytest.raises(ValueError, match="missing required sections"):
        wiki.validate_llm_page(page, "paper.pdf")


# -----------------------------
# core wiki pages
# -----------------------------

def test_ensure_core_wiki_pages_creates_overview_and_log(isolated_wiki):
    wiki.ensure_core_wiki_pages()
    assert wiki.OVERVIEW_FILE.exists()
    assert wiki.LOG_FILE.exists()
    assert "# Overview" in wiki.OVERVIEW_FILE.read_text(encoding="utf-8")
    assert "# Log" in wiki.LOG_FILE.read_text(encoding="utf-8")


def test_append_ingest_log_entry_and_update_overview(isolated_wiki):
    page_path = wiki.WIKI_DIR / "concepts" / "machine-learning.md"
    page_path.parent.mkdir(parents=True)
    page_path.write_text(
        wiki.dump_frontmatter(
            {
                "type": "concept",
                "sources": ["paper.pdf"],
                "created": "2026-01-01",
                "updated": "2026-01-01",
                "tags": [],
            },
            "# Anything\n\n"
            "## Definition\nshort def.\n\n"
            "## Intuition\nidea.\n\n"
            "## How It Works\nsteps.\n\n"
            "## Trade-offs\n| Pros | Cons |\n|------|------|\n| a | b |\n\n"
            "## Related Concepts\n- [[Other]]\n\n"
            "## Source Notes\n> From `paper.pdf`: note.\n",
        ),
        encoding="utf-8",
    )
    (wiki.RAW_DIR / "paper.pdf").write_text("source", encoding="utf-8")
    wiki.append_ingest_log_entry("paper.pdf", [(page_path, {}, "")], "Added ML notes.")
    wiki.update_overview_page()
    log_text = wiki.LOG_FILE.read_text(encoding="utf-8")
    overview_text = wiki.OVERVIEW_FILE.read_text(encoding="utf-8")
    assert "## [" in log_text
    assert "ingest | paper.pdf" in log_text
    assert "Changed page: [Machine Learning](concepts/machine-learning.md)" in log_text
    assert "Source count: 1" in overview_text
    assert "Page count: 2" in overview_text


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
# existing_context_for_ingest
# -----------------------------

def _write_concept_page(root: Path, stem: str, body_keyword: str) -> wiki.WikiPage:
    path = root / "concepts" / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = wiki.dump_frontmatter(
        {
            "type": "concept",
            "sources": ["src.md"],
            "created": "2026-01-01",
            "updated": "2026-01-01",
            "tags": [],
        },
        f"# {stem}\n\n## Definition\n{body_keyword}.\n\n## Intuition\nx.\n\n"
        "## How It Works\nx.\n\n## Trade-offs\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "## Related Concepts\n- [[Other]]\n\n## Source Notes\n> n.\n",
    )
    path.write_text(text, encoding="utf-8")
    return wiki.load_wiki_page(path)


def test_existing_context_caps_full_pages(isolated_wiki, monkeypatch):
    pages = [_write_concept_page(wiki.WIKI_DIR, f"Topic-{i}", "alpha beta gamma") for i in range(20)]
    monkeypatch.setattr(wiki, "MAX_FULL_PAGES", 3)
    ctx = wiki.existing_context_for_ingest("alpha beta gamma", pages)
    import json as _json
    data = _json.loads(ctx)
    assert data["full_relevant_existing_pages_capped_at"] == 3
    assert len(data["full_relevant_existing_pages"]) <= 3


def test_existing_context_uses_override(isolated_wiki):
    pages = [_write_concept_page(wiki.WIKI_DIR, f"Topic-{i}", "alpha") for i in range(5)]
    chosen = [pages[0], pages[2]]
    ctx = wiki.existing_context_for_ingest(
        "unrelated query text",
        pages,
        full_pages_override=chosen,
        max_full_pages=10,
    )
    import json as _json
    titles = [p["title"] for p in _json.loads(ctx)["full_relevant_existing_pages"]]
    assert titles == [chosen[0].title, chosen[1].title]


# -----------------------------
# parse_date
# -----------------------------

def test_parse_date_returns_utc_aware():
    d = wiki.parse_date("2026-04-26")
    assert d is not None
    assert d.tzinfo is not None


def test_parse_date_handles_unparseable():
    assert wiki.parse_date("not-a-date") is None
