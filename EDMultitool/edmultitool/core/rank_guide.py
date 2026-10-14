"""What moves each rank up and what its ranks open: the text of a rank's ladder on the Commander tab. Pure.

Only what several independent sources agree on. Frontier publishes no thresholds (the community's figures are
estimates that disagree), so none are given: the bar shows the game's own percent to the next rank.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RankGuide:
    how: str  # what moves you up
    unlocks: Mapping[int, str] = field(default_factory=dict)  # rank number -> what reaching it opens
    note: str = ""


_ELITE_PERMIT = {8: "Shinrarta Dezhra permit (Founders World, Jameson Memorial)"}  # read-only (Mapping)
_PAST_ELITE = "Elite I to V follow Elite: the same work, more of it."
_NO_THRESHOLDS = "Frontier publishes no thresholds: the bar is the game's own percent to the next rank."
_PILOT = (
    f"{_PAST_ELITE} Elite in Combat, Trade or Exploration opens the Shinrarta Dezhra permit. {_NO_THRESHOLDS}"
)
_NAVY = (
    "Missions and other work for {side}-aligned factions. At 100 % the bar stops until you complete a "
    "promotion mission from one of them."
)
_SHIPS = "Ships are sold to you from that rank on{eagle}; a permit lets you jump into the system."

RANK_GUIDE: dict[str, RankGuide] = {
    "Combat": RankGuide(
        "Destroying ships, and kills on foot too. A kill is worth more the higher the target's combat rank "
        "is compared with yours.",
        _ELITE_PERMIT,
        _PILOT,
    ),
    "Trade": RankGuide(
        "Trade profit: selling commodities for more than they cost, mined and black-market sales included.",
        _ELITE_PERMIT,
        _PILOT,
    ),
    "Explore": RankGuide(
        "Selling exploration data to Universal Cartographics; sightseeing passenger missions add to it.",
        _ELITE_PERMIT,
        _PILOT,
    ),
    "Soldier": RankGuide(
        "On-foot combat: on-foot missions, kills and ground conflict-zone bonds. Fighting in a ship counts "
        "toward Combat instead.",
        note=f"{_PAST_ELITE} {_NO_THRESHOLDS}",
    ),
    "Exobiologist": RankGuide(
        "Selling organic data (completed samples) at Vista Genomics: the more it sells for, the further "
        "you go.",
        note=f"{_PAST_ELITE} {_NO_THRESHOLDS}",
    ),
    "CQC": RankGuide("Playing CQC Championship matches.", note=_NO_THRESHOLDS),
    "Federation": RankGuide(
        _NAVY.format(side="Federation"),
        {
            3: "Federal Dropship",
            4: "Sol permit",
            5: "Federal Assault Ship · Vega permit",
            6: "PLX 695 permit",
            7: "Federal Gunship · Ross 128 permit",
            8: "Exbeur permit",
            10: "Hors permit",
            12: "Federal Corvette",
        },
        f"{_SHIPS.format(eagle='')} {_NO_THRESHOLDS}",
    ),
    "Empire": RankGuide(
        _NAVY.format(side="Empire"),
        {
            3: "Imperial Courier",
            4: "Achenar permit",
            7: "Imperial Clipper · Summerland permit",
            10: "Facece permit",
            12: "Imperial Cutter",
        },
        f"{_SHIPS.format(eagle=' (the Imperial Eagle needs no rank)')} {_NO_THRESHOLDS}",
    ),
}
