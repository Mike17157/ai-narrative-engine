import { createInterface } from "node:readline";
import { resolve } from "node:path";

import { HostError, type HostMessage, type HostRequest } from "./protocol";
import { StoryHost } from "./host";

const root = parseRoot(process.argv.slice(2));
const write = (message: HostMessage) => process.stdout.write(`${JSON.stringify(message)}\n`);
const host = new StoryHost(root, write);
const inFlight = new Set<Promise<void>>();
const activeRequestIds = new Set<string>();
const MAX_JSONL_LINE_BYTES = 1_000_000;
const MAX_IN_FLIGHT_REQUESTS = 8;

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on("line", (line) => {
  if (!line.trim()) return;
  if (Buffer.byteLength(line, "utf8") > MAX_JSONL_LINE_BYTES) {
    writeFailure("", "request_too_large", "Story Host requests may not exceed 1 MiB");
    return;
  }
  if (inFlight.size >= MAX_IN_FLIGHT_REQUESTS) {
    writeFailure("", "host_busy", "too many Story Host requests are already in flight", true);
    return;
  }
  const work = dispatch(line);
  inFlight.add(work);
  void work.finally(() => inFlight.delete(work));
});
input.on("close", () => void shutdown());

async function shutdown(): Promise<void> {
  // A one-shot JSONL caller closes stdin immediately after writing its final
  // request.  Let that request finish before killing the persistent Python
  // validator child; a desktop sidecar normally keeps stdin open indefinitely.
  await Promise.allSettled([...inFlight]);
  host.dispose();
}

async function dispatch(line: string): Promise<void> {
  let request: HostRequest | undefined;
  let requestId: string | undefined;
  try {
    request = JSON.parse(line) as HostRequest;
    if (!request || typeof request.id !== "string" || !request.id || typeof request.op !== "string") {
      throw new HostError("invalid_request", "request needs string id and op fields");
    }
    requestId = request.id;
    if (activeRequestIds.has(requestId)) {
      throw new HostError("duplicate_request", "a Story Host request is already using this id", { retryable: true });
    }
    activeRequestIds.add(requestId);
    await host.handle(request);
  } catch (error) {
    const hostError = error instanceof HostError
      ? error
      : new HostError("invalid_json", "request must be valid JSON");
    write({ type: "response", id: request?.id ?? "", ok: false, error: hostError.toWire() });
  } finally {
    if (requestId) activeRequestIds.delete(requestId);
  }
}

function writeFailure(id: string, code: string, message: string, retryable = false): void {
  write({
    type: "response",
    id,
    ok: false,
    error: { code, message, ...(retryable ? { retryable: true } : {}) },
  });
}

function parseRoot(args: string[]): string {
  const index = args.indexOf("--root");
  if (index >= 0) {
    const value = args[index + 1];
    if (!value) throw new Error("--root requires a path");
    return resolve(value);
  }
  return resolve(process.env.LOOM_ROOT || process.cwd());
}
