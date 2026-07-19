"""Build the blind A/B narrator reader widget page.

Pairs turns from two bench reports (same scenario), anonymizes which model
sits in which lane per turn (client-side, persisted), and writes a complete
index.html for the Daimon widget workspace.

Usage: python scripts/make_ab_reader.py <out_dir> [report_a] [report_b]
Defaults: configs/bench_glm_long.json vs configs/bench_deepseek_firstday.json
"""
import base64
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
REPORT_A = Path(sys.argv[2] if len(sys.argv) > 2 else "configs/bench_glm_long.json")
REPORT_B = Path(sys.argv[3] if len(sys.argv) > 3 else "configs/bench_deepseek_firstday.json")

a = json.load(open(REPORT_A, encoding="utf-8"))
b = json.load(open(REPORT_B, encoding="utf-8"))
assert len(a["turns"]) == len(b["turns"]), "turn count mismatch"

turns = []
for ta, tb in zip(a["turns"], b["turns"]):
    assert ta["turn"] == tb["turn"] and ta["user"] == tb["user"], f"turn mismatch at {ta['turn']}"
    turns.append({"turn": ta["turn"], "user": ta["user"], "m1": ta["reply"], "m2": tb["reply"]})

data = json.dumps(turns, ensure_ascii=False).replace("</", "<\\/")
ident = base64.b64encode(json.dumps({"m1": "glm-5.2", "m2": "deepseek-v4-pro"}).encode()).decode()

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blind narrator read</title>
<style>
/* ── kimi-host-safe-zone.css (inlined utility) ─────────────────────────── */
:root {
  --daimon-widget-host-safe-inline-end: 190px;
  --daimon-widget-host-safe-block-start: 44px;
}
.kimi-host-safe-context { container-type: inline-size; }
.kimi-host-safe-header {
  box-sizing: border-box;
  min-block-size: var(--daimon-widget-host-safe-block-start, 44px);
  padding-inline-end: var(--daimon-widget-host-safe-inline-end, 190px);
}
@container (max-width: 419px) {
  .kimi-host-safe-header {
    min-block-size: 0;
    padding-block-start: var(--daimon-widget-host-safe-block-start, 44px);
    padding-inline-end: 0;
  }
}

/* ── kimi-fit.css (inlined utility) ────────────────────────────────────── */
[data-kimi-root] { box-sizing: border-box; inline-size: 100%; min-inline-size: 0; max-inline-size: 100%; overflow: visible; }
[data-kimi-root] * { box-sizing: border-box; min-inline-size: 0; }
html[data-daimon-size-tier="compact"] [data-kimi-priority="p1"],
html[data-daimon-size-tier="compact"] [data-kimi-priority="p2"],
html[data-daimon-size-tier="compact"] [data-kimi-priority="p3"] { display: none !important; }
@media (prefers-reduced-motion: reduce) { [data-kimi-root] * { scroll-behavior: auto; } }

/* ── kimi-glass.css (button selectors in use) ──────────────────────────── */
.kimi-glass-button {
  box-sizing: border-box;
  border: 1px solid var(--kimi-color-border);
  border-radius: 8px;
  background: var(--kimi-color-surface-raised);
  color: var(--kimi-color-text-primary);
  min-height: 28px;
  font: inherit;
  cursor: pointer;
  padding: 6px 10px;
  transition: background-color 140ms ease, border-color 140ms ease, opacity 140ms ease;
}
.kimi-glass-button:hover,
.kimi-glass-button[aria-pressed="true"] {
  border-color: var(--kimi-color-accent);
  background: var(--kimi-color-surface-strong);
}
.kimi-glass-button:focus-visible { outline: 2px solid var(--kimi-color-accent); outline-offset: 2px; }
.kimi-glass-button:disabled { cursor: not-allowed; opacity: 0.46; }
@supports ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .kimi-glass-button {
    border-color: var(--kimi-glass-border);
    background: var(--kimi-glass-surface);
    box-shadow: inset 0 1px 0 var(--kimi-glass-highlight), inset 0 -1px 0 var(--kimi-glass-lowlight);
  }
  .kimi-glass-button[data-kimi-glass-standalone="true"] {
    backdrop-filter: blur(var(--kimi-glass-blur));
    -webkit-backdrop-filter: blur(var(--kimi-glass-blur));
  }
}
@media (prefers-reduced-motion: reduce) { .kimi-glass-button { transition: none; } }

/* ── widget ────────────────────────────────────────────────────────────── */
html, body { margin: 0; padding: 0; }
body {
  font-family: var(--kimi-font-sans, system-ui, sans-serif);
  color: var(--kimi-color-text-primary);
  letter-spacing: 0;
}
main.kimi-host-safe-context { padding: 4px 14px 10px; }

header.kimi-host-safe-header h1 {
  margin: 0;
  font-size: 15px;
  font-weight: 650;
  display: inline;
}
header .progress {
  display: inline;
  margin: 0 0 0 10px;
  font-size: 12px;
  color: var(--kimi-color-text-tertiary);
}

/* Pixel progress rail — one cell per turn; glyph family = picked side. */
.rail {
  display: grid;
  grid-template-columns: repeat(11, minmax(0, 1fr));
  gap: 3px;
  margin: 6px 0 8px;
}
.rail > i {
  aspect-ratio: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--kimi-font-mono, ui-monospace, monospace);
  font-style: normal;
  font-size: 12px;
  border: 1px solid var(--kimi-color-border);
  border-radius: 3px;
  color: var(--kimi-color-text-quaternary);
}
.rail > i.picked { color: var(--kimi-color-text-primary); }
.rail > i.tie { color: var(--kimi-color-text-tertiary); }
.rail > i.current { border-color: var(--kimi-color-accent); }

.context {
  margin: 0 0 8px;
  padding-block-start: 8px;
  border-block-start: 1px solid var(--kimi-color-border);
  font-size: 13px;
  line-height: 1.5;
  color: var(--kimi-color-text-secondary);
}
.context .you { font-weight: 650; color: var(--kimi-color-text-primary); }

.lanes {
  display: grid;
  grid-template-columns: 1fr;
  gap: 0;
  overflow: auto;
  max-height: 340px;
  border-block-start: 1px solid var(--kimi-color-border);
}
@media (min-height: 600px) { .lanes { max-height: 480px; } }
@media (min-height: 760px) { .lanes { max-height: 560px; } }
.lane { padding: 10px 0; }
.lane h2 {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 650;
  color: var(--kimi-color-text-tertiary);
}
.lane + .lane { border-block-start: 1px solid var(--kimi-color-border); }
.prose p { margin: 0 0 10px; font-size: 14px; line-height: 1.62; }
.prose p:last-child { margin-bottom: 2px; }
@media (min-width: 760px) {
  .lanes { grid-template-columns: 1fr 1fr; }
  .lane { padding: 10px 14px; }
  .lane:first-child { padding-inline-start: 0; }
  .lane:last-child { padding-inline-end: 0; }
  .lane + .lane { border-block-start: none; border-inline-start: 1px solid var(--kimi-color-border); }
}

.pickbar {
  position: sticky;
  bottom: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 8px 0 6px;
  border-block-start: 1px solid var(--kimi-color-border);
  background: var(--kimi-color-surface);
}
.linkish {
  border: none;
  background: none;
  padding: 6px 4px;
  font: inherit;
  font-size: 12px;
  color: var(--kimi-color-text-tertiary);
  cursor: pointer;
}
.linkish:hover { color: var(--kimi-color-text-primary); }
.linkish:focus-visible { outline: 2px solid var(--kimi-color-accent); outline-offset: 2px; }

/* Compact P0 recomposition — hidden at larger tiers; everything else hides
   via data-kimi-priority at compact. */
.compact-p0 { display: none; }
html[data-daimon-size-tier="compact"] .compact-p0 { display: block; }
html[data-daimon-size-tier="compact"] .lanes,
html[data-daimon-size-tier="compact"] .pickbar { display: none !important; }
.compact-p0 output {
  display: block;
  font-size: 15px;
  font-weight: 650;
  line-height: 1.15;
}
.compact-p0 .hint { font-size: 11px; color: var(--kimi-color-text-tertiary); }

.reveal h2 { font-size: 14px; margin: 10px 0 6px; }
.reveal p, .reveal dl, .reveal ol { font-size: 13px; line-height: 1.55; }
.reveal dl { display: flex; gap: 18px; margin: 6px 0 10px; }
.reveal dl div { display: flex; gap: 6px; }
.reveal dt { color: var(--kimi-color-text-tertiary); }
.reveal dd { margin: 0; font-weight: 650; }
.reveal ol { margin: 6px 0 10px; padding-inline-start: 20px; }
.reveal li { margin-block-end: 2px; }
.provenance {
  margin: 10px 0 0;
  font-size: 11px;
  color: var(--kimi-color-text-quaternary);
}
</style>
</head>
<body>
<main class="kimi-host-safe-context" data-kimi-root>
  <header class="kimi-host-safe-header" data-kimi-priority="p1">
    <h1>Blind narrator read</h1>
    <p class="progress" id="progress"></p>
  </header>

  <section class="compact-p0" data-kimi-priority="p0" aria-label="pick tally">
    <output id="compactStatus"></output>
    <span class="hint">open wider to read &amp; pick</span>
  </section>

  <div class="rail" id="rail" data-kimi-priority="p1" aria-label="per-turn pick rail"></div>

  <p class="context" data-kimi-priority="p1"><span class="you">You —</span> <span id="userText"></span></p>

  <section class="lanes" id="lanes" data-kimi-priority="p0" aria-label="two anonymized narrations">
    <article class="lane" id="laneA" aria-label="option A">
      <h2>A</h2>
      <div class="prose" id="proseA"></div>
    </article>
    <article class="lane" id="laneB" aria-label="option B">
      <h2>B</h2>
      <div class="prose" id="proseB"></div>
    </article>
  </section>

  <nav class="pickbar" data-kimi-priority="p0" aria-label="pick the stronger narration">
    <button class="kimi-glass-button" data-kimi-glass-standalone="true" id="pickA" aria-pressed="false">A is stronger</button>
    <button class="kimi-glass-button" data-kimi-glass-standalone="true" id="pickTie" aria-pressed="false">Tie</button>
    <button class="kimi-glass-button" data-kimi-glass-standalone="true" id="pickB" aria-pressed="false">B is stronger</button>
    <button class="linkish" id="backBtn">Back</button>
    <button class="linkish" id="revealBtn">Reveal</button>
  </nav>

  <section class="reveal" id="reveal" hidden data-kimi-priority="p0" aria-label="verdict">
    <h2>Identities</h2>
    <p id="revealMap"></p>
    <dl id="totals"></dl>
    <h2>Your picks</h2>
    <ol id="perTurn"></ol>
    <button class="kimi-glass-button" data-kimi-glass-standalone="true" id="resetBtn">Reset picks</button>
  </section>

  <p class="provenance" data-kimi-priority="p3">Two first-day benches, scene-length format · 11 turns · lane assignment is shuffled per turn and stored locally · identities reveal only on demand</p>
</main>

<script>
const TURNS = /*__DATA__*/;
const IDENT = JSON.parse(atob("__IDENT_B64__"));
const N = TURNS.length;
const STORE = "abReader.v1";

function freshState() {
  const sides = {};
  for (const t of TURNS) sides[t.turn] = Math.random() < 0.5 ? "m1A" : "m2A";
  return { sides, picks: {}, pos: 0, revealed: false };
}
function load() {
  try {
    const s = JSON.parse(localStorage.getItem(STORE));
    if (!s || !s.sides || !s.picks) return null;
    s.pos = Math.min(Math.max(0, s.pos | 0), N - 1);
    s.revealed = s.revealed === true;
    return s;
  } catch { return null; }
}
let state = load() || freshState();
// Storage is best-effort: a blocked or full store must never block picking.
function save() { try { localStorage.setItem(STORE, JSON.stringify(state)); } catch { /* optional */ } }

const $ = (id) => document.getElementById(id);

function laneModel(turn, lane) {
  const aIsM1 = state.sides[turn] === "m1A";
  return lane === "A" ? (aIsM1 ? "m1" : "m2") : (aIsM1 ? "m2" : "m1");
}
function laneText(t, lane) { return laneModel(t.turn, lane) === "m1" ? t.m1 : t.m2; }

function renderProse(el, text) {
  el.replaceChildren();
  for (const para of text.split(/\n{2,}/)) {
    const p = document.createElement("p");
    const parts = para.split(/\*([^*]+)\*/g);
    parts.forEach((seg, i) => {
      if (i % 2 === 1) { const em = document.createElement("em"); em.textContent = seg; p.append(em); }
      else p.append(document.createTextNode(seg));
    });
    el.append(p);
  }
}

function tally() {
  let a = 0, b = 0, tie = 0;
  for (const k of Object.keys(state.picks)) {
    const v = state.picks[k];
    if (v === "A") a++; else if (v === "B") b++; else tie++;
  }
  return { a, b, tie };
}

function renderRail() {
  const rail = $("rail");
  rail.replaceChildren();
  for (const t of TURNS) {
    const cell = document.createElement("i");
    const pick = state.picks[t.turn];
    cell.textContent = pick === "A" ? "/" : pick === "B" ? "\\" : pick === "tie" ? "=" : "·";
    if (pick) cell.classList.add(pick === "tie" ? "tie" : "picked");
    if (t.turn === TURNS[state.pos].turn && !state.revealed) cell.classList.add("current");
    cell.setAttribute("aria-label", `turn ${t.turn + 1}: ${pick ? "picked " + pick : "unpicked"}`);
    rail.append(cell);
  }
}

function render() {
  const t = TURNS[state.pos];
  const picking = !state.revealed;
  $("reveal").hidden = picking;
  for (const id of ["lanes", "pickA", "pickTie", "pickB", "backBtn", "revealBtn"]) {
    const el = $(id);
    if (el.tagName === "BUTTON") el.disabled = !picking;
  }
  $("lanes").style.display = picking ? "" : "none";
  document.querySelector(".pickbar").style.display = picking ? "" : "none";
  document.querySelector(".context").style.display = picking ? "" : "none";

  if (picking) {
    $("progress").textContent = `Turn ${state.pos + 1} of ${N}`;
    $("userText").textContent = t.user;
    renderProse($("proseA"), laneText(t, "A"));
    renderProse($("proseB"), laneText(t, "B"));
    $("lanes").scrollTop = 0;
    const cur = state.picks[t.turn];
    for (const [id, v] of [["pickA", "A"], ["pickTie", "tie"], ["pickB", "B"]]) {
      $(id).setAttribute("aria-pressed", cur === v ? "true" : "false");
    }
    $("backBtn").disabled = state.pos === 0;
  } else {
    $("progress").textContent = "Verdict";
    renderReveal();
  }
  renderRail();
  const { a, b, tie } = tally();
  $("compactStatus").textContent = `A ${a} · B ${b} · tie ${tie} — turn ${Math.min(state.pos + 1, N)}/${N}`;
}

function pick(choice) {
  // Advance synchronously in the click handler: a deferred setTimeout advance
  // can be lost when the host refreshes the iframe between click and timer,
  // which stranded picks on the current turn.
  const t = TURNS[state.pos];
  state.picks[t.turn] = choice;
  if (TURNS.every((x) => state.picks[x.turn])) {
    state.revealed = true;
    save();
    render();
    return;
  }
  const next = TURNS.findIndex((x, i) => i > state.pos && !state.picks[x.turn]);
  const jump = next !== -1 ? next : state.pos + 1;
  if (jump < N) state.pos = jump;
  save();
  render();
}

function renderReveal() {
  const mapLines = TURNS.map((t) => {
    const aM = IDENT[laneModel(t.turn, "A")];
    const bM = IDENT[laneModel(t.turn, "B")];
    return `T${t.turn}: A=${aM}, B=${bM}`;
  });
  $("revealMap").textContent = `A/B assignment per turn — ${mapLines.join(" · ")}`;
  const wins = { m1: 0, m2: 0, tie: 0 };
  for (const t of TURNS) {
    const p = state.picks[t.turn];
    if (p === "tie") wins.tie++;
    else if (p) wins[laneModel(t.turn, p)]++;
  }
  const dl = $("totals");
  dl.replaceChildren();
  for (const [label, val] of [[IDENT.m1, wins.m1], [IDENT.m2, wins.m2], ["ties", wins.tie]]) {
    const div = document.createElement("div");
    const dt = document.createElement("dt"); dt.textContent = label;
    const dd = document.createElement("dd"); dd.textContent = String(val);
    div.append(dt, dd); dl.append(div);
  }
  const ol = $("perTurn");
  ol.replaceChildren();
  for (const t of TURNS) {
    const p = state.picks[t.turn];
    const li = document.createElement("li");
    li.textContent = p
      ? (p === "tie" ? `Turn ${t.turn + 1} — tie` : `Turn ${t.turn + 1} — ${IDENT[laneModel(t.turn, p)]}`)
      : `Turn ${t.turn + 1} — unpicked`;
    ol.append(li);
  }
}

$("pickA").addEventListener("click", () => pick("A"));
$("pickTie").addEventListener("click", () => pick("tie"));
$("pickB").addEventListener("click", () => pick("B"));
$("backBtn").addEventListener("click", () => { if (state.pos > 0) { state.pos--; save(); render(); } });
$("revealBtn").addEventListener("click", () => { state.revealed = true; save(); render(); });
$("resetBtn").addEventListener("click", () => { state = freshState(); save(); render(); });

render();
</script>
</body>
</html>
"""

html = TEMPLATE.replace("/*__DATA__*/", data).replace("__IDENT_B64__", ident)
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "index.html").write_text(html, encoding="utf-8")
print(f"wrote {OUT / 'index.html'} ({len(html)} bytes, {len(turns)} turns)")
