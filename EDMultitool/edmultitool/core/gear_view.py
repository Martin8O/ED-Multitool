"""What the page's "Gear from the forum" card shows (`UiState.gear`) — pure data, built from
`core.gear` results; distances are measured again from wherever you are on every refresh."""

import math
from dataclasses import dataclass, field, replace
from typing import Literal

from edmultitool.catalog.gear import ITEMS, SLOTS_BY_GRADE
from edmultitool.core.events import StarPos
from edmultitool.core.gear import GearItem, GearPlace, rate, suits

GearStatus = Literal["idle", "running", "done", "error"]


@dataclass(frozen=True, slots=True)
class UiGearItem:
    name: str  # "Maverick", "Karma P-15"; the post's own word when not known
    kind: str  # "suit" | "weapon" | ""
    klass: str  # "suit", "plasma pistol", … ("" when not known)
    grade: int | None
    slots: int | None  # modification slots of that grade
    mods: tuple[str, ...]
    empty: bool  # the post says: no modifications
    count: int
    price: int | None  # Cr, as the post says
    taken: bool  # the post says it is gone
    stars: int  # 1-5, 0 = cannot rate (item or grade not known)
    why: str  # one line: what the rating rests on
    suits: tuple[str, ...] = ()  # who it is for: "combat" / "salvage" / "exobiology" / "stealth"


@dataclass(frozen=True, slots=True)
class UiGearPlace:
    found: bool  # Spansh knows the system
    system: str
    system_id64: str  # decimal string ("" when not found)
    station: str  # "" when the post names none, or Spansh does not list it
    station_type: str
    pad: str  # "L" / "M" / "S" / ""
    arrival_ls: float | None
    text: str  # the post's own line for this place
    note: str  # why it was not found, or a doubt
    items: tuple[UiGearItem, ...]
    stars: int  # the best item still there
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    distance_ly: float | None = None  # from where you are now, 2 decimals
    jumps: int | None = None  # plotted with your ship from `UiGear.jumps_from`
    jumps_note: str = ""  # why there is no jump count


@dataclass(frozen=True, slots=True)
class UiGearFields:
    """The separate fields shown when the paste box cannot decode a post (prefilled with what it found)."""

    show: bool = False
    system: str = ""
    station: str = ""
    gear: str = ""


MAX_EARLIER = 2  # finds kept under the newest one, so the last three can be compared


@dataclass(frozen=True, slots=True)
class UiGearFind:
    """An earlier find, kept as it was (its jumps stay counted from where you were then)."""

    places: tuple[UiGearPlace, ...]
    message: str
    decoded: str  # ISO time it was decoded
    jumps_from: str = ""
    jumps_from_id64: str = ""


@dataclass(frozen=True, slots=True)
class UiGear:
    status: GearStatus = "idle"
    message: str = ""
    places: tuple[UiGearPlace, ...] = ()
    fields: UiGearFields = field(default_factory=UiGearFields)
    jumps_status: GearStatus = "idle"  # the jump plots after the places are found
    jumps_from: str = ""  # the system the jumps are counted from
    jumps_from_id64: str = ""  # its id64 (decimal string): the page compares it with where you are now
    handled: int = 0  # gear requests carried out: the page knows its click was answered
    decoded: str = ""  # ISO time the newest find was decoded
    earlier: tuple[UiGearFind, ...] = ()  # the finds before it, newest first (≤ MAX_EARLIER); not saved


def push_earlier(gear: UiGear) -> UiGear:
    """A new post is being decoded: the find shown now moves down to the earlier ones."""
    if not any(p.found for p in gear.places):
        return gear
    find = UiGearFind(gear.places, gear.message, gear.decoded, gear.jumps_from, gear.jumps_from_id64)
    return replace(gear, places=(), earlier=(find, *gear.earlier)[:MAX_EARLIER])


def item_text(item: GearItem) -> str:
    """One line the fields can take back: "G3 Maverick - Damage Resistance, Night Vision"."""
    grade = f"G{item.grade} " if item.grade else ""
    mods = ", ".join(item.mods) if item.mods else ("no mods" if item.empty else "")
    return f"{grade}{item.name}" + (f" - {mods}" if mods else "")


def ui_item(item: GearItem) -> UiGearItem:
    stars, why = rate(item)
    return UiGearItem(
        name=item.name,
        kind=item.kind,
        klass=ITEMS[item.name][1] if item.name in ITEMS else "",
        grade=item.grade,
        slots=SLOTS_BY_GRADE.get(item.grade) if item.grade else None,
        mods=item.mods,
        empty=item.empty,
        count=item.count,
        price=item.price,
        taken=item.taken,
        stars=stars,
        why=why,
        suits=suits(item),
    )


def ui_place(place: GearPlace, all_gone: bool = False) -> UiGearPlace:
    items = tuple(ui_item(replace(i, taken=i.taken or all_gone)) for i in place.items)
    open_stars = [i.stars for i in items if not i.taken]
    sys, st = place.system, place.station
    return UiGearPlace(
        found=sys is not None,
        system=sys.name if sys else "",
        system_id64=str(sys.id64) if sys else "",
        station=st.name if st else "",
        station_type=st.type if st else "",
        pad=st.pad if st else "",
        arrival_ls=round(st.arrival_ls) if st and st.arrival_ls is not None else None,
        text=place.text,
        note=place.note,
        items=items,
        stars=max(open_stars, default=0),
        x=sys.x if sys else 0.0,
        y=sys.y if sys else 0.0,
        z=sys.z if sys else 0.0,
    )


def _measured(places: tuple[UiGearPlace, ...], star_pos: StarPos | None) -> tuple[UiGearPlace, ...]:
    return tuple(
        replace(
            p,
            distance_ly=(
                round(math.dist(star_pos, (p.x, p.y, p.z)), 2) if p.found and star_pos is not None else None
            ),
        )
        for p in places
    )


def with_distances(gear: UiGear, star_pos: StarPos | None) -> UiGear:
    """Straight-line distance from where you are now to every place found, earlier finds included (None
    when not known)."""
    if not gear.places and not gear.earlier:
        return gear
    places = _measured(gear.places, star_pos)
    earlier = tuple(replace(f, places=_measured(f.places, star_pos)) for f in gear.earlier)
    if places == gear.places and earlier == gear.earlier:
        return gear
    return replace(gear, places=places, earlier=earlier)
