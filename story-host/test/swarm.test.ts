import { expect, test } from "bun:test";

import { HostError } from "../src/protocol";
import { runStorySwarm, topologicalWaves } from "../src/swarm";

test("Story swarm preserves OMP-style dependency waves", async () => {
  const order: string[] = [];
  const steps = [
    { id: "explore", role: "explore" as const, execute: async () => order.push("explore") },
    { id: "world", role: "task" as const, waitsFor: ["explore"], execute: async () => order.push("world") },
    { id: "cast", role: "task" as const, waitsFor: ["explore"], execute: async () => order.push("cast") },
    { id: "reviewer", role: "reviewer" as const, waitsFor: ["world", "cast"], execute: async () => order.push("reviewer") },
  ];
  expect(topologicalWaves(steps).map((wave) => wave.map((step) => step.id))).toEqual([
    ["explore"], ["world", "cast"], ["reviewer"],
  ]);
  await runStorySwarm(steps, {}, () => {});
  expect(order[0]).toBe("explore");
  expect(order.at(-1)).toBe("reviewer");
});

test("Story swarm rejects cyclic dependencies before execution", () => {
  expect(() => topologicalWaves([
    { id: "architect", role: "architect" as const, waitsFor: ["reviewer"], execute: async () => {} },
    { id: "reviewer", role: "reviewer" as const, waitsFor: ["architect"], execute: async () => {} },
  ])).toThrow(HostError);
});
