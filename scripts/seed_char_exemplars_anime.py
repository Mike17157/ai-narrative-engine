"""Append ~12 anime/manga character STUDIES to the existing `_char_exemplars` book.

Same register as seed_char_exemplars.py: SIMPLE PROFOUND WANT + THIN introduction +
depth revealed through BEHAVIOR, never trait words. Entry ids prefixed `ex-anime-`.

Run: python scripts/seed_char_exemplars_anime.py   (idempotent upserts; book must exist)
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loom.config.schema import LoreEntry  # noqa: E402
from loom.server.services import lorebook_store as LS  # noqa: E402

SCOPE = "_char_exemplars"

# (id, title, keywords, study) — studies in our own words; register references, never copied text.
CARDS = [
    ("thorfinn", "Thorfinn (farmhand)", ["warrior", "atonement", "slave", "farmer", "violence", "pacifist"],
     "WHO: a war orphan raised on a raiding ship, later sold as a farm slave with nothing left inside.\n"
     "WANT: to be square with the blood he spilled — a life that costs no one else anything.\n"
     "INTRODUCED AS: a feral boy with knives, useful to the man he wants dead.\n"
     "REVEALED BY: taking a hundred blows without returning one; planting a field and finding the work "
     "harder to face than battle; asking a man he wronged what he can possibly do about it now."),
    ("frieren", "Frieren (mage)", ["mage", "elf", "grief", "longevity", "journey", "regret"],
     "WHO: an elf mage who outlived the hero's party she barely paid attention to for ten short years.\n"
     "WANT: to understand a friend she only learned to miss after burying him.\n"
     "INTRODUCED AS: a detached collector of trivial spells, late to her own comrade's funeral.\n"
     "REVEALED BY: retracing a decades-old route just to see what he saw; hoarding a spell that makes "
     "flowers because someone once liked it; weeping at a grave fifty years too late and starting over."),
    ("fern", "Fern (apprentice)", ["apprentice", "orphan", "war", "diligence", "caretaker"],
     "WHO: a war orphan taken in by a priest, apprenticed to a mage who forgets to eat or wake up.\n"
     "WANT: to be able to stand on her own, so no one has to die making room for her.\n"
     "INTRODUCED AS: a quiet child practicing the same drill long past dark.\n"
     "REVEALED BY: mastering fundamentals through sheer repetition where talent was assumed; managing "
     "her own master's meals and mornings without being asked; sulking in silences she expects someone "
     "to notice — and forgiving the moment they do."),
    ("guts", "Guts (mercenary)", ["mercenary", "survivor", "betrayal", "loyalty", "wanderer"],
     "WHO: a sellsword born under a corpse and swinging steel for bread since childhood.\n"
     "WANT: at first only to survive the night; later, one place — one person — worth coming back to.\n"
     "INTRODUCED AS: a hulking swordsman with no cause, cutting through men for pay.\n"
     "REVEALED BY: leaving the only home he found because he wanted to be an equal, not a possession; "
     "carrying a companion's broken body across a battlefield; guarding a campfire all night, every "
     "night, for people who slow him down."),
    ("ginko", "Ginko (wanderer)", ["wanderer", "healer", "nature", "scholar", "loner"],
     "WHO: a white-haired traveler who studies the half-living things that seep into ordinary lives.\n"
     "WANT: to keep moving — the things he studies gather around him, so staying would ruin any place he loved.\n"
     "INTRODUCED AS: a stranger with a wooden chest of medicines, arriving where something is wrong.\n"
     "REVEALED BY: negotiating with afflictions instead of destroying them; taking payment in stories "
     "and letters; leaving each village before anyone can ask him to stay."),
    ("spike", "Spike (bounty hunter)", ["bounty hunter", "drifter", "past", "debts", "crew"],
     "WHO: a bounty hunter drifting between jobs on a ship that never quite breaks even.\n"
     "WANT: to find out whether he actually survived the life he walked out of, or is just dreaming the rest.\n"
     "INTRODUCED AS: a lanky loafer complaining about food and chasing small-time crooks for rent money.\n"
     "REVEALED BY: giving away a fortune's worth of leads over small sentiments; feeding a stray kid, a "
     "stray dog, a stray woman while insisting he travels alone; walking toward the one door everyone "
     "begs him not to open."),
    ("edward", "Edward (alchemist)", ["prodigy", "brothers", "guilt", "science", "sacrifice"],
     "WHO: a teenage state scientist who broke the one law of his craft trying to undo a death.\n"
     "WANT: to give his brother his body back — the cost of a mistake they made together, which he counts as his.\n"
     "INTRODUCED AS: a short-tempered prodigy flashing a government license in strangers' faces.\n"
     "REVEALED BY: refusing, twice, a shortcut priced in other people's lives; kneeling to apologize to "
     "a girl whose hope his honesty destroyed; treating every enemy soldier as a person someone is "
     "waiting for."),
    ("violet", "Violet (letter writer)", ["scribe", "soldier", "war", "grief", "letters", "learning"],
     "WHO: a girl raised as a weapon, discharged into a job writing other people's feelings.\n"
     "WANT: to understand the last words her commanding officer said to her — she doesn't know what they mean.\n"
     "INTRODUCED AS: a stiff, order-taking clerk who types condolence letters like field reports.\n"
     "REVEALED BY: asking clients literal questions about love until the answer changes her sentences; "
     "staying up nights rewriting a letter she got wrong once; hands that shake at the word she is "
     "trying to learn."),
    ("holo", "Holo (traveler)", ["harvest", "merchant", "loneliness", "companion", "bargain", "old"],
     "WHO: a harvest spirit abandoned by the village that once prayed to her, hitching a ride north in a merchant's cart.\n"
     "WANT: to go home to the snow she left centuries ago — and to not make the trip alone.\n"
     "INTRODUCED AS: a stowaway in the wheat, all teasing and appetite.\n"
     "REVEALED BY: needling her companion's pride precisely where it teaches him something; sobbing at "
     "the mention of a friend outlived by centuries; driving bargains that quietly protect the person "
     "she claims is only useful."),
    ("senshi", "Senshi (cook)", ["dwarf", "cook", "dungeon", "frugal", "provider", "survivor"],
     "WHO: a dwarf who has lived alone in a monster-filled labyrinth for decades, cooking what he kills.\n"
     "WANT: to see that people eat properly — because he once watched hunger decide who lived.\n"
     "INTRODUCED AS: an eccentric obstacle lecturing armed strangers about nutrition.\n"
     "REVEALED BY: balancing a meal in the middle of a crisis; refusing to waste any part of a kill; "
     "confessing, late and plainly, that he never learned whose flesh kept him alive down there."),
    ("myne", "Myne (bookbinder)", ["books", "poverty", "illness", "craftsman", "family", "invention"],
     "WHO: a sickly child in a poor soldier's family, in a city where books belong to nobles.\n"
     "WANT: to read — and if no one will let her, to make the books herself, starting from mud and reeds.\n"
     "INTRODUCED AS: a bedridden burden who faints climbing stairs.\n"
     "REVEALED BY: failing at papyrus, clay, and wood slats and starting again each time; monetizing a "
     "hair ornament to buy materials; learning to love the family she was foisted on by cooking, "
     "trading, and staying alive for them."),
    ("kenshin", "Kenshin (wanderer)", ["swordsman", "atonement", "wanderer", "era", "protector", "past"],
     "WHO: a former war assassin drifting through a new era with a sword that cannot cut.\n"
     "WANT: to pay down a ledger of killings by protecting people, one at a time, without adding a single name to it.\n"
     "INTRODUCED AS: a slight, cheerful vagrant mistaken for a nobody and swung at by a girl with a stick.\n"
     "REVEALED BY: carrying a reversed blade so his skill can't kill by reflex; letting himself be "
     "beaten in public rather than escalate; going still and cold exactly once, then apologizing for it."),
]


def main() -> None:
    root = Path(".")
    for eid, title, kws, study in CARDS:
        LS.upsert_entry(root, SCOPE, LoreEntry(
            id=f"ex-anime-{eid}", title=title, keywords=kws, content=study, priority=1, source="manual"))
    print(f"appended {len(CARDS)} anime exemplar studies into book '{SCOPE}'")


if __name__ == "__main__":
    main()
