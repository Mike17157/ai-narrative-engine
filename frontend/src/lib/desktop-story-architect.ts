/**
 * Desktop-only adapter for the bounded public Story Architect capability.
 *
 * This deliberately does not imitate the legacy browser controller's
 * proposal-and-immediate-commit flow. The Story Host returns a reviewed,
 * single-use approval capability; the workspace must present it to the author
 * and call `commitApprovedTurn` only after an explicit confirmation.
 */

import {
  requestStoryHost,
  StoryHostError,
  type StoryHostJson,
} from './story-host-client';

export const DESKTOP_PUBLIC_ARCHITECT_SCOPES = Object.freeze([
  'world',
  'premise',
  'cast',
  'first_day',
]);

const publicScopes = new Set<string>(DESKTOP_PUBLIC_ARCHITECT_SCOPES);

export interface DesktopArchitectContext {
  revision: string;
  allowed_scopes: string[];
  card?: Record<string, StoryHostJson>;
  model_card?: Record<string, StoryHostJson>;
  control_graph?: StoryHostJson;
}

export interface DesktopProposedTurn {
  status: 'awaiting_author_approval';
  context: DesktopArchitectContext;
  scope: string;
  revision: string;
  proposal: Record<string, StoryHostJson>;
  review: Record<string, StoryHostJson>;
  workOrder: StoryHostJson;
  approvalToken: string;
  approvalExpiresAt: number;
}

export interface DesktopCommittedTurn {
  context: DesktopArchitectContext;
  scope: string;
  model: {
    proposal: Record<string, StoryHostJson>;
    review: Record<string, StoryHostJson>;
    work_order: StoryHostJson;
  };
  commit: Record<string, StoryHostJson>;
}

export class DesktopStoryArchitectController {
  context: DesktopArchitectContext | null = null;

  constructor(readonly storyKey: string) {}

  async loadContext({ signal }: { signal?: AbortSignal } = {}): Promise<DesktopArchitectContext> {
    const result = await requestStoryHost<StoryHostJson>('architect.context', { key: this.storyKey }, { signal });
    const context = parseContext(result);
    this.context = context;
    return context;
  }

  /** Generate and deterministically review a proposal without mutating canon. */
  async proposeDirectTurn({
    target,
    brief,
    signal,
  }: {
    target?: { section?: string } | string | null;
    brief?: string;
    signal?: AbortSignal;
  } = {}): Promise<DesktopProposedTurn | { legacy: true; reason: string; context: DesktopArchitectContext }> {
    const text = String(brief || '').trim();
    if (!text) {
      const context = await this.loadContext({ signal });
      return { legacy: true, reason: 'empty-brief', context };
    }

    const context = await this.loadContext({ signal });
    const scope = publicScope(target, context);
    if (!scope) return { legacy: true, reason: 'unsupported-scope', context };

    const result = asRecord(await requestStoryHost<StoryHostJson>('architect.propose', {
      key: this.storyKey,
      scope,
      brief: text,
      revision: context.revision,
    }, { signal }), 'The desktop Story Architect returned an incomplete proposal.');

    const approvalToken = stringField(result, 'approval_token');
    const approvalExpiresAt = numberField(result, 'approval_expires_at');
    const proposal = asRecord(result.proposal, 'The desktop Story Architect returned no structured proposal.');
    const review = asRecord(result.review, 'The desktop Story Architect returned no review.');
    const revision = stringField(result, 'revision');

    return {
      status: 'awaiting_author_approval',
      context,
      scope,
      revision,
      proposal,
      review,
      workOrder: result.work_order ?? {},
      approvalToken,
      approvalExpiresAt,
    };
  }

  /** Commit exactly the host-reviewed proposal capability; raw proposals never cross this call. */
  async commitApprovedTurn(
    pending: DesktopProposedTurn,
    { signal }: { signal?: AbortSignal } = {},
  ): Promise<DesktopCommittedTurn> {
    const commit = asRecord(await requestStoryHost<StoryHostJson>('architect.commit', {
      approval_token: pending.approvalToken,
    }, { signal }), 'The desktop Story Architect returned an incomplete commit.');

    const revision = typeof commit.revision === 'string' && commit.revision ? commit.revision : pending.revision;
    const card = isRecord(commit.card) ? commit.card : pending.context.card;
    this.context = { ...pending.context, revision, ...(card ? { card, model_card: card } : {}) };
    return {
      context: this.context,
      scope: pending.scope,
      model: {
        proposal: pending.proposal,
        review: pending.review,
        work_order: pending.workOrder,
      },
      commit,
    };
  }
}

/** Project a reviewed proposal into the existing plan surface without exposing a raw mutation path. */
export function desktopProposalPlan(pending: DesktopProposedTurn): {
  summary: string;
  sections: Array<{ id: string; label: string; description: string; body: string; status: string }>;
  build_order: string[];
} {
  const reviewMessage = typeof pending.review.message === 'string' ? pending.review.message.trim() : '';
  const patch = pending.review.patch ?? pending.proposal;
  const patchPreview = jsonPreview(patch);
  const label = scopeLabel(pending.scope);
  return {
    summary: reviewMessage || `The Architect prepared a bounded ${label.toLowerCase()} change.`,
    sections: [{
      id: pending.scope,
      label,
      description: 'This is the exact reviewed patch. It has not changed canon.',
      body: patchPreview || 'The reviewer found no material patch to apply.',
      status: 'ready for confirmation',
    }],
    build_order: [
      `Review the validated ${label.toLowerCase()} patch below.`,
      'Choose “Confirm plan & execute” to apply this exact proposal once.',
    ],
  };
}

function publicScope(target: { section?: string } | string | null | undefined, context: DesktopArchitectContext): string | null {
  const value = typeof target === 'object' && target ? target.section : target;
  const section = String(value || '').trim();
  return publicScopes.has(section) && context.allowed_scopes.includes(section) ? section : null;
}

function parseContext(value: StoryHostJson): DesktopArchitectContext {
  const context = asRecord(value, 'The desktop Story Architect returned incomplete context.');
  const revision = stringField(context, 'revision');
  const allowed = Array.isArray(context.allowed_scopes)
    ? context.allowed_scopes.filter((scope): scope is string => typeof scope === 'string')
    : [];
  if (!allowed.length) {
    throw new StoryHostError({
      code: 'invalid_response',
      message: 'The desktop Story Architect returned no allowed scopes.',
    });
  }
  return {
    revision,
    allowed_scopes: allowed,
    ...(isRecord(context.card) ? { card: context.card } : {}),
    ...(isRecord(context.model_card) ? { model_card: context.model_card } : {}),
    ...('control_graph' in context ? { control_graph: context.control_graph } : {}),
  };
}

function asRecord(value: unknown, message: string): Record<string, StoryHostJson> {
  if (!isRecord(value)) {
    throw new StoryHostError({ code: 'invalid_response', message });
  }
  return value;
}

function isRecord(value: unknown): value is Record<string, StoryHostJson> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function stringField(value: Record<string, StoryHostJson>, name: string): string {
  const field = value[name];
  if (typeof field !== 'string' || !field.trim()) {
    throw new StoryHostError({ code: 'invalid_response', message: `The desktop Story Architect returned no ${name}.` });
  }
  return field;
}

function numberField(value: Record<string, StoryHostJson>, name: string): number {
  const field = value[name];
  if (typeof field !== 'number' || !Number.isFinite(field)) {
    throw new StoryHostError({ code: 'invalid_response', message: `The desktop Story Architect returned no ${name}.` });
  }
  return field;
}

function scopeLabel(scope: string): string {
  return ({ world: 'World', premise: 'Opening', cast: 'People', first_day: 'Possible scenes' } as Record<string, string>)[scope] || 'Story';
}

function jsonPreview(value: StoryHostJson): string {
  try {
    const rendered = JSON.stringify(value, null, 2);
    if (!rendered || rendered === '{}') return '';
    return rendered.length > 3_500 ? `${rendered.slice(0, 3_500)}\n…` : rendered;
  } catch {
    return '';
  }
}
