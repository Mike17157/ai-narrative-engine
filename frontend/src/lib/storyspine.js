// ONE source of truth for a story's arc SPINE (arcs → scenes), so the Arcs page, the Scenes pane, and
// the left navigator never drift. Maps real st.arcs where present; else a representative, CONCRETE
// example. Plain language, no craft jargon — a PLAN is meant to read clearly. Replaced by the story
// master's real beats once the spine runs.
//
// Each arc has a ONE-WORD name and a concrete OBJECTIVE. ACCESS = how much of the story's hidden
// truth is open (3 tiers, not many); the menu colours arcs by it so the arc list is legible at a
// glance. Several arcs can share a tier — the truth opens in a few big steps, not a long ladder.
import { charName } from './characters.svelte.js';

// ACCESS = how deep into the CAST'S CARDS the story has gone at this arc. The secrets ARE the
// characters' depth ladder (tell → shape → full) — NOT an arbitrary plot scale. An arc's access is
// simply the deepest tier any character's card has opened by then.
export const ACCESS = {
  1: { label: 'tells only',  color: '#7b8496' },   // surface behaviour; no character secret open yet
  2: { label: 'wounds open', color: '#d68cff' },   // shape tier — the outline of a character's wound
  3: { label: 'hearts open', color: '#ff6d6d' },   // full tier — the act of love beneath it
};

function walkScenes(arc) {
  const map = arc?.nodes || {}; const keys = Object.keys(map);
  if (!keys.length) return [];
  const out = []; const seen = new Set(); let cur = arc.start || keys[0];
  while (cur && !seen.has(cur)) {
    seen.add(cur); const n = map[cur]; if (!n) break;
    out.push({ title: n.title || n.name || `Scene ${out.length + 1}`, beat: n.summary || '',
      day: '', tone: '', pov: '', location: n.location || '', cast: n.characters || [], serves: '', shadow: '' });
    cur = n.next?.[0] || null;
  }
  return out;
}

export function arcSpine(st) {
  const cast = st?.cast || [];
  const primaryK = cast.find((m) => m.primary)?.character || cast[0]?.character;
  const P = charName(primaryK) || 'the hero';
  const others = cast.filter((m) => !m.primary).map((m) => charName(m.character)).filter(Boolean);
  const A = others[0] || 'a friend';
  const B = others[1] || others[0] || 'a companion';
  const names = (st?.locations || []).map((l) => l.name).filter(Boolean);
  const loc = (i, fb) => names[i] || fb;

  const real = (st?.arcs || []).map((a, i) => ({
    id: a.id, n: i + 1, title: a.name || `Arc ${i + 1}`, tag: a.dramatic_function || '',
    access: Math.min(3, Math.ceil((i + 1) / 2)),
    mission: a.dramatic_function || a.rationale || '', boundary: a.mini_ending || '', ceiling: '',
    gates: (a.cast || []).map((k) => ({ key: k, tier: k === primaryK ? 'tell' : 'present' })),
    scenes: walkScenes(a),
  }));
  if (real.length) return { arcs: real, isExample: false, primaryK };

  // The SECRETS are the cast's cards: each character's ladder opens tell → shape → full on a schedule
  // (their wounds mid-journey, their hearts at/after the dark reveal). An arc's gates = each
  // character's tier there; its access = the deepest tier open. When real cards exist, each card names
  // its OWN reveal arcs — this per-character schedule is the placeholder, NOT an arbitrary plot scale.
  const RANK = { tell: 1, shape: 2, full: 3 };
  const fullArc = cast.map((m, i) => Math.min(5, 4 + i));    // when each character's heart (full) opens
  const gatesAt = (n) => cast.map((m, i) => ({ key: m.character,
    tier: n >= fullArc[i] ? 'full' : (n >= 3 ? 'shape' : 'tell') }));
  const S = (title, day, tone, li, cst, beat, serves, shadow, climax = false) =>
    ({ title, day, tone, pov: P, location: loc(li, li), cast: cst, beat, serves, shadow, climax });

  const arcs = [
    { id: 'ex1', n: 1, title: 'Departure', tag: 'gather',
      objective: `Gather the money, allies and supplies to leave home and get to the bottom of it — what happened to ${P}'s father, and why the country has begun to fade.`,
      boundary: `${P} has what he needs and sets out. There's no more pretending things are fine at home.`,
      ceiling: `Nothing overtly strange yet — only a country that is somehow too beautiful, and a grief no one will name.`,
      scenes: [
        S('The too-bright morning', 'Day 1 · dawn', 'wistful, uneasy', 2, [P], `${P} wakes to a country so green and golden it aches to look at — and lately, at the far edges, the colour has begun to thin.`, `Establish the beautiful land and the first hint that it's fading.`, `The beauty was bought, and the price has stopped being paid.`),
        S(`His father's medal`, 'Day 1 · morning', 'tender, aching', 2, [P], `Sorting old things, ${P} finds his father's medal — a man once radiant with pride for this country, who walked out one day and never came back.`, `Plant the personal drive: why would a proud, happy man simply vanish?`, `His father learned what the country is really built on.`),
        S('Asking for coin', 'Day 2 · day', 'awkward, stubborn', 1, [P, B], `${P} goes door to door trying to raise money for the road. Most doors close — but ${B} hears him out.`, `A concrete, humble objective: a journey costs money and goodwill.`, `Why the village would rather he stayed home.`),
        S('A patron with terms', 'Day 3 · evening', 'tense, transactional', 3, [P, A], `Someone of means agrees to fund ${P}'s journey — if he carries a message, and asks certain questions, on their behalf.`, `Get the resources, but tangle ${P} in someone else's agenda.`, `What the patron already suspects about the fading.`),
        S('What to take, who to trust', 'Day 4 · day', 'warm, nervous', 4, [P, B], `${P} packs light, and ${B} decides — over his protests — to come along.`, `Turn a lonely quest into a two-person journey; bank warmth before the road.`, `${B}'s own reason for wanting to leave.`),
        S('The road out', 'Day 5 · dawn', 'bittersweet, resolute', 1, [P, B], `${P} and ${B} leave the village behind and step onto the open road, the bright country rolling away ahead of them.`, `The objective met — resources gathered, the journey begins.`, `The answers lie much further than either of them thinks.`, true),
      ] },
    { id: 'ex2', n: 2, title: 'Training', tag: 'learn',
      objective: `On the road, learn who is really fighting over the country's fate — the factions, what they each believe, and where ${P} might stand.`,
      boundary: `${P} understands the sides and their stakes. He can't treat any of them as strangers anymore.`,
      ceiling: `Talk, tests and glimpses — the conflict is human and ideological, not yet monstrous.`,
      scenes: [
        S('Two names on every tongue', 'Day 8 · day', 'watchful', 9, [P, B], `In the first town on the road, ${P} hears the same two names spat and praised in the same breath — the factions dividing the country.`, `Introduce the sides as lived reality before explaining them.`, `Both sides think they are saving everyone.`),
        S('A hard teacher', 'Day 10 · day', 'wry, demanding', 9, [P], `An old hand agrees to teach ${P} what the country's real history is — and how to survive in it.`, `Give ${P} a mentor and the reader a way to learn the world.`, `The teacher lost someone to the practice.`),
        S("The keepers' creed", 'Day 12 · day', 'solemn', 9, [P, B], `${P} sits with the faction that wants the old practice restored: the land was a paradise, and paradise has a price.`, `Give the "bring it back" side genuine moral weight.`, `What, exactly, the price is.`),
        S("The abolishers' creed", 'Day 14 · night', 'urgent', 9, [P, B], `He hears the other side: whatever it cost, no country is worth that, and they will burn to keep it ended.`, `Give the opposing side equal weight; make neutrality impossible.`, `That they know the land may die without it.`),
        S('Proving it', 'Day 16 · day', 'tense, physical', 9, [P], `A test — of nerve, or blade, or word — decides whether either side will trust ${P} at all.`, `Earn ${P} a place in the story's conflict.`, `Someone is watching to see which way he leans.`),
        S('Asked to choose', 'Day 18 · dusk', 'pressured', 9, [P, A], `Both sides press ${P} for an answer. He puts them off — but he can't much longer.`, `Raise the stakes; the choice is coming.`, `He already leans one way and won't admit it.`, true),
      ] },
    { id: 'ex3', n: 3, title: 'Uprising', tag: 'act',
      objective: `Take real action in the unrest tearing the country apart — pick a side in an actual clash, and pay for it.`,
      boundary: `${P} is no longer an observer. Blood has been spilled with his help, and there's no walking it back.`,
      ceiling: `The danger is human and immediate — riots, raids, and reprisals — the stakes made of people, not spectacle.`,
      scenes: [
        S('The square catches fire', 'Day 20 · day', 'chaotic', 9, [P, B], `A protest over the fading land tips into a riot, and ${P} and ${B} are caught in the middle of it.`, `Throw ${P} bodily into the conflict he's been studying.`, `Who lit the first spark, and why.`),
        S('A door that must open', 'Day 22 · night', 'tense, quiet', 9, [P, B], `${P} joins a raid to reach something — someone, a record, a prisoner — locked away by the other side.`, `Give the action a concrete, human objective.`, `What they'll find is worse than what they came for.`),
        S('The cost', 'Day 23 · night', 'grief', 9, [P, A], `The raid succeeds, but not cleanly. Someone ${P} has come to care about pays for it.`, `Make the action hurt; raise the personal stakes.`, `That ${P}'s choice put them there.`),
        S('Named and hunted', 'Day 25 · day', 'cold', 9, [P, B], `Now that ${P} has acted, the losing side knows his face — and comes looking.`, `Turn ${P} from a participant into a target.`, `Someone close is feeding them information.`),
        S('A foothold, hard-won', 'Day 27 · dusk', 'weary, resolved', 9, [P, A], `Bloodied, ${P} and his side seize a real advantage — and with it, a way toward the thing at the country's centre.`, `Convert the fighting into forward motion toward the truth.`, `The advantage is bait.`, true),
      ] },
    { id: 'ex4', n: 4, title: 'Sacrifice', tag: 'reveal',
      objective: `Reach the heart of the country and uncover the truth the whole land is built on.`,
      boundary: `${P} knows everything now. The bright country and its horror are the same thing, and he can never un-see it.`,
      ceiling: `The full truth, laid bare — the grand array, the ritual, the King's choice, and where ${P}'s father fits.`,
      scenes: [
        S('The grand array', 'Day 30 · dawn', 'awed, cold', 9, [P, B], `${P} reaches the vast ringed array at the country's centre — a place of terrible, deliberate beauty.`, `Arrive at the heart of the mystery; let its scale land.`, `What the array is for.`),
        S('One name a year', 'Day 30 · day', 'sick dread', 9, [P], `In the records ${P} finds it plainly: one pure child, every single year, going back further than anyone living remembers.`, `Deliver the shape of the horror through cold documentation first.`, `How the children were chosen.`),
        S('What the array does', 'Day 31 · dawn', 'unbearable', 9, [P], `${P} learns the whole of it: a single pure child stands at the centre of the array and is dissolved into motes of mana that spread out and keep the country beautiful and alive.`, `The central reveal — monstrous, and the source of everything lovely he grew up in.`, `That it works. That it truly is what kept the land alive.`),
        S("The King's mercy", 'Day 32 · day', 'bitter', 9, [P, A], `${P} learns why the land is dying now: the King grew sick of the practice and ended it — and without it the country began to fade, and the unrest exploded out of control.`, `Explain the present crisis and complicate the "good" choice.`, `That mercy is what's killing everyone.`),
        S("His father's part", 'Day 33 · night', 'devastating', 9, [P], `And at last, ${P} learns what his radiant, proud father did — and why he walked away one day and never came home.`, `Collapse the whole cosmic horror into one grieving son.`, `Whether his father is still alive.`),
        S('The weight of knowing', 'Day 34 · dawn', 'hollow, then hard', 9, [P, A, B], `${P} carries the full truth back to the people who trusted him, and none of them are the same after.`, `Turn knowledge into the pressure that forces the finale.`, `What each of them will do with it.`, true),
      ] },
    { id: 'ex5', n: 5, title: 'Reckoning', tag: 'decide',
      objective: `Decide what to do with the truth — let the practice return and the country live, or let it die free — and live with the cost.`,
      boundary: `The country's fate is settled by ${P}'s choice, and the world is permanently changed by it.`,
      ceiling: `Everything is on the table: the array, the factions, the throne, and every secret finally in the open.`,
      scenes: [
        S('Both sides converge', 'Day 36 · day', 'gathering storm', 9, [P, A, B], `Word of what ${P} knows spreads, and both factions move on the array — one to light it, one to break it forever.`, `Set the board for the finale; everyone in motion.`, `Which of ${P}'s friends is on which side.`),
        S('The child at the centre', 'Day 37 · dawn', 'agonising', 9, [P], `${P} finds that a child has already been chosen and brought to the array — the ritual is one word away from beginning.`, `Make the abstract choice unbearably concrete: a real child, right now.`, `Whether ${P} could stop it and still save the land.`),
        S("A friend's last stand", 'Day 37 · day', 'heartbreaking', 9, [P, B], `${B} buys ${P} the moment he needs, at the price they'd been dreading all along.`, `The journey's companionship comes due.`, `What ${B} says to him at the end.`),
        S('The choice', 'Day 38 · dawn', 'climactic', 9, [P, A], `Alone at the centre of the array, with the country dying on one side and a child's life on the other, ${P} chooses — and the land answers.`, `The story's central question is answered by an act, not a speech.`, `The cost of whichever way he chose.`, true),
        S('After', 'Day 40 · day', 'mournful, hopeful', 9, [P, A], `In the quiet after, the country and ${P} are both remade, and he lives with exactly what his choice bought.`, `Let the new world and its price be felt.`, `What still waits at the centre of the array.`),
      ] },
  ].map((a) => {
    const gates = gatesAt(a.n);   // per-character card tiers at this arc
    return { ...a, mission: a.objective, gates, access: Math.max(1, ...gates.map((g) => RANK[g.tier] || 1)) };
  });

  return { arcs, isExample: true, primaryK };
}
