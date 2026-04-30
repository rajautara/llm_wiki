# LLM Wiki Engine - Python

Python implementation of the LLM Wiki Engine console app.

## Setup

```bash
pip install -r requirements.txt
```

Copy the environment template and set `OPENAI_API_KEY`:

```powershell
Copy-Item .env.example .env
```

## Usage

```bash
python wiki.py init
python wiki.py ingest raw/my-paper.pdf --dry-run
python wiki.py ingest raw/my-paper.pdf
python wiki.py query "What are the main concepts?"
python wiki.py lint
```

## Tests

Run from this `python/` directory:

```bash
python -m pytest tests/
```

## Ingest Performance

The ingest pipeline includes several knobs (all optional, sensible defaults) to keep the prompt size bounded and make long generations observable:

| Variable | Default | Purpose |
|---|---|---|
| `WIKI_MAX_FULL_PAGES` | `8` | Caps how many existing pages are embedded in full content in the ingest prompt. |
| `WIKI_INGEST_PRESELECT` | `true` | Use a cheap summaries-only LLM call to pick which existing pages to embed in full. Falls back to keyword overlap on failure. |
| `WIKI_INGEST_STREAM` | `true` | Stream the ingest response so live progress (chars / elapsed) is visible on stderr. |
| `WIKI_INGEST_SKIP_CONNECTION_TEST` | `true` | Skip the pre-flight connection ping during ingest; transport errors surface from the main call. |

See the top-level `README.md` for the complete environment variable reference.
