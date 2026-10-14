"""Ask Spansh which place pieces of a decoded gear post are systems and which are stations (worker
thread). `core.gear.locate` then ties the post's gear to them.

Polite and small: the autocomplete (one paced call per piece that may be a system, any letter case, cached
24 h) finds the systems; one system search returns them with their stations; a station search (one per
name) runs only for station names no found system holds. Pieces that read like station names ("Reiter
Gateway") are never asked for as systems.
"""

import threading

from edmultitool.core.gear import (
    Lookup,
    ParsedPost,
    StationInfo,
    SystemInfo,
    match_station,
    name_key,
    station_like,
)
from edmultitool.core.scout import SystemHit
from edmultitool.sources import spansh
from edmultitool.sources.http import Cancelled, PoliteClient, SourceError

MAX_SYSTEM_PIECES = 10  # autocomplete calls for one post (≤ 1/s each)
MAX_STATION_PIECES = 4  # station searches for one post


def _exact(client: PoliteClient, piece: str, cancel: threading.Event) -> SystemHit | None:
    """The one system called `piece` (any letter case); None when there is none or several."""
    wanted = " ".join(piece.split()).casefold()
    hits = {h.id64: h for h in spansh.system_names(client, piece, cancel) if h.name.casefold() == wanted}
    return next(iter(hits.values())) if len(hits) == 1 else None


def _spellings(piece: str) -> list[str]:
    """Spansh's name search wants the exact letter case: the post's own spelling, and each word
    capitalised ("Grover terminal" → "Grover Terminal", "COLET'S PROGRESS" → "Colet's Progress")."""
    words = piece.split()
    capitalised = " ".join(w[:1].upper() + w[1:].lower() for w in words)
    first_up = " ".join(w[:1].upper() + w[1:] for w in words)
    return list(dict.fromkeys([" ".join(words), first_up, capitalised]))


def lookup(client: PoliteClient, post: ParsedPost, cancel: threading.Event) -> Lookup:
    """A name Spansh fails on (network, HTTP error) is marked `failed` and the others still go ahead;
    only when every question failed does the whole lookup fail, with the last error."""
    pieces = post.pieces
    hits: dict[str, SystemHit] = {}
    failed: set[str] = set()
    error: SourceError | None = None
    answered = False  # Spansh answered at least one question, even if only "no such name"
    asked = 0
    for piece in pieces:
        if station_like(piece) or asked >= MAX_SYSTEM_PIECES:
            continue
        asked += 1
        try:
            hit = _exact(client, piece, cancel)
        except Cancelled:
            raise
        except SourceError as exc:
            failed.add(name_key(piece))
            error = exc
            continue
        answered = True
        if hit is not None:
            hits[name_key(piece)] = hit

    try:
        infos = {s.id64: s for s in spansh.systems_by_name(client, [h.name for h in hits.values()], cancel)}
    except Cancelled:
        raise
    except SourceError as exc:  # the systems are known by name and place still; only their stations are not
        infos, error = {}, exc
    systems: dict[str, SystemInfo] = {}
    for key, hit in hits.items():
        # a system the search does not return (new, or renamed) still has its place, without stations
        systems[key] = infos.get(hit.id64) or SystemInfo(hit.id64, hit.name, hit.x, hit.y, hit.z)

    found = list({s.id64: s for s in systems.values()}.values())
    unmatched = [
        p
        for p in pieces
        if name_key(p) not in systems and not any(match_station(p, s.stations) for s in found)
    ]
    wanted = [p for p in unmatched if station_like(p) or not found][:MAX_STATION_PIECES]
    stations: dict[str, tuple[StationInfo, ...]] = {}
    station_systems: dict[int, SystemInfo] = {}
    for piece in wanted:  # one search per name: its hits are then all of them (a shared name stays shared)
        try:
            rows = spansh.stations_by_name(client, _spellings(piece), cancel)
        except Cancelled:
            raise
        except SourceError as exc:
            failed.add(name_key(piece))
            error = exc
            continue
        answered = True
        same = tuple(st for st, _sys in rows if name_key(st.name) == name_key(piece))
        if same:
            stations[name_key(piece)] = same
        for st, sys in rows:
            station_systems.setdefault(sys.id64, replace_stations(sys, st))
    if error is not None and not answered:
        raise error  # no question was answered: say why
    return Lookup(systems, stations, station_systems, frozenset(failed))


def replace_stations(system: SystemInfo, station: StationInfo) -> SystemInfo:
    """A station-search system carries no station list: give it the one station we know."""
    return SystemInfo(system.id64, system.name, system.x, system.y, system.z, (station,))
