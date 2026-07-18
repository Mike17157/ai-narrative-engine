import { existsSync } from "node:fs";

import { SealedOmpArchitect } from "./omp";
import { HostError, type HostEvent, type HostMessage, type HostRequest, type JsonObject, type JsonValue, asObject, stringField } from "./protocol";
import { PythonStoryBridge } from "./python-bridge";
import { bunStoryListEnabled, tryReadStoryLibrary } from "./story-library";
import { runStorySwarm, type StorySwarmEvent } from "./swarm";

interface ArchitectRun {
  prepared?: JsonObject;
  proposal?: JsonObject;
  review?: JsonObject;
}

interface ApprovalCapability {
  key: string;
  scope: string;
  revision: string;
  proposal: JsonObject;
  expiresAt: number;
}

const APPROVAL_TTL_MS = 10 * 60 * 1_000;

export class StoryHost {
  readonly bridge: PythonStoryBridge;
  readonly architect: SealedOmpArchitect;
  readonly #approvals = new Map<string, ApprovalCapability>();
  #architectTail: Promise<void> = Promise.resolve();
  #activeArchitectRequest?: string;
  #queuedArchitectRequests = new Set<string>();
  #cancelledArchitectRequests = new Set<string>();

  constructor(readonly root: string, private readonly write: (message: HostMessage) => void) {
    this.bridge = new PythonStoryBridge(root);
    this.architect = new SealedOmpArchitect(root);
  }

  async handle(request: HostRequest): Promise<void> {
    try {
      const result = await this.#dispatch(request);
      this.write({ type: "response", id: request.id, ok: true, result });
    } catch (error) {
      const hostError = error instanceof HostError
        ? error
        : new HostError("host_error", error instanceof Error ? error.message : "Story Host operation failed");
      this.write({ type: "response", id: request.id, ok: false, error: hostError.toWire() });
    }
  }

  dispose(): void {
    void this.architect.abort();
    this.bridge.dispose();
  }

  async #dispatch(request: HostRequest): Promise<JsonValue> {
    const payload = request.payload ?? {};
    switch (request.op) {
      case "host.health":
        return {
          ok: true,
          transport: "jsonl",
          runtime: "bun",
          ompVersion: "15.10.5",
          pythonBridge: existsSync(this.bridge.python),
          storyListBackend: bunStoryListEnabled() ? "bun-preview" : "python",
          modelConfigured: Boolean(process.env.OPENROUTER_API_KEY),
          browserResearch: "reserved-for-managed-researcher",
        };
      case "story.list":
        if (bunStoryListEnabled()) {
          const native = tryReadStoryLibrary(this.root, asObject(payload));
          if (native !== undefined) return native;
        }
        return this.bridge.request("story.list", asObject(payload));
      case "story.create":
        return this.bridge.request("story.create", asObject(payload));
      case "story.read":
        return this.bridge.request("story.read", asObject(payload));
      case "story.inline_text":
        return this.bridge.request("story.inline_text", asObject(payload));
      case "story.cast_text":
        return this.bridge.request("story.cast_text", asObject(payload));
      case "story.readiness":
        return this.bridge.request("story.readiness", asObject(payload));
      case "story.control_graph":
        return this.bridge.request("story.control_graph", asObject(payload));
      case "architect.context":
        return this.bridge.request("architect.context", asObject(payload));
      case "architect.propose":
        return this.#queueArchitect(request.id, () => this.#propose(request.id, asObject(payload)));
      case "architect.cancel":
        return this.#cancelArchitect(asObject(payload));
      case "architect.commit":
        return this.#commit(asObject(payload));
      case "image.status":
        return this.bridge.request("image.status", asObject(payload));
      case "image.list":
        return this.bridge.request("image.list", asObject(payload));
      case "image.request":
        return this.bridge.request("image.request", asObject(payload));
      case "research.policy":
        return researchPolicy();
      default:
        throw new HostError("unknown_operation", "unsupported Story Host operation");
    }
  }

  async #propose(requestId: string, payload: JsonObject): Promise<JsonValue> {
    this.#throwIfArchitectCancelled(requestId);
    const key = stringField(payload, "key");
    const scope = stringField(payload, "scope");
    const brief = typeof payload.brief === "string" ? payload.brief : "";
    const revision = stringField(payload, "revision");
    const run: ArchitectRun = {};

    this.#activeArchitectRequest = requestId;
    try {
      await runStorySwarm([
        {
          id: "explore",
          role: "explore",
          execute: async () => {
            run.prepared = asObject(await this.bridge.request("architect.prepare", { key, scope, brief, revision }));
          },
        },
        {
          id: "architect",
          role: "architect",
          waitsFor: ["explore"],
          execute: async () => {
            const prepared = run.prepared;
            if (!prepared) throw new HostError("invalid_swarm", "Architect started without an explore artifact");
            run.proposal = await this.architect.propose({
              system: stringField(prepared, "system"),
              prompt: stringField(prepared, "prompt"),
              schema: prepared.schema ?? {},
            }, (event) => this.#event(requestId, { type: event.type, agent: "architect", role: "architect", tool: event.tool, delta: event.delta, detail: event.detail }));
          },
        },
        {
          id: "reviewer",
          role: "reviewer",
          waitsFor: ["architect"],
          execute: async () => {
            if (!run.proposal || !run.prepared) throw new HostError("invalid_swarm", "Reviewer started without an Architect proposal");
            run.review = asObject(await this.bridge.request("architect.validate", {
              key,
              scope,
              revision: stringField(run.prepared, "revision"),
              proposal: run.proposal,
            }));
          },
        },
      ], run, (event) => this.#swarmEvent(requestId, event));
    } catch (error) {
      // OMP abort surfaces provider/session-specific errors. Once the host
      // marked this request cancelled, preserve that stable local contract
      // instead of leaking the incidental abort wording to the UI.
      this.#throwIfArchitectCancelled(requestId);
      throw error;
    } finally {
      if (this.#activeArchitectRequest === requestId) this.#activeArchitectRequest = undefined;
    }

    this.#throwIfArchitectCancelled(requestId);
    if (!run.prepared || !run.proposal || !run.review) throw new HostError("invalid_swarm", "Story Architect did not complete its proposal lifecycle");
    const reviewed = asObject(run.review.review ?? {}, "the Story reviewer returned an invalid result");
    if (reviewed.status !== "approved") {
      throw new HostError("review_rejected", "the Story reviewer did not approve this proposal");
    }
    const approval = this.#issueApproval({
      key,
      scope,
      revision: stringField(run.prepared, "revision"),
      proposal: run.proposal,
    });
    return {
      revision: run.prepared.revision ?? revision,
      proposal: run.proposal,
      review: run.review,
      work_order: run.prepared.work_order ?? {},
      status: "awaiting_author_approval",
      approval_token: approval.token,
      approval_expires_at: approval.expiresAt,
    };
  }

  async #cancelArchitect(payload: JsonObject): Promise<JsonValue> {
    const unexpected = Object.keys(payload).filter((name) => name !== "request_id");
    if (unexpected.length) throw new HostError("invalid_request", "architect.cancel accepts only request_id");
    const requestId = stringField(payload, "request_id");
    if (!this.#queuedArchitectRequests.has(requestId)) {
      throw new HostError("not_running", "that Story Architect request is not currently running");
    }
    this.#cancelledArchitectRequests.add(requestId);
    const cancelled = this.#activeArchitectRequest === requestId
      ? await this.architect.abort()
      : true;
    return { request_id: requestId, cancelled, queued: this.#activeArchitectRequest !== requestId };
  }

  async #queueArchitect<T>(requestId: string, work: () => Promise<T>): Promise<T> {
    let release!: () => void;
    const next = new Promise<void>((resolve) => {
      release = resolve;
    });
    const previous = this.#architectTail;
    this.#architectTail = next;
    this.#queuedArchitectRequests.add(requestId);
    try {
      await previous;
      this.#throwIfArchitectCancelled(requestId);
      return await work();
    } finally {
      this.#queuedArchitectRequests.delete(requestId);
      this.#cancelledArchitectRequests.delete(requestId);
      release();
    }
  }

  #throwIfArchitectCancelled(requestId: string): void {
    if (this.#cancelledArchitectRequests.has(requestId)) {
      throw new HostError("cancelled", "the Story Architect request was cancelled", { retryable: true });
    }
  }

  async #commit(payload: JsonObject): Promise<JsonValue> {
    const unexpected = Object.keys(payload).filter((name) => name !== "approval_token");
    if (unexpected.length) {
      throw new HostError("invalid_commit", "architect.commit accepts only the approval_token from a reviewed proposal");
    }
    const token = stringField(payload, "approval_token");
    const capability = this.#approvals.get(token);
    if (!capability) {
      throw new HostError("approval_required", "request a fresh reviewed Story proposal before committing");
    }
    // Capabilities are deliberately consumed before attempting persistence.
    // A failed/stale write must be re-reviewed instead of becoming a replayable
    // mutation token.
    this.#approvals.delete(token);
    if (capability.expiresAt <= Date.now()) {
      throw new HostError("approval_expired", "the reviewed Story proposal expired; request a fresh proposal");
    }
    return this.bridge.request("architect.commit", {
      key: capability.key,
      scope: capability.scope,
      revision: capability.revision,
      proposal: capability.proposal,
    });
  }

  #issueApproval(capability: Omit<ApprovalCapability, "expiresAt">): { token: string; expiresAt: number } {
    const now = Date.now();
    for (const [token, existing] of this.#approvals) {
      if (existing.expiresAt <= now) this.#approvals.delete(token);
    }
    const token = crypto.randomUUID();
    const expiresAt = now + APPROVAL_TTL_MS;
    this.#approvals.set(token, { ...capability, expiresAt });
    return { token, expiresAt };
  }

  #swarmEvent(requestId: string, event: StorySwarmEvent): void {
    this.#event(requestId, {
      type: event.type,
      wave: event.wave,
      agent: event.agent,
      role: event.role,
      detail: (event.detail ?? null) as JsonValue,
    });
  }

  #event(requestId: string, event: HostEvent["event"]): void {
    this.write({ type: "event", id: requestId, event });
  }
}

function researchPolicy(): JsonValue {
  return {
    status: "planned",
    role: "librarian",
    allowed: ["search", "open", "extract", "source.store"],
    denied: ["login", "post", "message", "purchase", "download", "attach-user-browser"],
    persistence: "cited inspiration cards only; never direct canon writes",
  };
}
