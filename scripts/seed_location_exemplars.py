"""Seed the `_location_exemplars` reference book — compact studies of memorable FICTIONAL
PLACES (anime, web serials, fantasy), written as register exemplars for location generation.

Each study captures what makes a place feel LIVED-IN: function first, working routines,
recurring concrete particulars, and how the story actually USES the place. Retrieved by
similarity into location/world-generation prompts.

Run: python scripts/seed_location_exemplars.py   (idempotent upserts)
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loom.config.schema import LoreEntry  # noqa: E402
from loom.server.services import lorebook_store as LS  # noqa: E402

SCOPE = "_location_exemplars"

# (id, title, keywords, study) — studies in our own words; register references, never copied text.
CARDS = [
    ("wandering-inn", "The inn on the hill (TWI)", ["inn", "hub", "kitchen", "found family", "frontier"],
     "WHAT: a battered two-story inn on a hill outside a city's walls, run shorthanded.\n"
     "ALIVE BECAUSE: breakfast has to happen whether or not the world ended last night — someone is "
     "always chopping, mopping, or arguing over the menu; regulars claim the same tables; the door "
     "opens for monsters and adventurers on the same hinge.\n"
     "PARTICULARS: a magical door that guests step through from other cities; a chalkboard of house "
     "rules (no killing goblins); a chess set that's always mid-game.\n"
     "ROLE: the hub every arc returns to — neutral ground where enemies must share a room, and the "
     "thing the cast rebuilds every time it burns."),
    ("bathhouse", "The spirits' bathhouse (Spirited Away)", ["bathhouse", "workplace", "spirits", "hierarchy", "hub"],
     "WHAT: a towering bathhouse serving spirit clientele, staffed top to bottom like a hotel.\n"
     "ALIVE BECAUSE: it runs on shifts — boiler stoked below, tokens sent down on ropes for bath "
     "water, floors scrubbed before opening, workers eating rice in cramped dorms after close. Rank "
     "decides who scrubs the big tub.\n"
     "PARTICULARS: the wooden bath tokens; the soot-and-coal boiler room with its own tiny workforce; "
     "the foreman's counter where names and contracts are kept.\n"
     "ROLE: home-that-is-a-job — the heroine's whole arc is measured in tasks done well here, and "
     "every stranger who walks in changes the workplace."),
    ("ankh-morpork", "The twin city on the river (Discworld)", ["city", "market", "guilds", "crime", "hub", "crossroads"],
     "WHAT: a sprawling river city that works despite itself — commerce, guilds, and a licensed "
     "criminal underworld.\n"
     "ALIVE BECAUSE: everything is somebody's job, including theft (receipts issued); the river is "
     "thick enough to walk on; sausage vendors, beggars with union cards, and night watchmen all keep "
     "regular hours.\n"
     "PARTICULARS: a sausage seller who appears at every disaster; guild houses with brass plaques; "
     "the perpetually rebuilt shonky shops that burn down for insurance.\n"
     "ROLE: crossroads where every stranger, scheme, and invasion arrives — the city itself is the "
     "recurring character the cast polices, fleeces, or saves."),
    ("the-wall", "The ice wall garrison (ASOIAF)", ["wall", "garrison", "frontier", "workplace", "duty", "cold"],
     "WHAT: a colossal ice wall on a kingdom's northern border, manned by a shrunken order of "
     "sworn watchmen.\n"
     "ALIVE BECAUSE: it's mostly maintenance — hauling barrels up the winch cage, mucking stables, "
     "training in the yard at dawn, eating the same stew in a drafty common hall; too few men for too "
     "many miles, and everyone knows it.\n"
     "PARTICULARS: the creaking winch elevator; the armory where recruits are measured and found "
     "wanting; the weirwood grove beyond the gate where oaths are said.\n"
     "ROLE: a home that must be defended by people sent there as punishment — duty as setting; every "
     "arrival at the gate is a plot."),
    ("aria-cafe", "The canal-side café (Aria)", ["cafe", "canals", "workplace", "apprentice", "routine"],
     "WHAT: a small café-and-boathouse in a canal city, doubling as a gondolier company's office.\n"
     "ALIVE BECAUSE: mornings are oar practice and hull scrubbing before a single customer arrives; "
     "the espresso machine runs for regulars who come to gossip with the trainees; tourist seasons "
     "and tides set the schedule.\n"
     "PARTICULARS: the company's single well-worn gondola; a fat cat who is technically the company "
     "president; the mooring post the apprentice ties badly every time.\n"
     "ROLE: the home base an apprenticeship orbits — small daily failures and customers who wander in "
     "carry whole episodes."),
    ("dungeon-camp", "The dungeon cook-camps (Delicious in Dungeon)", ["dungeon", "kitchen", "camp", "workplace", "survival"],
     "WHAT: temporary kitchen-camps pitched inside a monster-filled dungeon, wherever a corridor "
     "is defensible.\n"
     "ALIVE BECAUSE: every stop is the same labor — butcher the kill, sort what's edible, balance the "
     "pot on a portable stove, argue about seasoning while someone keeps watch; cleanup before moving "
     "on, because leftovers attract things.\n"
     "PARTICULARS: the folding cookpot and knife kit; the self-styled expert's hand-drawn monster "
     "recipe notes; the watch rotation nobody wants before dawn.\n"
     "ROLE: the moving hearth — each camp scene is where the party actually talks, and the meal is "
     "how the story digests the last fight."),
    ("cafe-alpha", "Café Alpha on the coast road (YKK)", ["cafe", "coast", "solitude", "routine", "post-apocalypse"],
     "WHAT: a tiny coffee shop on a half-drowned coastal road, run alone by its android owner.\n"
     "ALIVE BECAUSE: business is one customer a week, so the work is upkeep — roasting the small "
     "coffee stock, patching the roof after storms, riding to town for supplies; the sea keeps "
     "eating the road and she keeps the café anyway.\n"
     "PARTICULARS: the hand-crank coffee grinder; the scooter with its sidecar of groceries; the "
     "camera she carries to keep what the water will take.\n"
     "ROLE: a fixed point in a slowly ending world — the story measures time by who stops in and "
     "what the tide has moved."),
    ("guild-hall", "The adventurers' guild hall", ["guild", "hub", "jobs", "tavern", "crossroads"],
     "WHAT: a combined job-board office, tavern, and clearing-house where freelance parties take "
     "contracts.\n"
     "ALIVE BECAUSE: it runs on paperwork — receptionists stamping quest slips, appraisers weighing "
     "monster parts at the counter, veterans hogging the good table, novices misreading the board; "
     "payouts and brawls both happen before noon.\n"
     "PARTICULARS: the cork job-board with tiered postings; the materials counter and its scale; the "
     "rankings ledger everyone pretends not to check.\n"
     "ROLE: the crossroads where plots are handed out — strangers, rumors, and rivalries all enter "
     "through the same front door."),
    ("night-market", "The lantern night market", ["market", "night", "food stalls", "crossroads", "smuggling"],
     "WHAT: a street market that assembles at dusk — food stalls, fortune tellers, and stalls that "
     "sell things with no daytime name.\n"
     "ALIVE BECAUSE: setup is its own ritual: carts wheeled in, lanterns strung, oil heating before "
     "the first customers; stallholders mind each other's pitches, and everyone knows which alley "
     "the watch doesn't walk.\n"
     "PARTICULARS: a skewer stall that anchors one end; the lantern-lighting that opens trading; the "
     "unspoken rule that debts made here are settled here.\n"
     "ROLE: crossroads-after-dark — where the cast buys what it shouldn't, meets informants, and is "
     "seen by exactly the wrong person."),
    ("border-waystation", "The mountain-pass waystation", ["waystation", "frontier", "travelers", "inn", "crossroads", "snow"],
     "WHAT: a fortified rest stop at the top of a mountain pass — stable, common room, and a toll "
     "gate under one roof.\n"
     "ALIVE BECAUSE: it exists for the work of transit — reshoeing horses, drying soaked cloaks by "
     "the one big hearth, logging every traveler in the gate ledger; when snow closes the pass, "
     "whoever is inside stays for the week.\n"
     "PARTICULARS: the gate ledger of names and dates; the wall of unclaimed letters travelers leave "
     "for each other; the storeroom rationed by the keeper's iron key.\n"
     "ROLE: a bottleneck where strangers are forced together — snowed-in nights make confessions, "
     "and everything that crosses the border passes the keeper's desk."),
    ("clinic", "The back-alley clinic", ["clinic", "doctor", "slum", "workplace", "sanctuary"],
     "WHAT: an unlicensed clinic in a poor quarter, one doctor and whoever owes them a favor.\n"
     "ALIVE BECAUSE: care is triage and barter — stitches paid in eggs, the waiting bench full before "
     "the kettle boils, instruments cleaned at night because daylight is for patients; both gangs use "
     "it, so neither burns it.\n"
     "PARTICULARS: the debt book of unpaid treatments; the bell-rope patients pull after dark; the "
     "one locked cabinet nobody asks about.\n"
     "ROLE: neutral sanctuary — the place wounded plot arrives at, where enemies lie in adjacent cots "
     "and the doctor hears every secret in the district."),
    ("harbor-shipyard", "The working harbor shipyard", ["shipyard", "harbor", "workplace", "crew", "trade"],
     "WHAT: a commercial shipyard and dock — slipways, rope-walks, and a hiring office at the gate.\n"
     "ALIVE BECAUSE: it moves on tide tables — caulkers hammering at low tide, cranes swinging cargo "
     "at high, the hiring line forming at dawn for day work; the foreman's whistle divides the day, "
     "and gossip travels faster than manifests.\n"
     "PARTICULARS: the chalked tide-and-berth board; the hiring line at the gate; the half-finished "
     "hull that's been 'nearly done' for years.\n"
     "ROLE: the working world the crew belongs to — jobs, departures, and every arriving ship "
     "delivers strangers, cargo, and trouble in one manifest."),
]


def main() -> None:
    root = Path(".")
    LS.upsert_book(root, SCOPE, name="Location exemplars", category="craft", rating="sfw",
                   description="Register studies of lived-in fictional places (inns, bathhouses, "
                               "garrisons, markets): function first, working routines, recurring "
                               "particulars. Retrieved into location-generation prompts.")
    for eid, title, kws, study in CARDS:
        LS.upsert_entry(root, SCOPE, LoreEntry(
            id=f"loc-{eid}", title=title, keywords=kws, content=study, priority=1, source="manual"))
    print(f"seeded {len(CARDS)} location studies into book '{SCOPE}'")


if __name__ == "__main__":
    main()
