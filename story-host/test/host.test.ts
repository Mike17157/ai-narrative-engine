import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { expect, test } from "bun:test";

import { StoryHost } from "../src/host";
import type { HostMessage } from "../src/protocol";

test("architect.commit rejects an unreviewed raw proposal", async () => {
  const root = mkdtempSync(join(tmpdir(), "loom-story-host-"));
  const messages: HostMessage[] = [];
  const host = new StoryHost(root, (message) => messages.push(message));
  try {
    await host.handle({
      id: "raw-commit",
      op: "architect.commit",
      payload: {
        key: "not-authorized",
        scope: "world",
        revision: "not-authorized",
        proposal: {},
      },
    });

    expect(messages).toEqual([{
      type: "response",
      id: "raw-commit",
      ok: false,
      error: {
        code: "invalid_commit",
        message: "architect.commit accepts only the approval_token from a reviewed proposal",
      },
    }]);
  } finally {
    host.dispose();
    rmSync(root, { recursive: true, force: true });
  }
});

test("architect.commit rejects a nonexistent approval token without starting Python", async () => {
  const root = mkdtempSync(join(tmpdir(), "loom-story-host-"));
  const messages: HostMessage[] = [];
  const host = new StoryHost(root, (message) => messages.push(message));
  try {
    await host.handle({
      id: "missing-approval",
      op: "architect.commit",
      payload: { approval_token: "missing" },
    });

    expect(messages[0]).toMatchObject({
      type: "response",
      id: "missing-approval",
      ok: false,
      error: { code: "approval_required" },
    });
  } finally {
    host.dispose();
    rmSync(root, { recursive: true, force: true });
  }
});

test("Bun Story library preview falls back to Python while a root needs bootstrap", async () => {
  const root = mkdtempSync(join(tmpdir(), "loom-story-host-"));
  const messages: HostMessage[] = [];
  const previous = process.env.LOOM_STORY_HOST_BUN_LIST;
  const hadPrevious = Object.hasOwn(process.env, "LOOM_STORY_HOST_BUN_LIST");
  process.env.LOOM_STORY_HOST_BUN_LIST = "1";
  const host = new StoryHost(root, (message) => messages.push(message));
  let calls = 0;
  (host.bridge as unknown as { request: (op: string, payload: Record<string, unknown>) => Promise<unknown> }).request = async (op, payload) => {
    calls += 1;
    expect(op).toBe("story.list");
    expect(payload).toEqual({});
    return { stories: [{ key: "from-python" }] };
  };
  try {
    await host.handle({ id: "list", op: "story.list", payload: {} });
    expect(calls).toBe(1);
    expect(messages).toEqual([{
      type: "response",
      id: "list",
      ok: true,
      result: { stories: [{ key: "from-python" }] },
    }]);
  } finally {
    host.dispose();
    if (hadPrevious) process.env.LOOM_STORY_HOST_BUN_LIST = previous;
    else delete process.env.LOOM_STORY_HOST_BUN_LIST;
    rmSync(root, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
  }
});

test("architect.cancel also prevents a queued Architect request from reaching OMP", async () => {
  const root = mkdtempSync(join(tmpdir(), "loom-story-host-"));
  const messages: HostMessage[] = [];
  const host = new StoryHost(root, (message) => messages.push(message));
  let releaseFirst!: () => void;
  let firstStarted!: () => void;
  const firstGate = new Promise<void>((resolve) => { releaseFirst = resolve; });
  const firstStartedGate = new Promise<void>((resolve) => { firstStarted = resolve; });
  let ompCalls = 0;

  // Keep this a host-lifecycle test: the deterministic edge services and OMP
  // call are replaced with controlled local promises, not a provider request.
  (host.bridge as unknown as { request: (op: string, payload: Record<string, unknown>) => Promise<unknown> }).request = async (op, payload) => {
    if (op === "architect.prepare") {
      return { revision: payload.revision, system: "system", prompt: "prompt", schema: {}, work_order: {} };
    }
    if (op === "architect.validate") {
      return { review: { status: "approved" }, message: "reviewed", patch: {}, updated_sections: [] };
    }
    throw new Error(`unexpected bridge operation: ${op}`);
  };
  (host.architect as unknown as { propose: () => Promise<Record<string, unknown>> }).propose = async () => {
    ompCalls += 1;
    if (ompCalls === 1) {
      firstStarted();
      await firstGate;
    }
    return {};
  };

  try {
    const first = host.handle({
      id: "first",
      op: "architect.propose",
      payload: { key: "story", scope: "world", brief: "First", revision: "r1" },
    });
    await firstStartedGate;

    const queued = host.handle({
      id: "queued",
      op: "architect.propose",
      payload: { key: "story", scope: "world", brief: "Second", revision: "r1" },
    });
    await host.handle({
      id: "cancel-queued",
      op: "architect.cancel",
      payload: { request_id: "queued" },
    });

    releaseFirst();
    await Promise.all([first, queued]);

    expect(ompCalls).toBe(1);
    expect(messages.find((message) => message.type === "response" && message.id === "cancel-queued")).toMatchObject({
      ok: true,
      result: { request_id: "queued", cancelled: true, queued: true },
    });
    expect(messages.find((message) => message.type === "response" && message.id === "queued")).toMatchObject({
      ok: false,
      error: { code: "cancelled", retryable: true },
    });
  } finally {
    host.dispose();
    rmSync(root, { recursive: true, force: true });
  }
});
