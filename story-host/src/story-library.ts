import { existsSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { Database } from "bun:sqlite";

import { HostError, type JsonObject, type JsonValue } from "./protocol";

interface StoryRow {
  key: unknown;
  name: unknown;
  premise: unknown;
  tone: unknown;
  themes: unknown;
  start: unknown;
  locations: unknown;
}

interface CastRow {
  story_key: unknown;
  character: unknown;
}

interface StoryLibraryEntry {
  key: string;
  name: string;
  premise: string;
  tone: string;
  themes: string[];
  locations: number;
  start: string | null;
  cast: string[];
  mtime: number;
}

const STORY_LIST_SQL = `
  SELECT stories.key, stories.name, stories.premise, stories.tone, stories.themes,
         stories."start", COUNT(locations.id) AS locations
  FROM stories
  LEFT JOIN locations ON locations.story_key = stories.key
  GROUP BY stories.key, stories.name, stories.premise, stories.tone, stories.themes, stories."start"
  ORDER BY stories.name COLLATE NOCASE
`;

const CAST_SQL = "SELECT story_key, character FROM cast_members ORDER BY story_key, ord";

/**
 * Development-preview switch for the first Python-to-Bun migration slice.
 * Default remains Python until golden parity has covered more than the Story
 * library projection.
 */
export function bunStoryListEnabled(): boolean {
  return process.env.LOOM_STORY_HOST_BUN_LIST === "1";
}

/**
 * Read the same lightweight Story library projection as loom.lean.stdio.
 *
 * This deliberately owns no schema creation, migrations, or writes. Python
 * remains the default oracle and mutation boundary while this narrow Bun read
 * path accumulates parity coverage.
 */
export function tryReadStoryLibrary(root: string, payload: JsonObject): JsonValue | undefined {
  if (Object.keys(payload).length) {
    throw new HostError("forbidden_capability", "story.list does not accept arguments");
  }

  if (process.env.TURSO_DATABASE_URL) return undefined;
  const storyDirectory = join(root, "configs", "stories");
  const databasePath = join(root, "configs", "stories.db");
  // Python owns legacy JSON migration and schema setup. The preview becomes
  // eligible only after that canonicalization has completed, so it cannot
  // silently bypass a migration or create an empty replacement database.
  if (!existsSync(databasePath) || hasPendingJsonMigration(storyDirectory)) return undefined;

  let database: Database | undefined;
  try {
    database = new Database(databasePath, { create: false, readonly: true, strict: true });
    if (!hasExpectedSchema(database)) return undefined;
    const rows = database.query(STORY_LIST_SQL).all() as StoryRow[];
    const casts = readCasts(database);
    const entries: StoryLibraryEntry[] = [];
    for (const row of rows) {
      const entry = asEntry(root, row, casts);
      // Python's Settings loader omits a malformed aggregate rather than
      // exposing a partial one. Let it remain the oracle in that rare case.
      if (!entry) return undefined;
      entries.push(entry);
    }
    // Python first gets the stable name order from SQLite, then uses the JSON
    // source-file mtime as a stable descending sort key. Preserve that exact
    // ordering while legacy JSON migration artifacts still exist.
    entries.sort((left, right) => right.mtime - left.mtime);
    return {
      stories: entries.map(({ mtime: _mtime, ...entry }) => entry),
    };
  } catch {
    return undefined;
  } finally {
    database?.close();
  }
}

function hasPendingJsonMigration(storyDirectory: string): boolean {
  try {
    const entries = readdirSync(storyDirectory, { withFileTypes: true });
    return entries.some((entry) => {
      if (entry.isFile()) return entry.name.endsWith(".json");
      return entry.isDirectory() && existsSync(join(storyDirectory, entry.name, "story.json"));
    });
  } catch {
    return true;
  }
}

function hasExpectedSchema(database: Database): boolean {
  return hasColumns(database, "stories", ["key", "name", "premise", "tone", "themes", "start"])
    && hasColumns(database, "locations", ["story_key", "id"])
    && hasColumns(database, "cast_members", ["story_key", "character", "ord"]);
}

function hasColumns(database: Database, table: string, expected: string[]): boolean {
  const rows = database.query(`PRAGMA table_info(${table})`).all() as Array<{ name?: unknown }>;
  const columns = new Set(rows.map((row) => row.name).filter((name): name is string => typeof name === "string"));
  return expected.every((column) => columns.has(column));
}

function readCasts(database: Database): Map<string, string[]> {
  const casts = new Map<string, string[]>();
  if (!database.query("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cast_members'").get()) {
    return casts;
  }
  for (const row of database.query(CAST_SQL).all() as CastRow[]) {
    if (typeof row.story_key !== "string" || typeof row.character !== "string") continue;
    const current = casts.get(row.story_key) ?? [];
    current.push(row.character);
    casts.set(row.story_key, current);
  }
  return casts;
}

function asEntry(root: string, row: StoryRow, casts: Map<string, string[]>): StoryLibraryEntry | undefined {
  if (
    typeof row.key !== "string"
    || typeof row.name !== "string"
    || typeof row.premise !== "string"
    || typeof row.tone !== "string"
    || (typeof row.start !== "string" && row.start !== null)
    || typeof row.locations !== "number"
  ) return undefined;
  const themes = parseThemes(row.themes);
  if (!themes) return undefined;
  return {
    key: row.key,
    name: row.name,
    premise: row.premise,
    tone: row.tone,
    themes,
    locations: row.locations,
    start: row.start,
    cast: casts.get(row.key) ?? [],
    mtime: sourceMtime(root, row.key),
  };
}

function parseThemes(value: unknown): string[] | undefined {
  if (typeof value !== "string") return undefined;
  try {
    const themes = JSON.parse(value);
    return Array.isArray(themes) && themes.every((theme) => typeof theme === "string") ? themes : undefined;
  } catch {
    return undefined;
  }
}

function sourceMtime(root: string, key: string): number {
  // Python's story_json_path removes unsafe key characters before considering
  // the folder or legacy-flat JSON path. Do the same before touching disk.
  const safe = key.replace(/[^\p{L}\p{N}_-]+/gu, "");
  if (!safe) return 0;
  const storyDir = join(root, "configs", "stories");
  const folder = join(storyDir, safe, "story.json");
  const legacy = join(storyDir, `${safe}.json`);
  return mtime(folder) ?? mtime(legacy) ?? 0;
}

function mtime(path: string): number | undefined {
  try {
    return statSync(path).isFile() ? statSync(path).mtimeMs / 1_000 : undefined;
  } catch {
    return undefined;
  }
}
