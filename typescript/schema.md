# LLM Wiki Schema v2.0

## 1. Architecture

| Layer | Path | Owner | Rule |
|-------|------|-------|------|
| Raw Sources | `raw/` | Human | Immutable. Agent reads only. Never edit. |
| Wiki Pages | `wiki/` | LLM + TypeScript validator | Agent proposes; TypeScript validates and writes. |
| Schema | `schema.md` | Human | Rules for page format, writing, linting, and safety. |

The engine must follow this flow:

```text
LLM suggests -> TypeScript validates -> TypeScript writes safely
```

The LLM is never trusted as a filesystem operator.

---

## 2. Directory Structure

Recommended structure:

```text
llm-wiki/
├── raw/
│   └── source-files.pdf
├── wiki/
│   ├── index.md
│   ├── entities/
│   ├── concepts/
│   ├── sources/
│   ├── archive/
│   └── .backups/
├── package.json
├── schema.md
├── src/
│   └── index.ts
└── tsconfig.json
```

Rules:
- `raw/` contains original source files.
- `wiki/` contains generated markdown.
- `wiki/archive/` stores obsolete or merged pages.
- `wiki/.backups/` stores automatic backups before overwrite.
- `schema.md` is human-owned and should be edited deliberately.

---

## 3. Page Naming Rules

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

## 4. Frontmatter Rules

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

Field rules:
- `sources` must be a list.
- `tags` must be a list.
- `created` and `updated` use `YYYY-MM-DD`.
- `updated` changes whenever page content changes.
- Never remove old sources unless a human explicitly asks.

---

## 5. Page Types

### 5.1 Entity Page

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

Writing rules:
- Keep facts verifiable.
- Prefer short bullets.
- Link related concepts and entities.
- Do not turn an entity page into a general concept essay.

---

### 5.2 Concept Page

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

Writing rules:
- Define the concept clearly.
- Use one concrete example if helpful.
- Keep trade-offs balanced.
- Prefer concept links over entity links when both are possible.

---

### 5.3 Source Page

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

Writing rules:
- A source page summarizes the imported file.
- It should link outward to extracted entity and concept pages.
- It should not replace detailed concept or entity pages.

---

### 5.4 Note Page

Use only when a page does not fit entity, concept, or source.

Recommended structure:

```markdown
# Page Title

## Notes
Concise notes.

## Links
- [[Related-Page]]

## Source Notes
> From `filename.pdf`: Supporting note.
```

---

### 5.5 Index Page

The index is auto-generated at:

```text
wiki/index.md
```

Do not manually edit it.
The LLM must never write `wiki/index.md` directly.

---

## 6. Ingest Rules

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

## 7. Source Re-Ingestion

If the same filename is ingested again:

1. Treat it as an update.
2. Do not create duplicate pages for the same concepts/entities.
3. Append or revise `## Source Notes`.
4. Preserve older useful notes.
5. Update `updated`.
6. Keep the source filename in `sources`.

---

## 8. Query Rules

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

Do not answer from raw sources during query.
Raw sources are used only during ingest.

---

## 9. Lint Rules

Lint should report:

1. Total pages.
2. Missing frontmatter.
3. Invalid frontmatter.
4. H1 and filename mismatches.
5. Missing required sections.
6. Missing links.
7. Orphan pages.
8. Contradiction flags.
9. Pages stale for 90+ days.
10. Duplicate or near-duplicate page titles.
11. Pages with too few outgoing links.

Lint may suggest fixes, but must not delete pages.

---

## 10. Archive Rules

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

Archive when:
- the page was merged into another page
- the concept is deprecated
- the entity is obsolete
- a human explicitly requests removal

---

## 11. Style Rules

| Rule | Standard |
|------|----------|
| Headings | H1 for page title, H2 for major sections |
| Paragraphs | Max 4 sentences |
| Facts | Use bullets |
| Comparisons | Use tables |
| Quotes | Use blockquotes with source filename |
| Links | Use `[[Exact-Page-Title]]` |
| Math | Inline `$...$`, block `$$...$$` |
| Code | Fenced blocks with language tag |

Tone:
- Clear
- Compact
- Verifiable
- No marketing fluff
- No hallucinated claims

---

## 12. Quality Checklist

Before finishing ingest, verify:

- [ ] Every page has valid YAML frontmatter.
- [ ] Every page has `type`, `sources`, `created`, `updated`, and `tags`.
- [ ] H1 matches filename stem exactly.
- [ ] Required sections exist for the page type.
- [ ] Source filename is included in `sources`.
- [ ] Every new page has useful `## Source Notes`.
- [ ] Contradictions are flagged, not hidden.
- [ ] Internal links use `[[Exact-Page-Title]]`.
- [ ] The LLM did not write `wiki/index.md`.
- [ ] The index can regenerate cleanly.

---

## 13. Prohibited Actions

1. Never write to `raw/`.
2. Never delete wiki pages permanently.
3. Never allow paths outside `wiki/`.
4. Never let the LLM write `wiki/index.md`.
5. Never invent facts not present in the source during ingest.
6. Never ignore contradiction flags.
7. Never write invalid YAML frontmatter.
8. Never use relative markdown links inside wiki pages.
9. Never overwrite existing content without preserving useful source notes.
