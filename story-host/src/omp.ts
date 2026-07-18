import { mkdirSync } from "node:fs";
import { join, parse } from "node:path";

import type { Model } from "@oh-my-pi/pi-ai";
import type { CustomTool } from "@oh-my-pi/pi-coding-agent/extensibility/custom-tools";
import type { WorkspaceTree } from "@oh-my-pi/pi-coding-agent/workspace-tree";

import { HostError, type JsonObject, type JsonValue } from "./protocol";

type AiRuntime = typeof import("@oh-my-pi/pi-ai");
type OmpRuntime = typeof import("@oh-my-pi/pi-coding-agent");
type RegistryRuntime = typeof import("@oh-my-pi/pi-coding-agent/registry/agent-registry");
type UtilsRuntime = typeof import("@oh-my-pi/pi-utils");

interface LoadedOmp {
  ai: AiRuntime;
  omp: OmpRuntime;
  registry: RegistryRuntime;
}

interface OmpSessionHandle {
  abort(options?: { reason?: string }): Promise<void>;
}

export interface OmpEvent {
  type: string;
  tool?: string;
  delta?: string;
  detail?: JsonValue;
}

export interface ArchitectPrompt {
  system: string;
  prompt: string;
  schema: JsonValue;
}

const ARCHITECT_TIMEOUT_MS = 4 * 60 * 1_000;

/**
 * A sealed Oh My Pi v15.10.5 session factory. It retains OMP's session loop,
 * compaction, retries, event stream, and custom-tool execution, while its
 * process configuration, credential store, model registry, workspace, and
 * agent registry all remain app-owned.
 */
export class SealedOmpArchitect {
  readonly stateRoot: string;
  readonly agentDir: string;
  readonly workspace: string;
  #runtime?: Promise<LoadedOmp>;
  #activeSession?: OmpSessionHandle;

  constructor(readonly root: string) {
    this.stateRoot = join(root, ".story-host");
    this.agentDir = join(this.stateRoot, "agent");
    this.workspace = join(this.stateRoot, "workspace");
    mkdirSync(this.agentDir, { recursive: true });
    mkdirSync(this.workspace, { recursive: true });
  }

  /** Abort an active model turn when the desktop request is cancelled or exits. */
  async abort(): Promise<boolean> {
    const session = this.#activeSession;
    if (!session) return false;
    await session.abort({ reason: "Story Host request cancelled" });
    return true;
  }

  /** Offline regression seam: prove the live SDK exposes only Story tools. */
  async inspectToolNames(): Promise<string[]> {
    const runtime = await this.#loadRuntime();
    const parameters = runtime.ai.z.object({}).strict();
    const probe: CustomTool<typeof parameters> = {
      name: "story.propose",
      label: "Probe Story tool",
      description: "Test-only sealed Story tool probe.",
      parameters,
      execute: async () => ({ content: [{ type: "text", text: "ok" }] }),
    };
    const { session, close } = await this.#createSession(runtime, {
      system: "You are a sealed test session.",
      prompt: "No prompt is sent during inspection.",
      schema: {},
    }, [probe]);
    try {
      return session.getAllToolNames().sort();
    } finally {
      await close();
    }
  }

  async propose(prepared: ArchitectPrompt, emit: (event: OmpEvent) => void): Promise<JsonObject> {
    const runtime = await this.#loadRuntime();
    let proposal: JsonObject | undefined;
    const proposalParameters = runtime.ai.z.object({
      proposal: runtime.ai.z.record(runtime.ai.z.string(), runtime.ai.z.unknown()),
    }).strict();
    const proposalTool: CustomTool<typeof proposalParameters> = {
      name: "story.propose",
      label: "Propose Story patch",
      description: "Submit exactly one structured, public Story proposal for deterministic review. This never commits canon.",
      parameters: proposalParameters,
      execute: async (_callId, params) => {
        if (proposal) throw new Error("a Story proposal was already submitted for this turn");
        proposal = asJsonObject(params.proposal, "story.propose requires an object proposal");
        return {
          content: [{ type: "text", text: "Proposal captured for the deterministic Story reviewer. It has not been committed." }],
          details: { captured: true },
        };
      },
    };

    const { session, close } = await this.#createSession(runtime, prepared, [proposalTool]);
    this.#activeSession = session;
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      void session.abort({ reason: "Story Architect request timed out" });
    }, ARCHITECT_TIMEOUT_MS);
    const unsubscribe = session.subscribe((event: unknown) => emit(normalizeEvent(event)));
    try {
      await session.prompt(prepared.prompt, {
        expandPromptTemplates: false,
        toolChoice: { type: "tool", name: "story.propose" },
      });
    } catch (error) {
      if (timedOut) {
        throw new HostError("architect_timeout", "the Architect did not finish before the four-minute request limit", { retryable: true });
      }
      throw modelError(error);
    } finally {
      clearTimeout(timeout);
      unsubscribe();
      if (this.#activeSession === session) this.#activeSession = undefined;
      await close();
    }
    if (!proposal) {
      throw new HostError("no_proposal", "the Architect finished without a structured Story proposal");
    }
    return proposal;
  }

  async #loadRuntime(): Promise<LoadedOmp> {
    if (!this.#runtime) {
      configureOmpEnvironment(this.stateRoot, this.agentDir);
      this.#runtime = Promise.all([
        import("@oh-my-pi/pi-ai"),
        import("@oh-my-pi/pi-coding-agent"),
        import("@oh-my-pi/pi-coding-agent/registry/agent-registry"),
        import("@oh-my-pi/pi-utils"),
      ]).then(([ai, omp, registry, utils]: [AiRuntime, OmpRuntime, RegistryRuntime, UtilsRuntime]) => {
        // OMP's default logger is a coding-CLI file transport. It is neither
        // useful nor safe in a JSONL sidecar, so close it after module load.
        utils.logger.setTransports({ file: false, console: false });
        return { ai, omp, registry };
      });
    }
    return this.#runtime;
  }

  async #createSession(
    runtime: LoadedOmp,
    prepared: ArchitectPrompt,
    customTools: CustomTool[],
  ) {
    const hostCwd = process.cwd();
    const apiKey = requiredOpenRouterKey();
    const authStorage = await runtime.ai.AuthStorage.create(join(this.agentDir, "story-host-auth.db"), {
      sourceLabel: "Loom Story Host isolated runtime",
    });
    await authStorage.reload();
    // A runtime override has higher precedence than all environment/config
    // fallbacks, so the isolated session can use only this OpenRouter key.
    authStorage.setRuntimeApiKey("openrouter", apiKey);
    const modelRegistry = new runtime.omp.ModelRegistry(authStorage, join(this.agentDir, "story-host-models.yml"));
    const settings = runtime.omp.Settings.isolated({
      "compaction.enabled": true,
      "retry.enabled": true,
      "plan.enabled": false,
      "goal.enabled": false,
      "irc.enabled": false,
      "async.enabled": false,
      "tools.discoveryMode": "off",
      "memory.backend": "off",
      "skills.enabled": false,
      "lsp.enabled": false,
      "browser.enabled": false,
      "web_search.enabled": false,
      "tts.enabled": false,
      "mcp.enableProjectConfig": false,
      "task.isolation.mode": "none",
      "secrets.enabled": false,
    });
    try {
      const { session } = await runtime.omp.createAgentSession({
        cwd: this.workspace,
        agentDir: this.agentDir,
        authStorage,
        modelRegistry,
        model: openRouterModel(),
        sessionManager: runtime.omp.SessionManager.inMemory(this.workspace),
        settings,
        systemPrompt: [prepared.system, proposalContract(prepared.schema)],
        customTools,
        // The local OMP patch adds this boundary. Empty toolNames is NOT a safe
        // substitute because upstream treats it as its all-builtins default.
        builtinTools: false,
        spawns: "",
        agentRegistry: new runtime.registry.AgentRegistry(),
        agentId: `loom-story-${crypto.randomUUID()}`,
        agentDisplayName: "story-architect",
        providerSessionId: `loom-story-${crypto.randomUUID()}`,
        workspaceTree: emptyWorkspaceTree(this.workspace),
        enableMCP: false,
        enableLsp: false,
        skipPythonPreflight: true,
        disableExtensionDiscovery: true,
        extensions: [],
        skills: [],
        rules: [],
        contextFiles: [],
        promptTemplates: [],
        slashCommands: [],
        requireYieldTool: false,
        hasUI: false,
        autoApprove: false,
      });
      return {
        session,
        close: async () => {
          try {
            await session.dispose();
          } finally {
            authStorage.close();
            if (process.cwd() !== hostCwd) process.chdir(hostCwd);
          }
        },
      };
    } catch (error) {
      authStorage.close();
      if (process.cwd() !== hostCwd) process.chdir(hostCwd);
      throw error;
    }
  }
}

function configureOmpEnvironment(stateRoot: string, agentDir: string): void {
  // OMP's logger and default configuration directory are captured when its
  // modules load. This sidecar is dedicated to Story work, so redirect those
  // globals before the first dynamic import rather than touching ~/.omp.
  const ompHome = join(stateRoot, "omp-home");
  mkdirSync(ompHome, { recursive: true });
  process.env.HOME = ompHome;
  process.env.USERPROFILE = ompHome;
  if (process.platform === "win32") {
    const root = parse(ompHome).root;
    process.env.HOMEDRIVE = root;
    process.env.HOMEPATH = ompHome.slice(root.length) || "\\";
  }
  process.env.PI_CONFIG_DIR = ".omp";
  process.env.PI_CODING_AGENT_DIR = agentDir;
  // Explicit AuthStorage below makes this redundant, but stripping broker
  // discovery prevents a future SDK call from inheriting remote credentials.
  delete process.env.OMP_AUTH_BROKER_URL;
  delete process.env.OMP_AUTH_BROKER_TOKEN;
}

function emptyWorkspaceTree(rootPath: string): WorkspaceTree {
  return { rootPath, rendered: ".", truncated: false, totalLines: 1, agentsMdFiles: [] };
}

function requiredOpenRouterKey(): string {
  const apiKey = process.env.OPENROUTER_API_KEY?.trim();
  if (!apiKey) {
    throw new HostError("model_unavailable", "configure OPENROUTER_API_KEY before running the Architect");
  }
  return apiKey;
}

function openRouterModel(): Model<"openai-completions"> {
  const id = process.env.LOOM_OMP_MODEL?.trim() || "deepseek/deepseek-v4-pro";
  return {
    id,
    name: id,
    api: "openai-completions",
    provider: "openrouter",
    // Never inherit an arbitrary OPENROUTER_BASE_URL: the runtime API key is
    // intentionally sent only to the official OpenRouter endpoint.
    baseUrl: "https://openrouter.ai/api/v1",
    // The host deliberately does not infer provider-specific reasoning
    // metadata from ambient OMP models. A registered Story model can add that
    // later; the sealed baseline is ordinary tool-capable completion.
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128_000,
    maxTokens: 8_192,
  };
}

function proposalContract(schema: JsonValue): string {
  return [
    "You are the Story Architect. Work only on the public, bounded Story request supplied by the host.",
    "Do not use or invent private mechanics, filesystem content, shell commands, browser actions, or external tools.",
    "Call story.propose exactly once. It is proposal-only: canon changes require a separate author-approved commit.",
    "The proposal must conform to this JSON Schema; include required empty fields where the schema requires them:",
    JSON.stringify(schema),
  ].join("\n\n");
}

function asJsonObject(value: unknown, message: string): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new HostError("invalid_proposal", message);
  return value as JsonObject;
}

function normalizeEvent(value: unknown): OmpEvent {
  const event = value as Record<string, unknown>;
  if (event?.type === "message_update") {
    const update = event.assistantMessageEvent as Record<string, unknown> | undefined;
    if (update?.type === "text_delta" && typeof update.delta === "string") {
      return { type: "omp.text_delta", delta: update.delta };
    }
  }
  if (event?.type === "tool_execution_start") {
    return { type: "omp.tool_started", tool: String(event.toolName ?? "story tool") };
  }
  if (event?.type === "tool_execution_end") {
    return { type: "omp.tool_completed", tool: String(event.toolName ?? "story tool") };
  }
  return { type: `omp.${String(event?.type ?? "event")}` };
}

function modelError(error: unknown): HostError {
  const message = error instanceof Error ? error.message : "the Oh My Pi Architect session failed";
  if (/api key|credential|auth/i.test(message)) {
    return new HostError("model_unavailable", "configure OPENROUTER_API_KEY before running the Architect");
  }
  return new HostError("architect_failed", message);
}
