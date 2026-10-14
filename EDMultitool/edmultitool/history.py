"""Read-only passes over the player's journal history: the remembered fleet, what you have
already explored, who you are to the stations (faction reputations, Powerplay pledge) and
your career: superpower standings, ranks, Powerplay merits; what you earned and spent, and the data you
carry unsold; your open missions; the game's own lifetime statistics.

Runs on the worker thread. Files are opened read-only, newest first, and only lines naming a fleet
event are decoded; `core.fleet.scan` stops the pass once every owned ship has a build, so a long history
is not read to the end. A file the game is still writing may end in half a line: that line is skipped.
"""

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from edmultitool.core.career import Career
from edmultitool.core.commander import Commander, apply_commander
from edmultitool.core.earnings import Earnings
from edmultitool.core.events import (
    CAREER_EVENTS,
    COMMANDER_EVENTS,
    DEATH_EVENTS,
    EXPLORE_EVENTS,
    FLEET_EVENTS,
    MISSION_DONE_EVENT,
    MISSION_EVENTS,
    MONEY_EVENTS,
    PLEDGE_EVENTS,
    REPUTATION_FIELD,
    STATISTICS_EVENTS,
    Arrived,
    BodyMapped,
    Died,
    ExplorationSold,
    MissionEnded,
    OrganicSold,
    PowerPledged,
    SpeciesAnalysed,
    entry_time,
    parse_career,
    parse_commander,
    parse_death,
    parse_event,
    parse_mission,
    parse_money,
    parse_statistics,
)
from edmultitool.core.explored import Located
from edmultitool.core.factions import Factions
from edmultitool.core.fleet import ScanResult, scan
from edmultitool.core.missions import Missions
from edmultitool.core.stats import Stats
from edmultitool.sources.http import Cancelled

_MARKERS = tuple(f'"{name}"'.encode() for name in FLEET_EVENTS)  # cheap pre-filter; parse_event decides
_EXPLORE_MARKERS = tuple(f'"{name}"'.encode() for name in EXPLORE_EVENTS)
_COMMANDER_MARKERS = tuple(f'"{name}"'.encode() for name in COMMANDER_EVENTS)
_CAREER_MARKERS = tuple(f'"{name}"'.encode() for name in CAREER_EVENTS)
_EVENT_KEY = b'"event":'
_MONEY_NAMES = frozenset(name.encode() for name in MONEY_EVENTS)
_MISSION_NAMES = frozenset(name.encode() for name in MISSION_EVENTS)
_STATISTICS_NAMES = frozenset(name.encode() for name in (*STATISTICS_EVENTS, *DEATH_EVENTS))
_STANDING_MARKERS = tuple(
    f'"{name}"'.encode() for name in (REPUTATION_FIELD, *PLEDGE_EVENTS, MISSION_DONE_EVENT)
)


def journal_files(journal_dir: Path, newer_than: float = 0.0) -> list[tuple[float, Path]]:
    """`Journal.*.log` files modified after `newer_than`, newest first. Names are not used for the order:
    the old (pre-2021) and new name formats do not sort together."""
    found: list[tuple[float, Path]] = []
    for path in journal_dir.glob("Journal.*.log"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > newer_than:
            found.append((mtime, path))
    found.sort(reverse=True)
    return found


def _entries(files: list[tuple[float, Path]], cancel: threading.Event) -> Iterator[dict[str, Any]]:
    for _, path in files:
        if cancel.is_set():
            raise Cancelled("cancelled")
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        kept: list[dict[str, Any]] = []
        for line in raw.splitlines():
            if not any(marker in line for marker in _MARKERS):
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                kept.append(entry)
        yield from reversed(kept)


def scan_history(journal_dir: Path, newer_than: float, cancel: threading.Event) -> tuple[ScanResult, float]:
    """The fleet found in journal files newer than `newer_than`, and the newest file time to remember."""
    files = journal_files(journal_dir, newer_than)
    newest = files[0][0] if files else newer_than
    return scan(_entries(files, cancel)), newest


def scan_explored(
    journal_dir: Path, newer_than: float, cancel: threading.Event
) -> tuple[list[Located], float]:
    """What you explored in journal files newer than `newer_than`, OLDEST first and in journal order (the
    store's sale and death rules need the order), each with the system you were in; plus the newest file
    time to remember. Every file is read to the end: unlike the fleet, nothing here is ever settled."""
    files = journal_files(journal_dir, newer_than)
    newest = files[0][0] if files else newer_than
    found: list[Located] = []
    here = ""
    for _, path in reversed(files):
        if cancel.is_set():
            raise Cancelled("cancelled")
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        for line in raw.splitlines():
            if not any(marker in line for marker in _EXPLORE_MARKERS):
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            event = parse_event(entry) if isinstance(entry, dict) else None
            if isinstance(event, Arrived):
                here = "" if event.multicrew else event.system
            elif isinstance(event, BodyMapped | SpeciesAnalysed | ExplorationSold | OrganicSold | Died):
                found.append((event, here))
    return found, newest


def scan_standings(journal_dir: Path, cancel: threading.Event) -> tuple[Factions, str | None]:
    """Every minor faction met — your last known standing with it, where you met it, missions done for it
    — and the power you were last pledged to ("" = none, None = no Powerplay entry in the history).
    Every file, oldest first, so the newest entry wins; not remembered between starts (a pass over the
    journals is quick)."""
    factions = Factions()
    power: str | None = None
    for _, path in reversed(journal_files(journal_dir)):
        if cancel.is_set():
            raise Cancelled("cancelled")
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        for line in raw.splitlines():
            if not any(marker in line for marker in _STANDING_MARKERS):
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            event = parse_event(entry)
            if isinstance(event, Arrived):
                factions.arrived(event, entry_time(entry))
            elif isinstance(event, PowerPledged):
                power = event.power
            else:
                ended = parse_mission(entry)
                if isinstance(ended, MissionEnded):
                    factions.mission(ended)
    return factions, power


def scan_commander(journal_dir: Path, cancel: threading.Event) -> tuple[Commander, str]:
    """Your unpaid fines and bounties since the last destruction, notoriety and balance at the last login,
    from every file oldest first; plus the time of the last entry used, so live entries the pass already saw
    are not counted twice. Legal state is left unknown: only Status.json tells it, live."""
    c = Commander()
    last = ""
    for _, path in reversed(journal_files(journal_dir)):
        if cancel.is_set():
            raise Cancelled("cancelled")
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        for line in raw.splitlines():
            if not any(marker in line for marker in _COMMANDER_MARKERS):
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                for event in parse_commander(entry):
                    c = apply_commander(c, event)
                last = max(last, entry_time(entry))
    return c, last


def scan_career(journal_dir: Path, cancel: threading.Event) -> Career:
    """Your superpower standings, ranks and Powerplay career with their history, from every file oldest
    first; `Career.last` is the time of the newest entry used, so live entries the pass already saw are not
    counted twice. Not remembered between starts (a pass over the journals is quick)."""
    c = Career()
    for _, path in reversed(journal_files(journal_dir)):
        if cancel.is_set():
            raise Cancelled("cancelled")
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        for line in raw.splitlines():
            if not any(marker in line for marker in _CAREER_MARKERS):
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                for event in parse_career(entry):
                    c.add(event)
    return c


def _event_name(line: bytes) -> bytes:
    """The entry's event name without decoding the line (b"" when there is none)."""
    start = line.find(_EVENT_KEY)
    if start < 0:
        return b""
    start = line.find(b'"', start + len(_EVENT_KEY)) + 1
    return line[start : line.find(b'"', start)] if start > 0 else b""


def _first_time(path: Path) -> str:
    """The time of a journal file's first entry ("" when it cannot be read): orders files when their
    modification times cannot be trusted (a copied or restored folder)."""
    try:
        with path.open("rb") as f:
            entry = json.loads(f.readline())
    except (OSError, ValueError):
        return ""
    return entry_time(entry) if isinstance(entry, dict) else ""


def scan_earnings(journal_dir: Path, cancel: threading.Event) -> Earnings:
    """Every credit earned and spent, and the data still unsold, from every file in time order (by each file's
    first entry); `Earnings.last` and `last_seen` let the live entries the pass already read be skipped. Not
    remembered between starts."""
    e = Earnings()
    # a file whose first line cannot be read yet is the one the game is starting to write: the newest
    files = sorted((_first_time(path) or "~", path) for _, path in journal_files(journal_dir))
    for _, path in files:
        if cancel.is_set():
            raise Cancelled("cancelled")
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        lines = raw.splitlines()
        for line in lines:
            if _event_name(line) not in _MONEY_NAMES:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                for event in parse_money(entry):
                    e.add(event)
        for line in reversed(lines):  # the game ran until the file's last entry, whatever it was
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                e.saw(entry_time(entry))
                break
    return e


def scan_missions(journal_dir: Path, cancel: threading.Event) -> Missions:
    """Your open missions from every file in time order (by each file's first entry): each login's list, every
    mission taken, redirected and closed; `Missions.last` lets the live entries the pass already read be
    skipped. Not remembered between starts."""
    m = Missions()
    files = sorted((_first_time(path) or "~", path) for _, path in journal_files(journal_dir))
    for _, path in files:
        if cancel.is_set():
            raise Cancelled("cancelled")
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        for line in raw.splitlines():
            if _event_name(line) not in _MISSION_NAMES:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            event = parse_mission(entry) if isinstance(entry, dict) else None
            if event is not None:
                m.add(event)
    return m


def scan_stats(journal_dir: Path, cancel: threading.Event) -> Stats:
    """Every Statistics entry (one per login) and every death with the resurrection after it, from every file;
    one read both here and live is kept once. Not remembered between starts."""
    s = Stats()
    for _, path in journal_files(journal_dir):
        if cancel.is_set():
            raise Cancelled("cancelled")
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        for line in raw.splitlines():
            if _event_name(line) not in _STATISTICS_NAMES:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            event = parse_statistics(entry)
            if event is not None:
                s.add(event)
            death = parse_death(entry)
            if death is not None:
                s.deaths.add(death)
    return s
