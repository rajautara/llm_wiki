#!/usr/bin/env python3
"""
LLM Wiki Engine v2 — Pure Python / OpenAI-Compatible API

Design principle:
    LLM suggests -> Python validates -> Python writes safely

Environment:
    OPENAI_API_KEY       Required for ingest/query/deep lint
    OPENAI_BASE_URL      Optional, default: https://api.openai.com/v1
    WIKI_MODEL           Optional, default: gpt-4o
    WIKI_VERIFY_SSL      Optional, default: true. Set false/0/off/no to disable.

Recommended dependencies:
    pip install openai pyyaml httpx pymupdf

Optional:
    pymupdf is only required for PDF ingestion.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openai import OpenAI


# ============================================================
# CONFIG
# ============================================================

DEFAULT_MODEL = os.getenv("WIKI_MODEL", "gpt-4o")
API_KEY = os.getenv("OPENAI_API_KEY")
BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
VERIFY_SSL = os.getenv("WIKI_VERIFY_SSL", "true").lower() not in {
    "false",
    "0",
    "off",
    "no",
}

RAW_DIR = Path("raw")
WIKI_DIR = Path("wiki")
BACKUP_DIR = WIKI_DIR / ".backups"
SCHEMA_FILE = Path("schema.md")

DATE_FMT = "%Y-%m-%d"
MAX_SOURCE_CHARS = 100_000
MAX_EXISTING_FULL_PAGES = 8
MAX_EXISTING_FULL_CHARS_PER_PAGE = 12_000

VALID_PAGE_TYPES = {"entity", "concept", "source", "index", "note"}
REQUIRED_FRONTMATTER = {"type", "sources", "created", "updated", "tags"}

ENTITY_REQUIRED_SECTIONS = [
    "## Summary",
    "## Key Claims / Facts",
    "## Related Entities",
    "## Source Notes",
]

CONCEPT_REQUIRED_SECTIONS = [
    "## Definition",
    "## Intuition",
    "## How It Works",
    "## Trade-offs",
    "## Related Concepts",
    "## Source Notes",
]

SOURCE_REQUIRED_SECTIONS = [
    "## Source Summary",
    "## Extracted Entities",
    "## Extracted Concepts",
    "## Source Notes",
]


DEFAULT_SCHEMA = """# LLM Wiki Schema v2.0

## 1. Architecture

| Layer | Path | Owner | Rule |
|-------|------|-------|------|
| Raw Sources | `raw/` | Human | Immutable. Agent reads only. Never edit. |
| Wiki Pages | `wiki/` | LLM + Python validator | Agent proposes; Python validates and writes. |
| Schema | `schema.md` | Human | Rules for page format, writing, linting, and safety. |

The engine must follow this flow:

```text
LLM suggests -> Python validates -> Python writes safely
```

The LLM is never trusted as a filesystem operator.

---

## 2. Page Naming Rules

Every wiki page lives under `wiki/`.

Filenames:
- Must end with `.md`.
- Must not contain path traversal such as `../`.
- Must use the page title as the filename stem.
- Recommended format: `Title-Case-With-Hyphens.md`.

Examples:
- `Self-Attention.md`
- `Ashish-Vaswani.md`
- `Transformer-Architecture.md`

The page title is the filename without `.md`.

Use that exact title in all wiki links:

```markdown
[[Self-Attention]]
[[Transformer-Architecture]]
```

Do not use relative markdown links such as:

```markdown
[Self Attention](./Self-Attention.md)
```

---

## 3. Frontmatter Rules

Every page must start with YAML frontmatter:

```yaml
---
type: entity
sources: ["filename.pdf"]
created: 2026-04-26
updated: 2026-04-26
tags: []
---
```

Required fields:
- `type`
- `sources`
- `created`
- `updated`
- `tags`

Allowed `type` values:
- `entity`
- `concept`
- `source`
- `note`

---

## 4. Page Types

### 4.1 Entity Page

Use for a person, organization, product, paper, dataset, tool, company, or concrete object.

Required structure:

```markdown
# Page Title

## Summary
2–4 sentences. What this entity is and why it matters.

## Key Claims / Facts
- Verifiable fact.
- Relationship to [[Related-Concept]].

## Related Entities
- [[Entity-A]]
- [[Entity-B]]

## Source Notes
> From `filename.pdf`: Supporting quote or paraphrase.
```

---

### 4.2 Concept Page

Use for an idea, method, theory, algorithm, pattern, or reusable abstraction.

Required structure:

```markdown
# Concept Name

## Definition
1–2 concise sentences.

## Intuition
Explain it like the reader is a smart intern. Use analogy if helpful.

## How It Works
Step-by-step or mechanism explanation.

## Trade-offs
| Pros | Cons |
|------|------|
| Benefit | Limitation |

## Related Concepts
- [[Concept-A]]
- [[Concept-B]]

## Source Notes
> From `filename.pdf`: Supporting evidence or origin story.
```

---

### 4.3 Source Page

Use for a book, paper, report, long PDF, article, transcript, or imported file that deserves its own source-level page.

Required structure:

```markdown
# Source Title

## Source Summary
Short summary of the source.

## Extracted Entities
- [[Entity-A]]

## Extracted Concepts
- [[Concept-A]]

## Source Notes
> From `filename.pdf`: Important source-level detail.
```

---

### 4.4 Index Page

The index is auto-generated at:

```text
wiki/index.md
```

Do not manually edit it.
The LLM must never write `wiki/index.md` directly.

---

## 5. Ingest Rules

When ingesting a source:

1. Read the source content.
2. Identify new entities, concepts, and source-level pages.
3. Update existing pages only when the source adds useful information.
4. Use full page content when updating existing pages.
5. Append new source notes instead of erasing old notes.
6. Update the `updated` frontmatter date.
7. Add the exact source filename to `sources`.
8. Cross-link aggressively.
9. New pages should have at least 2 outgoing `[[Links]]` when possible.
10. If a fact contradicts existing wiki content, flag it inline:

```markdown
> ⚠️ Contradiction: Existing claim says X, but `source.pdf` says Y.
```

Never silently overwrite a contradiction.

---

## 6. Query Rules

When answering a user question:

1. Read the index and page summaries.
2. Select the most relevant 3–5 pages.
3. Read the full content of those pages.
4. Answer using only wiki content.
5. Cite using `[[Page Title]]`.
6. If the wiki lacks the answer, say so clearly.
7. If the answer reveals a reusable concept not yet in the wiki, append:

```text
💡 New concept suggestion: <Concept Name>
```

---

## 7. Lint Rules

Lint should report:

1. Total pages.
2. Missing frontmatter.
3. Invalid frontmatter.
4. Missing required sections.
5. Missing links.
6. Orphan pages.
7. Contradiction flags.
8. Pages stale for 90+ days.
9. Duplicate or near-duplicate page titles.
10. Pages with too few outgoing links.

Lint may suggest fixes, but must not delete pages.

---

## 8. Archive Rules

Never permanently delete a page.

Move obsolete pages to:

```text
wiki/archive/
```

Preserve frontmatter and add:

```yaml
archived: true
reason: "Merged into [[New-Page]]"
```

---

## 9. Prohibited Actions

1. Never write to `raw/`.
2. Never delete wiki pages permanently.
3. Never allow paths outside `wiki/`.
4. Never let the LLM write `wiki/index.md`.
5. Never invent facts not present in the source during ingest.
6. Never ignore contradiction flags.
7. Never write invalid YAML frontmatter.
"""


# ============================================================
# DATA TYPES
# ============================================================

@dataclass
class WikiPage:
    path: Path
    title: str
    frontmatter: Dict[str, Any]
    content: str

    @property
    def page_type(self) -> str:
        return str(self.frontmatter.get("type", "note"))

    @property
    def summary(self) -> str:
        body = strip_frontmatter(self.content)[1]
        body = re.sub(r"\s+", " ", body).strip()
        return body[:500]


# ============================================================
# BASIC HELPERS
# ============================================================

def today_str() -> str:
    return datetime.now(timezone.utc).strftime(DATE_FMT)


def ensure_dependencies_for_yaml() -> Any:
    try:
        import yaml
        return yaml
    except ImportError as exc:
        raise ImportError(
            "YAML frontmatter requires PyYAML. Install it with: pip install pyyaml"
        ) from exc


def get_client() -> OpenAI:
    if not API_KEY:
        raise RuntimeError("Set OPENAI_API_KEY environment variable.")

    client_kwargs: Dict[str, Any] = {
        "api_key": API_KEY,
        "base_url": BASE_URL,
    }

    if not VERIFY_SSL:
        import httpx

        print("[!] WARNING: SSL certificate verification is DISABLED.", file=sys.stderr)
        client_kwargs["http_client"] = httpx.Client(verify=False)

    return OpenAI(**client_kwargs)


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def normalize_title_from_filename(path: Path) -> str:
    return path.stem


def extract_wiki_links(text: str) -> List[str]:
    return re.findall(r"\[\[([^\[\]\n]+?)\]\]", text)


def strip_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(pattern, text, re.DOTALL)

    if not match:
        return {}, text

    yaml = ensure_dependencies_for_yaml()
    frontmatter = yaml.safe_load(match.group(1)) or {}

    if not isinstance(frontmatter, dict):
        raise ValueError("Frontmatter must be a YAML mapping/object.")

    return frontmatter, match.group(2)


def dump_frontmatter(frontmatter: Dict[str, Any], content: str) -> str:
    yaml = ensure_dependencies_for_yaml()
    fm = yaml.safe_dump(
        frontmatter,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )

    return f"---\n{fm}---\n\n{content.strip()}\n"


def first_h1(content: str) -> Optional[str]:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def trim_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text

    return text[:max_chars] + "\n\n[Trimmed for context]"


def tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z0-9][a-zA-Z0-9\-]{2,}", text.lower())


# ============================================================
# PATH SAFETY
# ============================================================

def wiki_root_resolved() -> Path:
    return WIKI_DIR.resolve()


def sanitize_wiki_path(path_str: str) -> Path:
    """
    Ensure LLM-generated paths stay inside wiki/.

    Allows:
        wiki/entities/Page.md
        entities/Page.md
        Page.md

    Rejects:
        ../evil.md
        wiki/../../evil.md
        absolute paths
        non-markdown files
        wiki/index.md
    """
    if not path_str or not isinstance(path_str, str):
        raise ValueError("Page path must be a non-empty string.")

    raw = Path(path_str)

    if raw.is_absolute():
        raise ValueError(f"Absolute paths are not allowed: {path_str}")

    parts = list(raw.parts)

    if parts and parts[0] == WIKI_DIR.name:
        parts = parts[1:]

    if not parts:
        raise ValueError(f"Invalid empty wiki path: {path_str}")

    candidate = Path(*parts)
    final_path = (wiki_root_resolved() / candidate).resolve()

    if not final_path.is_relative_to(wiki_root_resolved()):
        raise ValueError(f"Unsafe wiki path rejected: {path_str}")

    if final_path.suffix.lower() != ".md":
        raise ValueError(f"Wiki page must end with .md: {path_str}")

    if final_path == (wiki_root_resolved() / "index.md").resolve():
        raise ValueError("LLM is not allowed to write wiki/index.md directly.")

    if any(part.startswith(".") for part in final_path.relative_to(wiki_root_resolved()).parts):
        raise ValueError(f"Hidden wiki paths are not allowed: {path_str}")

    return final_path


def ensure_source_readable(path: Path, allow_outside_raw: bool = False) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"Source not found: {path}")

    if not path.is_file():
        raise ValueError(f"Source must be a file: {path}")

    resolved = path.resolve()

    if not allow_outside_raw:
        raw_root = RAW_DIR.resolve()

        if RAW_DIR.exists() and not resolved.is_relative_to(raw_root):
            raise ValueError(
                f"Source must be inside raw/: {path}\n"
                "Use --allow-outside-raw if you intentionally want to ingest this file."
            )

    return resolved


# ============================================================
# SOURCE EXTRACTION
# ============================================================

def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        try:
            import fitz
        except ImportError as exc:
            raise ImportError("PDF ingestion requires: pip install pymupdf") from exc

        with fitz.open(path) as doc:
            return "\n\n".join(page.get_text() for page in doc)

    if suffix in {
        ".md",
        ".txt",
        ".rst",
        ".html",
        ".htm",
        ".csv",
        ".json",
        ".yaml",
        ".yml",
    }:
        return path.read_text(encoding="utf-8", errors="replace")

    return path.read_text(encoding="utf-8", errors="ignore")


# ============================================================
# PAGE LOADING
# ============================================================

def load_wiki_page(path: Path) -> WikiPage:
    text = read_text_file(path)
    fm, _body = strip_frontmatter(text)

    return WikiPage(
        path=path,
        title=normalize_title_from_filename(path),
        frontmatter=fm,
        content=text,
    )


def get_existing_pages() -> List[WikiPage]:
    if not WIKI_DIR.exists():
        return []

    pages: List[WikiPage] = []

    for path in sorted(WIKI_DIR.rglob("*.md")):
        if BACKUP_DIR in path.parents:
            continue

        if path.name == "index.md" and path.parent == WIKI_DIR:
            continue

        try:
            pages.append(load_wiki_page(path))
        except Exception as exc:
            pages.append(
                WikiPage(
                    path=path,
                    title=normalize_title_from_filename(path),
                    frontmatter={"type": "note"},
                    content=f"[Unreadable page: {exc}]",
                )
            )

    return pages


def page_summaries_for_prompt(pages: Sequence[WikiPage]) -> List[Dict[str, Any]]:
    summaries = []

    for p in pages:
        summaries.append(
            {
                "path": str(p.path),
                "title": p.title,
                "type": p.page_type,
                "sources": p.frontmatter.get("sources", []),
                "updated": p.frontmatter.get("updated"),
                "summary": p.summary,
            }
        )

    return summaries


def rank_pages_by_overlap(
    query_text: str,
    pages: Sequence[WikiPage],
    limit: int,
) -> List[WikiPage]:
    query_terms = Counter(tokenize(query_text))
    scored: List[Tuple[int, WikiPage]] = []

    for p in pages:
        haystack = f"{p.title} {p.summary} {' '.join(map(str, p.frontmatter.get('tags', [])))}"
        page_terms = Counter(tokenize(haystack))

        score = sum(min(query_terms[t], page_terms[t]) for t in query_terms)

        if p.title.lower() in query_text.lower():
            score += 10

        scored.append((score, p))

    scored.sort(key=lambda item: item[0], reverse=True)

    return [p for score, p in scored[:limit] if score > 0]


def existing_context_for_ingest(source_text: str, pages: Sequence[WikiPage]) -> str:
    summaries = page_summaries_for_prompt(pages)

    ranked = rank_pages_by_overlap(
        source_text,
        pages,
        MAX_EXISTING_FULL_PAGES,
    )

    full_pages = []

    for p in ranked:
        full_pages.append(
            {
                "path": str(p.path),
                "title": p.title,
                "type": p.page_type,
                "content": trim_text(
                    read_text_file(p.path),
                    MAX_EXISTING_FULL_CHARS_PER_PAGE,
                ),
            }
        )

    return json.dumps(
        {
            "all_page_summaries": summaries,
            "full_relevant_existing_pages": full_pages,
        },
        ensure_ascii=False,
        indent=2,
    )


# ============================================================
# VALIDATION
# ============================================================

def validate_frontmatter(
    frontmatter: Dict[str, Any],
    source_filename: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(frontmatter, dict):
        raise ValueError("frontmatter must be an object.")

    fm = dict(frontmatter)
    now = today_str()

    fm.setdefault("created", now)
    fm.setdefault("updated", now)
    fm.setdefault("tags", [])

    page_type = fm.get("type")

    if page_type not in VALID_PAGE_TYPES:
        raise ValueError(
            f"Invalid page type: {page_type!r}. "
            f"Valid types: {sorted(VALID_PAGE_TYPES)}"
        )

    if "sources" not in fm:
        fm["sources"] = []

    if not isinstance(fm["sources"], list):
        raise ValueError("frontmatter.sources must be a list.")

    if source_filename and source_filename not in fm["sources"]:
        fm["sources"].append(source_filename)

    if not isinstance(fm["tags"], list):
        raise ValueError("frontmatter.tags must be a list.")

    missing = REQUIRED_FRONTMATTER - set(fm)

    if missing:
        raise ValueError(f"Missing required frontmatter fields: {sorted(missing)}")

    return fm


def required_sections_for_type(page_type: str) -> List[str]:
    if page_type == "entity":
        return ENTITY_REQUIRED_SECTIONS

    if page_type == "concept":
        return CONCEPT_REQUIRED_SECTIONS

    if page_type == "source":
        return SOURCE_REQUIRED_SECTIONS

    return []


def validate_content_sections(content: str, page_type: str) -> List[str]:
    missing = []

    for section in required_sections_for_type(page_type):
        if section not in content:
            missing.append(section)

    return missing


def validate_llm_page(
    page: Dict[str, Any],
    source_filename: str,
) -> Tuple[Path, Dict[str, Any], str]:
    if not isinstance(page, dict):
        raise ValueError("Each page must be an object.")

    for key in ("path", "frontmatter", "content"):
        if key not in page:
            raise ValueError(f"Page missing required key: {key}")

    path = sanitize_wiki_path(str(page["path"]))
    fm = validate_frontmatter(
        page["frontmatter"],
        source_filename=source_filename,
    )

    content = str(page["content"]).strip()

    if not content:
        raise ValueError(f"{path}: content is empty.")

    h1 = first_h1(content)

    if not h1:
        raise ValueError(
            f"{path}: content must begin with an H1 heading, e.g. '# Page Title'."
        )

    expected_title = path.stem

    if h1 != expected_title:
        raise ValueError(
            f"{path}: H1 title must match filename stem exactly. "
            f"Expected '# {expected_title}', got '# {h1}'."
        )

    missing_sections = validate_content_sections(content, str(fm["type"]))

    if missing_sections:
        raise ValueError(f"{path}: missing required sections: {missing_sections}")

    return path, fm, content


def validate_llm_result(result: Dict[str, Any]) -> None:
    if not isinstance(result, dict):
        raise ValueError("LLM output must be a JSON object.")

    if "pages" not in result:
        raise ValueError("LLM output must contain a 'pages' field.")

    if not isinstance(result["pages"], list):
        raise ValueError("'pages' must be a list.")

    if "summary" in result and not isinstance(result["summary"], str):
        raise ValueError("'summary' must be a string when provided.")


# ============================================================
# BACKUP + INDEX
# ============================================================

def backup_page(path: Path) -> Optional[Path]:
    if not path.exists():
        return None

    relative = path.resolve().relative_to(wiki_root_resolved())
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUP_DIR / relative.parent / f"{relative.stem}.{timestamp}{relative.suffix}"

    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, backup_path)

    return backup_path


def regenerate_index() -> None:
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    pages = get_existing_pages()

    groups: Dict[str, List[WikiPage]] = defaultdict(list)

    for p in pages:
        groups[p.page_type].append(p)

    preferred_order = ["source", "entity", "concept", "note"]

    lines = [
        "# Wiki Index",
        "",
        "Auto-generated. Do not edit manually.",
        "",
        f"Last updated: {today_str()}",
        "",
    ]

    for page_type in preferred_order:
        group = groups.get(page_type, [])

        if not group:
            continue

        label = {
            "source": "Sources",
            "entity": "Entities",
            "concept": "Concepts",
            "note": "Other Notes",
        }.get(page_type, page_type.title())

        lines.append(f"## {label}")
        lines.append("")

        for p in sorted(group, key=lambda item: item.title.lower()):
            rel = p.path.resolve().relative_to(wiki_root_resolved())
            lines.append(f"- [[{p.title}]] — `{rel}`")

        lines.append("")

    write_text_file(WIKI_DIR / "index.md", "\n".join(lines).rstrip() + "\n")


# ============================================================
# OPENAI HELPERS
# ============================================================

def chat_json(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.2,
) -> Dict[str, Any]:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=temperature,
    )

    content = resp.choices[0].message.content or ""

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model did not return valid JSON:\n{content}") from exc


def chat_text(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.3,
) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
    )

    return resp.choices[0].message.content or ""


# ============================================================
# COMMAND: INIT
# ============================================================

def cmd_init(force_schema: bool = False) -> None:
    RAW_DIR.mkdir(exist_ok=True)
    WIKI_DIR.mkdir(exist_ok=True)

    (WIKI_DIR / "entities").mkdir(exist_ok=True)
    (WIKI_DIR / "concepts").mkdir(exist_ok=True)
    (WIKI_DIR / "sources").mkdir(exist_ok=True)
    (WIKI_DIR / "archive").mkdir(exist_ok=True)
    BACKUP_DIR.mkdir(exist_ok=True)

    if force_schema or not SCHEMA_FILE.exists():
        write_text_file(SCHEMA_FILE, DEFAULT_SCHEMA)

    regenerate_index()

    print("Initialized LLM Wiki Engine structure.")
    print("")
    print("Created:")
    print(f"  - {RAW_DIR}/")
    print(f"  - {WIKI_DIR}/")
    print(f"  - {SCHEMA_FILE}")
    print("")
    print("Next:")
    print("  export OPENAI_API_KEY='...'")
    print("  python wiki.py ingest raw/your_file.pdf")


# ============================================================
# COMMAND: INGEST
# ============================================================

def cmd_ingest(
    source_path: Path,
    model: str,
    allow_outside_raw: bool = False,
    dry_run: bool = False,
) -> None:
    client = get_client()
    source_path = ensure_source_readable(
        source_path,
        allow_outside_raw=allow_outside_raw,
    )

    source_filename = source_path.name
    source_text = extract_text(source_path)

    if len(source_text) > MAX_SOURCE_CHARS:
        print(
            f"[!] Source truncated: {len(source_text)} -> {MAX_SOURCE_CHARS} chars",
            file=sys.stderr,
        )
        source_text = source_text[:MAX_SOURCE_CHARS] + "\n\n[Content truncated]"

    schema = read_text_file(SCHEMA_FILE) if SCHEMA_FILE.exists() else DEFAULT_SCHEMA
    existing_pages = get_existing_pages()
    existing_context = existing_context_for_ingest(source_text, existing_pages)

    prompt = f"""You are a careful wiki compiler.

Your job:
- Read the source.
- Create new pages or update existing pages.
- Return JSON only.
- Do not invent facts.
- Do not modify raw source content.
- Use the schema exactly.

Today's date: {today_str()}

=== WIKI SCHEMA ===
{schema}

=== EXISTING WIKI CONTEXT ===
{existing_context}

=== SOURCE FILE ===
Filename: {source_filename}

=== SOURCE TEXT ===
{source_text}

=== OUTPUT JSON SHAPE ===
Return exactly this JSON object:

{{
  "pages": [
    {{
      "path": "wiki/concepts/Example-Concept.md",
      "frontmatter": {{
        "type": "concept",
        "sources": ["{source_filename}"],
        "created": "{today_str()}",
        "updated": "{today_str()}",
        "tags": []
      }},
      "content": "# Example-Concept\\n\\n## Definition\\n...\\n\\n## Intuition\\n...\\n\\n## How It Works\\n...\\n\\n## Trade-offs\\n| Pros | Cons |\\n|------|------|\\n| ... | ... |\\n\\n## Related Concepts\\n- [[Another-Concept]]\\n\\n## Source Notes\\n> From `{source_filename}`: ..."
    }}
  ],
  "summary": "Brief summary of what was created or updated."
}}

Hard rules:
- Path must be inside wiki/.
- Never write wiki/index.md.
- Filename stem must match H1 exactly.
  Example: path "wiki/concepts/Self-Attention.md" must have content starting with "# Self-Attention".
- Use entity, concept, source, or note only.
- Entity pages must include all entity sections from schema.
- Concept pages must include all concept sections from schema.
- Source pages must include all source sections from schema.
- For existing pages, return the full updated content, not a diff.
- Preserve useful existing content when updating.
- Append new source notes; do not erase old source notes.
- Flag contradictions inline using: > ⚠️ Contradiction: ...
- Use [[Exact-Page-Title]] links.
"""

    result = chat_json(
        client=client,
        model=model,
        system="You are a precise wiki compiler. Output only valid JSON.",
        user=prompt,
        temperature=0.2,
    )

    validate_llm_result(result)

    validated_pages: List[Tuple[Path, Dict[str, Any], str]] = []
    errors: List[str] = []

    for i, page in enumerate(result["pages"], start=1):
        try:
            validated_pages.append(
                validate_llm_page(
                    page,
                    source_filename=source_filename,
                )
            )
        except Exception as exc:
            errors.append(f"Page #{i}: {exc}")

    if errors:
        print("Ingest aborted. Model output failed validation:", file=sys.stderr)

        for err in errors:
            print(f"  - {err}", file=sys.stderr)

        raise SystemExit(1)

    if dry_run:
        print("Dry run passed validation. Pages that would be written:")

        for path, fm, _content in validated_pages:
            print(f"  - {path} ({fm.get('type')})")

        print(f"\nSummary: {result.get('summary', 'Done.')}")
        return

    for path, fm, content in validated_pages:
        backup = backup_page(path)
        full = dump_frontmatter(fm, content)

        write_text_file(path, full)

        if backup:
            print(f"  ✓ {path}  (backup: {backup})")
        else:
            print(f"  ✓ {path}")

    regenerate_index()
    print(f"\nIngest complete: {result.get('summary', 'Done.')}")


# ============================================================
# COMMAND: QUERY
# ============================================================

def select_relevant_pages_with_llm(
    client: OpenAI,
    model: str,
    question: str,
    pages: Sequence[WikiPage],
    max_titles: int = 5,
) -> List[str]:
    summaries = page_summaries_for_prompt(pages)

    prompt = f"""Given the user question, select the most relevant wiki page titles.

Question:
{question}

Wiki page summaries:
{json.dumps(summaries, ensure_ascii=False, indent=2)}

Return JSON only:
{{
  "relevant_titles": ["Title-One", "Title-Two"]
}}

Rules:
- Max {max_titles} titles.
- Use exact titles only.
- If none are relevant, return an empty list.
"""

    data = chat_json(
        client=client,
        model=model,
        system="You select relevant wiki pages. Output only JSON.",
        user=prompt,
        temperature=0.1,
    )

    titles = data.get("relevant_titles", [])

    if not isinstance(titles, list):
        return []

    all_titles = {p.title for p in pages}

    return [str(t) for t in titles if str(t) in all_titles][:max_titles]


def cmd_query(
    question: str,
    model: str,
    no_llm_select: bool = False,
) -> None:
    client = get_client()
    pages = get_existing_pages()

    if not pages:
        print("Wiki is empty. Run: python wiki.py ingest raw/your_file.pdf")
        return

    if no_llm_select:
        relevant_pages = rank_pages_by_overlap(question, pages, limit=5)
        relevant_titles = [p.title for p in relevant_pages]
    else:
        relevant_titles = select_relevant_pages_with_llm(
            client,
            model,
            question,
            pages,
            max_titles=5,
        )
        relevant_pages = [p for p in pages if p.title in set(relevant_titles)]

    if not relevant_pages:
        print("No relevant pages found. Try ingesting related sources.")
        return

    schema = read_text_file(SCHEMA_FILE) if SCHEMA_FILE.exists() else DEFAULT_SCHEMA

    context_blocks = []

    for p in relevant_pages:
        context_blocks.append(f"=== {p.title} ===\n{read_text_file(p.path)}")

    context = "\n\n".join(context_blocks)

    prompt = f"""You are answering from a personal knowledge wiki.

=== SCHEMA ===
{schema}

=== RELEVANT WIKI PAGES ===
{context}

=== USER QUESTION ===
{question}

Instructions:
- Answer using ONLY the information in the wiki pages above.
- Cite using [[Page Title]].
- If the wiki does not contain the answer, say so clearly.
- Do not use outside knowledge.
- If the answer reveals a new reusable concept worth saving, end with:
  💡 New concept suggestion: <Concept Name>
"""

    answer = chat_text(
        client=client,
        model=model,
        system="You are a precise research assistant who only uses the provided wiki context.",
        user=prompt,
        temperature=0.3,
    )

    print(answer)


# ============================================================
# COMMAND: LINT
# ============================================================

def parse_date(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, datetime):
        return value

    text = str(value).strip()

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass

    return None


def find_duplicate_like_titles(titles: Sequence[str]) -> List[Tuple[str, str]]:
    normalized: Dict[str, str] = {}
    duplicates: List[Tuple[str, str]] = []

    for title in titles:
        key = re.sub(r"[^a-z0-9]+", "", title.lower())

        if key in normalized and normalized[key] != title:
            duplicates.append((normalized[key], title))
        else:
            normalized[key] = title

    return duplicates


def lint_pages() -> Dict[str, Any]:
    pages = get_existing_pages()
    all_titles = {p.title for p in pages}

    incoming: Dict[str, List[str]] = defaultdict(list)

    issues = {
        "total_pages": len(pages),
        "missing_frontmatter": [],
        "invalid_frontmatter": [],
        "missing_required_sections": [],
        "missing_links": [],
        "orphan_pages": [],
        "contradictions": [],
        "stale_pages": [],
        "duplicate_like_titles": [],
        "too_few_outgoing_links": [],
        "h1_filename_mismatches": [],
    }

    for p in pages:
        try:
            text = read_text_file(p.path)
            fm, body = strip_frontmatter(text)
        except Exception as exc:
            issues["invalid_frontmatter"].append(f"{p.path}: {exc}")
            continue

        if not fm:
            issues["missing_frontmatter"].append(str(p.path))

        try:
            validate_frontmatter(fm)
        except Exception as exc:
            issues["invalid_frontmatter"].append(f"{p.path}: {exc}")

        h1 = first_h1(body)

        if h1 and h1 != p.title:
            issues["h1_filename_mismatches"].append(
                f"{p.path}: H1 '{h1}' != filename stem '{p.title}'"
            )

        page_type = str(fm.get("type", "note"))
        missing_sections = validate_content_sections(body, page_type)

        if missing_sections:
            issues["missing_required_sections"].append(
                {
                    "page": p.title,
                    "path": str(p.path),
                    "missing": missing_sections,
                }
            )

        links = extract_wiki_links(body)

        for link in links:
            incoming[link].append(p.title)

            if link not in all_titles:
                issues["missing_links"].append(
                    {
                        "from": p.title,
                        "missing": link,
                    }
                )

        if len(set(links)) < 2 and page_type in {"entity", "concept", "source"}:
            issues["too_few_outgoing_links"].append(
                {
                    "page": p.title,
                    "links": sorted(set(links)),
                }
            )

        for line in body.splitlines():
            if "⚠️ Contradiction" in line or "Contradiction:" in line:
                issues["contradictions"].append(f"{p.title}: {line.strip()}")

        updated = parse_date(fm.get("updated"))

        if updated:
            age_days = (datetime.now() - updated).days

            if age_days >= 90:
                issues["stale_pages"].append(
                    {
                        "page": p.title,
                        "updated": str(fm.get("updated")),
                        "age_days": age_days,
                    }
                )

    for p in pages:
        if p.title not in incoming and p.title != "index":
            issues["orphan_pages"].append(
                {
                    "page": p.title,
                    "path": str(p.path),
                }
            )

    issues["duplicate_like_titles"] = [
        {"a": a, "b": b}
        for a, b in find_duplicate_like_titles([p.title for p in pages])
    ]

    return issues


def print_lint_report(report: Dict[str, Any]) -> None:
    print("=== LINT REPORT ===\n")
    print(f"Total pages: {report['total_pages']}")

    sections = [
        ("Missing frontmatter", "missing_frontmatter"),
        ("Invalid frontmatter", "invalid_frontmatter"),
        ("H1 / filename mismatches", "h1_filename_mismatches"),
        ("Missing required sections", "missing_required_sections"),
        ("Missing links", "missing_links"),
        ("Orphan pages", "orphan_pages"),
        ("Too few outgoing links", "too_few_outgoing_links"),
        ("Contradiction flags", "contradictions"),
        ("Stale pages", "stale_pages"),
        ("Duplicate-like titles", "duplicate_like_titles"),
    ]

    for label, key in sections:
        items = report.get(key, [])

        print(f"\n{label}: {len(items)}")

        for item in items:
            if isinstance(item, dict):
                print(f"  - {json.dumps(item, ensure_ascii=False)}")
            else:
                print(f"  - {item}")


def cmd_lint(
    model: str,
    deep: bool = False,
    json_output: bool = False,
) -> None:
    report = lint_pages()

    if json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_lint_report(report)

    if not deep:
        return

    client = get_client()

    sample_pages = get_existing_pages()[:8]
    sample = "\n\n".join(
        f"=== {p.title} ===\n{trim_text(read_text_file(p.path), 1500)}"
        for p in sample_pages
    )

    prompt = f"""Analyze this wiki lint report and page sample.

=== LINT REPORT ===
{json.dumps(report, ensure_ascii=False, indent=2)}

=== PAGE SAMPLE ===
{sample}

Return JSON only:
{{
  "suggested_merges": [],
  "taxonomy_gaps": [],
  "stale_or_weak_pages": [],
  "highest_priority_fixes": []
}}
"""

    try:
        data = chat_json(
            client=client,
            model=model,
            system="You are a wiki quality auditor. Output only JSON.",
            user=prompt,
            temperature=0.2,
        )
    except Exception as exc:
        print(f"\nDeep audit skipped: {exc}")
        return

    print("\n=== LLM AUDIT ===")

    for key, value in data.items():
        print(f"\n{key}:")

        if isinstance(value, list):
            for item in value:
                print(f"  - {item}")
        else:
            print(f"  {value}")


# ============================================================
# COMMAND: ARCHIVE
# ============================================================

def cmd_archive(title: str, reason: str) -> None:
    pages = get_existing_pages()
    matches = [p for p in pages if p.title == title]

    if not matches:
        print(f"No page found with title: {title}")
        return

    page = matches[0]
    text = read_text_file(page.path)
    fm, body = strip_frontmatter(text)

    fm["archived"] = True
    fm["reason"] = reason
    fm["updated"] = today_str()

    archived_path = WIKI_DIR / "archive" / page.path.name

    backup_page(page.path)
    write_text_file(archived_path, dump_frontmatter(fm, body))
    page.path.unlink()

    regenerate_index()

    print(f"Archived [[{title}]] -> {archived_path}")


# ============================================================
# CLI
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LLM Wiki Engine v2")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="Create directory structure")
    p_init.add_argument(
        "--force-schema",
        action="store_true",
        help="Overwrite schema.md with the default schema",
    )

    p_ingest = sub.add_parser("ingest", help="Ingest a raw source")
    p_ingest.add_argument("path", type=Path)
    p_ingest.add_argument("--model", default=DEFAULT_MODEL)
    p_ingest.add_argument(
        "--allow-outside-raw",
        action="store_true",
        help="Allow ingesting a file outside raw/",
    )
    p_ingest.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate model output but do not write files",
    )

    p_query = sub.add_parser("query", help="Query the wiki")
    p_query.add_argument("question", nargs="+")
    p_query.add_argument("--model", default=DEFAULT_MODEL)
    p_query.add_argument(
        "--no-llm-select",
        action="store_true",
        help="Use local keyword overlap instead of an LLM to select relevant pages",
    )

    p_lint = sub.add_parser("lint", help="Audit the wiki")
    p_lint.add_argument("--model", default=DEFAULT_MODEL)
    p_lint.add_argument(
        "--deep",
        action="store_true",
        help="Run an additional LLM audit",
    )
    p_lint.add_argument(
        "--json",
        action="store_true",
        help="Print lint report as JSON",
    )

    sub.add_parser("rebuild-index", help="Regenerate wiki/index.md")

    p_archive = sub.add_parser("archive", help="Archive a page instead of deleting it")
    p_archive.add_argument("title")
    p_archive.add_argument("--reason", required=True)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            cmd_init(force_schema=args.force_schema)

        elif args.command == "ingest":
            cmd_ingest(
                source_path=args.path,
                model=args.model,
                allow_outside_raw=args.allow_outside_raw,
                dry_run=args.dry_run,
            )

        elif args.command == "query":
            cmd_query(
                question=" ".join(args.question),
                model=args.model,
                no_llm_select=args.no_llm_select,
            )

        elif args.command == "lint":
            cmd_lint(
                model=args.model,
                deep=args.deep,
                json_output=args.json,
            )

        elif args.command == "rebuild-index":
            regenerate_index()
            print("Regenerated wiki/index.md")

        elif args.command == "archive":
            cmd_archive(
                title=args.title,
                reason=args.reason,
            )

        else:
            parser.print_help()

    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        raise SystemExit(130)

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
