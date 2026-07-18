/** JSONL contract shared by the Tauri bridge and the Bun Story Host. */

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

export interface HostRequest {
  id: string;
  op: string;
  payload?: JsonObject;
}

export interface HostSuccess {
  type: "response";
  id: string;
  ok: true;
  result: JsonValue;
}

export interface HostFailure {
  type: "response";
  id: string;
  ok: false;
  error: {
    code: string;
    message: string;
    retryable?: boolean;
    revision?: string;
  };
}

export interface HostEvent {
  type: "event";
  id: string;
  event: {
    type: string;
    role?: string;
    wave?: number;
    agent?: string;
    tool?: string;
    delta?: string;
    detail?: JsonValue;
  };
}

export type HostMessage = HostSuccess | HostFailure | HostEvent;

export class HostError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly revision?: string;

  constructor(code: string, message: string, options: { retryable?: boolean; revision?: string } = {}) {
    super(message);
    this.name = "HostError";
    this.code = code;
    this.retryable = options.retryable ?? false;
    this.revision = options.revision;
  }

  toWire(): HostFailure["error"] {
    return {
      code: this.code,
      message: this.message,
      ...(this.retryable ? { retryable: true } : {}),
      ...(this.revision ? { revision: this.revision } : {}),
    };
  }
}

export function asObject(value: unknown, message = "request payload must be an object"): JsonObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new HostError("invalid_request", message);
  }
  return value as JsonObject;
}

export function stringField(payload: JsonObject, name: string): string {
  const value = payload[name];
  if (typeof value !== "string" || !value.trim()) {
    throw new HostError("invalid_request", `${name} must be a non-empty string`);
  }
  return value.trim();
}
