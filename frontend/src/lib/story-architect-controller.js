// Public, browser-owned coordinator for the first Story Architect slice.
//
// This module intentionally knows nothing about the full author card or any
// protected Architect role.  It only receives the server's model-safe context,
// asks for one non-mutating proposal, and submits that exact proposal with the
// revision it was based on.  Callers must fall back to the legacy Architect
// route for every scope this proof does not explicitly own.

export const PUBLIC_ARCHITECT_SCOPES = Object.freeze([
  'world',
  'premise',
  'cast',
  'first_day'
]);

const publicScopes = new Set(PUBLIC_ARCHITECT_SCOPES);

export class PublicArchitectRequestError extends Error {
  constructor(message, { status = 0, data = null } = {}) {
    super(message);
    this.name = 'PublicArchitectRequestError';
    this.status = status;
    this.data = data;
    this.retryable = Boolean(data?.retryable);
    this.modelRoute = data?.model_route && typeof data.model_route === 'object'
      ? data.model_route
      : null;
  }
}

function endpoint(storyKey, suffix) {
  return `/api/stories/${encodeURIComponent(storyKey)}/architect/public/${suffix}`;
}

async function requestJson(fetchImpl, url, { method = 'GET', body, signal } = {}) {
  let response;
  try {
    response = await fetchImpl(url, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal
    });
  } catch (error) {
    // Let the workspace turn its shared request deadline into the same
    // author-friendly timeout message used by the legacy Architect route.
    if (error?.name === 'AbortError') throw error;
    throw new PublicArchitectRequestError(error?.message || 'Could not reach the public Story Architect');
  }

  let data = null;
  try { data = await response.json(); } catch { /* an old route may return the SPA shell */ }

  // A missing capability is the one case where callers deliberately continue
  // with the established server-owned Architect.  Do not hide validation,
  // revision, provider, or network failures behind that fallback.
  if (response.status === 404) return { unavailable: true, status: response.status, data };
  if (!response.ok) {
    throw new PublicArchitectRequestError(
      data?.error || `The public Story Architect request failed (${response.status})`,
      { status: response.status, data }
    );
  }
  return { unavailable: false, status: response.status, data: data || {} };
}

function allowedPublicScopes(context) {
  const allowed = Array.isArray(context?.allowed_scopes) ? context.allowed_scopes : [];
  return allowed.filter((scope) => publicScopes.has(String(scope)));
}

/**
 * Return the single scope this proof owns, or null when the request must stay
 * with the legacy coordinator.  In particular, never turn `overview` into an
 * inferred broad pass: the author must select one public card surface.
 */
export function publicArchitectScope(target, context) {
  const section = String(target?.section || target || '').trim();
  if (!publicScopes.has(section)) return null;
  return allowedPublicScopes(context).includes(section) ? section : null;
}

/**
 * Browser coordinator for a single public direct-edit turn.
 *
 * Context and commit cards are deliberately retained only as model-safe
 * snapshots.  The workspace must reload its normal story endpoint after a
 * commit instead of rendering either snapshot as the full author card.
 */
export class PublicStoryArchitectController {
  constructor(storyKey, { fetchImpl = globalThis.fetch } = {}) {
    this.storyKey = storyKey;
    this.fetchImpl = fetchImpl;
    this.context = null;
  }

  async loadContext({ signal } = {}) {
    const result = await requestJson(this.fetchImpl, endpoint(this.storyKey, 'context'), { signal });
    if (result.unavailable) return result;

    const context = result.data;
    if (!context?.revision || !Array.isArray(context?.allowed_scopes)) {
      throw new PublicArchitectRequestError('The public Story Architect returned incomplete context', {
        status: result.status,
        data: context
      });
    }
    this.context = context;
    return { ...result, context };
  }

  async runDirectTurn({ target, brief, signal } = {}) {
    const text = String(brief || '').trim();
    if (!text) return { legacy: true, reason: 'empty-brief' };

    // Refresh immediately before proposal generation.  The returned revision
    // is the optimistic-concurrency token carried through model and commit.
    const contextResult = await this.loadContext({ signal });
    if (contextResult.unavailable) return contextResult;
    const scope = publicArchitectScope(target, contextResult.context);
    if (!scope) return { legacy: true, reason: 'unsupported-scope', context: contextResult.context };

    const modelResult = await requestJson(this.fetchImpl, endpoint(this.storyKey, 'model'), {
      method: 'POST',
      body: { scope, brief: text, revision: contextResult.context.revision },
      signal
    });
    if (modelResult.unavailable) return modelResult;
    const proposal = modelResult.data?.proposal;
    if (!proposal || typeof proposal !== 'object' || Array.isArray(proposal)) {
      throw new PublicArchitectRequestError('The public Story Architect did not return a proposal', {
        status: modelResult.status,
        data: modelResult.data
      });
    }

    // This is the only mutating request in the public flow.  It carries the
    // model's proposal untouched along with its exact source revision/scope.
    const revision = String(modelResult.data?.revision || contextResult.context.revision || '');
    if (!revision) {
      throw new PublicArchitectRequestError('The public Story Architect proposal has no revision', {
        status: modelResult.status,
        data: modelResult.data
      });
    }
    const commitResult = await requestJson(this.fetchImpl, endpoint(this.storyKey, 'commit'), {
      method: 'POST',
      body: { scope, proposal, revision },
      signal
    });
    if (commitResult.unavailable) return commitResult;

    // Commit returns another model-safe card projection. Keep it for the next
    // controller call, never expose it as the workspace's complete author UI.
    this.context = {
      ...contextResult.context,
      revision: commitResult.data?.revision || revision,
      ...(commitResult.data?.card ? { card: commitResult.data.card, model_card: commitResult.data.card } : {})
    };
    return {
      context: this.context,
      scope,
      model: modelResult.data,
      commit: commitResult.data
    };
  }
}
