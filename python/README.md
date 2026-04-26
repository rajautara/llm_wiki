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
