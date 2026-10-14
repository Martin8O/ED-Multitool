"""Your standing with every minor faction met, folded from the journal: the game's word and number for
it, where and when you last met the faction and what it was going through there, every system you saw it in
(for the nearest), and how many missions you completed for it. Pure.

The game tells your standing with a minor faction only on arriving in a system where it is present, so each
value is the one from that last arrival. Like `Career`, `Factions` is changed in place: the history pass
builds it, then hands it to the main thread, which goes on folding live entries into it.
"""

import math
import re
from dataclasses import dataclass, field

from edmultitool.core.career import UiStanding, standing
from edmultitool.core.events import Arrived, MissionEnded, StarPos
from edmultitool.core.market import standing as game_standing

# a state the journal spells in one word → the game's words ("CivilWar" → "Civil war")
_WORD = re.compile(r"(?<=[a-z])(?=[A-Z])")


def state_words(state: str) -> str:
    return _WORD.sub(" ", state).capitalize()


@dataclass(slots=True)
class FactionRecord:
    name: str  # as the game spells it
    reputation: float | None = None  # -100..100 at the last arrival; None = only met through a mission
    seen: str = ""  # ISO time of that arrival
    system: str = ""  # where it was
    address: int | None = None
    state: str = ""  # its main state there ("" = none)
    active: tuple[str, ...] = ()  # every active state there
    influence: float | None = None  # 0..1 there
    controls: bool = False  # it controlled that system
    # every system you saw it in: id64 → name, position
    places: dict[int, tuple[str, StarPos]] = field(default_factory=dict)
    missions: int = 0  # missions completed for it


class Factions:
    """Every minor faction met, keyed by casefolded name."""

    def __init__(self) -> None:
        self.records: dict[str, FactionRecord] = {}
        self.last = ""  # the newest entry folded in (ISO): live entries up to it are not counted again
        # the system of the latest arrival and the factions present there (casefolded): a faction you met
        # in that system earlier may have left it since
        self.here: tuple[int | None, frozenset[str]] = (None, frozenset())

    def record(self, name: str) -> FactionRecord:
        key = name.casefold()
        rec = self.records.get(key)
        if rec is None:
            rec = self.records[key] = FactionRecord(name)
        return rec

    def arrived(self, event: Arrived, when: str) -> None:
        owner = event.system_faction.casefold()
        self.here = (event.address, frozenset(f.name.casefold() for f in event.faction_details))
        for f in event.faction_details:
            rec = self.record(f.name)
            rec.name = f.name
            rec.reputation = f.reputation
            rec.seen = when or rec.seen
            rec.system, rec.address = event.system, event.address
            rec.state, rec.active, rec.influence = f.state, f.active, f.influence
            rec.controls = f.name.casefold() == owner
            rec.places[event.address] = (event.system, event.star_pos)
        self.last = max(self.last, when)

    def mission(self, event: MissionEnded) -> None:
        if event.how == "completed" and event.faction:
            self.record(event.faction).missions += 1
        self.last = max(self.last, event.when)

    def reputation(self) -> dict[str, float]:
        """Casefolded name → your standing, for every faction whose standing is known."""
        return {k: r.reputation for k, r in self.records.items() if r.reputation is not None}


# --- what the page shows ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UiFaction:
    name: str
    standing: UiStanding  # the game's word, the number, how far to the next word
    hostile: bool  # its stations refuse you (and its ships may attack)
    state: str  # what it was going through when last met, in words ("" = nothing)
    system: str  # where you last met it
    seen: str  # ISO time of that
    controls: bool  # it controlled that system
    influence: int | None  # % there
    nearest: str  # the nearest system you saw it in
    nearest_ly: float | None  # how far that is from you (None = your position is not known)
    places: int  # how many systems you saw it in
    missions: int  # completed for it


@dataclass(frozen=True, slots=True)
class UiFactions:
    known: bool = False  # the journals have been read
    rows: tuple[UiFaction, ...] = ()  # standing not 0 (or missions done for it), best first
    neutral: int = 0  # met, standing exactly 0 and no missions: not listed
    warning: str = ""  # a faction here holds you hostile (the EDMC panel line)


def _state(rec: FactionRecord) -> str:
    names = rec.active or ((rec.state,) if rec.state else ())
    return ", ".join(state_words(s) for s in names)


def _nearest(rec: FactionRecord, here: StarPos | None) -> tuple[str, float | None]:
    if here is None or not rec.places:
        return rec.system, None
    name, pos = min(rec.places.values(), key=lambda p: math.dist(here, p[1]))
    return name, round(math.dist(here, pos), 2)


def hostile_here(f: Factions, address: int | None) -> str:
    """The panel line when a faction present where you are holds you hostile; "" otherwise."""
    at, present = f.here
    if address is None or at != address:
        return ""
    names = sorted(
        (r for k in present if (r := f.records.get(k)) is not None and r.reputation is not None
         and game_standing(r.reputation) == "hostile"),
        key=lambda r: (not r.controls, r.name),
    )  # fmt: skip
    if not names:
        return ""
    who = ", ".join(r.name + (" (controls the system)" if r.controls else "") for r in names)
    its = "Its" if len(names) == 1 else "Their"
    return f"Hostile to you here: {who}. {its} stations refuse you, {its.lower()} ships may attack."


def ui_factions(f: Factions | None, here: StarPos | None, address: int | None) -> UiFactions:
    if f is None:
        return UiFactions()
    rows = []
    neutral = 0
    for rec in f.records.values():
        if rec.reputation is None:
            continue  # met only through a mission: no standing to show
        if rec.reputation == 0 and not rec.missions:
            neutral += 1
            continue
        nearest, ly = _nearest(rec, here)
        rows.append(
            UiFaction(
                name=rec.name,
                standing=standing(rec.name, rec.reputation),
                hostile=game_standing(rec.reputation) == "hostile",
                state=_state(rec),
                system=rec.system,
                seen=rec.seen,
                controls=rec.controls,
                influence=None if rec.influence is None else round(rec.influence * 100),
                nearest=nearest,
                nearest_ly=ly,
                places=len(rec.places),
                missions=rec.missions,
            )
        )
    rows.sort(key=lambda r: (-r.standing.value, -r.missions, r.name.casefold()))
    return UiFactions(True, tuple(rows), neutral, hostile_here(f, address))
