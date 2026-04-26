# LLM Wiki Engine - TypeScript

Node.js TypeScript implementation of the LLM Wiki Engine console app.

## Setup

```bash
npm install
```

Copy the environment template and set `OPENAI_API_KEY`:

```powershell
Copy-Item .env.example .env
```

## Usage

```bash
npm start -- init
npm start -- ingest raw/my-paper.pdf --dry-run
npm start -- ingest raw/my-paper.pdf
npm start -- query "What are the main concepts?"
npm start -- lint
```

## Build

```bash
npm run typecheck
npm run build
```
