"""The system you just arrived in: is it worth your time? Pure — no I/O, no threads.

On every arrival Spansh is asked once for the bodies it knows there (`sources.spansh.system_bodies`); while
you scan, the journal's FSS / DSS results add what Spansh did not know. The card counts, with the
route-scout thresholds (0 = off):
- worth mapping: planets whose scan value ≥ min_scan and mapping value ≥ min_mapping (with the "prime"
  planet filter: only Earth-like, water and ammonia worlds and terraformables);
- exobiology: species worth ≥ min_species, on a body where the species left add up to ≥ min_bio_body;
- both only within max_distance_ls of the arrival star.
What you mapped or analysed already is not counted. Bodies with biological signals but no species
known yet are listed apart: their worth is unknown until someone samples them.

Values Spansh did not give (a body only your scan found, or one Spansh lists without values) are
estimated from the scan, with the formula Spansh's own estimates follow
(checked against its recorded values, tests/test_arrival.py): base = k + k·0.56591828·mass^0.2 for planets
(Earth masses), k + mass·k/66.25 for stars (solar masses), at least 500, whole credits down; mapping =
⌊scan·3.3333333333⌋ + ⌊max(scan·0.3, 555)⌋. Neither counts the first-discovery or first-mapped bonus.
"""

import math
from dataclasses import dataclass, replace
from typing import Literal

from edmultitool.catalog.genera import genus_name
from edmultitool.core.events import AllBodiesFound, BodyScanned, BodySignals, SurveyEvent, SystemSurveyed
from edmultitool.core.explored import Explored, body_id64, species_key
from edmultitool.core.route import PRIME_SUBTYPES
from edmultitool.core.scout import BioSpecies
from edmultitool.core.settings import ScoutPrefs

ArrivalStatus = Literal["none", "looking", "done", "error"]

# journal PlanetClass → Spansh's subtype name (the card shows Spansh's names for every body)
PLANET_SUBTYPES: dict[str, str] = {
    "Metal rich body": "Metal-rich body",
    "High metal content body": "High metal content world",
    "Rocky body": "Rocky body",
    "Icy body": "Icy body",
    "Rocky ice body": "Rocky Ice world",
    "Earthlike body": "Earth-like world",
    "Water world": "Water world",
    "Ammonia world": "Ammonia world",
    "Water giant": "Water giant",
    "Water giant with life": "Water giant with life",
    "Gas giant with water based life": "Gas giant with water-based life",
    "Gas giant with ammonia based life": "Gas giant with ammonia-based life",
    "Sudarsky class I gas giant": "Class I gas giant",
    "Sudarsky class II gas giant": "Class II gas giant",
    "Sudarsky class III gas giant": "Class III gas giant",
    "Sudarsky class IV gas giant": "Class IV gas giant",
    "Sudarsky class V gas giant": "Class V gas giant",
    "Helium rich gas giant": "Helium-rich gas giant",
    "Helium gas giant": "Helium gas giant",
}

# k per Spansh subtype, and what being terraformable adds to it; anything else: 300 (+ 93,328)
_PLANET_K: dict[str, tuple[int, int]] = {
    "Metal-rich body": (21_790, 0),
    "Ammonia world": (96_932, 0),
    "Class I gas giant": (1_656, 0),
    "High metal content world": (9_654, 100_677),
    "Class II gas giant": (9_654, 0),
    "Earth-like world": (64_831 + 116_295, 0),
    "Water world": (64_831, 116_295),
}
_OTHER_K = (300, 93_328)
_Q = 0.56591828
_MIN_VALUE = 500
_MAPPING = 3.3333333333


def _star_k(star_type: str) -> int:
    if star_type in ("N", "H", "SupermassiveBlackHole"):
        return 22_628
    if star_type.startswith("D"):
        return 14_057
    return 1_200


def planet_values(subtype: str, mass_em: float, terraformable: bool) -> tuple[int, int]:
    """(scan, mapping) value of a planet from its scan (Spansh's subtype name)."""
    k, terra = _PLANET_K.get(subtype, _OTHER_K)
    k += terra if terraformable else 0
    scan = math.floor(max(_MIN_VALUE, k + k * _Q * max(mass_em, 0.0) ** 0.2))
    return scan, math.floor(scan * _MAPPING) + math.floor(max(scan * 0.3, 555))


def star_value(star_type: str, solar_masses: float) -> int:
    k = _star_k(star_type)
    return math.floor(max(_MIN_VALUE, k + max(solar_masses, 0.0) * k / 66.25))


def sale_value(e: BodyScanned, mapped: bool, efficient: bool) -> int:
    """What Universal Cartographics pays for your data of one body, with the bonuses the scan tells:
    first discovery x2.6, first mapped (x3.699622554 when also first discovered, else x8.0956 instead of
    x3.3333333333), mapping within the probe target x1.25, and the extra max(value·0.3, 555) for mapping."""
    first = e.discovered is False
    if e.star_type:
        value = float(star_value(e.star_type, e.mass))
    else:
        k, terra = _PLANET_K.get(PLANET_SUBTYPES.get(e.planet_class, e.planet_class), _OTHER_K)
        k += terra if e.terraformable else 0
        factor = 1.0
        if mapped:
            first_mapped = e.mapped is False
            factor = 3.699622554 if first and first_mapped else 8.0956 if first_mapped else _MAPPING
            factor *= 1.25 if efficient else 1.0
        value = max(_MIN_VALUE, (k + k * _Q * max(e.mass, 0.0) ** 0.2) * factor)
        if mapped:
            value += max(value * 0.3, 555)
    value = round(value)
    return round(value * 2.6) if first else value


def honk_value(e: BodyScanned | None) -> int:
    """What one body other than the arrival star adds to its system's sale, scanned or not: a third of its
    base value (stars: no 500 minimum, planets: at least 500), x2.6 where nobody discovered it before.
    `None` = a body known only from the discovery scan's count: valued at the 500 minimum."""
    if e is None:
        return _MIN_VALUE
    if e.star_type:
        k = _star_k(e.star_type)
        value = (k + max(e.mass, 0.0) * k / 66.25) / 3
    else:
        k, terra = _PLANET_K.get(PLANET_SUBTYPES.get(e.planet_class, e.planet_class), _OTHER_K)
        k += terra if e.terraformable else 0
        value = max(_MIN_VALUE, (k + k * _Q * max(e.mass, 0.0) ** 0.2) / 3)
    value = round(value)
    return round(value * 2.6) if e.discovered is False else value


@dataclass(frozen=True, slots=True)
class SystemBody:
    """One body of the system, as Spansh knows it or as your own scan found it."""

    body_id: int  # the journal's BodyID (Spansh's body id64 = system id64 | body_id << 55)
    name: str
    star: bool
    subtype: str  # Spansh's naming ("High metal content world", "K (Yellow-Orange) Star"…)
    distance_ls: float
    scan_value: int
    mapping_value: int  # 0 for a star: stars are not mapped
    terraformable: bool = False
    landable: bool = False
    bio_signals: int = 0  # biological signals (FSS / DSS, or Spansh's record of them)
    genera: tuple[str, ...] = ()  # the game's genus names
    species: tuple[BioSpecies, ...] = ()  # species someone sampled here, with their value
    discovered: bool | None = None  # your scan: someone discovered it before you (None: not known)


def scanned_body(e: BodyScanned) -> SystemBody:
    """A body from your own scan, valued like Spansh values it."""
    if e.star_type:
        return SystemBody(
            e.body_id, e.name, True, e.star_type, e.distance_ls, star_value(e.star_type, e.mass), 0,
            discovered=e.discovered,
        )  # fmt: skip
    subtype = PLANET_SUBTYPES.get(e.planet_class, e.planet_class)
    scan, mapping = planet_values(subtype, e.mass, e.terraformable)
    return SystemBody(
        e.body_id, e.name, False, subtype, e.distance_ls, scan, mapping, e.terraformable, e.landable,
        discovered=e.discovered,
    )  # fmt: skip


@dataclass(frozen=True, slots=True)
class Arrival:
    """What is known about the system you are in. `known` = Spansh's bodies (None until it answered);
    `scanned` / `signals` = your own scans of this visit, by BodyID."""

    address: int | None = None
    system: str = ""
    status: ArrivalStatus = "none"
    message: str = ""  # while looking: waiting for the route search; on error: what failed
    known: tuple[SystemBody, ...] | None = None
    scanned: tuple[SystemBody, ...] = ()
    signals: tuple[tuple[int, int, tuple[str, ...]], ...] = ()  # (body_id, biological, genera)
    body_count: int | None = None  # from the FSS honk
    all_found: bool = False


def arrive(address: int, system: str, message: str = "") -> Arrival:
    return Arrival(address, system, "looking", message)


def survey(a: Arrival, e: SurveyEvent) -> Arrival:
    """One FSS / DSS result of the system you are in (results from elsewhere change nothing)."""
    if e.system_address != a.address:
        return a
    match e:
        case SystemSurveyed():
            return replace(a, body_count=e.body_count)
        case AllBodiesFound():
            return replace(a, all_found=True)
        case BodyScanned():
            body = scanned_body(e)
            return replace(a, scanned=(*(b for b in a.scanned if b.body_id != e.body_id), body))
        case BodySignals():
            old = next((s for s in a.signals if s[0] == e.body_id), None)
            genera = tuple(genus_name(g) for g in e.genera) or (old[2] if old else ())
            row = (e.body_id, max(e.biological, old[1] if old else 0), genera)
            return replace(a, signals=(*(s for s in a.signals if s[0] != e.body_id), row))
    return a


def bodies(a: Arrival) -> tuple[list[SystemBody], set[int]]:
    """Every body known here — Spansh's, with your scans' discovery marks and signals added, then the ones
    only your scans found — and the BodyIDs Spansh did not know."""
    known = {b.body_id: b for b in a.known or ()}
    mine = {b.body_id: b for b in a.scanned}
    out: dict[int, SystemBody] = {}
    for bid, b in known.items():
        s = mine.get(bid)
        if s is None:
            out[bid] = b
        elif not b.star and not b.mapping_value:  # Spansh lists it without values: your scan's estimate
            out[bid] = replace(
                b, scan_value=s.scan_value, mapping_value=s.mapping_value, discovered=s.discovered
            )
        else:
            out[bid] = replace(b, discovered=s.discovered)
    new = {bid for bid in mine if bid not in known}
    for bid in new:
        out[bid] = mine[bid]
    for bid, count, genera in a.signals:
        body = out.get(bid)
        if body is not None:
            out[bid] = replace(body, bio_signals=max(body.bio_signals, count), genera=genera or body.genera)
    return list(out.values()), new


@dataclass(frozen=True, slots=True)
class UiSpot:
    """One body worth something here (or with life nobody has sampled yet)."""

    short: str  # "A 5": the name without the system's
    subtype: str
    distance_ls: float
    terraformable: bool
    mapping_value: int  # counted mapping value (includes the scan); 0 = not counted
    bio_value: int  # counted species value; 0 = not counted
    species: tuple[tuple[str, int], ...]  # counted species (name, value)
    signals: int  # biological signals
    unknown: int  # biological signals no known species accounts for
    genera: tuple[str, ...]  # genus names known (from the DSS, or Spansh)
    new: bool  # your scan found it; Spansh did not know it
    first: bool  # your scan says nobody discovered it before you
    worth: int  # mapping_value + bio_value


@dataclass(frozen=True, slots=True)
class UiArrival:
    """The "Here" card (`UiState.arrival`) and the panel's "Here:" row (`text`)."""

    status: ArrivalStatus = "none"
    system: str = ""
    message: str = ""
    known: int = 0  # bodies Spansh knows here
    new: int = 0  # bodies your scans found that Spansh did not know
    body_count: int | None = None  # bodies in the system (FSS honk)
    all_found: bool = False
    unknown_system: bool = False  # Spansh knows no body here: first discovery possible
    first: int = 0  # bodies your scans say nobody discovered before you
    spots: tuple[UiSpot, ...] = ()  # worth first, then unsampled life
    map_value: int = 0
    map_count: int = 0
    bio_value: int = 0
    bio_count: int = 0
    unknown_bio: int = 0  # bodies with life no known species accounts for
    yours: int = 0  # bodies over your thresholds that you mapped / analysed already
    worth: int = 0
    best: int = 0  # the most one body here is worth (the page flashes over Settings.flash_over)
    text: str = "—"


def short_name(body: str, system: str) -> str:
    if body.casefold().startswith(system.casefold()) and len(body) > len(system):
        return body[len(system) :].strip() or body
    return body


def millions(n: int) -> str:
    return f"{n / 1e6:.1f}M" if n >= 50_000 else "<0.1M"


def ui_arrival(a: Arrival, prefs: ScoutPrefs, explored: Explored) -> UiArrival:
    """The card. While Spansh is still being asked, or did not answer, your own scans are counted alone."""
    if a.status == "none" or a.address is None:
        return UiArrival()
    found, new = bodies(a)
    if a.known is None:
        new = set()  # not known to be new until Spansh has answered
    spots: list[UiSpot] = []
    yours = 0
    for b in found:
        spot, done = _spot(b, a, prefs, explored, b.body_id in new)
        yours += done
        if spot is not None:
            spots.append(spot)
    spots.sort(key=lambda s: (-s.worth, -s.unknown, s.distance_ls))
    counted = [s for s in spots if s.worth]
    ui = UiArrival(
        a.status,
        a.system,
        a.message,
        known=len(a.known or ()),
        new=len(new),
        body_count=a.body_count,
        all_found=a.all_found,
        unknown_system=a.status == "done" and not a.known,
        first=sum(1 for b in found if b.discovered is False),
        spots=tuple(spots),
        map_value=sum(s.mapping_value for s in counted),
        map_count=sum(1 for s in counted if s.mapping_value),
        bio_value=sum(s.bio_value for s in counted),
        bio_count=sum(1 for s in counted if s.bio_value),
        unknown_bio=sum(1 for s in spots if s.unknown),
        yours=yours,
    )
    ui = replace(ui, worth=ui.map_value + ui.bio_value, best=max((s.worth for s in spots), default=0))
    if a.status == "done":
        return replace(ui, text=_text(ui))
    lead = "looking up…" if a.status == "looking" else "no answer from Spansh"
    return replace(ui, text=f"{lead} · {_text(ui)}" if found else lead)


def _spot(
    b: SystemBody, a: Arrival, prefs: ScoutPrefs, explored: Explored, new: bool
) -> tuple[UiSpot | None, bool]:
    """The body as the card lists it (None: nothing to list), and whether it is left out because you
    mapped / analysed it already."""
    if b.star or (prefs.max_distance_ls and b.distance_ls > prefs.max_distance_ls):
        return None, False
    assert a.address is not None
    bid = body_id64(a.address, b.body_id)
    prime = prefs.planets != "prime" or b.terraformable or b.subtype in PRIME_SUBTYPES
    map_hit = prime and b.scan_value >= prefs.min_scan and b.mapping_value >= prefs.min_mapping
    mapped = explored.is_mapped(bid)

    species = [s for s in b.species if s.value >= prefs.min_species]
    bio_hit = bool(species) and sum(s.value for s in species) >= prefs.min_bio_body
    open_species = sorted(
        (s for s in species if not explored.is_analysed(bid, s.species)), key=lambda s: -s.value
    )
    left = sum(s.value for s in open_species)
    bio_counted = bio_hit and left > 0 and left >= prefs.min_bio_body
    mapping_value = b.mapping_value if map_hit and not mapped else 0
    bio_value = left if bio_counted else 0
    done = (map_hit or bio_hit) and not mapping_value and not bio_value  # over a threshold, but yours
    known_species = {species_key(s.species) for s in b.species}
    unknown = max(0, b.bio_signals - len(known_species))
    if not mapping_value and not bio_value and not unknown:
        return None, done
    spot = UiSpot(
        short=short_name(b.name, a.system),
        subtype=b.subtype,
        distance_ls=round(b.distance_ls, 1),
        terraformable=b.terraformable,
        mapping_value=mapping_value,
        bio_value=bio_value,
        species=tuple((s.species, s.value) for s in open_species) if bio_counted else (),
        signals=b.bio_signals,
        unknown=unknown,
        genera=b.genera,
        new=new,
        first=b.discovered is False,
        worth=mapping_value + bio_value,
    )
    return spot, done


def _text(ui: UiArrival) -> str:
    """The panel's "Here:" row."""
    parts: list[str] = []
    if ui.worth:
        what = []
        if ui.map_count:
            what.append(f"map {ui.map_count}")
        if ui.bio_count:
            what.append(f"bio {ui.bio_count}")
        parts.append(f"{millions(ui.worth)} Cr: " + " · ".join(what))
    if ui.unknown_bio:
        parts.append(f"life on {ui.unknown_bio} unsampled")
    if ui.unknown_system and not ui.new:
        parts.append("unknown to Spansh: first discovery possible")
    elif ui.first:
        parts.append(f"{ui.first} undiscovered")
    if not parts:
        parts.append("all yours already" if ui.yours else "nothing over your minimums")
    return " · ".join(parts)
