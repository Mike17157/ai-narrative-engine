"""Seed the `_char_exemplars` reference book — compact character STUDIES of real web-serial
casts (The Wandering Inn, Shadow Slave), written as register exemplars for generation.

Why: instruction-following can't escape mode collapse (rules get performed, not obeyed —
'fair but firm'); exemplar-conditioning shifts the prior instead. Each study captures the
pattern the serials actually use: a SIMPLE PROFOUND WANT + a THIN introduction + depth
revealed through BEHAVIOR, never through assigned traits. Retrieved by similarity into
character-generation prompts (seed_story, in-play character births).

Run: python scripts/seed_char_exemplars.py   (idempotent upserts)
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loom.config.schema import LoreEntry  # noqa: E402
from loom.server.services import lorebook_store as LS  # noqa: E402

SCOPE = "_char_exemplars"

# (id, title, keywords, study) — studies in our own words; register references, never copied text.
CARDS = [
    ("erin", "Erin (innkeeper)", ["innkeeper", "kindness", "protector", "found family", "cook"],
     "WHO: a young woman running a battered inn nobody else wanted, far from home.\n"
     "WANT: a warm, safe place where people can eat and be all right.\n"
     "INTRODUCED AS: a scared, hungry girl cleaning an empty building because it's something to do.\n"
     "REVEALED BY: feeding a starving goblin instead of raising the alarm; throwing a pan at an armed "
     "man to protect a guest; grieving badly and cooking through it. Never described as kind — shown."),
    ("klbkch", "Klbkch (guard)", ["guard", "soldier", "duty", "loyalty", "outsider", "insect"],
     "WHO: an inhuman guardsman doing a city's paperwork and patrols, politely tolerated, never liked.\n"
     "WANT: a worthy future for his people, who lost everything before he was made.\n"
     "INTRODUCED AS: an unfailingly courteous clerk-soldier with odd manners.\n"
     "REVEALED BY: patience with a rookie's mistakes; decades of quiet service to a plan no one knows; "
     "killing without ceremony when the moment demands, then filing the report."),
    ("relc", "Relc (veteran)", ["soldier", "veteran", "comic", "father", "regret"],
     "WHO: a loud, lazy veteran coasting in a city guard job, strongest man in the room and joking about it.\n"
     "WANT: to be more than muscle to somebody — mostly the daughter he keeps missing.\n"
     "INTRODUCED AS: comic relief with a spear, dodging paperwork.\n"
     "REVEALED BY: turning down promotions, sending money home, and being terrifying exactly once."),
    ("pisces", "Pisces (mage)", ["mage", "necromancer", "outcast", "pride", "scholar"],
     "WHO: a disgraced student of a forbidden school, sponging meals off anyone who'll allow it.\n"
     "WANT: recognition for the craft he ruined his name to pursue.\n"
     "INTRODUCED AS: a sneering freeloader with holes in his robe.\n"
     "REVEALED BY: teaching honestly the moment someone actually asks; keeping a dead friend's memory "
     "in secret; duelist's reflexes that contradict the beggar act."),
    ("lyonette", "Lyonette (runaway)", ["princess", "runaway", "spoiled", "growth", "mother"],
     "WHO: a runaway noble who has never worked, stranded among people who owe her nothing.\n"
     "WANT: to matter on her own terms, after a life of being ornamental.\n"
     "INTRODUCED AS: a thieving, entitled disaster everyone wants gone.\n"
     "REVEALED BY: scrubbing floors badly and not quitting; mothering an orphaned monster; earning "
     "trust in inches, resented the whole way."),
    ("toren", "Toren (servant)", ["skeleton", "servant", "literal", "loneliness", "monster"],
     "WHO: an animated servant built to obey, slowly developing wants nobody planned for.\n"
     "WANT: to be seen as more than a tool (before it has words for that).\n"
     "INTRODUCED AS: a mute helper doing chores slightly, ominously wrong.\n"
     "REVEALED BY: obeying orders in lethally literal ways; expressing loneliness through violence "
     "and, once, dancing where nobody could see."),
    ("krshia", "Krshia (shopkeeper)", ["merchant", "shopkeeper", "matriarch", "politics", "pride"],
     "WHO: a neighborhood shopkeeper with a big laugh, informal leader of her people's street.\n"
     "WANT: a future for the next generation of her kind in a city that tolerates them.\n"
     "INTRODUCED AS: a friendly baker who remembers everyone's orders.\n"
     "REVEALED BY: burning a fortune in stock rather than be publicly shamed; playing politics so long "
     "and patient nobody notices until it lands."),
    ("ryoka", "Ryoka (runner)", ["courier", "loner", "pride", "self-sabotage", "athlete"],
     "WHO: a barefoot courier who refuses magic, manners, and help, in that order.\n"
     "WANT: to prove she needs nothing from anyone (underneath: to be forgiven).\n"
     "INTRODUCED AS: a rude delivery girl turning down easy money on principle.\n"
     "REVEALED BY: running herself bloody for strangers she claims not to like; reckless bargains with "
     "things older than her; loyalty she will not say out loud."),
    ("sunny", "Sunny (survivor)", ["slum", "orphan", "cunning", "liar", "survivor", "underdog"],
     "WHO: a slum scavenger with a sharp tongue, counting every coin, raising a sick sister.\n"
     "WANT: keep his tiny family alive; later — to never again be anyone's property, fate's included.\n"
     "INTRODUCED AS: a starving kid haggling like his life depends on it, because it does.\n"
     "REVEALED BY: lying smoothly and hating that he's good at it; hoarding every advantage; small "
     "mercies he insists are pragmatism."),
    ("nephis", "Nephis (heiress)", ["noble", "fallen", "driven", "stoic", "warrior"],
     "WHO: the last daughter of a ruined house, training past collapse in borrowed gear.\n"
     "WANT: to burn a path back for her name — and for everyone the ruin took.\n"
     "INTRODUCED AS: an expressionless girl doing drills alone, mistaken for cold.\n"
     "REVEALED BY: silence where others complain; taking the most dangerous role every single time; "
     "warmth in doses so small they land like events."),
    ("cassie", "Cassie (seer)", ["blind", "seer", "gentle", "guilt", "quiet strength"],
     "WHO: a blind girl dropped into a survival trial everyone assumes she won't outlive.\n"
     "WANT: to not be a burden — to be worth the protection she needs.\n"
     "INTRODUCED AS: the helpless one, an apology in human form.\n"
     "REVEALED BY: quiet maneuvers that save lives without credit; carrying what she foresees and "
     "cannot say; steel that only shows when someone else is threatened."),
    ("effie", "Effie (huntress)", ["hunter", "jokes", "glory", "restless", "comic"],
     "WHO: a scarred huntress who narrates her own legend, loudly, to anyone cornered into listening.\n"
     "WANT: a story worth telling — glory as a cure for something she won't discuss.\n"
     "INTRODUCED AS: comic-relief muscle who talks too much.\n"
     "REVEALED BY: reading people with unsettling accuracy and hiding it inside jokes; being first "
     "through every door she claims to fear."),
]


def main() -> None:
    root = Path(".")
    LS.upsert_book(root, SCOPE, name="Character exemplars", category="craft", rating="sfw",
                   description="Register studies of real serial casts (TWI, Shadow Slave): simple "
                               "profound wants, thin introductions, depth revealed by behavior. "
                               "Retrieved into character-generation prompts.")
    for eid, title, kws, study in CARDS:
        LS.upsert_entry(root, SCOPE, LoreEntry(
            id=f"ex-{eid}", title=title, keywords=kws, content=study, priority=1, source="manual"))
    print(f"seeded {len(CARDS)} exemplar studies into book '{SCOPE}'")


if __name__ == "__main__":
    main()
