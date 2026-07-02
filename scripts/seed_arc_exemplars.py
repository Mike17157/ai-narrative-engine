"""Seed the `_arc_exemplars` reference book — compact studies of well-built STORY ARCS from
serials/anime/manga, written as structural exemplars for arc generation.

Each study is 4 lines: SHAPE (setup → turn → payoff), ENGINE (what drives it turn to turn),
PLANTED/PAID (the slow-burn mechanic), EXIT (what changed + the new pressure it leaves).
Retrieved by similarity into plot/arc-generation prompts.

Run: python scripts/seed_arc_exemplars.py   (idempotent upserts)
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loom.config.schema import LoreEntry  # noqa: E402
from loom.server.services import lorebook_store as LS  # noqa: E402

SCOPE = "_arc_exemplars"

# (id, title, keywords, study) — studies in our own words; structure references, never copied text.
CARDS = [
    ("farm", "Farm arc (Vinland Saga)", ["farm", "slavery", "penance", "pacifism", "redemption"],
     "SHAPE: a revenge-hollowed warrior is sold as a farm slave → seasons of labor and a fellow "
     "slave's ruin crack his numbness → he earns back his own life with a purpose rebuilt from zero.\n"
     "ENGINE: the farm's work calendar — clear a forest, raise a crop, bank the purchase price, "
     "one season at a time.\n"
     "PLANTED/PAID: a father's dying claim that no one is born a slave and a true warrior needs no "
     "sword, heard by a child, becomes the grown man's own creed a decade later.\n"
     "EXIT: revenge is dead; in its place an impossible promise — a land with no war — and he sails "
     "for it owning nothing but the vow."),
    ("conspiracy", "Nationwide conspiracy (FMA: Brotherhood)", ["conspiracy", "mystery", "military", "investigation", "war"],
     "SHAPE: two brothers hunt a relic to fix their own bodies → every lead exposes a piece of a "
     "state-sized plot → the personal quest becomes a coup against the country's hidden ruler.\n"
     "ENGINE: the investigation trail — each lab, city, and officer pulled on unravels one more "
     "thread and costs one more ally.\n"
     "PLANTED/PAID: a genocide the state buried in the backstory turns out to be a rehearsal — the "
     "whole nation is one giant ritual circle, cashed at the finale.\n"
     "EXIT: the brothers stop being investigators and become the spearhead of a counter-conspiracy; "
     "every faction they recruited now owes and expects blood."),
    ("basement", "Basement key (Attack on Titan)", ["mystery", "siege", "secret", "war", "key"],
     "SHAPE: a walled humanity loses a district to giants → a father leaves his son a basement key "
     "→ reclaiming the ruined home turns out to be a race for the truth, not territory.\n"
     "ENGINE: each expedition and siege is justified as one more step toward the basement — the "
     "objective never moves, the cost keeps rising.\n"
     "PLANTED/PAID: the key shown in the opening hours is cashed seasons later; the basement holds "
     "not a weapon but a history that inverts who the monsters are.\n"
     "EXIT: the enemy is no longer the giants but a world across the sea; every prior victory is "
     "reframed as a small move in someone else's longer war."),
    ("ant", "Chimera Ant escalation (Hunter x Hunter)", ["escalation", "monster", "hunt", "war", "countdown"],
     "SHAPE: an offscreen pest report → man-eating insects that inherit human minds → a palace "
     "assault where the strongest human alive trades his life to stop a king learning mercy.\n"
     "ENGINE: the ants' feeding-and-breeding cycle sets a countdown; every day of human delay "
     "upgrades the enemy roster.\n"
     "PLANTED/PAID: the king's idle board-game hobby, dropped in as characterization, becomes the "
     "fulcrum — a blind human player teaches him the humanity his species devoured.\n"
     "EXIT: the boy hero wins by self-destruction and leaves the story broken; the organization "
     "that sent him is exposed as rotten, seeding the succession fight."),
    ("exam", "First-class exam (Frieren)", ["exam", "competition", "magic", "memory", "rivals"],
     "SHAPE: an ageless mage must sit a licensing exam far beneath her → team trials with hostile "
     "strangers → she advances not by raw power but by being read, for once, by someone else.\n"
     "ENGINE: staged tests with explicit closed rules — capture a warded bird, escape a replicating "
     "dungeon — each stage forcing new alliances.\n"
     "PLANTED/PAID: her habit of flashing back to a dead master's offhand lessons pays when a "
     "trial's real question is exactly the thing he taught her to notice about people.\n"
     "EXIT: the party gains legal passage north and a rival-turned-colleague; the exam's examiner "
     "now watches her journey with an agenda of her own."),
    ("inn", "Inn and goblins (The Wandering Inn)", ["inn", "goblin", "levels", "defense", "survival"],
     "SHAPE: a stranded girl claims a derelict inn → chores become levels, levels draw patrons, "
     "patrons draw enemies → a goblin horde forces the inn and the town to finally need each other.\n"
     "ENGINE: the innkeeper class itself — every meal cooked and guest sheltered is measurable "
     "progress, so daily work IS the plot.\n"
     "PLANTED/PAID: feeding one small goblin instead of raising the alarm pays chapters later, when "
     "an army arrives and one chieftain remembers being fed.\n"
     "EXIT: the town stops treating her as a joke; her kindness has made her responsible for "
     "nonhumans nobody else will protect, and that bill keeps arriving."),
    ("trial", "First Nightmare trial (Shadow Slave)", ["trial", "survival", "dungeon", "curse", "secret"],
     "SHAPE: a cursed slum kid is conscripted into a dream-realm trial → a forest that kills the "
     "careless → he escapes by out-thinking the nightmare rather than out-fighting it.\n"
     "ENGINE: survival economics — hunger, soul essence, a chain of small hunts, each kill buying "
     "exactly one upgrade toward the exit.\n"
     "PLANTED/PAID: his curse of seeing hidden truths, introduced as a handicap that ruins his "
     "life, is cashed when it reveals the trial's way out no one else can perceive.\n"
     "EXIT: he wakes ranked and rewarded — but the reward is a slave brand he must hide from his "
     "own side, making the prize itself the next arc's problem."),
    ("descent", "Descent by floors (Delicious in Dungeon)", ["dungeon", "descent", "cooking", "rescue", "journey"],
     "SHAPE: a sister is eaten by a dragon → her broke party descends floor by floor, eating the "
     "dungeon itself → the rescue succeeds, and the resurrection creates a worse problem.\n"
     "ENGINE: descent-by-floors — each floor is one ecosystem, one hazard, one meal; cooking is "
     "the pacing device that turns travel into chapters.\n"
     "PLANTED/PAID: eating monsters, a comic money-saving choice in chapter one, pays off when "
     "deep-dungeon law makes you-are-what-you-eat literal for the rescued girl.\n"
     "EXIT: the sister revives entangled with the dragon; rescue hands off to a hunt, and the party "
     "that broke the rules is now wanted by the island's authorities."),
    ("psychic", "Restraint under pressure (Mob Psycho 100)", ["school", "psychic", "cult", "mentor", "self-improvement"],
     "SHAPE: an overpowered psychic boy joins the fitness club instead of the espers courting him → "
     "cults and gangs keep testing the vow → his refusal to use power becomes his identity, until "
     "an overload forces the question.\n"
     "ENGINE: a running emotion meter — a visible percentage climbing toward explosion — paces "
     "every confrontation.\n"
     "PLANTED/PAID: his con-man mentor's fake wisdom, planted as comedy, pays when the one genuine "
     "lesson — it's fine to run away — ends a fight that force couldn't.\n"
     "EXIT: the boy keeps choosing muscles over miracles; the cult he cracked open reports upward, "
     "and its upper echelon now has his name."),
    ("trade", "Per-town trade scheme (Spice and Wolf)", ["trade", "merchant", "journey", "scheme", "currency"],
     "SHAPE: a peddler and a harvest goddess roll into a town → a currency scam or trade rumor "
     "promises a fortune → the scheme half-works and they leave richer in debt or in trust.\n"
     "ENGINE: one commercial mechanism per town — coin debasement, armor futures, smuggling — with "
     "real market logic doing the work a villain usually does.\n"
     "PLANTED/PAID: her northern homeland, named at the first campfire, converts every profit into "
     "progress and every loss into delay, so the ledger is always also a map.\n"
     "EXIT: each town closes with the balance changed and the partnership recalibrated — a new "
     "jealousy, promise, or debt that the next town's scheme will stress."),
    ("fracture", "Crew fracture (One Piece: Water 7)", ["crew", "fracture", "betrayal", "shipwright", "rescue"],
     "SHAPE: a crew docks to repair their dying ship → money vanishes, a crewmate disappears, the "
     "city turns on them → the betrayal is revealed as a member's sacrificial defection, launching "
     "a rescue against the world's government.\n"
     "ENGINE: shipwright-city logistics — the ship's death sentence, the stolen funds, and the "
     "search for a carpenter all funnel toward the same hidden culprit.\n"
     "PLANTED/PAID: the quiet crewmate's erased-island past, planted when she joined, pays when her "
     "defection proves to be blackmail built on that exact history.\n"
     "EXIT: the old ship is declared dead and mourned; the halved, bloodied crew declares war on "
     "the government, and the next arc is the assault that answers for it."),
]


def main() -> None:
    root = Path(".")
    LS.upsert_book(root, SCOPE, name="Arc exemplars", category="craft", rating="sfw",
                   description="Structural studies of well-built serial/anime arcs: shape, engine, "
                               "planted/paid slow-burns, and exits. Retrieved into arc-generation prompts.")
    for eid, title, kws, study in CARDS:
        LS.upsert_entry(root, SCOPE, LoreEntry(
            id=f"arc-{eid}", title=title, keywords=kws, content=study, priority=1, source="manual"))
    print(f"seeded {len(CARDS)} arc studies into book '{SCOPE}'")


if __name__ == "__main__":
    main()
