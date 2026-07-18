import { invoke, isTauri } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';

export type StoryHostJson =
  | null
  | boolean
  | number
  | string
  | StoryHostJson[]
  | { [key: string]: StoryHostJson };

export interface StoryHostRequest {
  id: string;
  op: string;
  payload?: Record<string, StoryHostJson>;
}

export interface StoryHostRequestOptions {
  /** Supply an id only when another local capability must refer to this turn. */
  id?: string;
  /** Abort is best-effort: an active Architect turn also receives host cancel. */
  signal?: AbortSignal;
}

/** A Story Host image descriptor is an opaque, story-owned file reference. */
export interface StoryAssetDescriptor {
  story_key: string;
  image_name: string;
  media_type: string;
}

export interface StoryHostFailure {
  type: 'response';
  id: string;
  ok: false;
  error: {
    code: string;
    message: string;
    retryable?: boolean;
    revision?: string;
  };
}

export interface StoryHostSuccess<T = StoryHostJson> {
  type: 'response';
  id: string;
  ok: true;
  result: T;
}

export interface StoryHostEvent {
  type: 'event';
  id: string;
  event: {
    type: string;
    role?: string;
    wave?: number;
    agent?: string;
    tool?: string;
    delta?: string;
    detail?: StoryHostJson;
  };
}

export type StoryHostResponse<T = StoryHostJson> = StoryHostSuccess<T> | StoryHostFailure;

export class StoryHostError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly revision?: string;

  constructor(error: StoryHostFailure['error']) {
    super(error.message);
    this.name = 'StoryHostError';
    this.code = error.code;
    this.retryable = error.retryable ?? false;
    this.revision = error.revision;
  }
}

let requestSequence = 0;

/** Returns true for the packaged Tauri shell and its explicitly local-only build mode. */
export function isStoryHostDesktop(): boolean {
  // The packaged app is compiled with this mode as well as running inside a
  // Tauri webview. Treat either signal as local-only so a bootstrapping or
  // test webview can fail closed through IPC instead of falling back to the
  // old loopback HTTP application.
  return import.meta.env.VITE_LEAN_STORY === '1' || isTauri();
}

/**
 * Send one request through Tauri's command IPC. The Rust process owns the
 * persistent Bun sidecar and matches JSONL responses by request id.
 */
export async function requestStoryHost<T = StoryHostJson>(
  op: string,
  payload?: Record<string, StoryHostJson>,
  options: StoryHostRequestOptions = {},
): Promise<T> {
  if (!isStoryHostDesktop()) {
    throw new StoryHostError({
      code: 'desktop_required',
      message: 'Story Host is available only in the Loom Story desktop application.',
    });
  }

  const request: StoryHostRequest = {
    id: options.id || nextRequestId(),
    op,
    ...(payload ? { payload } : {}),
  };
  if (options.signal?.aborted) {
    throw cancelledStoryHostRequest(request.id);
  }

  const invocation = invoke<StoryHostResponse<T>>('story_request', { request });
  const response = await waitForStoryHostResponse(invocation, request, options.signal);

  if (response.type !== 'response' || response.id !== request.id) {
    throw new StoryHostError({
      code: 'invalid_response',
      message: 'The Story Host returned a response for a different request.',
    });
  }
  if (response.ok === false) {
    throw new StoryHostError(response.error);
  }
  return response.result;
}

/**
 * Resolve one already-authorized Story image through Tauri. This deliberately
 * does not expose a path, URL, or generic filesystem capability to the web
 * view; the Rust command validates the descriptor again before returning a
 * data URL.
 */
export async function resolveStoryAssetDataUrl(asset: StoryAssetDescriptor): Promise<string> {
  if (!isStoryHostDesktop()) {
    throw new StoryHostError({
      code: 'desktop_required',
      message: 'Story assets are available only in the Loom Story desktop application.',
    });
  }
  return invoke<string>('story_asset_data_url', { asset });
}

/** Subscribe to progress and lifecycle events from the persistent Story Host. */
export function listenToStoryHost(
  handler: (event: StoryHostEvent) => void,
): Promise<UnlistenFn> {
  if (!isStoryHostDesktop()) {
    return Promise.resolve(() => {});
  }
  return listen<StoryHostEvent>('story-event', ({ payload }) => handler(payload));
}

function nextRequestId(): string {
  requestSequence += 1;
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `story-${crypto.randomUUID()}`;
  }
  return `story-${Date.now()}-${requestSequence}`;
}

async function waitForStoryHostResponse<T>(
  invocation: Promise<StoryHostResponse<T>>,
  request: StoryHostRequest,
  signal?: AbortSignal,
): Promise<StoryHostResponse<T>> {
  if (!signal) return invocation;

  let abortListener: (() => void) | undefined;
  const aborted = new Promise<never>((_resolve, reject) => {
    abortListener = () => {
      // `architect.cancel` is deliberately a separate JSONL request so the
      // original invocation can still be drained by Tauri's response map.
      // Other operations are short local reads/writes and need no cancel RPC.
      if (request.op === 'architect.propose') {
        void invoke('story_request', {
          request: {
            id: nextRequestId(),
            op: 'architect.cancel',
            payload: { request_id: request.id },
          },
        }).catch(() => undefined);
      }
      reject(cancelledStoryHostRequest(request.id));
    };
    signal.addEventListener('abort', abortListener, { once: true });
  });

  try {
    return await Promise.race([invocation, aborted]);
  } finally {
    if (abortListener) signal.removeEventListener('abort', abortListener);
  }
}

function cancelledStoryHostRequest(requestId: string): StoryHostError {
  return new StoryHostError({
    code: 'cancelled',
    message: `Story Host request ${requestId} was cancelled.`,
    retryable: true,
  });
}
