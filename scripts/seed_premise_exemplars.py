"""Seed the `_premise_exemplars` reference book — compact studies of story PREMISES that
sustained long serials/anime, written as register exemplars for premise generation.

Each study is 4 lines: SITUATION (a person + a place + a pressure), PRESSURE (what squeezes
them, plainly), ENGINE (the renewable source of new trouble/people), FIRST HOUR (how the
story opens thin — usually mundane work). Retrieved by similarity into genesis prompts.

Run: python scripts/seed_premise_exemplars.py   (idempotent upserts)
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loom.config.schema import LoreEntry  # noqa: E402
from loom.server.services import lorebook_store as LS  # noqa: E402

SCOPE = "_premise_exemplars"

# (id, title, keywords, study) — studies in our own words; register references, never copied text.
CARDS = [
    ("inn", "Stranded innkeeper", ["survival", "commerce", "food", "hospitality", "found family"],
     "SITUATION: a teenager stranded far from home takes over an abandoned inn on a road "
     "outside a city that distrusts her kind.\n"
     "PRESSURE: no coin and no stores — tonight's meal must be earned tonight, and the road "
     "outside has things that eat travelers.\n"
     "ENGINE: an inn is a door; every traveler, adventurer, refugee, and monster who walks in "
     "brings a new problem with them, so trouble delivers itself daily.\n"
     "FIRST HOUR: scrubbing a wrecked common room, foraging something edible, pricing a first "
     "meal, and getting through one night of noises outside."),
    ("shield", "Blacklisted defender", ["commerce", "grind", "deadline", "pariah", "peddling"],
     "SITUATION: a summoned defender is framed for a crime on day one and blacklisted by the "
     "very court that summoned him.\n"
     "PRESSURE: a monster wave arrives on a fixed public schedule; he must grow stronger, but "
     "no party will hire a pariah and every shop overcharges him.\n"
     "ENGINE: each countdown between waves is spent grinding trades, escort jobs, and village "
     "contracts — every town saved is a new market and a new enemy at court.\n"
     "FIRST HOUR: selling monster drops for pennies, learning which herbs fetch a margin, "
     "buying the one companion who legally cannot refuse to fight beside him."),
    ("dream", "Drafted slum scavenger", ["survival", "slum", "trials", "family", "rations"],
     "SITUATION: a slum scavenger supporting a sick sister is conscripted into a state program "
     "that sends the afflicted into a lethal shared dream.\n"
     "PRESSURE: dying in the dream is dying awake, and his sister's medicine, his rations, and "
     "his gear are all graded out by an indifferent bureaucracy.\n"
     "ENGINE: every nightmare is a fresh survival economy — new terrain, factions, loot — while "
     "the waking side stacks ranks, politics, and debts between runs.\n"
     "FIRST HOUR: counting coins, buying medicine, haggling for a knife, then a first dream "
     "night spent on nothing grander than water, fire, and a place to hide."),
    ("dungeon", "Eat-the-dungeon rescue", ["food", "dungeon", "deadline", "poverty", "cooking"],
     "SITUATION: a wiped adventuring party re-enters a dungeon to retrieve a sister swallowed "
     "near the bottom, before her body digests.\n"
     "PRESSURE: the rescue has a biological deadline and the wipe took all their money — they "
     "cannot afford rations, so they must eat what they kill on the way down.\n"
     "ENGINE: every floor is a new ecosystem, so every meal is a new problem — catch it, clean "
     "it, cook it — and each dish teaches how the dungeon actually works.\n"
     "FIRST HOUR: emptying purses on a table, recruiting the one eccentric who already cooks "
     "monsters, butchering a first kill into a first passable stew."),
    ("stone", "Stone-age rebuilder", ["rebuilding", "science", "survival", "craft", "rivalry"],
     "SITUATION: a science-obsessed teenager wakes millennia after humanity turned to stone, "
     "alone in overgrown wilderness.\n"
     "PRESSURE: calories, fire, and shelter first; then a rival who revives strong men faster "
     "than he can revive thinkers.\n"
     "ENGINE: the tech tree is the plot — soap, iron, glass, antibiotics each demand an "
     "expedition, labor, and bargaining, and every milestone unlocks the next want.\n"
     "FIRST HOUR: making fire, drying meat, counting food stores, and weeks of drip-slow "
     "chemistry in a cave to free one more pair of hands."),
    ("vinland", "Revenge apprentice", ["revenge", "mercenary", "war", "apprentice", "travel"],
     "SITUATION: a boy joins the mercenary crew of the man who killed his father, serving as "
     "its scout and errand-runner.\n"
     "PRESSURE: the only sanctioned path to his revenge duel is usefulness — so every job he "
     "takes makes the man he means to kill richer and harder to reach.\n"
     "ENGINE: a mercenary band follows the wars and the pay; each contract lands the crew in a "
     "new port, siege, or betrayal, and each deepens the contradiction he lives inside.\n"
     "FIRST HOUR: camp chores, scouting a village before a raid, demanding a duel, being "
     "laughed at, and doing the next job anyway."),
    ("horizon", "Governance after apocalypse", ["governance", "economy", "city", "guilds", "food"],
     "SITUATION: thirty thousand players wake trapped inside their game's capital city, "
     "immortal, with food that tastes of wet cardboard.\n"
     "PRESSURE: nobody can starve, so nobody works — the city rots into apathy and predation "
     "on beginners, and order costs money no single guild owns.\n"
     "ENGINE: institutions are inexhaustible — currency, courts, food science, guild treaties, "
     "relations with native nations — and every one solved creates two new stakeholders.\n"
     "FIRST HOUR: walking listless streets, buying flavorless bread, pulling one kidnapped "
     "beginner out of a bad contract, discovering hand-cooked food has taste."),
    ("bookworm", "Bookless bibliophile", ["commerce", "craft", "class", "books", "guilds"],
     "SITUATION: a book-lover is reborn as a sickly craftsman's daughter in a city where one "
     "book costs as much as a house.\n"
     "PRESSURE: a frail body, a family living wage-to-wage, and guilds that notice — anything "
     "she wants she must invent from kitchen materials and sell without giving offense.\n"
     "ENGINE: import-substitution as plot — paper, ink, printing each demand workshops, "
     "contracts, patrons, and every product pulls her one rung up a ladder with new gatekeepers.\n"
     "FIRST HOUR: failing to make papyrus from dead grass, failing clay tablets, minding a "
     "market stall, learning that a merchant's spoken word binds like law."),
    ("spice", "Merchant and deity", ["commerce", "travel", "currency", "harvest", "negotiation"],
     "SITUATION: a traveling merchant with one wagon takes on a passenger — a harvest deity "
     "quitting her village and riding north with him.\n"
     "PRESSURE: margins are thin; one mispriced cargo or currency swing ruins him, and his "
     "passenger's appetite and pride are line items too.\n"
     "ENGINE: road commerce resets every town — new coinage, new guild politics, a new scheme "
     "to sniff out or fall into — so profit and trust renegotiate at each stop.\n"
     "FIRST HOUR: loading pelts, arguing exchange rates, and testing whether a rumor about "
     "debased silver coin is an opportunity or a trap."),
    ("kino", "Three-day traveler", ["travel", "countries", "deadline", "observer", "provisions"],
     "SITUATION: a traveler with a talking motorcycle crosses from country to country, staying "
     "exactly three days in each.\n"
     "PRESSURE: the self-imposed three-day rule — long enough to see how a place works, too "
     "short to fix it — plus fuel, food, and ammunition to budget between borders.\n"
     "ENGINE: the route never ends; every crossing delivers a new closed society with one "
     "strange rule, and the deadline forces a complete story arc per stop.\n"
     "FIRST HOUR: engine maintenance, buying provisions, small talk with a border guard, and "
     "asking what this country's one custom is."),
    ("frieren", "Outliving the party", ["travel", "grief", "time", "apprentice", "errands"],
     "SITUATION: an elf mage who outlived her hero party takes an apprentice and retraces the "
     "old quest route to the far north.\n"
     "PRESSURE: human lifespans — everyone she means to revisit is dying on a schedule she "
     "keeps misjudging, while exams, monsters, and winters gate the roads.\n"
     "ENGINE: the route dispenses story by the mile — each town holds a memory of the old "
     "journey, a leftover obligation, and a present-day errand that pays the road.\n"
     "FIRST HOUR: a funeral, an apprentice she didn't ask for, and small paid magic jobs — "
     "finding lost earrings, clearing chores — to fund the next leg."),
    ("mushishi", "Itinerant spirit doctor", ["travel", "spirits", "villages", "barter", "mystery"],
     "SITUATION: an itinerant specialist walks between mountain villages diagnosing ailments "
     "caused by primitive lifeforms most people cannot see.\n"
     "PRESSURE: he can never settle — his own body draws the creatures — and he lives on fees, "
     "barter, and the sale of rare specimens to a collectors' network.\n"
     "ENGINE: house-call structure — every village has one affliction, one family secret "
     "tangled in it, and one imperfect remedy; the road manufactures cases forever.\n"
     "FIRST HOUR: walking a forest path, lodging with the client family, listening to "
     "symptoms, and trading medicine for room and board."),
]


def main() -> None:
    root = Path(".")
    LS.upsert_book(root, SCOPE, name="Premise exemplars", category="craft", rating="sfw",
                   description="Studies of premises that sustained long serials: situation, "
                               "pressure, story engine, and a thin first hour.")
    for eid, title, kws, study in CARDS:
        LS.upsert_entry(root, SCOPE, LoreEntry(
            id=f"pr-{eid}", title=title, keywords=kws, content=study, priority=1, source="manual"))
    print(f"seeded {len(CARDS)} premise studies into book '{SCOPE}'")


if __name__ == "__main__":
    main()
