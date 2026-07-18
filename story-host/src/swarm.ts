import { HostError } from "./protocol";

/**
 * Adapted from Oh My Pi's v15.10.5 swarm-extension DAG/pipeline split.
 *
 * The original runner uses YAML plus a shared coding workspace. Story work is
 * server-owned instead: each role exchanges typed artifacts and has no generic
 * filesystem capability. The useful parts retained here are dependency
 * validation, topological waves, parallel execution, and lifecycle events.
 */
export interface StorySwarmStep<TContext> {
  id: string;
  role: "architect" | "explore" | "librarian" | "task" | "quick_task" | "reviewer" | "oracle" | "designer";
  waitsFor?: string[];
  execute(context: TContext): Promise<unknown>;
}

export interface StorySwarmEvent {
  type: "swarm.wave.started" | "swarm.wave.completed" | "agent.started" | "agent.completed" | "agent.failed";
  wave: number;
  agent?: string;
  role?: string;
  detail?: unknown;
}

export function topologicalWaves<TContext>(steps: StorySwarmStep<TContext>[]): StorySwarmStep<TContext>[][] {
  const byId = new Map<string, StorySwarmStep<TContext>>();
  for (const step of steps) {
    if (byId.has(step.id)) throw new HostError("invalid_swarm", `duplicate Story swarm step '${step.id}'`);
    byId.set(step.id, step);
  }

  const remaining = new Map<string, Set<string>>();
  for (const step of steps) {
    const waitsFor = new Set(step.waitsFor ?? []);
    for (const parent of waitsFor) {
      if (!byId.has(parent)) throw new HostError("invalid_swarm", `Story swarm step '${step.id}' waits for unknown '${parent}'`);
      if (parent === step.id) throw new HostError("invalid_swarm", `Story swarm step '${step.id}' cannot wait for itself`);
    }
    remaining.set(step.id, waitsFor);
  }

  const waves: StorySwarmStep<TContext>[][] = [];
  const complete = new Set<string>();
  while (complete.size < steps.length) {
    const wave = steps.filter((step) => !complete.has(step.id) && [...(remaining.get(step.id) ?? [])]
      .every((dependency) => complete.has(dependency)));
    if (!wave.length) throw new HostError("invalid_swarm", "Story swarm has a dependency cycle");
    waves.push(wave);
    for (const step of wave) complete.add(step.id);
  }
  return waves;
}

export async function runStorySwarm<TContext>(
  steps: StorySwarmStep<TContext>[],
  context: TContext,
  emit: (event: StorySwarmEvent) => void,
): Promise<void> {
  const waves = topologicalWaves(steps);
  for (const [index, wave] of waves.entries()) {
    const waveNumber = index + 1;
    emit({ type: "swarm.wave.started", wave: waveNumber, detail: { agents: wave.map((step) => step.id) } });
    await Promise.all(wave.map(async (step) => {
      emit({ type: "agent.started", wave: waveNumber, agent: step.id, role: step.role });
      try {
        await step.execute(context);
        emit({ type: "agent.completed", wave: waveNumber, agent: step.id, role: step.role });
      } catch (error) {
        emit({ type: "agent.failed", wave: waveNumber, agent: step.id, role: step.role });
        throw error;
      }
    }));
    emit({ type: "swarm.wave.completed", wave: waveNumber });
  }
}
