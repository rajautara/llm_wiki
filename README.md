# LLM Wiki Engine

LLM Wiki Engine is a lightweight, file-based personal knowledge wiki builder powered by an OpenAI-compatible chat API. This repository contains separate Python and TypeScript console app implementations.

The core design principle is:

```text
LLM suggests -> app validates -> app writes safely
```

The LLM is used for extraction, summarization, linking, page selection, and optional quality auditing. The selected implementation remains responsible for filesystem safety, validation, backups, indexing, and command execution.

## Project Variants

- **Python project**: `python/`
- **TypeScript project**: `typescript/`
- **Shared schema reference**: `schema.md`

Each project folder also contains its own `schema.md` and `.env.example` so it can be run independently from that folder.

## Features

- **Source ingestion**: Convert PDFs and text-like files into linked Markdown wiki pages.
- **OpenAI-compatible API support**: Works with OpenAI and compatible providers through `OPENAI_BASE_URL`.
- **Schema-driven writing**: Enforces page types, frontmatter, required sections, naming rules, and link style.
- **Safe filesystem writes**: Rejects unsafe paths, absolute paths, hidden paths, non-Markdown files, and direct LLM writes to `wiki/index.md`.
- **Automatic backups**: Existing pages are backed up before being overwritten.
- **Automatic index generation**: Builds `wiki/index.md` grouped by page type.
- **Wiki querying**: Answers questions using only existing wiki content and cites pages with `[[Page Title]]` links.
- **Linting**: Reports structural, linking, freshness, contradiction, and duplicate-title issues.
- **Archiving instead of deletion**: Moves obsolete pages to `wiki/archive/` with archive metadata.
- **Dry-run ingestion**: Validates model output without writing files.

## Project Structure

Repository layout:

```text
llm_wiki/
├── README.md
├── schema.md
├── python/
│   ├── README.md
│   ├── requirements.txt
│   ├── schema.md
│   ├── tests/
│   └── wiki.py
└── typescript/
    ├── README.md
    ├── package.json
    ├── schema.md
    ├── src/
    │   └── index.ts
    └── tsconfig.json
```

### Directory Responsibilities

- **`raw/`**: Human-managed source files. The engine reads files here but does not modify them.
- **`wiki/`**: Generated and updated Markdown wiki pages.
- **`wiki/entities/`**: Entity pages for people, organizations, tools, products, papers, datasets, and concrete objects.
- **`wiki/concepts/`**: Concept pages for ideas, methods, theories, algorithms, and reusable abstractions.
- **`wiki/sources/`**: Source-level summary pages for imported documents.
- **`wiki/archive/`**: Archived pages. Pages should be archived rather than permanently deleted.
- **`wiki/.backups/`**: Automatic backups made before overwriting existing pages.
- **`wiki/index.md`**: Auto-generated index. Do not edit manually.
- **`schema.md`**: Shared reference schema defining page format, style, validation, and safety rules.
- **`python/`**: Python CLI project.
- **`typescript/`**: Node.js TypeScript CLI project.

## Requirements

- Python 3.9 or newer for `python/`.
- Node.js 20 or newer for `typescript/`.
- An OpenAI-compatible API key is required for commands that call the LLM.

Install dependencies from the implementation folder you want to use:

```bash
cd python
pip install -r requirements.txt
```

```bash
cd typescript
npm install
```

### Dependency Notes

- **`openai`**: Required for LLM-backed commands such as `ingest`, `query`, and `lint --deep`.
- **`yaml`**: Required for YAML frontmatter parsing and writing.
- **`pdf-parse`**: Required for PDF ingestion.
- **`commander`**: Required for the console command interface.
- **`dotenv`**: Required for loading local `.env` files.

## Environment Variables

Configuration can be stored in a local `.env` file. Start by copying the example file:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` and replace placeholder values such as `OPENAI_API_KEY`.

| Variable | Required | Default | Description |
|---|---:|---|---|
| `OPENAI_API_KEY` | Yes for LLM commands | None | API key for OpenAI or an OpenAI-compatible provider. |
| `OPENAI_BASE_URL` | No | `https://api.openai.com/v1` | Base URL for the OpenAI-compatible API. |
| `WIKI_MODEL` | No | `gpt-4o` | Default model used by CLI commands. |
| `WIKI_VERIFY_SSL` | No | `true` | Set to `false`, `0`, `off`, or `no` to disable SSL certificate verification. |
| `WIKI_RAW_DIR` | No | `raw` | Directory containing source files to ingest. |
| `WIKI_OUTPUT_DIR` | No | `wiki` | Directory where generated wiki pages are written. |
| `WIKI_BACKUP_DIR_NAME` | No | `.backups` | Backup directory name inside `WIKI_OUTPUT_DIR`. |
| `WIKI_SCHEMA_FILE` | No | `schema.md` | Schema file used to guide and validate generated pages. |
| `WIKI_DATE_FORMAT` | No | `%Y-%m-%d` | Date format used for frontmatter dates. |
| `WIKI_MAX_SOURCE_CHARS` | No | `100000` | Maximum source text characters sent during ingestion. |
| `WIKI_MAX_EXISTING_FULL_PAGES` | No | `8` | Maximum existing full pages included as ingestion context. |
| `WIKI_MAX_EXISTING_FULL_CHARS_PER_PAGE` | No | `12000` | Maximum characters included per existing full page. |
| `WIKI_MAX_EXISTING_SUMMARIES` | No | `200` | Maximum number of existing-page summaries sent during ingestion. |
| `WIKI_USE_JSON_RESPONSE_FORMAT` | No | `true` | Set to `false` for providers that do not support OpenAI's JSON response format. |
| `WIKI_CHAT_MAX_RETRIES` | No | `2` | Retries on transient chat errors (`0` disables retries). |

### `.env` Example

```env
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
WIKI_MODEL=gpt-4o
WIKI_VERIFY_SSL=true
WIKI_USE_JSON_RESPONSE_FORMAT=true
WIKI_CHAT_MAX_RETRIES=2
WIKI_RAW_DIR=raw
WIKI_OUTPUT_DIR=wiki
WIKI_BACKUP_DIR_NAME=.backups
WIKI_SCHEMA_FILE=schema.md
WIKI_DATE_FORMAT=%Y-%m-%d
WIKI_MAX_SOURCE_CHARS=100000
WIKI_MAX_EXISTING_FULL_PAGES=8
WIKI_MAX_EXISTING_FULL_CHARS_PER_PAGE=12000
WIKI_MAX_EXISTING_SUMMARIES=200
```

The application loads `.env` automatically. Real `.env` files are ignored by Git, while `.env.example` is kept as the safe template.

## Quick Start

Choose an implementation and run commands from that folder.

### Python

```bash
cd python
pip install -r requirements.txt
python wiki.py init
python wiki.py ingest raw/my-paper.pdf --dry-run
```

### TypeScript

```bash
cd typescript
npm install
npm start -- init
npm start -- ingest raw/my-paper.pdf --dry-run
```

Create your local environment file inside the selected project folder:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set `OPENAI_API_KEY`.

## CLI Reference

The examples below use the TypeScript project command format. For Python command examples, see `python/README.md`.

Run the TypeScript CLI from `typescript/` with:

```bash
npm start -- <command> [options]
```

### `init`

Create the recommended directory structure and generate `wiki/index.md`.

```bash
npm start -- init
```

Options:

| Option | Description |
|---|---|
| `--force-schema` | Overwrite `schema.md` with the built-in default schema. |

Example:

```bash
npm start -- init --force-schema
```

Use `--force-schema` carefully because it replaces your current schema file.

### `ingest`

Ingest a source file and create or update wiki pages.

```bash
npm start -- ingest raw/your_file.pdf
```

Options:

| Option | Description |
|---|---|
| `--model <model>` | Override the model for this ingestion run. Defaults to `WIKI_MODEL` or `gpt-4o`. |
| `--allow-outside-raw` | Allow ingesting a file outside `raw/`. |
| `--dry-run` | Validate LLM output and show planned writes without writing files. |

Examples:

```bash
npm start -- ingest raw/paper.pdf --dry-run
npm start -- ingest raw/paper.pdf --model gpt-4o-mini
npm start -- ingest C:/Users/you/Desktop/source.txt --allow-outside-raw
```

#### Supported Source Types

PDF files are read with `pdf-parse`:

```text
.pdf
```

Text-like files are read as UTF-8 text:

```text
.md, .txt, .rst, .html, .htm, .csv, .json, .yaml, .yml
```

Other file extensions are read as text with decoding errors ignored.

#### Ingestion Behavior

During ingestion, the engine:

1. Verifies the source file exists and is readable.
2. Requires the file to be inside `raw/` unless `--allow-outside-raw` is used.
3. Extracts source text.
4. Truncates very large sources to `100,000` characters.
5. Loads existing wiki context.
6. Sends the schema, existing context, and source content to the model.
7. Requires the model to return JSON with proposed pages.
8. Validates every proposed page.
9. Backs up existing pages before overwriting.
10. Writes Markdown files under `wiki/`.
11. Regenerates `wiki/index.md`.

### `query`

Ask a question using only existing wiki content.

```bash
npm start -- query "What is self-attention?"
```

Options:

| Option | Description |
|---|---|
| `--model <model>` | Override the model for this query. |
| `--no-llm-select` | Select relevant pages using local keyword overlap instead of the LLM. |

Examples:

```bash
npm start -- query "Which entities are related to transformers?"
npm start -- query "Summarize the core trade-offs" --no-llm-select
```

Query answers must:

- Use only the selected wiki pages as context.
- Cite pages using `[[Page Title]]`.
- Clearly say when the wiki does not contain the answer.
- Avoid outside knowledge.

### `lint`

Audit the wiki for structural and quality issues.

```bash
npm start -- lint
```

Options:

| Option | Description |
|---|---|
| `--json` | Print the lint report as JSON. |
| `--deep` | Run an additional LLM-based audit over the lint report and a page sample. |
| `--model <model>` | Override the model used by the deep audit. |

Examples:

```bash
npm start -- lint
npm start -- lint --json
npm start -- lint --deep
```

The lint command reports:

- Total pages.
- Missing frontmatter.
- Invalid frontmatter.
- H1 and filename mismatches.
- Missing required sections.
- Missing links.
- Orphan pages.
- Pages with too few outgoing links.
- Contradiction flags.
- Pages stale for 90 or more days.
- Duplicate-like page titles.

### `rebuild-index`

Regenerate the wiki index.

```bash
npm start -- rebuild-index
```

This writes `wiki/index.md` based on current wiki pages and groups them by page type.

### `archive`

Archive a page instead of deleting it.

```bash
npm start -- archive "Page-Title" --reason "Merged into [[New-Page]]"
```

Behavior:

- Finds the live page by exact title (archived pages are skipped).
- Refuses to archive pages that are missing or have invalid frontmatter.
- Adds archive metadata to frontmatter (`archived: true`, `reason`, updated date).
- Moves the page under `wiki/archive/`, preserving its original subdirectory.
- Appends a UTC timestamp to the archived filename if a collision exists.
- Backs up the original page first.
- Regenerates `wiki/index.md` with archived pages listed under a separate `## Archived` section.

## Wiki Page Format

Every page must start with YAML frontmatter:

```yaml
---
type: concept
sources:
  - source-file.pdf
created: 2026-04-26
updated: 2026-04-26
tags: []
---
```

Required frontmatter fields:

- **`type`**: Page type.
- **`sources`**: List of source filenames supporting the page.
- **`created`**: Creation date in `YYYY-MM-DD` format.
- **`updated`**: Last update date in `YYYY-MM-DD` format.
- **`tags`**: List of tags.

Allowed page types:

- **`entity`**: Person, organization, tool, product, company, paper, dataset, or concrete object.
- **`concept`**: Idea, method, algorithm, theory, pattern, or reusable abstraction.
- **`source`**: Source-level page for an imported document.
- **`note`**: General note when the page does not fit another type.

The `wiki/index.md` file is auto-generated and is not assigned a `type`. The LLM is not allowed to write it directly.

## Required Page Sections

### Entity Pages

Entity pages must contain:

- `## Summary`
- `## Key Claims / Facts`
- `## Related Entities`
- `## Source Notes`

### Concept Pages

Concept pages must contain:

- `## Definition`
- `## Intuition`
- `## How It Works`
- `## Trade-offs`
- `## Related Concepts`
- `## Source Notes`

### Source Pages

Source pages must contain:

- `## Source Summary`
- `## Extracted Entities`
- `## Extracted Concepts`
- `## Source Notes`

## Naming and Linking Rules

- Page files must end with `.md`.
- Filename stems must match the H1 exactly.
- Page titles should use `Title-Case-With-Hyphens`.
- Internal links must use wiki-link syntax:

  ```markdown
  [[Exact-Page-Title]]
  ```

- Do not use relative Markdown links between wiki pages:

  ```markdown
  [Page](./Page.md)
  ```

## Safety Model

The application intentionally limits what the LLM can do.

### The LLM Can Suggest

- Page paths under `wiki/`.
- Frontmatter.
- Markdown content.
- Links between pages.
- Updates to existing pages.

### TypeScript Enforces

- Paths must remain inside `wiki/`.
- Paths must be relative.
- Files must be Markdown files.
- Hidden paths are rejected.
- `wiki/index.md` cannot be written directly by the LLM.
- Frontmatter must include required fields.
- Page types must be valid.
- Required sections must exist.
- Filename stems must match H1 headings.
- Source filenames must be preserved in `sources`.

### Backup Policy

Before overwriting an existing page, the engine creates a backup in:

```text
wiki/.backups/
```

This makes ingestion safer when updating existing knowledge.

### Deletion Policy

Pages should not be permanently deleted. Use `archive` instead:

```bash
npm start -- archive "Old-Page" --reason "Merged into [[Better-Page]]"
```

## Typical Workflow

### Build a New Wiki

```bash
npm start -- init
```

Place files into `raw/`, then run:

```bash
npm start -- ingest raw/source.pdf --dry-run
npm start -- ingest raw/source.pdf
npm start -- lint
```

### Add a New Source

```bash
npm start -- ingest raw/new-source.md --dry-run
npm start -- ingest raw/new-source.md
npm start -- rebuild-index
npm start -- lint
```

### Ask Questions

```bash
npm start -- query "What are the main entities in the wiki?"
npm start -- query "Which concepts are connected to retrieval augmented generation?"
```

### Maintain Quality

```bash
npm start -- lint
npm start -- lint --deep
```

Use lint results to fix missing links, weak pages, stale pages, duplicate titles, or missing required sections.

## OpenAI-Compatible Providers

To use a compatible API provider, set these values in `.env`:

```env
OPENAI_API_KEY=your_provider_key
OPENAI_BASE_URL=https://your-provider.example.com/v1
WIKI_MODEL=provider-model-name
```

The provider must support OpenAI-compatible chat completions. JSON-mode support is needed for ingestion, LLM page selection, and deep lint audit because the engine requests JSON objects from the model.

## Troubleshooting

### `Set OPENAI_API_KEY environment variable.`

The command needs an API key. Set `OPENAI_API_KEY` in `.env` before running `ingest`, `query`, or `lint --deep`.

### Missing Node dependencies

Install dependencies:

```bash
npm install
```

### `Source must be inside raw/`

Move the file into `raw/` or intentionally allow an outside source:

```bash
npm start -- ingest path/to/source.txt --allow-outside-raw
```

### `Model did not return valid JSON`

The selected model did not follow the required JSON output format. Try:

- Using a stronger model.
- Re-running the command.
- Reducing source size.
- Checking whether your provider supports JSON response format.

### `Ingest aborted. Model output failed validation`

The model returned pages that failed TypeScript validation. Common causes include:

- Missing required frontmatter fields.
- Invalid page type.
- H1 does not match filename stem.
- Missing required sections.
- Unsafe path.
- Attempted write to `wiki/index.md`.

Try running with `--dry-run`, using a stronger model, or adjusting `schema.md` for clearer instructions.

### SSL Verification Problems

If you are using a local or corporate proxy with custom certificates, you can disable SSL verification in `.env`:

```env
WIKI_VERIFY_SSL=false
```

Use this only when you understand the security implications.

## Best Practices

- Keep original source files in `raw/` unchanged.
- Run `--dry-run` before ingesting important sources.
- Run `lint` after each ingestion batch.
- Review generated pages for factual correctness.
- Keep `schema.md` strict and explicit.
- Prefer archiving pages instead of deleting them.
- Use exact `[[Page-Title]]` links.
- Keep pages compact, verifiable, and source-backed.
- Preserve useful source notes when updating pages.
- Flag contradictions instead of silently resolving them.

## Limitations

- The engine depends on the selected model following instructions and returning valid JSON.
- Very large sources are truncated to `100,000` characters before ingestion.
- Query answers are only as complete as the current wiki content.
- `query --no-llm-select` uses keyword overlap, which is less semantic than LLM page selection.
- PDF extraction quality depends on the PDF structure and `pdf-parse` output.
- The tool does not maintain a database; the wiki is file-based Markdown.

## License

No license file is currently included. Add a license before distributing or publishing this project.
