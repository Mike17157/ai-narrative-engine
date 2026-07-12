# Unified story-card network

Every story enters Loom through one conversion boundary. An imported roleplay card,
an authored premise, and an accumulating play session all become projections of the
same network; they are not separate formats with separate retrieval rules.

## Source of truth

The network is a read model, not a database. Character cards remain portable registry
records. `Story` holds authored story-specific structure. A session's `world_state` holds
live per-playthrough evidence. The network is rebuilt from those sources on read, so
roleplay can evolve without contaminating the reusable source card.

| Thing | Authoritative source | Responsibility |
| --- | --- | --- |
| Story card | `Story` | premise, world, locations, arcs, cast references |
| Character card | registry key plus `Story.cast` harness | portable identity, voice, history |
| Cross-character edge (optional) | `Story.relationships` or play evidence | source, target, one cross-card fact |
| Location / arc | sections of the Story card | place/map structure; staged story progression |
| Runtime fact/promise/state | session `world_state` | about character, established in play |

Scene/beat ordering remains a narrative tree. A scene refers into this network through
its cast, place, and pressure links; it is not incorrectly represented as a relationship edge.

## Conversion contract

1. Extract external prose/card fields into a schema-constrained draft.
2. Resolve names to stable registry/story ids; model prose never becomes an id.
3. Validate every cross-reference through `Story`; surface unresolved draft references.
4. Commit only approved authored cards. Create a direct graph edge only for an explicit cross-card fact
   (kinship, debt, an alliance, a witnessed change); store live play changes as runtime evidence
   with turn provenance.
5. Project all sources through `build_network(story, world)`; do not create importer-specific graphs.

Unknown is better than a fabricated fact. Later play or authoring can promote evidence into
an authored card deliberately.

## Raw turns and residuals

Every completed play turn is stored mechanically as immutable raw text with its turn number,
location, player input, and present character keys. It is not interpreted during ingestion.

Periodic consolidation reads a plain-text dossier of those raw turns plus a small graph slice.
It can only assert, revise, or retire a **residual**: a compact `subject → predicate → object`
claim carrying source-turn ids, a status (`observed`, `inferred`, or `contested`), and a
`requires_source` flag for nuanced claims. Residuals are evidence-backed graph state, not
rewritten character cards or replacements for the transcript.

The dossier renders one **Story card** with premise, world pressure, locations, arcs, and
the active arc. Each cast entry then references and renders its portable Character card. This
keeps world structure unified on the Story card while preserving character identity as reusable.

## Tool-mediated card access

The consolidation model receives a small navigation brief and raw evidence, then reads card
sections as plain text on demand. The intended read surface is:

- `read_story(section)` — `premise`, `world`, `locations`, `arcs`, or `cast`.
- `read_character(key, section)` — `spine`, `identity`, `voice`, `history`, or `play_overlay`.
- `read_residuals(subjects, query)` and `read_turns(turn_ids)`.

Write access remains narrow: the model may assert or retire evidence-citing residuals, never
rewrite a character's identity card. This keeps storage structured while avoiding a large JSON
reasoning tax in the model context.

## Deterministic location activation

Locations stay flat in `Story.locations`; `parent` is optional UI grouping, not a reasoning
hierarchy. Every turn receives one objective sentence for the current location. Code may also
activate a one-line entry for direct exits, a place explicitly mentioned in the recent turn, or
a place referenced by an active residual/promise. Other places remain dormant and retrievable.

## Automatic reference maps

The semantic map is the Story card's locations and connections. The rendered map is a derived
asset: its reference image supplies broad street/terrain geometry, while a dedicated low-denoise
image-to-image workflow applies story-specific map styling. The workflow is
`krea2_turbo_map` (`workflows/krea2_turbo_map_reference_api.json`). A future GLIGEN/ControlNet
upgrade can add bounding-box layout conditioning after the corresponding checkpoint is installed;
the reference-map workflow must not claim that unavailable capability.

## Standard scene query

Call `scene_context(network, location, present, mentioned, query)`. It returns a bounded
context packet in precedence order: location and scene people; direct edges between those people; up to two
involving arcs; their live state; then a small lexical-ranked set of facts and open promises.

`query` only ranks already-admissible runtime evidence. It must never bypass scope to retrieve
global or sealed material. The caller applies secret/perception visibility before building the
packet; the network query remains deterministic and explainable afterward.

`records/network.py` is the canonical implementation. Runtime prompt assembly should consume its
packet instead of independently filtering edges, locations, facts, and promises.
