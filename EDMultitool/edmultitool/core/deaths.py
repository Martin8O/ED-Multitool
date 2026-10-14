"""Your deaths: every death in the journals, who killed you and what coming back cost (Stats tab).
The game's own Statistics count no deaths (only what rebuys cost in total), so they are read one by one. Pure.
"""

from dataclasses import dataclass

from edmultitool.catalog.ships import SHIP_NAMES, ship_display_name
from edmultitool.core.events import Died, Killer, Resurrected

# the station and settlement types the game names as a killer (their defences fired) -> the names the market
# and nearby cards use
STATION_KINDS = {
    "coriolis": "Coriolis Starport",
    "orbis": "Orbis Starport",
    "ocellus": "Ocellus Starport",
    "bernal": "Ocellus Starport",
    "dodec": "Dodec Starport",
    "asteroidbase": "Asteroid Base",
    "outpostcivilian": "Civilian Outpost",
    "outpostcommercial": "Commercial Outpost",
    "outpostindustrial": "Industrial Outpost",
    "outpostmilitary": "Military Outpost",
    "outpostmining": "Mining Outpost",
    "outpostscientific": "Scientific Outpost",
    "outpost": "Outpost",
    "megaship": "Megaship",
    "fleetcarrier": "Fleet Carrier",
    "settlement": "Settlement",
    "crateroutpost": "Planetary Outpost",
    "craterport": "Planetary Port",
}
# a type not listed still reads as a station
STATION_WORDS = ("outpost", "station", "starport", "settlement", "base", "carrier")


@dataclass(frozen=True, slots=True)
class UiKiller:
    name: str  # the pilot or the station ("" = the game gave none)
    what: str  # the ship or the station type, plain English ("" = none)
    rank: str  # combat rank ("" = none, always for a station)
    station: bool  # a station's or settlement's defences, not a pilot


@dataclass(frozen=True, slots=True)
class UiDeath:
    when: str  # ISO
    killers: tuple[UiKiller, ...]  # none = the game named no one (a crash, a self-destruct…)
    outcome: str  # "rebuy" | "free" | "bankrupt" | another game word | "" (no resurrection read yet)
    cost: int  # credits paid to come back (0 when free or bankrupt)


@dataclass(frozen=True, slots=True)
class UiDeaths:
    count: int = 0
    paid: int = 0  # credits paid to come back, every death
    rows: tuple[UiDeath, ...] = ()  # newest first


class Deaths:
    """Every death in time order, each with the resurrection that followed it; one read twice (the history
    pass and the live journal) is kept once."""

    def __init__(self) -> None:
        self.died: list[Died] = []
        self.back: list[Resurrected] = []

    @property
    def key(self) -> tuple[int, int, str]:
        """Changes whenever something new came in."""
        last = max([d.when for d in self.died] + [b.when for b in self.back], default="")
        return (len(self.died), len(self.back), last)

    def add(self, event: Died | Resurrected) -> None:
        if isinstance(event, Died):
            if all(d.when != event.when for d in self.died):
                self.died.append(event)
                self.died.sort(key=lambda d: d.when)
        elif all(b.when != event.when for b in self.back):
            self.back.append(event)
            self.back.sort(key=lambda b: b.when)

    def merge(self, other: "Deaths") -> None:
        for died in other.died:
            self.add(died)
        for back in other.back:
            self.add(back)


def _killer(k: Killer) -> UiKiller:
    station = k.ship in STATION_KINDS or (
        k.ship not in SHIP_NAMES and any(word in k.ship for word in STATION_WORDS)
    )
    if station:
        what = STATION_KINDS.get(k.ship) or k.ship.replace("_", " ").title()
        return UiKiller(k.name, what, "", True)
    return UiKiller(k.name, ship_display_name(k.ship) if k.ship else "", k.rank, False)


def ui_deaths(deaths: Deaths) -> UiDeaths:
    rows = []
    for i, died in enumerate(deaths.died):
        until = deaths.died[i + 1].when if i + 1 < len(deaths.died) else "~"
        # the first resurrection after this death and before the next one
        back = next((b for b in deaths.back if died.when <= b.when < until), None)
        if back is None:
            outcome, cost = "", 0
        elif back.bankrupt:
            outcome, cost = "bankrupt", 0
        else:
            outcome, cost = back.option, back.cost
        rows.append(UiDeath(died.when, tuple(_killer(k) for k in died.killers), outcome, cost))
    return UiDeaths(len(rows), sum(r.cost for r in rows), tuple(reversed(rows)))
