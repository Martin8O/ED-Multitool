"""Your open missions: built from the journal — the list the game gives at login, then every mission
taken, redirected and closed — with where each sends you, how far that is and how long you have."""

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from edmultitool.catalog.missions import mission_kind
from edmultitool.core.events import (
    DockedAt,
    MissionEnded,
    MissionEvent,
    MissionRedirected,
    MissionsListed,
    MissionTaken,
    StarPos,
)
from edmultitool.core.route import min_jumps


@dataclass(frozen=True, slots=True)
class Mission:
    mission_id: int
    kind: str  # in plain words, e.g. "Passengers"
    title: str  # the game's own line ("" when only the login list named it)
    faction: str
    system: str  # where it sends you now ("" = not known)
    station: str
    expiry: str  # ISO; "" = no time limit known
    reward: int  # 0 = not known
    wing: bool = False
    taken: str = ""  # ISO; "" = taken before the journals we have
    redirected: bool = False  # the job is done: hand it in at `system` / `station`


@dataclass
class Missions:
    """The open missions by id, and where you are docked (a mission taken there may send you back)."""

    open: dict[int, Mission] = field(default_factory=dict)
    docked: tuple[str, str] = ("", "")  # (system, station) of the last dock
    last: str = ""  # the newest entry used, ISO
    # missions a login list left out: their details come back if a later list names them again (a login
    # in another game mode lists other missions; the next one lists yours again)
    gone: dict[int, Mission] = field(default_factory=dict)

    def add(self, event: MissionEvent) -> None:
        self.last = max(self.last, event.when)
        match event:
            case DockedAt():
                self.docked = (event.system, event.station)
            case MissionTaken():
                system, station = event.system, event.station
                if not system:  # a donation: handed in where it was taken
                    system, station = self.docked
                self.open[event.mission_id] = Mission(
                    mission_id=event.mission_id,
                    kind=mission_kind(event.code),
                    title=event.title,
                    faction=event.faction,
                    system=system,
                    station=station,
                    expiry=event.expiry,
                    reward=event.reward,
                    wing=event.wing,
                    taken=event.when,
                )
            case MissionEnded():
                self.open.pop(event.mission_id, None)
                self.gone.pop(event.mission_id, None)
            case MissionRedirected():
                m = self.open.get(event.mission_id)
                if m is not None:
                    system = event.system or m.system
                    station = event.station if event.system else m.station
                    self.open[event.mission_id] = replace(m, system=system, station=station, redirected=True)
            case MissionsListed():
                # the game's own list at login is the truth: what is not on it ended while we did not look
                kept: dict[int, Mission] = {}
                for mission_id, code, seconds in event.active:
                    m = self.open.get(mission_id) or self.gone.pop(mission_id, None)
                    if m is None:
                        m = Mission(mission_id, mission_kind(code), "", "", "", "", "", 0)
                    if seconds == 0:  # no time limit: a done job waits for you to hand it in
                        m = replace(m, expiry="")
                    elif seconds is not None:
                        m = replace(m, expiry=_iso(_time(event.when) + timedelta(seconds=seconds)))
                    kept[mission_id] = m
                self.gone.update((i, m) for i, m in self.open.items() if i not in kept)
                self.open = kept


def _time(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _iso(t: datetime) -> str:
    return t.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class UiMission:
    id: str  # MissionID as a string
    kind: str
    title: str
    faction: str
    system: str
    station: str
    expiry: str  # ISO; "" = no limit known (the page counts down from it)
    reward: int
    wing: bool
    taken: str
    done: bool  # redirected: hand it in at system / station
    here: bool  # you are in its system now
    distance_ly: float | None  # from where you are; None = the system's place is not known (yet)
    jumps: int | None  # fewest jumps at the routing range, an estimate


@dataclass(frozen=True, slots=True)
class UiMissions:
    known: bool = False  # the journals were read
    missions: tuple[UiMission, ...] = ()  # soonest to end first
    reward: int = 0  # all of them together, Cr
    lookup: str = ""  # why some distances are missing ("" = none missing, or still being looked up)
    text: str = "—"  # the EDMC panel's Missions: row


def ui_missions(
    missions: Missions,
    now: datetime,
    *,
    known: bool,
    places: dict[str, StarPos],
    star_pos: StarPos | None,
    here: str | None,
    range_ly: float | None,
    lookup: str = "",
    local: Callable[[datetime], datetime] = lambda t: t,
) -> UiMissions:
    """The open missions still in time, soonest to end first (no limit last). `places` = system name →
    position, as far as known."""
    rows = []
    for m in missions.open.values():
        if m.expiry and _time(m.expiry) <= now and not m.redirected:
            continue  # ran out: the game fails it at the next login (a done job no longer runs out)
        at = bool(m.system) and m.system == here
        pos = places.get(m.system)
        d = round(math.dist(star_pos, pos), 2) if star_pos is not None and pos is not None else None
        if at:
            d = 0.0  # you are there: no place needed
        rows.append(
            UiMission(
                id=str(m.mission_id),
                kind=m.kind,
                title=m.title,
                faction=m.faction,
                system=m.system,
                station=m.station,
                expiry="" if m.redirected else m.expiry,
                reward=m.reward,
                wing=m.wing,
                taken=m.taken,
                done=m.redirected,
                here=at,
                distance_ly=d,
                jumps=0 if at else min_jumps(d, range_ly) if d is not None and range_ly else None,
            )
        )
    rows.sort(key=lambda r: (not r.expiry, r.expiry, r.taken, r.id))
    reward = sum(r.reward for r in rows)
    return UiMissions(
        known=known,
        missions=tuple(rows),
        reward=reward,
        lookup=lookup,
        text=_panel(rows, reward, local, now) if known or rows else "—",
    )


def _panel(rows: list[UiMission], reward: int, local: Callable[[datetime], datetime], now: datetime) -> str:
    """ "3 open · 2.4M Cr · next ends 14:30 (Sun Wen)" — a clock time, so it never goes stale (the day's
    name when it is not today)."""
    if not rows:
        return "none open"
    text = f"{len(rows)} open"
    if reward:
        text += f" · {reward / 1e6:.1f}M Cr"
    first = rows[0]
    if first.expiry:
        end = local(_time(first.expiry))
        text += (
            f" · next ends {end:%H:%M}" if end.date() == local(now).date() else f" · next ends {end:%a %H:%M}"
        )
        if first.system:
            text += f" ({first.system})"
    return text
