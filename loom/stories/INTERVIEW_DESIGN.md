# Story-card interview design

## One document, from the first turn

The interview edits a real Story card immediately. There is no draft-story
format, conversion step, or second schema.

New stories begin with:

```json
{
  "fields": {
    "status": "interviewing",
    "open_questions": [],
    "interview_history": []
  }
}
```

The conversation progressively patches the card's existing owned sections:

- `world`: genre engine, biome, setting, routines
- `premise`: immediate situation
- `cast`: initial character-card membership
- `relationships`: only relationships established in conversation
- `themes`: short public labels for the story's human question
- `fields.arc_design`: the author-only theme/character-pressure document
- `fields.first_day_plan`: a causal, player-changeable baseline for the opening day
- `time_system`: entity windows, capabilities, and hard availability constraints
- `fields.open_questions`: deliberately unresolved material

## Model boundary

Models write plain authorial text plus a hidden fenced `CMD` block. Loom parses
that command stream, validates it against the selected card section, and
applies the result through the normal Story-card mutation path. Model output
never writes raw JSON directly.

If a lightweight interview model omits the command but the author supplied a
real fact, a second, narrow structured extractor may recover only explicitly
stated facts into the same selected section. A prose claim such as “saved” is
never treated as proof that a mutation occurred.

Every focused edit is a write boundary, not merely prompt context:

- World → `world` and unresolved questions
- Opening → `premise` and unresolved questions
- Day One → `fields.first_day_plan` only
- Entity schedule → `time_system` only
- People → `cast`, `relationships`, and unresolved questions
- Theme & arcs → `themes` and `fields.arc_design` only

The relational store persists `time_system` beside `world`; it may not be
allowed to survive only in interview history.

## Compact public dramatic kernels

The card can carry a small, narrator-safe dramatic surface without turning the
private arc document into generic model context:

- `fields.character_cores` maps `player` or a stable cast key to one short,
  public driving posture. It is not a wound, hidden need, diagnosis, secret,
  or promised outcome.
- A Day One event may add public `theme`, `tone`, and `roles`. `roles` maps an
  on-page participant key to that person's immediate function in the scene,
  not their hidden motive.

The interview and development boundaries normalize these fields, reject hidden
wrappers and roles for off-stage people, and retain them in the generic
model/card projection. The private `fields.arc_design`, hidden event material,
knowledge gates, and entity schedule remain excluded from that projection.

## Theme-led character arcs

An arc is not a linear list of events the narrator must force. It is a durable
pressure: a theme poses a human question, and each character meets it through
their own incomplete way of seeing the world. The canonical private document is
`fields.arc_design`:

```json
{
  "version": 1,
  "themes": [{"id": "impermanence", "label": "Impermanence", "question": "What does waiting cost?"}],
  "arcs": [{
    "id": "shuri-impermanence",
    "title": "Shuri learns what delay costs",
    "theme_id": "impermanence",
    "owner": "shuri",
    "dramatic_question": "Can Shuri risk asking for what she wants?",
    "starting_belief": "Being useful is safer than asking to be chosen.",
    "truth": "author-only",
    "turning_points": [{"id": "dock-pressure", "kind": "pressure", "scene_id": "dock-warning", "when": "evening", "public_surface": "The old routine stops being enough.", "private_pressure": "author-only"}],
    "character_threads": [{
      "character": "shuri",
      "want": "Keep the Returner safe.",
      "protective_strategy": "Turn intimate moments into practical tasks.",
      "blind_spot": "author/director diagnosis",
      "unacknowledged_need": "author-only",
      "limitation": "Cannot ask directly for closeness.",
      "visible_tell": "Straightens objects when cornered.",
      "pressure_points": [{"scene_id": "dock-warning", "when": "evening", "public_pressure": "The player asks why she missed their meeting."}],
      "recognition": "author-only possible turn"
    }]
  }]
}
```

`truth`, `blind_spot`, `unacknowledged_need`, `recognition`,
`private_pressure`, and a turning point's private change are never narrator
facts. They are the Director's account of why a person resists an easy answer.
At runtime, the storyteller receives only the on-stage character's explicitly
stated surface belief, protective strategy, limitation, visible tell, and the
public pressure whose scene/time/flag gate is currently open. A possible turn
may be earned; it is never compulsory, a permission to reveal the answer, or a
substitute for player choice.

The public Story card receives only `fields.arc_outline`: theme labels and
questions plus each arc's title/owner/dramatic question. The full private
document appears only in the author-only Director view. Generic card actions
such as **Review**, **Organize**, and **Build** receive that same safe outline,
so they cannot accidentally paraphrase a private recognition into public canon.

## Architect-led construction

The **Architect** is the default authoring interface. It is a bounded,
server-side co-author, not a card-section picker wrapped in a chat window. An
author can give it a high-level direction such as “make the first day feel more
interconnected” or “keep Shuri central”; it reads the real card, selects the
next useful target itself, and carries out only the safe connected work it can
justify from established canon.

Each Architect turn has a short, auditable rhythm:

1. inspect the current card and deterministic readiness/gap report;
2. make at most a small bounded set of additive, model-validated changes;
3. stop at the next authorial hinge and ask one concrete question.

An authorial hinge is a decision the system must not manufacture: the story
promise or tone, a relationship's truth, a theme, a private villain fact, loop
rules, a knowledge/evidence gate, a character's wound, or anything that
meaningfully changes player agency. Conflicting or rewrite-like directions
also stop for confirmation. The Architect may connect supporting locations,
incidental people, scene opportunities, and other scaffolding only when the
author has explicitly asked it to flesh the story out; it never silently turns
an interview answer into a broad rewrite.

Its mission, last chosen target, pending question, bounded-action state, and a
fingerprint of the card revision are persisted author-side so a refresh resumes
the same conversation. They are not public Story-card fields and are excluded
from generic model payloads. If another editor changes canonical story data,
the fingerprint invalidates the old pending target rather than letting an
answer steer a stale plan. The normal flow never asks the author to choose a
scope or click a reported gap. Clicking a card item remains an advanced,
explicit override for an author who wants to focus the Architect on one fact.

**Build a starting set** remains a secondary explicit operation for authors
who want a deliberately wider, one-pass construction. It may fill missing
practical opening material or create a small connected starting cast, but it
may add only missing facts in its requested scope; established canon is
reference material, never something to rewrite. Generated people become real
story-bound character cards and the story stores their stable keys, not
anonymous name blobs.

Before saving, Loom validates the full proposed card, resolves relationships
and scene participants to those character keys, and rejects an invalid proposal
without committing a partial roster. A repeat build reuses a cast member with
the same normalized name—and preserves a prose-established `Shuri` rather
than inventing a separate `Shuri Yukawa`—rather than creating `shuri_2`. The resulting card,
new card names, and readiness report return in the same response so the live
card can update without waiting for a second fetch.

Character keys are execution details. Public authoring payloads additionally
include a display-only cast index (name, role, appearance, and present
connection) so both the author and the interviewer can say “Shuri” while
runtime state still uses the stable key.

## Interview behavior

New stories enter the interview immediately with an assistant-only opening
turn—never a synthetic author message. Existing stories resume their stored
conversation and must never receive the blank-story opener.

The interviewer asks one concrete question from the strongest live uncertainty,
not a fixed checklist. It must preserve uncertainty as open questions rather
than inventing canon. Once the opening and threat exist, it should shape the
first day into visible event / director-private context / trigger / evidence /
knowledge. A private note is not automatically a current villain action;
`entity_action` marks the events that must obey an entity activity window.
When an entity has constrained activity, it should define that schedule rather
than leave availability to prose.

The interviewer can also return a private `NEXT_FOCUS` marker. It is stripped
from the reader-facing response and tells the client which card section the
follow-up question actually concerns. This prevents a question about a person
or a Day One scene from being accidentally saved back into the section edited
on the previous turn.

## Lifecycle

- `interviewing`: the author conversation is shaping the real card.
- `active`: `/api/stories/{key}/activate` has compiled and validated a playable
  scenario and stored its exact contract in `fields.runtime_scenario`.
- No commit/migration is required between these states.

The interview card displays its model-free readiness report inline. Each blocker
links back to its owning card section; only a ready card shows **Start play**.
Any later substantive interview patch returns the card to `interviewing` and
removes the old runtime contract, so the author must explicitly reactivate it.

## One control room, Architect first

Every Story root opens the same control room, regardless of whether it was
created before or after the interview lifecycle existed. The Architect is the
first surface; the card and director are complementary ways to inspect or
explicitly intervene in the same canonical story rather than competing routes
the author must manually navigate:

- **Architect** is the ordinary conversation. It chooses the next target,
  makes bounded safe progress, and periodically asks the author one useful
  question. It shows its current card/revision and a plain-language account of
  changes, never raw command blocks or hidden director facts.
- **Story** is the public canon and an advanced focused-edit surface. Its
  outline may explicitly direct the Architect to one item, and the live Story
  card remains visible alongside the conversation. Its Theme & character arcs
  section intentionally shows only a non-spoiling outline.
- **Director** is an author-only, freshly compiled preview of possible scenes,
  gates, entity windows, loop policy, knowledge, and full character pressure.
  It is diagnostic and does not persist or activate anything. Private plan and
  arc data are returned only by the dedicated director-preview endpoint, never
  by the public card payload.
- A Director drag persists its explicit placement first, then reports a
  `scene_moved` event to the Architect. It may update only unambiguous derived
  arc-time mirrors. If the move could change a hidden action, knowledge gate,
  public evidence, entity window, or intentional arc offset, it preserves
  those mechanics and asks one protected author question instead.
- **Play** is the lifecycle surface. Before activation it groups the exact
  missing decisions by their card section; when ready it starts the existing
live session. Its edit actions return to the matching Story-card section
rather than moving the author into a different UI.

The Director preview may show compiler fallbacks for diagnosis, but it must
mark a scene with no author-supplied time as **Unplaced** rather than presenting
a fallback slot as a real schedule. Nothing from that private preview is sent
wholesale to narration.

## Required before live play

The interview card alone is not a playable scenario. Before a story becomes
`active`, `runtime.scenario_compiler.compile_authored_scenario` produces a
model-free contract and validates:

- an opening location, initial time, and present characters;
- a scene catalog with deterministic time, location, flag, and presence gates;
- a private director plan for hidden context and separately scheduled entity actions;
- a loop/reset policy that states exactly which runtime facts and memories
  survive death;
- a presence-indexed observation ledger for future per-character actor calls.

The live runtime reads the stored contract, not free-form card prose. It seeds
an immutable baseline in the session's private `runtime` State level, uses
authored scene eligibility for day offers, locks the opening roster, and passes
only the selected scene's public surface to narration. The hidden director plan
is never injected wholesale into the narrator prompt. The same is true of the
private arc document: the live prompt uses only its gated behavioral surface,
so the narrator can play resistance convincingly without knowing the answer it
must not yet give away.

On an executable `death` loop policy, the runtime restores that baseline,
deletes loop-local retrieval facts, increments the loop, and preserves only the
configured Returner memory. The Player clears its sent transcript on this
response, so loop one cannot leak through browser history into loop two.

Until the compiler accepts the card, `status: interviewing` is honest: a
well-written card may be compelling but must not be presented as ready to drive
live play.

## Character cards

Characters are created only after the world and immediate situation provide
context. Each starts with the facts needed now: role, appearance, immediate
personality/background, and present connection. Further facts are added later
only when play or conversation establishes a need for them.
