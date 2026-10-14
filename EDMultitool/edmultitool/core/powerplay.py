"""Nearest system by Powerplay power — pure.

Three ways to ask, one per thing your merits can do: the systems a power **holds** (Exploited / Fortified /
Stronghold: reinforce), those **another power holds** where it is present (undermine), and the Unoccupied
ones it can **acquire**. An optional power state narrows the first two. Spansh answers with the nearest
systems first; this module holds the request the page sends, the typed rows, and what the page's
"Powerplay" card shows (`UiState.powerplay`).
"""

import math
from dataclasses import dataclass, replace
from typing import Literal

from edmultitool.catalog.powers import CONTROLLED_STATES, POWERS
from edmultitool.core.events import StarPos
from edmultitool.core.route import min_jumps

PowerMode = Literal["held", "others", "acquire"]
PowerStatus = Literal["idle", "running", "done", "error"]
# What your merits do in a system, for the power you are pledged to (the game's three activities):
# reinforce your own power's system, undermine another power's, acquire an Unoccupied one your power is
# present in; "out-of-reach" = Unoccupied without your power; "" = not pledged (or not known yet).
Effect = Literal["reinforce", "undermine", "acquire", "out-of-reach", ""]
POWER_SHOWN = 10  # systems per search, nearest first
FAR_LY = 200.0  # a nearest answer at least this far away says so: "nothing closer", not a wrong distance

# The power states each way of asking can be narrowed to ("acquire" = Unoccupied only, nothing to pick).
MODES: dict[PowerMode, tuple[str, ...]] = {
    "held": CONTROLLED_STATES,
    "others": CONTROLLED_STATES,
    "acquire": (),
}


@dataclass(frozen=True, slots=True)
class PowerRequest:
    """What the page asks for (POST /api/powerplay)."""

    power: str
    mode: PowerMode = "held"
    state: str = ""  # one of MODES[mode]; "" = any


@dataclass(frozen=True, slots=True)
class PowerQuery:
    """A request with the place it is measured from (the worker's input)."""

    request: PowerRequest
    system: str
    star_pos: StarPos


@dataclass(frozen=True, slots=True)
class PowerRow:
    """One system, as Spansh reports it."""

    system: str
    system_id64: int
    x: float
    y: float
    z: float
    held_by: str  # the controlling power ("" = none)
    powers: tuple[str, ...]  # every power present
    state: str  # "Exploited" / "Fortified" / "Stronghold" / "Unoccupied" / "" (not known)
    progress: float | None  # control progress within the state (1.0 = 100 %; may pass 100 % or go below 0)
    reinforcement: int | None  # merits for the controlling power this cycle
    undermining: int | None  # merits against it this cycle
    updated: str  # ISO time of the system data


@dataclass(frozen=True, slots=True)
class PowerFound:
    rows: tuple[PowerRow, ...]
    count: int  # every system that matches (Spansh caps it at 10,000)


@dataclass(frozen=True, slots=True)
class UiPowerRow:
    system: str
    system_id64: str  # decimal string
    held_by: str
    others: tuple[str, ...]  # the other powers present, by name
    powers: tuple[str, ...]  # every power present
    state: str
    progress_pct: int | None  # rounded percent
    reinforcement: int | None
    undermining: int | None
    updated: str
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    distance_ly: float | None = None
    jumps: int | None = None
    effect: Effect = ""  # what your merits do there (`effect`)


@dataclass(frozen=True, slots=True)
class UiPowerplay:
    status: PowerStatus = "idle"
    message: str = ""
    power: str = ""  # the power asked last ("" = none yet)
    mode: str = ""
    state: str = ""
    rows: tuple[UiPowerRow, ...] = ()
    count: int = 0  # every system that matches
    source: str = ""  # "Spansh"
    searched: str = ""  # ISO time of the search
    searched_from: str = ""  # the system it was measured from
    handled: int = 0  # powerplay requests carried out: the page knows its click was answered


def ui_power_rows(power: str, rows: tuple[PowerRow, ...] | list[PowerRow]) -> tuple[UiPowerRow, ...]:
    return tuple(
        UiPowerRow(
            system=r.system,
            system_id64=str(r.system_id64),
            held_by=r.held_by,
            others=tuple(sorted((p for p in r.powers if p != power and p != r.held_by), key=str.casefold)),
            powers=r.powers,
            state=r.state,
            progress_pct=None if r.progress is None else round(r.progress * 100),
            reinforcement=r.reinforcement,
            undermining=r.undermining,
            updated=r.updated,
            x=r.x,
            y=r.y,
            z=r.z,
        )
        for r in rows
    )


def _jumps(d: float | None, range_ly: float | None) -> int | None:
    return min_jumps(d, range_ly) if d is not None and range_ly is not None and range_ly > 0 else None


def effect(row: UiPowerRow, pledged: str) -> Effect:
    """What the merits of a pilot pledged to `pledged` do in the system."""
    if not pledged:
        return ""
    if row.held_by:
        return "reinforce" if row.held_by == pledged else "undermine"
    return "acquire" if pledged in row.powers else "out-of-reach"


def with_distances(
    pp: UiPowerplay, star_pos: StarPos | None, range_ly: float | None, pledged: str = ""
) -> UiPowerplay:
    """Distance and fewest jumps from where you are now to every row (None when not known), and what your
    merits do there for the power you are pledged to."""
    if not pp.rows:
        return pp
    rows = []
    for r in pp.rows:
        d = None if star_pos is None else round(math.dist(star_pos, (r.x, r.y, r.z)), 2)
        rows.append(replace(r, distance_ly=d, jumps=_jumps(d, range_ly), effect=effect(r, pledged)))
    out = tuple(rows)
    return pp if out == pp.rows else replace(pp, rows=out)


def describe(req: PowerRequest) -> str:
    """ "Fortified systems Li Yong-Rui holds" / "systems another power holds where Li Yong-Rui is present" /
    "Unoccupied systems Li Yong-Rui can acquire"."""
    what = f"{req.state} systems" if req.state else "systems"
    if req.mode == "held":
        return f"{what} {req.power} holds"
    if req.mode == "others":
        return f"{what} another power holds, {req.power} present"
    return f"Unoccupied systems {req.power} can acquire"


def found_message(req: PowerRequest, shown: int, count: int, nearest_ly: float | None) -> str:
    """The card's answer: "Li Yong-Rui holds 2 185 systems: the 10 nearest." — and, when even the nearest is
    far, that nothing is closer (so a long distance does not read as a wrong one)."""
    if not shown:
        return f"No {describe(req)}."
    many = "more than 10,000" if count >= 10_000 else f"{count:,}"
    state = f"{req.state} " if req.state else ""
    systems = "system" if count == 1 else "systems"
    if req.mode == "held":
        head = f"{req.power} holds {many} {state}{systems}"
    elif req.mode == "others":
        head = f"{req.power} is present in {many} {state}{systems} another power holds"
    else:
        head = f"{req.power} can acquire {many} Unoccupied {systems}"
    if shown >= count:
        text = f"{head}." if count == 1 else f"{head}, all shown."
    else:
        text = f"{head}: the {shown} nearest." if shown > 1 else f"{head}: the nearest."
    if nearest_ly is not None and nearest_ly >= FAR_LY:
        text += f" None is closer than {nearest_ly:,.0f} ly from where you searched."
    return text


def parse_power_request(raw: object) -> PowerRequest | str:
    """POST /api/powerplay: {power, mode, state}; a message when it is not one."""
    if not isinstance(raw, dict):
        return "send a JSON object"
    power, mode, state = raw.get("power"), raw.get("mode"), raw.get("state", "")
    if not isinstance(power, str) or power not in POWERS:
        return "power must be one of the 12 Powerplay powers"
    if not isinstance(mode, str) or mode not in MODES:
        return "mode must be held, others or acquire"
    if state != "" and (not isinstance(state, str) or state not in MODES[mode]):
        allowed = MODES[mode]
        return (
            f"state must be empty or one of {', '.join(allowed)}"
            if allowed
            else f"state must be empty for {mode}"
        )
    return PowerRequest(power=power, mode=mode, state=state)
