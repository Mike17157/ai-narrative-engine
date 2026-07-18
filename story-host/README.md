# Loom Story Host

The Story Host is the local Bun sidecar for the desktop Story product. It
communicates exclusively through JSON Lines over stdin/stdout; it never listens
on an HTTP port.

It runs a pinned Oh My Pi v15.10.5 SDK session with only app-owned Story tools.
During migration, its persistence boundary is `python -m loom.lean.stdio`,
which retains the existing SQLite aggregate validation and atomic commit rules.

## Development

```powershell
Set-Location story-host
npm install
npm run start -- --root ..
```

The host expects `OPENROUTER_API_KEY` and `LOOM_OMP_MODEL` only when an
Architect proposal is requested. `host.health` and safe-context requests do not
make model calls. Local Krea2/Comfy rendering is disabled by default; set
`LOOM_STORY_HOST_COMFY=1` to permit an explicit `image.request` to start it.

## Local protocol

Each request has an `id`, an `op`, and a JSON payload. Responses and live
events are JSONL. The first implemented operations are:

- `host.health`
- `story.list`, `story.create`, `story.read`, `story.readiness`, `story.control_graph` — local
  Story library/card/readiness/navigation projections
- `story.inline_text`, `story.cast_text` — transactionally validated, allow-listed
  author-card prose and cast fields; each requires the current opaque author revision
- `architect.context`
- `architect.propose` — creates a proposal, a deterministic review, and a
  10-minute single-use approval token; it never commits
- `architect.commit` — accepts only that approval token and commits its bound
  proposal through the SQLite adapter
- `image.status`, `image.list`, `image.request` — Story-owned, allow-listed Krea2/Comfy
  candidates; Comfy starts only for a valid render request

The host deliberately does not expose generic filesystem, shell, browser, MCP,
Git, LSP, extension discovery, raw database, raw workflow, or provider-routing
operations. The current image path is optional local Krea2/Comfy; OpenRouter is
used only for the Architect text model until a dedicated image provider exists.
