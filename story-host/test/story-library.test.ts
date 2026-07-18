import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { Database } from "bun:sqlite";
import { expect, test } from "bun:test";

import { HostError } from "../src/protocol";
import { tryReadStoryLibrary } from "../src/story-library";

test("Bun Story library read preserves Python's normalized relational projection", () => {
  const root = mkdtempSync(join(tmpdir(), "loom-story-library-"));
  try {
    const configs = join(root, "configs");
    const stories = join(configs, "stories");
    mkdirSync(stories, { recursive: true });
    const database = new Database(join(configs, "stories.db"));
    database.run(`CREATE TABLE stories (
      key TEXT PRIMARY KEY, name TEXT NOT NULL, premise TEXT NOT NULL, tone TEXT NOT NULL,
      themes TEXT NOT NULL, "start" TEXT
    )`);
    database.run("CREATE TABLE locations (story_key TEXT NOT NULL, id TEXT NOT NULL)");
    database.run("CREATE TABLE cast_members (story_key TEXT NOT NULL, character TEXT NOT NULL, ord INTEGER NOT NULL)");
    database.run("INSERT INTO stories VALUES ('older', 'Alpha', 'Old premise', 'Quiet', '[\"grief\"]', 'dock')");
    database.run("INSERT INTO stories VALUES ('newer', 'Beta', 'New premise', 'Sharp', '[\"change\",\"hope\"]', NULL)");
    database.run("INSERT INTO locations VALUES ('older', 'dock'), ('newer', 'station'), ('newer', 'bar')");
    database.run("INSERT INTO cast_members VALUES ('older', 'eli', 1), ('older', 'mara', 0), ('newer', 'dana', 0)");
    database.close();

    expect(tryReadStoryLibrary(root, {})).toEqual({
      stories: [
        {
          key: "older",
          name: "Alpha",
          premise: "Old premise",
          tone: "Quiet",
          themes: ["grief"],
          locations: 1,
          start: "dock",
          cast: ["mara", "eli"],
        },
        {
          key: "newer",
          name: "Beta",
          premise: "New premise",
          tone: "Sharp",
          themes: ["change", "hope"],
          locations: 2,
          start: null,
          cast: ["dana"],
        },
      ],
    });
  } finally {
    rmSync(root, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  }
});

test("Bun Story library read retains the sealed empty payload contract", () => {
  const root = mkdtempSync(join(tmpdir(), "loom-story-library-"));
  try {
    expect(() => tryReadStoryLibrary(root, { key: "not-allowed" })).toThrow(HostError);
    try {
      tryReadStoryLibrary(root, { key: "not-allowed" });
    } catch (error) {
      expect(error).toMatchObject({
        code: "forbidden_capability",
        message: "story.list does not accept arguments",
      });
    }
  } finally {
    rmSync(root, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  }
});

test("Bun Story library preview defers bootstrap and JSON migration to Python", () => {
  const root = mkdtempSync(join(tmpdir(), "loom-story-library-"));
  try {
    expect(tryReadStoryLibrary(root, {})).toBeUndefined();
    const configs = join(root, "configs");
    const stories = join(configs, "stories");
    mkdirSync(stories, { recursive: true });
    const database = new Database(join(configs, "stories.db"));
    database.run("CREATE TABLE stories (key TEXT, name TEXT, premise TEXT, tone TEXT, themes TEXT, \"start\" TEXT)");
    database.run("CREATE TABLE locations (story_key TEXT, id TEXT)");
    database.run("CREATE TABLE cast_members (story_key TEXT, character TEXT, ord INTEGER)");
    database.close();
    expect(tryReadStoryLibrary(root, {})).toEqual({ stories: [] });
    const legacy = join(stories, "legacy");
    mkdirSync(legacy, { recursive: true });
    writeFileSync(join(legacy, "story.json"), "{}", "utf8");
    expect(tryReadStoryLibrary(root, {})).toBeUndefined();
  } finally {
    rmSync(root, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  }
});
