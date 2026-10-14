"""The remembered fleet: every ship the player owns, with the build last seen for it.

Kept so the plugin knows a ship (and its jump range) even when the game has not sent a `Loadout` this
session — logging in on foot, or EDMC losing the entry — and so the page can plan with a ship other than
the one you sit in. Pure data: the plugin persists it as JSON (`to_doc` / `from_doc`), the history
scanner feeds it journal entries newest first (`scan`).

Ships are keyed by the game's ShipID. A ShipID is reused after a sale, so the newest Loadout for an id
wins, and a sale only removes the build seen before it.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from typing import Any

from edmultitool.catalog.fsd import FsdSpec
from edmultitool.core.events import (
    FleetListed,
    FsdFit,
    LoadoutChanged,
    ShipSold,
    entry_time,
    parse_event,
)

DOC_VERSION = 1


@dataclass(frozen=True, slots=True)
class StoredShip:
    loadout: LoadoutChanged
    seen: str  # ISO time of the Loadout this build comes from


@dataclass(frozen=True, slots=True)
class Fleet:
    ships: dict[int, StoredShip] = field(default_factory=dict)  # never mutated: every change copies
    # newest journal file already read (its modification time), so a restart only reads newer ones
    scanned_mtime: float = 0.0

    @property
    def last_flown(self) -> StoredShip | None:
        return max(self.ships.values(), key=lambda s: s.seen, default=None)

    def get(self, ship_id: int | None) -> StoredShip | None:
        return None if ship_id is None else self.ships.get(ship_id)

    def ordered(self) -> list[StoredShip]:
        """Most recently flown first."""
        return sorted(self.ships.values(), key=lambda s: s.seen, reverse=True)


def remember(fleet: Fleet, loadout: LoadoutChanged, when: str) -> Fleet:
    """Record the ship's build unless a newer one is already known. Nothing new → the same Fleet."""
    if loadout.ship_id is None:
        return fleet
    old = fleet.ships.get(loadout.ship_id)
    if old is not None:
        if when and old.seen > when:
            return fleet
        if old.loadout == loadout and (not when or old.seen == when):
            return fleet
    seen = when or (old.seen if old is not None else "")
    return replace(fleet, ships={**fleet.ships, loadout.ship_id: StoredShip(loadout, seen)})


def forget_sold(fleet: Fleet, ship_id: int, when: str) -> Fleet:
    """A sale removes the build seen before it (a newer Loadout under the same id is a new ship)."""
    old = fleet.ships.get(ship_id)
    if old is None or (when and old.seen > when):
        return fleet
    return replace(fleet, ships={k: v for k, v in fleet.ships.items() if k != ship_id})


def keep_owned(fleet: Fleet, owned: frozenset[int], current: int | None, when: str) -> Fleet:
    """StoredShips lists every owned ship but the current one: anything else seen before it is gone."""
    keep = owned | ({current} if current is not None else set())
    gone = [k for k, v in fleet.ships.items() if k not in keep and (not when or v.seen <= when)]
    if not gone:
        return fleet
    return replace(fleet, ships={k: v for k, v in fleet.ships.items() if k not in gone})


# --- reading journal history ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScanResult:
    """What a newest-first pass over journal history found."""

    builds: dict[int, StoredShip]  # newest build per ShipID, sold-before-seen ships left out
    sold: dict[int, str]  # ShipID → time of its newest sale that is newer than any build found
    # newest StoredShips list + ships flown after it + the ship in use when it was written (the next older
    # Loadout). None = not known completely (no list, or no Loadout older than it was read): prune nothing.
    owned: frozenset[int] | None
    owned_at: str  # time of that StoredShips


def scan(entries_newest_first: Iterable[dict[str, Any]]) -> ScanResult:
    """Fold journal entries, newest first, into the newest build of every owned ship.

    Stops reading as soon as the newest fleet list is known and every ship on it has a build, so the
    caller's lazy iterator never opens older files than needed.
    """
    builds: dict[int, StoredShip] = {}
    sold: dict[int, str] = {}
    listed: frozenset[int] | None = None  # newest StoredShips + ships flown after it
    sold_since_list: frozenset[int] = frozenset()  # sales newer than the list: those ships are gone
    owned: frozenset[int] | None = None
    owned_at = ""
    for e in entries_newest_first:
        ev = parse_event(e)
        when = entry_time(e)
        match ev:
            case LoadoutChanged() if ev.ship_id is not None:
                if listed is not None and owned is None:
                    # The list leaves out the ship in use at that moment: the next older Loadout's.
                    owned = (listed | {ev.ship_id}) - sold_since_list
                if ev.ship_id not in builds and ev.ship_id not in sold:
                    builds[ev.ship_id] = StoredShip(ev, when)
            case ShipSold():
                if ev.ship_id not in builds and ev.ship_id not in sold:
                    sold[ev.ship_id] = when
            case FleetListed() if listed is None:
                listed = ev.ship_ids | builds.keys()  # + ships first flown after the list
                sold_since_list = frozenset(sold)
                owned_at = when
        # Done once every owned ship has its build — or a sale newer than anything older (a reused id:
        # every older build is of the ship sold, so reading on cannot find this one).
        if owned is not None and owned <= builds.keys() | sold.keys():
            break
    # A list without an older Loadout misses the ship in use when it was written (lists never name it):
    # `owned` stays None then, or a later start's pass would forget the ship flown last.
    if owned is not None:
        builds = {k: v for k, v in builds.items() if k in owned}
    return ScanResult(builds, sold, owned, owned_at)


def merge_scan(fleet: Fleet, found: ScanResult, scanned_mtime: float) -> Fleet:
    """Add what a history pass found to the fleet; anything newer already in the fleet wins."""
    for stored in found.builds.values():
        fleet = remember(fleet, stored.loadout, stored.seen)
    for ship_id, when in found.sold.items():
        fleet = forget_sold(fleet, ship_id, when)
    if found.owned is not None:
        fleet = keep_owned(fleet, found.owned, None, found.owned_at)
    if scanned_mtime <= fleet.scanned_mtime:
        return fleet  # unchanged → no rewrite of the file
    return replace(fleet, scanned_mtime=scanned_mtime)


# --- persistence ---------------------------------------------------------------------------------


def _loadout_doc(lo: LoadoutChanged) -> dict[str, Any]:
    fsd = lo.fsd
    return {
        "ship_type": lo.ship_type,
        "ship_name": lo.ship_name,
        "ship_ident": lo.ship_ident,
        "ship_id": lo.ship_id,
        "unladen_mass": lo.unladen_mass,
        "fuel_capacity_main": lo.fuel_capacity_main,
        "fuel_capacity_reserve": lo.fuel_capacity_reserve,
        "cargo_capacity": lo.cargo_capacity,
        "game_max_jump_range": lo.game_max_jump_range,
        "guardian_boost_ly": lo.guardian_boost_ly,
        "fsd": None
        if fsd is None
        else {
            "item": fsd.item,
            "overcharge": fsd.overcharge,
            "size": fsd.size,
            "rating": fsd.rating,
            "engineered": fsd.engineered,
            "opt_mass": fsd.spec.opt_mass,
            "max_fuel": fsd.spec.max_fuel,
            "fuel_mul": fsd.spec.fuel_mul,
            "fuel_power": fsd.spec.fuel_power,
        },
    }


def _loadout_from(d: dict[str, Any]) -> LoadoutChanged:
    f = d.get("fsd")
    fsd = (
        None
        if f is None
        else FsdFit(
            item=str(f["item"]),
            overcharge=bool(f["overcharge"]),
            size=int(f["size"]),
            rating=str(f["rating"]),
            spec=FsdSpec(
                float(f["opt_mass"]), float(f["max_fuel"]), float(f["fuel_mul"]), float(f["fuel_power"])
            ),
            engineered=bool(f["engineered"]),
        )
    )
    return LoadoutChanged(
        ship_type=str(d["ship_type"]),
        ship_name=str(d.get("ship_name", "")),
        ship_id=int(d["ship_id"]),
        unladen_mass=float(d["unladen_mass"]),
        fuel_capacity_main=float(d["fuel_capacity_main"]),
        fuel_capacity_reserve=float(d["fuel_capacity_reserve"]),
        cargo_capacity=int(d["cargo_capacity"]),
        game_max_jump_range=float(d["game_max_jump_range"]),
        fsd=fsd,
        guardian_boost_ly=float(d["guardian_boost_ly"]),
        ship_ident=str(d.get("ship_ident", "")),
    )


def to_doc(fleet: Fleet) -> dict[str, Any]:
    return {
        "version": DOC_VERSION,
        "scanned_mtime": fleet.scanned_mtime,
        "ships": [{"seen": s.seen, **_loadout_doc(s.loadout)} for s in fleet.ordered()],
    }


def _ships(raw: object) -> Iterator[StoredShip]:
    for d in raw if isinstance(raw, list) else []:
        try:
            yield StoredShip(_loadout_from(d), str(d.get("seen", "")))
        except (KeyError, TypeError, ValueError, AttributeError):
            continue  # one damaged row must not lose the rest


def from_doc(doc: object) -> Fleet:
    """Anything unreadable → an empty fleet (the next start reads the journal history again)."""
    if not isinstance(doc, dict) or doc.get("version") != DOC_VERSION:
        return Fleet()
    ships = {s.loadout.ship_id: s for s in _ships(doc.get("ships")) if s.loadout.ship_id is not None}
    mtime = doc.get("scanned_mtime")
    return Fleet(ships, float(mtime) if isinstance(mtime, int | float) and ships else 0.0)
