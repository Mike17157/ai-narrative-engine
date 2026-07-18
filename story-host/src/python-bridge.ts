import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { existsSync } from "node:fs";
import { createInterface } from "node:readline";
import { join } from "node:path";

import { HostError, type JsonObject, type JsonValue } from "./protocol";

interface PythonResponse {
  id: string;
  ok: boolean;
  result?: JsonValue;
  error?: {
    code?: string;
    message?: string;
    retryable?: boolean;
    revision?: string;
  };
}

interface PendingRequest {
  resolve(value: JsonValue): void;
  reject(reason: Error): void;
  timeout: ReturnType<typeof setTimeout>;
}

const DEFAULT_BRIDGE_TIMEOUT_MS = 45_000;
const IMAGE_RENDER_TIMEOUT_MS = 12 * 60 * 1_000;

/**
 * One persistent, local-only Python process. The Python side owns canonical
 * SQLite validation until the Bun port has golden-parity coverage.
 */
export class PythonStoryBridge {
  #child?: ChildProcessWithoutNullStreams;
  #pending = new Map<string, PendingRequest>();
  #sequence = 0;
  #writeTail: Promise<void> = Promise.resolve();

  constructor(
    readonly root: string,
    readonly python = resolvePython(root),
  ) {}

  async request(op: string, payload: JsonObject): Promise<JsonValue> {
    const child = this.#ensure();
    const id = `py-${++this.#sequence}`;
    const message = JSON.stringify({ id, op, payload });
    return new Promise<JsonValue>((resolve, reject) => {
      const timeout = setTimeout(() => {
        const pending = this.#pending.get(id);
        if (!pending) return;
        this.#pending.delete(id);
        pending.reject(new HostError("bridge_timeout", "the local Story validation bridge did not respond in time", { retryable: true }));
        this.#resetChild(new HostError("bridge_closed", "the local Story validation bridge closed"));
      }, timeoutFor(op));
      this.#pending.set(id, { resolve, reject, timeout });
      this.#queueWrite(child, `${message}\n`, id);
    });
  }

  dispose(): void {
    this.#child?.kill();
    this.#child = undefined;
    this.#rejectPending(new HostError("bridge_closed", "the local Story validation bridge closed"));
  }

  #ensure(): ChildProcessWithoutNullStreams {
    if (this.#child && this.#child.exitCode === null && !this.#child.killed) return this.#child;

    const child = spawn(this.python, ["-m", "loom.lean.stdio", "--root", this.root], {
      cwd: this.root,
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
      env: pythonEnvironment(),
    });
    const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
    lines.on("line", (line) => this.#onLine(line));
    child.stderr.on("data", () => {
      // Never relay Python diagnostics through stdout: stdout is a typed
      // protocol and might otherwise corrupt the Tauri bridge.
    });
    child.on("error", () => {
      if (this.#child !== child) return;
      this.#child = undefined;
      this.#rejectPending(new HostError(
        "bridge_unavailable",
        "the local Story validation bridge could not start",
      ));
    });
    child.on("exit", () => {
      if (this.#child !== child) return;
      this.#child = undefined;
      this.#rejectPending(new HostError("bridge_closed", "the local Story validation bridge closed"));
    });
    this.#child = child;
    return child;
  }

  #onLine(line: string): void {
    let response: PythonResponse;
    try {
      response = JSON.parse(line) as PythonResponse;
    } catch {
      this.#rejectPending(new HostError("bridge_protocol", "the local Story validation bridge returned invalid JSON"));
      this.#resetChild();
      return;
    }
    const pending = this.#pending.get(response.id);
    if (!pending) return;
    this.#pending.delete(response.id);
    clearTimeout(pending.timeout);
    if (response.ok) {
      pending.resolve(response.result ?? null);
      return;
    }
    pending.reject(new HostError(
      response.error?.code ?? "bridge_error",
      response.error?.message ?? "the local Story validation bridge rejected the request",
      { retryable: response.error?.retryable, revision: response.error?.revision },
    ));
  }

  #rejectPending(error: Error): void {
    for (const { reject, timeout } of this.#pending.values()) {
      clearTimeout(timeout);
      reject(error);
    }
    this.#pending.clear();
  }

  #queueWrite(child: ChildProcessWithoutNullStreams, line: string, id: string): void {
    this.#writeTail = this.#writeTail
      .catch(() => undefined)
      .then(() => new Promise<void>((resolve, reject) => {
        child.stdin.write(line, "utf8", (error) => error ? reject(error) : resolve());
      }));
    void this.#writeTail.catch(() => {
      const pending = this.#pending.get(id);
      if (!pending) return;
      this.#pending.delete(id);
      clearTimeout(pending.timeout);
      pending.reject(new HostError("bridge_unavailable", "the local Story validation bridge is unavailable", { retryable: true }));
    });
  }

  #resetChild(error?: Error): void {
    const child = this.#child;
    this.#child = undefined;
    if (error) this.#rejectPending(error);
    if (child && child.exitCode === null && !child.killed) child.kill();
  }
}

function resolvePython(root: string): string {
  if (process.env.LOOM_PYTHON) return process.env.LOOM_PYTHON;
  const localVenv = join(root, ".venv", "Scripts", "python.exe");
  if (existsSync(localVenv)) return localVenv;
  return process.platform === "win32" ? "python.exe" : "python3";
}

function timeoutFor(op: string): number {
  return op === "image.request" ? IMAGE_RENDER_TIMEOUT_MS : DEFAULT_BRIDGE_TIMEOUT_MS;
}

function pythonEnvironment(): NodeJS.ProcessEnv {
  // The validator/image worker never needs model credentials or OMP settings.
  // Keep the Windows process/runtime variables needed to launch Python and an
  // explicitly requested local Comfy option—nothing else crosses this boundary.
  const allowed = [
    "APPDATA", "COMSPEC", "ComSpec", "HOMEDRIVE", "HOMEPATH", "HOME", "LOCALAPPDATA",
    "PATH", "Path", "PATHEXT", "SYSTEMROOT", "SystemRoot", "TEMP", "TMP", "USERPROFILE", "WINDIR",
    "CUDA_PATH", "CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES",
    "LOOM_STORY_HOST_COMFY", "LOOM_LEAN_COMFY",
  ];
  const env: NodeJS.ProcessEnv = { PYTHONUTF8: "1" };
  for (const name of allowed) {
    const value = process.env[name];
    if (value !== undefined) env[name] = value;
  }
  return env;
}
