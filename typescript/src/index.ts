#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import process from "node:process";
import { Agent } from "node:https";
import { Command } from "commander";
import dotenv from "dotenv";
import OpenAI from "openai";
import YAML from "yaml";
import pdfParse from "pdf-parse";

dotenv.config();

type Frontmatter = Record<string, unknown>;

type WikiPage = {
  path: string;
  title: string;
  frontmatter: Frontmatter;
  content: string;
};

type LlmPage = {
  path?: unknown;
  frontmatter?: unknown;
  content?: unknown;
};

type ValidatedPage = {
  path: string;
  frontmatter: Frontmatter;
  content: string;
};

const DEFAULT_MODEL = process.env.OPENAI_MODEL ?? "gpt-4o";
const API_KEY = process.env.OPENAI_API_KEY;
const BASE_URL = process.env.OPENAI_BASE_URL ?? "https://api.openai.com/v1";
const VERIFY_SSL = !["true", "1", "on", "yes"].includes((process.env.OPENAI_SKIP_SSL_VERIFY ?? "false").toLowerCase());
const RAW_DIR = process.env.WIKI_RAW_DIR ?? "raw";
const WIKI_DIR = process.env.WIKI_OUTPUT_DIR ?? "wiki";
const BACKUP_DIR = path.join(WIKI_DIR, process.env.WIKI_BACKUP_DIR_NAME ?? ".backups");
const SCHEMA_FILE = process.env.WIKI_SCHEMA_FILE ?? "llmwiki_skill.md";
const OVERVIEW_FILE = path.join(WIKI_DIR, "overview.md");
const LOG_FILE = path.join(WIKI_DIR, "log.md");
const DATE_FMT = process.env.WIKI_DATE_FORMAT ?? "%Y-%m-%d";
const MAX_EXISTING_SUMMARIES = envInt("WIKI_MAX_EXISTING_SUMMARIES", 200);
const MAX_FULL_PAGES = envInt("WIKI_MAX_FULL_PAGES", 8);
const INGEST_PRESELECT = envBool("WIKI_INGEST_PRESELECT", true);
const INGEST_STREAM = envBool("WIKI_INGEST_STREAM", true);
const INGEST_SKIP_CONNECTION_TEST = envBool("WIKI_INGEST_SKIP_CONNECTION_TEST", true);
const CHAT_MAX_RETRIES = envInt("WIKI_CHAT_MAX_RETRIES", 2);
const USE_JSON_RESPONSE_FORMAT = !["false", "0", "off", "no"].includes((process.env.WIKI_USE_JSON_RESPONSE_FORMAT ?? "true").toLowerCase());

const VALID_PAGE_TYPES = new Set(["entity", "concept", "source", "note"]);
const REQUIRED_FRONTMATTER = ["type", "sources", "created", "updated", "tags"];
const ENTITY_REQUIRED_SECTIONS = ["## Summary", "## Key Claims / Facts", "## Related Entities", "## Source Notes"];
const CONCEPT_REQUIRED_SECTIONS = ["## Definition", "## Intuition", "## How It Works", "## Trade-offs", "## Related Concepts", "## Source Notes"];
const SOURCE_REQUIRED_SECTIONS = ["## Source Summary", "## Extracted Entities", "## Extracted Concepts", "## Source Notes"];

const DEFAULT_SCHEMA = `# LLM Wiki Schema v2.0

See llmwiki_skill.md for the complete default skill file.
`;

function envInt(name: string, defaultValue: number): number {
  const value = process.env[name];
  if (value === undefined) return defaultValue;
  const parsed = Number.parseInt(value, 10);
  if (Number.isNaN(parsed)) throw new Error(`${name} must be an integer.`);
  return parsed;
}

function envBool(name: string, defaultValue: boolean): boolean {
  const value = process.env[name];
  if (value === undefined) return defaultValue;
  return !["false", "0", "off", "no", ""].includes(value.trim().toLowerCase());
}

function todayStr(): string {
  const now = new Date();
  if (DATE_FMT === "%Y-%m-%d") return now.toISOString().slice(0, 10);
  return now.toISOString().slice(0, 10);
}

function readTextFile(filePath: string): string {
  return fs.readFileSync(filePath, "utf8");
}

function writeTextFile(filePath: string, text: string): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, text, "utf8");
}

function normalizeTitleFromFilename(filePath: string): string {
  return path.basename(filePath, path.extname(filePath)).split(/[-_]+/).filter(Boolean).map((word: string) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase()).join(" ");
}

function extractWikiLinks(text: string): string[] {
  return [...text.matchAll(/\[\[([^\[\]\n]+?)\]\]/g)].map((match) => match[1]);
}

function stripFrontmatter(text: string): [Frontmatter, string] {
  const match = text.match(/^---\s*\r?\n([\s\S]*?)\r?\n---\s*\r?\n([\s\S]*)$/);
  if (!match) return [{}, text];
  const frontmatter = YAML.parse(match[1]) ?? {};
  if (!isRecord(frontmatter)) throw new Error("Frontmatter must be a YAML mapping/object.");
  return [frontmatter, match[2]];
}

function dumpFrontmatter(frontmatter: Frontmatter, content: string): string {
  const fm = YAML.stringify(frontmatter, { sortMapEntries: false });
  return `---\n${fm}---\n\n${content.trim()}\n`;
}

function firstH1(content: string): string | null {
  for (const line of content.split(/\r?\n/)) {
    if (line.startsWith("# ")) return line.slice(2).trim();
  }
  return null;
}

function trimText(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text;
  return `${text.slice(0, maxChars)}\n\n[Trimmed for context]`;
}

function tokenize(text: string): string[] {
  return text.toLowerCase().match(/[a-zA-Z0-9][a-zA-Z0-9\-]{2,}/g) ?? [];
}

function wikiRootResolved(): string {
  return path.resolve(WIKI_DIR);
}

function pathInside(child: string, parent: string): boolean {
  const relative = path.relative(parent, child);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function sanitizeWikiPath(pathStr: string): string {
  if (!pathStr || typeof pathStr !== "string") throw new Error("Page path must be a non-empty string.");
  if (path.isAbsolute(pathStr) || /^[a-zA-Z]:[\\/]/.test(pathStr)) throw new Error(`Absolute paths are not allowed: ${pathStr}`);
  let parts = pathStr.split(/[\\/]+/).filter(Boolean);
  if (parts[0] === path.basename(WIKI_DIR)) parts = parts.slice(1);
  if (parts.length === 0) throw new Error(`Invalid empty wiki path: ${pathStr}`);
  const finalPath = path.resolve(wikiRootResolved(), path.join(...parts));
  if (!pathInside(finalPath, wikiRootResolved())) throw new Error(`Unsafe wiki path rejected: ${pathStr}`);
  if (path.extname(finalPath).toLowerCase() !== ".md") throw new Error(`Wiki page must end with .md: ${pathStr}`);
  if (finalPath === path.resolve(wikiRootResolved(), "index.md")) throw new Error("LLM is not allowed to write wiki/index.md directly.");
  if (finalPath === path.resolve(LOG_FILE)) throw new Error("LLM is not allowed to write wiki/log.md directly.");
  const relParts = path.relative(wikiRootResolved(), finalPath).split(path.sep);
  if (relParts.some((part) => part.startsWith("."))) throw new Error(`Hidden wiki paths are not allowed: ${pathStr}`);
  return finalPath;
}

function ensureSourceReadable(filePath: string, allowOutsideRaw = false): string {
  if (!fs.existsSync(filePath)) throw new Error(`Source not found: ${filePath}`);
  if (!fs.statSync(filePath).isFile()) throw new Error(`Source must be a file: ${filePath}`);
  const resolved = path.resolve(filePath);
  if (!allowOutsideRaw && fs.existsSync(RAW_DIR) && !pathInside(resolved, path.resolve(RAW_DIR))) {
    throw new Error(`Source must be inside raw/: ${filePath}\nUse --allow-outside-raw if you intentionally want to ingest this file.`);
  }
  return resolved;
}

async function extractText(filePath: string): Promise<string> {
  const suffix = path.extname(filePath).toLowerCase();
  if (suffix === ".pdf") {
    const buffer = fs.readFileSync(filePath);
    const data = await pdfParse(buffer);
    return data.text;
  }
  return fs.readFileSync(filePath, "utf8");
}

function loadWikiPage(filePath: string): WikiPage {
  const text = readTextFile(filePath);
  const [frontmatter] = stripFrontmatter(text);
  return { path: filePath, title: normalizeTitleFromFilename(filePath), frontmatter, content: text };
}

function walkMarkdownFiles(dir: string): string[] {
  if (!fs.existsSync(dir)) return [];
  const results: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) results.push(...walkMarkdownFiles(full));
    else if (entry.isFile() && entry.name.endsWith(".md")) results.push(full);
  }
  return results.sort();
}

function walkFiles(dir: string): string[] {
  if (!fs.existsSync(dir)) return [];
  const results: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) results.push(...walkFiles(full));
    else if (entry.isFile()) results.push(full);
  }
  return results.sort();
}

function getExistingPages(): WikiPage[] {
  if (!fs.existsSync(WIKI_DIR)) return [];
  const pages: WikiPage[] = [];
  for (const filePath of walkMarkdownFiles(WIKI_DIR)) {
    if (pathInside(path.resolve(filePath), path.resolve(BACKUP_DIR))) continue;
    if (path.basename(filePath) === "index.md" && path.dirname(path.resolve(filePath)) === wikiRootResolved()) continue;
    try {
      pages.push(loadWikiPage(filePath));
    } catch (error) {
      pages.push({ path: filePath, title: normalizeTitleFromFilename(filePath), frontmatter: { type: "note" }, content: `[Unreadable page: ${String(error)}]` });
    }
  }
  return pages;
}

function isArchivedPage(page: WikiPage): boolean {
  if (page.frontmatter.archived) return true;
  return pathInside(path.resolve(page.path), path.resolve(WIKI_DIR, "archive"));
}

function getLivePages(): WikiPage[] {
  return getExistingPages().filter((page) => !isArchivedPage(page));
}

function pageType(page: WikiPage): string {
  return String(page.frontmatter.type ?? "note");
}

function pageSummary(page: WikiPage): string {
  const [, body] = stripFrontmatter(page.content);
  return body.replace(/\s+/g, " ").trim().slice(0, 500);
}

function pageSummariesForPrompt(pages: WikiPage[]): Record<string, unknown>[] {
  return pages.map((page) => ({ path: page.path, title: page.title, type: pageType(page), sources: page.frontmatter.sources ?? [], updated: page.frontmatter.updated, summary: pageSummary(page) }));
}

function rankPagesByOverlap(queryText: string, pages: WikiPage[], limit: number): WikiPage[] {
  const queryTerms = countTerms(tokenize(queryText));
  const scored = pages.map((page) => {
    const tags = Array.isArray(page.frontmatter.tags) ? page.frontmatter.tags.join(" ") : "";
    const pageTerms = countTerms(tokenize(`${page.title} ${pageSummary(page)} ${tags}`));
    let score = 0;
    for (const [term, count] of queryTerms) score += Math.min(count, pageTerms.get(term) ?? 0);
    if (queryText.toLowerCase().includes(page.title.toLowerCase())) score += 10;
    return { score, page };
  });
  return scored.sort((a, b) => b.score - a.score).slice(0, limit).filter((item) => item.score > 0).map((item) => item.page);
}

function countTerms(terms: string[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const term of terms) counts.set(term, (counts.get(term) ?? 0) + 1);
  return counts;
}

function existingContextForIngest(sourceText: string, pages: WikiPage[], fullPagesOverride?: WikiPage[], maxFullPages?: number): string {
  const rankedForSummaries = rankPagesByOverlap(sourceText, pages, MAX_EXISTING_SUMMARIES);
  const seen = new Set(rankedForSummaries.map((page) => page.path));
  for (const page of pages) {
    if (rankedForSummaries.length >= MAX_EXISTING_SUMMARIES) break;
    if (!seen.has(page.path)) rankedForSummaries.push(page);
  }
  const cap = maxFullPages ?? MAX_FULL_PAGES;
  const selected = fullPagesOverride ? fullPagesOverride.slice(0, cap) : rankPagesByOverlap(sourceText, pages, cap);
  return JSON.stringify({
    page_summaries: pageSummariesForPrompt(rankedForSummaries),
    page_summaries_truncated: pages.length > rankedForSummaries.length,
    total_existing_pages: pages.length,
    full_relevant_existing_pages: selected.map((page) => ({ path: page.path, title: page.title, type: pageType(page), content: readTextFile(page.path) })),
    full_relevant_existing_pages_capped_at: cap
  }, null, 2);
}

function validateFrontmatter(frontmatter: unknown, sourceFilename?: string): Frontmatter {
  if (!isRecord(frontmatter)) throw new Error("frontmatter must be an object.");
  const fm = { ...frontmatter };
  fm.created ??= todayStr();
  fm.updated ??= todayStr();
  fm.tags ??= [];
  if (!VALID_PAGE_TYPES.has(String(fm.type))) throw new Error(`Invalid page type: ${JSON.stringify(fm.type)}. Valid types: ${JSON.stringify([...VALID_PAGE_TYPES].sort())}`);
  fm.sources ??= [];
  if (!Array.isArray(fm.sources)) throw new Error("frontmatter.sources must be a list.");
  if (sourceFilename && !fm.sources.includes(sourceFilename)) fm.sources.push(sourceFilename);
  if (!Array.isArray(fm.tags)) throw new Error("frontmatter.tags must be a list.");
  const missing = REQUIRED_FRONTMATTER.filter((key) => !(key in fm));
  if (missing.length > 0) throw new Error(`Missing required frontmatter fields: ${JSON.stringify(missing)}`);
  return fm;
}

function requiredSectionsForType(type: string): string[] {
  if (type === "entity") return ENTITY_REQUIRED_SECTIONS;
  if (type === "concept") return CONCEPT_REQUIRED_SECTIONS;
  if (type === "source") return SOURCE_REQUIRED_SECTIONS;
  return [];
}

function validateContentSections(content: string, type: string): string[] {
  return requiredSectionsForType(type).filter((section) => !content.includes(section));
}

function validateLlmPage(page: unknown, sourceFilename: string): ValidatedPage {
  if (!isRecord(page)) throw new Error("Each page must be an object.");
  for (const key of ["path", "frontmatter", "content"]) if (!(key in page)) throw new Error(`Page missing required key: ${key}`);
  const llmPage = page as LlmPage;
  const finalPath = sanitizeWikiPath(String(llmPage.path));
  const frontmatter = validateFrontmatter(llmPage.frontmatter, sourceFilename);
  const content = String(llmPage.content).trim();
  if (!content) throw new Error(`${finalPath}: content is empty.`);
  const h1 = firstH1(content);
  if (!h1) throw new Error(`${finalPath}: content must begin with an H1 heading, e.g. '# Page Title'.`);
  const missingSections = validateContentSections(content, String(frontmatter.type));
  if (missingSections.length > 0) throw new Error(`${finalPath}: missing required sections: ${JSON.stringify(missingSections)}`);
  return { path: finalPath, frontmatter, content };
}

function validateLlmResult(result: unknown): asserts result is { pages: unknown[]; summary?: string } {
  if (!isRecord(result)) throw new Error("LLM output must be a JSON object.");
  if (!("pages" in result)) throw new Error("LLM output must contain a 'pages' field.");
  if (!Array.isArray(result.pages)) throw new Error("'pages' must be a list.");
  if ("summary" in result && typeof result.summary !== "string") throw new Error("'summary' must be a string when provided.");
}

function backupPage(filePath: string): string | null {
  if (!fs.existsSync(filePath)) return null;
  const relative = path.relative(wikiRootResolved(), path.resolve(filePath));
  const parsed = path.parse(relative);
  const timestamp = timestampStr();
  const backupPath = path.join(BACKUP_DIR, parsed.dir, `${parsed.name}.${timestamp}${parsed.ext}`);
  fs.mkdirSync(path.dirname(backupPath), { recursive: true });
  fs.copyFileSync(filePath, backupPath);
  return backupPath;
}

function defaultOverviewContent(): string {
  return dumpFrontmatter(
    { type: "note", sources: [], created: todayStr(), updated: todayStr(), tags: ["overview", "hub"] },
    "# Overview\n\nThis wiki compiles raw source documents into structured, cross-linked knowledge pages.\n\n## Scope\n\n- Source count: 0\n- Page count: 0\n\n## Key Findings\n\n- Add key findings during ingest.\n\n## Recent Updates\n\n- No updates recorded yet.\n"
  );
}

function defaultLogContent(): string {
  return dumpFrontmatter(
    { type: "note", sources: [], created: todayStr(), updated: todayStr(), tags: ["log", "maintenance"] },
    "# Log\n\nAppend-only chronological record of ingests, major edits, queries, and lint passes.\n"
  );
}

function ensureCoreWikiPages(): void {
  fs.mkdirSync(WIKI_DIR, { recursive: true });
  if (!fs.existsSync(OVERVIEW_FILE)) writeTextFile(OVERVIEW_FILE, defaultOverviewContent());
  if (!fs.existsSync(LOG_FILE)) writeTextFile(LOG_FILE, defaultLogContent());
}

function sourceCount(): number {
  if (!fs.existsSync(RAW_DIR)) return 0;
  return walkFiles(RAW_DIR).length;
}

function recentLogEntries(limit = 5): string[] {
  if (!fs.existsSync(LOG_FILE)) return [];
  const entries = readTextFile(LOG_FILE).split(/\r?\n/).filter((line) => line.startsWith("## [")).map((line) => line.slice(3).trim());
  return entries.slice(-limit);
}

function appendIngestLogEntry(sourceFilename: string, validatedPages: ValidatedPage[], summary: string): void {
  ensureCoreWikiPages();
  const lines = ["", `## [${todayStr()}] ingest | ${sourceFilename}`];
  for (const page of validatedPages) {
    const rel = path.relative(wikiRootResolved(), path.resolve(page.path)).replaceAll(path.sep, "/");
    lines.push(`- Changed page: [${normalizeTitleFromFilename(page.path)}](${rel})`);
  }
  lines.push("- Updated overview with source and page counts");
  lines.push(`- Key takeaway: ${summary}`);
  fs.appendFileSync(LOG_FILE, `${lines.join("\n").trimEnd()}\n`, "utf8");
}

function extractKeyFindingsFromOverview(): string[] {
  if (!fs.existsSync(OVERVIEW_FILE)) return ["- Add key findings during ingest."];
  const match = readTextFile(OVERVIEW_FILE).match(/## Key Findings\s*\r?\n([\s\S]*?)(?:\r?\n## |\s*$)/);
  if (!match) return ["- Add key findings during ingest."];
  const findings = match[1].split(/\r?\n/).map((line) => line.trimEnd()).filter(Boolean);
  return findings.length > 0 ? findings : ["- Add key findings during ingest."];
}

function updateOverviewPage(): void {
  ensureCoreWikiPages();
  const pages = getLivePages().filter((page) => path.resolve(page.path) !== path.resolve(OVERVIEW_FILE));
  const findings = extractKeyFindingsFromOverview();
  const recent = recentLogEntries().map((entry) => `- ${entry}`);
  const content = `# Overview

This wiki compiles raw source documents into structured, cross-linked knowledge pages.

## Scope

- Source count: ${sourceCount()}
- Page count: ${pages.length}

## Key Findings

${findings.join("\n")}

## Recent Updates

${(recent.length > 0 ? recent : ["- No updates recorded yet."]).join("\n")}
`;
  const [frontmatter] = stripFrontmatter(readTextFile(OVERVIEW_FILE));
  const fm = validateFrontmatter(Object.keys(frontmatter).length > 0 ? frontmatter : { type: "note", sources: [], tags: [] });
  fm.updated = todayStr();
  writeTextFile(OVERVIEW_FILE, dumpFrontmatter(fm, content));
}

function regenerateIndex(): void {
  fs.mkdirSync(WIKI_DIR, { recursive: true });
  const allPages = getExistingPages();
  const livePages = allPages.filter((page) => !isArchivedPage(page));
  const archivedPages = allPages.filter(isArchivedPage);
  const groups = new Map<string, WikiPage[]>();
  for (const page of livePages) {
    const type = pageType(page);
    groups.set(type, [...(groups.get(type) ?? []), page]);
  }
  const lines = ["# Wiki Index", "", "Auto-generated. Do not edit manually.", "", `Last updated: ${todayStr()}`, ""];
  for (const type of ["source", "entity", "concept", "note"]) {
    const group = groups.get(type) ?? [];
    if (group.length === 0) continue;
    const label = { source: "Sources", entity: "Entities", concept: "Concepts", note: "Other Notes" }[type] ?? titleCase(type);
    lines.push(`## ${label}`, "");
    for (const page of group.sort((a, b) => a.title.localeCompare(b.title))) {
      lines.push(`- [[${page.title}]] — \`${path.relative(wikiRootResolved(), path.resolve(page.path))}\``);
    }
    lines.push("");
  }
  if (archivedPages.length > 0) {
    lines.push("## Archived", "");
    for (const page of archivedPages.sort((a, b) => a.title.localeCompare(b.title))) {
      const reason = page.frontmatter.reason ? ` — ${String(page.frontmatter.reason)}` : "";
      lines.push(`- [[${page.title}]] — \`${path.relative(wikiRootResolved(), path.resolve(page.path))}\`${reason}`);
    }
    lines.push("");
  }
  const content = `${lines.join("\n").trimEnd()}\n`;
  const indexPath = path.join(WIKI_DIR, "index.md");
  if (fs.existsSync(indexPath) && readTextFile(indexPath) === content) return;
  writeTextFile(indexPath, content);
}

function getClient(): OpenAI {
  if (!API_KEY) throw new Error("Set OPENAI_API_KEY environment variable.");
  if (!VERIFY_SSL) console.error("[!] WARNING: SSL certificate verification is DISABLED.");
  return new OpenAI({ apiKey: API_KEY, baseURL: BASE_URL, httpAgent: VERIFY_SSL ? undefined : new Agent({ rejectUnauthorized: false }) });
}

async function chatCreateWithRetries(client: OpenAI, kwargs: OpenAI.Chat.Completions.ChatCompletionCreateParamsNonStreaming): Promise<OpenAI.Chat.Completions.ChatCompletion> {
  let lastError: unknown;
  for (let attempt = 0; attempt <= CHAT_MAX_RETRIES; attempt += 1) {
    try {
      console.log(`  - Sending chat request (attempt ${attempt + 1}/${CHAT_MAX_RETRIES + 1})...`);
      return await client.chat.completions.create(kwargs);
    } catch (error) {
      lastError = error;
      if (attempt === CHAT_MAX_RETRIES) break;
      console.error(`[!] Chat call failed (attempt ${attempt + 1}/${CHAT_MAX_RETRIES + 1}): ${String(error)}`);
    }
  }
  throw lastError;
}

async function chatJson(client: OpenAI, model: string, system: string, user: string, temperature = 0.2): Promise<Record<string, unknown>> {
  const kwargs: OpenAI.Chat.Completions.ChatCompletionCreateParamsNonStreaming = { model, messages: [{ role: "system", content: system }, { role: "user", content: user }], temperature };
  if (USE_JSON_RESPONSE_FORMAT) kwargs.response_format = { type: "json_object" };
  const response = await chatCreateWithRetries(client, kwargs);
  const content = response.choices[0]?.message?.content ?? "";
  try {
    const data = JSON.parse(content);
    if (!isRecord(data)) throw new Error("JSON root is not an object.");
    return data;
  } catch (error) {
    throw new Error(`Model did not return valid JSON:\n${content}`, { cause: error });
  }
}

async function chatJsonStreaming(client: OpenAI, model: string, system: string, user: string, temperature = 0.2): Promise<Record<string, unknown>> {
  const kwargs: OpenAI.Chat.Completions.ChatCompletionCreateParamsStreaming = {
    model,
    messages: [{ role: "system", content: system }, { role: "user", content: user }],
    temperature,
    stream: true
  };
  if (USE_JSON_RESPONSE_FORMAT) kwargs.response_format = { type: "json_object" };

  let lastError: unknown;
  for (let attempt = 0; attempt <= CHAT_MAX_RETRIES; attempt += 1) {
    try {
      console.log(`  - Streaming chat request (attempt ${attempt + 1}/${CHAT_MAX_RETRIES + 1})...`);
      const stream = await client.chat.completions.create(kwargs);
      const chunks: string[] = [];
      const started = Date.now();
      let lastPrint = started;
      let total = 0;
      const isTty = Boolean(process.stderr.isTTY);
      try {
        for await (const event of stream) {
          const piece = event.choices?.[0]?.delta?.content ?? "";
          if (!piece) continue;
          chunks.push(piece);
          total += piece.length;
          const now = Date.now();
          if (now - lastPrint >= 1000) {
            const elapsed = ((now - started) / 1000).toFixed(1);
            const line = `    streaming: ${total.toLocaleString()} chars / ${elapsed}s`;
            if (isTty) process.stderr.write(`\r${line}`);
            else process.stderr.write(`${line}\n`);
            lastPrint = now;
          }
        }
      } finally {
        const closer = (stream as unknown as { controller?: { abort?: () => void } }).controller;
        if (closer && typeof closer.abort === "function") {
          // Best-effort cleanup; ignore if already finished.
          try { /* no-op */ } catch { /* ignore */ }
        }
      }
      const elapsed = ((Date.now() - started) / 1000).toFixed(1);
      const finalLine = `    streaming: ${total.toLocaleString()} chars / ${elapsed}s`;
      process.stderr.write(`${isTty ? "\r" : ""}${finalLine}\n`);
      const content = chunks.join("");
      try {
        const data = JSON.parse(content);
        if (!isRecord(data)) throw new Error("JSON root is not an object.");
        return data;
      } catch (error) {
        // JSON parse failures are not retried; surface immediately.
        throw new Error(`Model did not return valid JSON:\n${content}`, { cause: error });
      }
    } catch (error) {
      // Re-throw JSON parse errors immediately; retry transport errors.
      if (error instanceof Error && error.message.startsWith("Model did not return valid JSON")) throw error;
      lastError = error;
      if (attempt === CHAT_MAX_RETRIES) break;
      console.error(`[!] Streaming chat call failed (attempt ${attempt + 1}/${CHAT_MAX_RETRIES + 1}): ${String(error)}`);
    }
  }
  throw lastError;
}

async function chatText(client: OpenAI, model: string, system: string, user: string, temperature = 0.3): Promise<string> {
  const response = await chatCreateWithRetries(client, { model, messages: [{ role: "system", content: system }, { role: "user", content: user }], temperature });
  return response.choices[0]?.message?.content ?? "";
}

async function testLlmConnection(client: OpenAI, model: string): Promise<void> {
  console.log(`  - Testing LLM connection with ${model}...`);
  try {
    const data = await chatJson(client, model, "You are a connection test. Output only valid JSON.", 'Return exactly this JSON: {"ok": true}', 0);
    if (data.ok !== true) throw new Error(`Expected JSON with ok=true, got: ${JSON.stringify(data)}`);
    console.log("  - LLM connection OK.");
  } catch (error) {
    console.error("\nLLM connection test failed.");
    console.error(`Base URL: ${BASE_URL}`);
    console.error(`Model: ${model}`);
    console.error(`Error: ${errorMessage(error)}`);
    process.exitCode = 1;
    throw error;
  }
}

function bootstrapSchemaText(): string {
  const bundled = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "llmwiki_skill.md");
  if (fs.existsSync(bundled) && path.resolve(bundled) !== path.resolve(SCHEMA_FILE)) return readTextFile(bundled);
  if (fs.existsSync("llmwiki_skill.md")) return readTextFile("llmwiki_skill.md");
  return DEFAULT_SCHEMA;
}

function cmdInit(forceSchema = false): void {
  fs.mkdirSync(RAW_DIR, { recursive: true });
  fs.mkdirSync(WIKI_DIR, { recursive: true });
  for (const dir of ["entities", "concepts", "sources", "archive"]) fs.mkdirSync(path.join(WIKI_DIR, dir), { recursive: true });
  fs.mkdirSync(BACKUP_DIR, { recursive: true });
  ensureCoreWikiPages();
  if (forceSchema || !fs.existsSync(SCHEMA_FILE)) writeTextFile(SCHEMA_FILE, bootstrapSchemaText());
  regenerateIndex();
  console.log("Initialized LLM Wiki Engine structure.");
  console.log("");
  console.log("Created:");
  console.log(`  - ${RAW_DIR}/`);
  console.log(`  - ${WIKI_DIR}/`);
  console.log(`  - ${SCHEMA_FILE}`);
  console.log("");
  console.log("Next:");
  console.log("  set OPENAI_API_KEY=...");
  console.log("  npm start -- ingest raw/your_file.pdf");
}

async function selectRelevantPagesForIngest(client: OpenAI, model: string, sourceText: string, sourceFilename: string, pages: WikiPage[], maxTitles: number): Promise<WikiPage[]> {
  if (pages.length === 0 || maxTitles <= 0) return [];
  const preview = sourceText.slice(0, 4000);
  const prompt = `Given a new source about to be ingested, select existing wiki page titles
most likely to be relevant context for compiling new pages or updating existing ones.

Source filename: ${sourceFilename}

Source text (truncated preview):
${preview}

Wiki page summaries:
${JSON.stringify(pageSummariesForPrompt(pages), null, 2)}

Return JSON only:
{
  "relevant_titles": ["Title-One", "Title-Two"]
}

Rules:
- Max ${maxTitles} titles.
- Use exact titles only.
- Prefer pages that overlap topically with the source.
- If none are relevant, return an empty list.
`;
  let data: Record<string, unknown>;
  try {
    data = await chatJson(client, model, "You select relevant wiki pages. Output only JSON.", prompt, 0.1);
  } catch (error) {
    console.error(`  - Preselect call failed, falling back to overlap ranking: ${errorMessage(error)}`);
    return [];
  }
  const titles = data.relevant_titles;
  if (!Array.isArray(titles)) return [];
  const byTitle = new Map(pages.map((page) => [page.title, page] as const));
  const selected: WikiPage[] = [];
  for (const t of titles) {
    if (typeof t !== "string") continue;
    const page = byTitle.get(t);
    if (page && !selected.includes(page)) selected.push(page);
    if (selected.length >= maxTitles) break;
  }
  return selected;
}

async function cmdIngest(sourcePath: string, model: string, allowOutsideRaw = false, dryRun = false): Promise<void> {
  const startedAt = Date.now();
  console.log(`Starting ingest: ${sourcePath}`);
  console.log("  - Connecting to chat client...");
  const client = getClient();
  if (INGEST_SKIP_CONNECTION_TEST) {
    console.log("  - Skipping pre-flight connection test (errors will surface on the main call).");
  } else {
    await testLlmConnection(client, model);
  }
  console.log("  - Validating source path...");
  const resolvedSourcePath = ensureSourceReadable(sourcePath, allowOutsideRaw);
  const sourceFilename = path.basename(resolvedSourcePath);
  console.log(`  - Reading source: ${sourceFilename}`);
  const sourceText = await extractText(resolvedSourcePath);
  console.log(`  - Source text size: ${sourceText.length.toLocaleString()} characters`);
  console.log("  - Preparing wiki context...");
  ensureCoreWikiPages();
  const schema = fs.existsSync(SCHEMA_FILE) ? readTextFile(SCHEMA_FILE) : DEFAULT_SCHEMA;
  const existingPages = getLivePages();
  let fullPagesOverride: WikiPage[] | undefined;
  if (INGEST_PRESELECT && existingPages.length > MAX_FULL_PAGES) {
    console.log(`  - Preselecting up to ${MAX_FULL_PAGES} relevant pages from ${existingPages.length} existing...`);
    fullPagesOverride = await selectRelevantPagesForIngest(client, model, sourceText, sourceFilename, existingPages, MAX_FULL_PAGES);
    if (fullPagesOverride.length > 0) {
      console.log(`  - Preselected ${fullPagesOverride.length} page(s): ${fullPagesOverride.map((p) => p.title).join(", ")}`);
    } else {
      console.log("  - Preselect returned no titles; using overlap ranking.");
      fullPagesOverride = undefined;
    }
  }
  const existingContext = existingContextForIngest(sourceText, existingPages, fullPagesOverride);
  const wikiDirName = path.basename(WIKI_DIR);
  const prompt = `You are a careful wiki compiler.

Your job:
- Read the source.
- Create new pages or update existing pages.
- Update ${wikiDirName}/overview.md when new source-level findings change the wiki scope, key findings, or recent updates.
- Return JSON only.
- Do not invent facts.
- Do not modify raw source content.
- Use the schema exactly.

Today's date: ${todayStr()}

=== WIKI SCHEMA ===
${schema}

=== EXISTING WIKI CONTEXT ===
${existingContext}

=== SOURCE FILE ===
Filename: ${sourceFilename}

=== SOURCE TEXT ===
${sourceText}

=== OUTPUT JSON SHAPE ===
Return exactly this JSON object:

{
  "pages": [
    {
      "path": "${wikiDirName}/concepts/Example-Concept.md",
      "frontmatter": {
        "type": "concept",
        "sources": ["${sourceFilename}"],
        "created": "${todayStr()}",
        "updated": "${todayStr()}",
        "tags": []
      },
      "content": "# Example-Concept\\n\\n## Definition\\n...\\n\\n## Intuition\\n...\\n\\n## How It Works\\n...\\n\\n## Trade-offs\\n| Pros | Cons |\\n|------|------|\\n| ... | ... |\\n\\n## Related Concepts\\n- [[Another-Concept]]\\n\\n## Source Notes\\n> From \`${sourceFilename}\`: ..."
    }
  ],
  "summary": "Brief summary of what was created or updated."
}

Hard rules:
- Path must be inside ${wikiDirName}/.
- Never write ${wikiDirName}/index.md.
- Never write ${wikiDirName}/log.md; the engine appends log entries after ingest.
- Indexed page title comes from the filename, not the H1.
  Example: path "${wikiDirName}/concepts/machine-learning.md" is indexed as "Machine Learning" even if the H1 says something else.
- Use entity, concept, source, or note only.
- Entity pages must include all entity sections from schema.
- Concept pages must include all concept sections from schema.
- Source pages must include all source sections from schema.
- For existing pages, return the full updated content, not a diff.
- Preserve useful existing content when updating.
- Append new source notes; do not erase old source notes.
- Prefer concepts/ for abstract ideas, entities/ for concrete things, and sources/ for source-level summaries.
- Flag contradictions inline using: > ⚠️ Contradiction: ...
- Use [[Exact-Page-Title]] links.
`;
  console.log(`  - Chat base URL: ${BASE_URL}`);
  console.log(`  - Prompt size: ${prompt.length.toLocaleString()} characters`);
  console.log(`  - Asking model to compile wiki updates with ${model}...`);
  let result: Record<string, unknown>;
  try {
    const chatCall = INGEST_STREAM ? chatJsonStreaming : chatJson;
    result = await chatCall(client, model, "You are a precise wiki compiler. Output only valid JSON.", prompt, 0.2);
  } catch (error) {
    console.error(`\nIngest failed during model call: ${errorMessage(error)}`);
    process.exitCode = 1;
    return;
  }
  validateLlmResult(result);
  console.log("  - Model response received.");
  console.log("  - Validating model output...");
  const validatedPages: ValidatedPage[] = [];
  const errors: string[] = [];
  result.pages.forEach((page, index) => {
    try {
      validatedPages.push(validateLlmPage(page, sourceFilename));
    } catch (error) {
      errors.push(`Page #${index + 1}: ${errorMessage(error)}`);
    }
  });
  if (errors.length > 0) {
    console.error("Ingest aborted. Model output failed validation:");
    for (const error of errors) console.error(`  - ${error}`);
    process.exitCode = 1;
    return;
  }
  if (dryRun) {
    console.log("  - Dry run requested; skipping writes.");
    console.log("Dry run passed validation. Pages that would be written:");
    for (const page of validatedPages) console.log(`  - ${page.path} (${String(page.frontmatter.type)})`);
    console.log(`\nSummary: ${result.summary ?? "Done."}`);
    return;
  }
  console.log(`  - Writing ${validatedPages.length} wiki page(s)...`);
  for (const page of validatedPages) {
    const backup = backupPage(page.path);
    writeTextFile(page.path, dumpFrontmatter(page.frontmatter, page.content));
    console.log(backup ? `  ✓ ${page.path}  (backup: ${backup})` : `  ✓ ${page.path}`);
  }
  console.log("  - Appending ingest log...");
  appendIngestLogEntry(sourceFilename, validatedPages, String(result.summary ?? "Done."));
  console.log("  - Updating overview...");
  updateOverviewPage();
  console.log("  - Regenerating index...");
  regenerateIndex();
  const elapsedSeconds = ((Date.now() - startedAt) / 1000).toFixed(1);
  console.log(`\nIngest complete in ${elapsedSeconds}s: ${result.summary ?? "Done."}`);
}

async function selectRelevantPagesWithLlm(client: OpenAI, model: string, question: string, pages: WikiPage[], maxTitles = 5): Promise<string[]> {
  const prompt = `Given the user question, select the most relevant wiki page titles.

Question:
${question}

Wiki page summaries:
${JSON.stringify(pageSummariesForPrompt(pages), null, 2)}

Return JSON only:
{
  "relevant_titles": ["Title-One", "Title-Two"]
}

Rules:
- Max ${maxTitles} titles.
- Use exact titles only.
- If none are relevant, return an empty list.
`;
  const data = await chatJson(client, model, "You select relevant wiki pages. Output only JSON.", prompt, 0.1);
  const titles = data.relevant_titles;
  if (!Array.isArray(titles)) return [];
  const allTitles = new Set(pages.map((page) => page.title));
  return titles.map(String).filter((title) => allTitles.has(title)).slice(0, maxTitles);
}

async function cmdQuery(question: string, model: string, noLlmSelect = false): Promise<void> {
  console.log("Starting query.");
  console.log("  - Connecting to chat client...");
  const client = getClient();
  const pages = getLivePages();
  if (pages.length === 0) {
    console.log("Wiki is empty. Run: npm start -- ingest raw/your_file.pdf");
    return;
  }
  await testLlmConnection(client, model);
  let relevantPages: WikiPage[];
  if (noLlmSelect) {
    relevantPages = rankPagesByOverlap(question, pages, 5);
  } else {
    const relevantTitles = await selectRelevantPagesWithLlm(client, model, question, pages, 5);
    const titleSet = new Set(relevantTitles);
    relevantPages = pages.filter((page) => titleSet.has(page.title));
  }
  if (relevantPages.length === 0) {
    console.log("No relevant pages found. Try ingesting related sources.");
    return;
  }
  const schema = fs.existsSync(SCHEMA_FILE) ? readTextFile(SCHEMA_FILE) : DEFAULT_SCHEMA;
  const context = relevantPages.map((page) => `=== ${page.title} ===\n${readTextFile(page.path)}`).join("\n\n");
  const prompt = `You are answering from a personal knowledge wiki.

=== SCHEMA ===
${schema}

=== RELEVANT WIKI PAGES ===
${context}

=== USER QUESTION ===
${question}

Instructions:
- Answer using ONLY the information in the wiki pages above.
- Cite using [[Page Title]].
- If the wiki does not contain the answer, say so clearly.
- Do not use outside knowledge.
- If the answer reveals a new reusable concept worth saving, end with:
  💡 New concept suggestion: <Concept Name>
`;
  const answer = await chatText(client, model, "You are a precise research assistant who only uses the provided wiki context.", prompt, 0.3);
  console.log(answer);
  console.log("");
  console.log(`[Pages used: ${relevantPages.map((page) => page.title).join(", ")}]`);
}

function parseDate(value: unknown): Date | null {
  if (value === null || value === undefined) return null;
  if (value instanceof Date) return value;
  const text = String(value).trim();
  const ymd = text.match(/^(\d{4})[-/](\d{2})[-/](\d{2})$/);
  const dmy = text.match(/^(\d{2})-(\d{2})-(\d{4})$/);
  if (!ymd && !dmy) return null;
  const year = Number(ymd ? ymd[1] : dmy?.[3]);
  const month = Number(ymd ? ymd[2] : dmy?.[2]);
  const day = Number(ymd ? ymd[3] : dmy?.[1]);
  const date = new Date(Date.UTC(year, month - 1, day));
  return Number.isNaN(date.getTime()) ? null : date;
}

function findDuplicateLikeTitles(titles: string[]): [string, string][] {
  const groups = new Map<string, string[]>();
  for (const title of titles) {
    const key = title.toLowerCase().replace(/[^a-z0-9]+/g, "");
    groups.set(key, [...(groups.get(key) ?? []), title]);
  }
  const duplicates: [string, string][] = [];
  for (const members of groups.values()) {
    const unique = [...new Set(members)].sort();
    for (let i = 0; i < unique.length; i += 1) for (let j = i + 1; j < unique.length; j += 1) duplicates.push([unique[i], unique[j]]);
  }
  return duplicates;
}

function lintPages(): Record<string, unknown> {
  const pages = getExistingPages().filter((page) => !isArchivedPage(page));
  const allTitles = new Set(pages.map((page) => page.title));
  const incoming = new Map<string, string[]>();
  const issues: {
    total_pages: number;
    missing_frontmatter: unknown[];
    invalid_frontmatter: unknown[];
    missing_required_sections: unknown[];
    missing_links: unknown[];
    orphan_pages: unknown[];
    contradictions: unknown[];
    stale_pages: unknown[];
    duplicate_like_titles: unknown[];
    too_few_outgoing_links: unknown[];
  } = {
    total_pages: pages.length,
    missing_frontmatter: [],
    invalid_frontmatter: [],
    missing_required_sections: [],
    missing_links: [],
    orphan_pages: [],
    contradictions: [],
    stale_pages: [],
    duplicate_like_titles: [],
    too_few_outgoing_links: []
  };
  for (const page of pages) {
    let frontmatter: Frontmatter;
    let body: string;
    try {
      [frontmatter, body] = stripFrontmatter(readTextFile(page.path));
    } catch (error) {
      issues.invalid_frontmatter.push(`${page.path}: ${errorMessage(error)}`);
      continue;
    }
    if (Object.keys(frontmatter).length === 0) issues.missing_frontmatter.push(page.path);
    try {
      validateFrontmatter(frontmatter);
    } catch (error) {
      issues.invalid_frontmatter.push(`${page.path}: ${errorMessage(error)}`);
    }
    const missingSections = validateContentSections(body, String(frontmatter.type ?? "note"));
    if (missingSections.length > 0) issues.missing_required_sections.push({ page: page.title, path: page.path, missing: missingSections });
    const links = extractWikiLinks(body);
    for (const link of links) {
      incoming.set(link, [...(incoming.get(link) ?? []), page.title]);
      if (!allTitles.has(link)) issues.missing_links.push({ from: page.title, missing: link });
    }
    if (new Set(links).size < 2 && ["entity", "concept", "source"].includes(String(frontmatter.type ?? "note"))) issues.too_few_outgoing_links.push({ page: page.title, links: [...new Set(links)].sort() });
    for (const line of body.split(/\r?\n/)) if (line.includes("⚠️ Contradiction") || line.includes("Contradiction:")) issues.contradictions.push(`${page.title}: ${line.trim()}`);
    const updated = parseDate(frontmatter.updated);
    if (updated) {
      const ageDays = Math.floor((Date.now() - updated.getTime()) / 86_400_000);
      if (ageDays >= 90) issues.stale_pages.push({ page: page.title, updated: String(frontmatter.updated), age_days: ageDays });
    }
  }
  for (const page of pages) if (!incoming.has(page.title) && page.title !== "index") issues.orphan_pages.push({ page: page.title, path: page.path });
  issues.duplicate_like_titles = findDuplicateLikeTitles(pages.map((page) => page.title)).map(([a, b]) => ({ a, b }));
  return issues;
}

function printLintReport(report: Record<string, unknown>): void {
  console.log("=== LINT REPORT ===\n");
  console.log(`Total pages: ${String(report.total_pages)}`);
  for (const [label, key] of [["Missing frontmatter", "missing_frontmatter"], ["Invalid frontmatter", "invalid_frontmatter"], ["Missing required sections", "missing_required_sections"], ["Missing links", "missing_links"], ["Orphan pages", "orphan_pages"], ["Too few outgoing links", "too_few_outgoing_links"], ["Contradiction flags", "contradictions"], ["Stale pages", "stale_pages"], ["Duplicate-like titles", "duplicate_like_titles"]]) {
    const items = Array.isArray(report[key]) ? report[key] : [];
    console.log(`\n${label}: ${items.length}`);
    for (const item of items) console.log(`  - ${typeof item === "object" ? JSON.stringify(item) : String(item)}`);
  }
}

async function cmdLint(model: string, deep = false, jsonOutput = false): Promise<void> {
  const report = lintPages();
  if (jsonOutput) console.log(JSON.stringify(report, null, 2));
  else printLintReport(report);
  if (!deep) return;
  const client = getClient();
  const sample = getExistingPages().slice(0, 8).map((page) => `=== ${page.title} ===\n${trimText(readTextFile(page.path), 1500)}`).join("\n\n");
  const prompt = `Analyze this wiki lint report and page sample.

=== LINT REPORT ===
${JSON.stringify(report, null, 2)}

=== PAGE SAMPLE ===
${sample}

Return JSON only:
{
  "suggested_merges": [],
  "taxonomy_gaps": [],
  "stale_or_weak_pages": [],
  "highest_priority_fixes": []
}
`;
  try {
    const data = await chatJson(client, model, "You are a wiki quality auditor. Output only JSON.", prompt, 0.2);
    console.log("\n=== LLM AUDIT ===");
    for (const [key, value] of Object.entries(data)) {
      console.log(`\n${key}:`);
      if (Array.isArray(value)) for (const item of value) console.log(`  - ${String(item)}`);
      else console.log(`  ${String(value)}`);
    }
  } catch (error) {
    console.log(`\nDeep audit skipped: ${errorMessage(error)}`);
  }
}

function cmdArchive(title: string, reason: string): void {
  const matches = getExistingPages().filter((page) => page.title === title && !isArchivedPage(page));
  if (matches.length === 0) {
    console.log(`No live page found with title: ${title}`);
    return;
  }
  if (matches.length > 1) {
    console.log(`Multiple live pages share title ${JSON.stringify(title)}; refusing to archive ambiguously:`);
    for (const page of matches) console.log(`  - ${page.path}`);
    return;
  }
  const page = matches[0];
  const [frontmatter, body] = stripFrontmatter(readTextFile(page.path));
  if (Object.keys(frontmatter).length === 0) {
    console.log(`Refusing to archive ${page.path}: missing YAML frontmatter.`);
    return;
  }
  let fm: Frontmatter;
  try {
    fm = validateFrontmatter(frontmatter);
  } catch (error) {
    console.log(`Refusing to archive ${page.path}: invalid frontmatter: ${errorMessage(error)}`);
    return;
  }
  fm.archived = true;
  fm.reason = reason;
  fm.updated = todayStr();
  const relative = path.relative(wikiRootResolved(), path.resolve(page.path));
  let archivedPath = path.join(WIKI_DIR, "archive", relative);
  if (fs.existsSync(archivedPath)) {
    const parsed = path.parse(archivedPath);
    archivedPath = path.join(parsed.dir, `${parsed.name}.${timestampStr()}${parsed.ext}`);
  }
  backupPage(page.path);
  writeTextFile(archivedPath, dumpFrontmatter(fm, body));
  fs.unlinkSync(page.path);
  regenerateIndex();
  console.log(`Archived [[${title}]] -> ${archivedPath}`);
}

function timestampStr(): string {
  return new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d{3}Z$/, "").replace("T", "-");
}

function titleCase(text: string): string {
  return `${text.slice(0, 1).toUpperCase()}${text.slice(1)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

const program = new Command();
program.name("llm-wiki").description("LLM Wiki Engine v2");
program.command("init").description("Create directory structure").option("--force-schema", "Overwrite llmwiki_skill.md with the default skill file").action((options: { forceSchema?: boolean }) => cmdInit(Boolean(options.forceSchema)));
program.command("ingest").description("Ingest a raw source").argument("<path>").option("--model <model>", "Model to use", DEFAULT_MODEL).option("--allow-outside-raw", "Allow ingesting a file outside raw/").option("--dry-run", "Validate model output but do not write files").action(async (sourcePath: string, options: { model: string; allowOutsideRaw?: boolean; dryRun?: boolean }) => cmdIngest(sourcePath, options.model, Boolean(options.allowOutsideRaw), Boolean(options.dryRun)));
program.command("query").description("Query the wiki").argument("<question...>").option("--model <model>", "Model to use", DEFAULT_MODEL).option("--no-llm-select", "Use local keyword overlap instead of an LLM to select relevant pages").action(async (question: string[], options: { model: string; noLlmSelect?: boolean }) => cmdQuery(question.join(" "), options.model, Boolean(options.noLlmSelect)));
program.command("lint").description("Audit the wiki").option("--model <model>", "Model to use", DEFAULT_MODEL).option("--deep", "Run an additional LLM audit").option("--json", "Print lint report as JSON").action(async (options: { model: string; deep?: boolean; json?: boolean }) => cmdLint(options.model, Boolean(options.deep), Boolean(options.json)));
program.command("rebuild-index").description("Regenerate wiki/index.md").action(() => {
  regenerateIndex();
  console.log("Regenerated wiki/index.md");
});
program.command("archive").description("Archive a page instead of deleting it").argument("<title>").requiredOption("--reason <reason>").action((title: string, options: { reason: string }) => cmdArchive(title, options.reason));

try {
  await program.parseAsync(process.argv);
} catch (error) {
  console.error(`Error: ${errorMessage(error)}`);
  process.exit(1);
}
