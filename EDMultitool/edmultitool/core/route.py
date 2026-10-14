"""Scout route: turn the remembered form into Spansh queries, and the two search results into one list of
stops ordered along the from→to line. Pure — no I/O, no threads.

Thresholds (0 = off):
- scan search: a body counts when its scan value ≥ min_scan AND its mapping value ≥ min_mapping;
- bio search: species cheaper than min_species are dropped; a body counts when the species left add up
  to ≥ min_bio_body;
- with `planets` = "prime", the scan search keeps only Earth-like, water and ammonia worlds and
  terraformables. Spansh cannot filter by type and returns its N most valuable systems, so the scan
  search asks for MAX_POOL of them and `build_route(keep=…)` keeps the best `max_stops` that pass;
- a stop with no counted body is dropped. A system found by both searches is one stop; a body found by
  both is one body carrying both marks.

What you already explored is not counted: a body you mapped loses its scan / mapping value, an
analysed species its bio value (the per-body bio total is checked again on the species left). A body that
passed the thresholds stays on its stop and is shown as yours; a stop left with no counted body is
"spent". `swap_spent` replaces a spent stop with the best one of a bigger search when that adds at most
`max_detour_ly` to the trip; otherwise the spent stop stays, marked.
"""

import math
from dataclasses import dataclass, replace
from itertools import pairwise

from edmultitool.core.explored import NOTHING, Explored, species_key
from edmultitool.core.scout import BioSpecies, ScoutBody, ScoutKind, ScoutQuery, ScoutResult, ScoutSystem
from edmultitool.core.settings import ScoutPrefs

Point = tuple[float, float, float]

# A spent stop's replacement comes from the same searches asked for this many times more results (at most
# MAX_POOL). Spansh returns the best N stops along the route, so the bigger answer holds the normal one
# plus the next best (checked live 2026-10-12, docs/api-notes.md).
POOL_FACTOR = 3
MAX_POOL = 100

# Spansh subtypes the "prime" planet filter keeps, besides any terraformable body
PRIME_SUBTYPES = frozenset({"Earth-like world", "Water world", "Ammonia world"})


def pool_size(max_stops: int) -> int:
    return min(MAX_POOL, max_stops * POOL_FACTOR)


def scout_queries(
    prefs: ScoutPrefs, from_system: str, range_ly: float, *, max_results: int | None = None
) -> list[tuple[ScoutKind, ScoutQuery]]:
    """The Spansh searches the form asks for. Spansh takes one minimum per search:
    - scan search: a per-body minimum on mapping value (or on scan value when the mapping threshold is
      off); the other scan threshold is applied by `build_route`;
    - bio search: a per-SPECIES minimum — Spansh leaves cheaper species out of each body's list (checked
      live 2026-10-12, docs/api-notes.md); the per-body total is applied by `build_route`.
    `max_results` overrides the form's stop count (the bigger search for replacements). With the prime
    planet filter the scan search always asks for MAX_POOL results (the filter runs on our side)."""
    base = ScoutQuery(
        from_system=from_system,
        to_system=prefs.to_system,
        range_ly=range_ly,
        radius_ly=prefs.radius_ly,
        max_results=prefs.max_stops if max_results is None else max_results,
        max_distance_ls=prefs.max_distance_ls,
    )
    out: list[tuple[ScoutKind, ScoutQuery]] = []
    if prefs.riches:
        by_mapping = prefs.min_mapping > 0
        min_value = prefs.min_mapping if by_mapping else prefs.min_scan
        riches = replace(base, min_value=min_value, use_mapping_value=by_mapping)
        if prefs.planets == "prime":
            riches = replace(riches, max_results=MAX_POOL)
        out.append(("riches", riches))
    if prefs.bio:
        out.append(("bio", replace(base, min_value=prefs.min_species, use_mapping_value=False)))
    return out


@dataclass(frozen=True, slots=True)
class RouteBody:
    id64: int
    name: str
    type: str
    subtype: str
    distance_ls: float
    scan_value: int
    mapping_value: int
    terraformable: bool
    scan_hit: bool  # passed the scan search's thresholds
    species: tuple[BioSpecies, ...]  # species that passed the bio search's thresholds (empty if it did not)
    mapped: bool = False  # you mapped it: its scan / mapping value is yours already
    done_species: tuple[str, ...] = ()  # names of `species` you analysed on this body
    bio_counted: bool = False  # the species you did NOT analyse still pass the per-body bio total

    @property
    def open_species(self) -> tuple[BioSpecies, ...]:
        done = {species_key(n) for n in self.done_species}
        return tuple(s for s in self.species if species_key(s.species) not in done)

    @property
    def bio_value(self) -> int:
        """Counted bio value: the species not analysed yet, when they still pass the per-body total."""
        return sum(s.value for s in self.open_species) if self.bio_counted else 0

    @property
    def counts_scan(self) -> bool:
        return self.scan_hit and not self.mapped

    @property
    def counted(self) -> bool:
        return self.counts_scan or self.bio_counted


@dataclass(frozen=True, slots=True)
class RouteStop:
    id64: int
    name: str
    pos: Point
    along_ly: float  # distance along the from→to line from the start (its projection)
    off_line_ly: float  # distance from that line
    leg_ly: float  # straight distance from the previous stop (or the start)
    bodies: tuple[RouteBody, ...]
    replaces: str = ""  # this stop took the place of a spent one (its name)
    detour_ly: float = 0.0  # what taking this stop instead added to the trip

    @property
    def scan_value(self) -> int:
        return sum(b.scan_value for b in self.bodies if b.counts_scan)

    @property
    def mapping_value(self) -> int:
        return sum(b.mapping_value for b in self.bodies if b.counts_scan)

    @property
    def bio_value(self) -> int:
        return sum(b.bio_value for b in self.bodies)

    @property
    def counted(self) -> bool:
        """False = spent: every body that made it a stop is already yours."""
        return any(b.counted for b in self.bodies)

    @property
    def worth(self) -> int:
        """What is left to earn here: counted bodies mapped (or scanned) plus counted species."""
        return max(self.scan_value, self.mapping_value) + self.bio_value


@dataclass(frozen=True, slots=True)
class Route:
    from_name: str
    to_name: str
    range_ly: float
    total_ly: float  # straight from→to
    final_leg_ly: float  # last stop (or the start) → destination
    stops: tuple[RouteStop, ...]
    from_cache: bool
    from_id64: int = 0
    to_id64: int = 0
    from_pos: Point = (0.0, 0.0, 0.0)
    to_pos: Point = (0.0, 0.0, 0.0)
    min_bio_body: int = 0  # the form's per-body bio total, for grading what is left later (view.regrade)


def min_jumps(distance_ly: float, range_ly: float) -> int:
    """Fewest jumps a straight leg can take at this range (the in-game plotter may need more)."""
    if distance_ly <= 0:
        return 0
    return math.ceil(distance_ly / range_ly - 1e-9)


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _dist(a: Point, b: Point) -> float:
    return math.sqrt(_dot(_sub(a, b), _sub(a, b)))


def _pos(s: ScoutSystem) -> Point:
    return (s.x, s.y, s.z)


def _riches_body(b: ScoutBody, prefs: ScoutPrefs, done: Explored) -> RouteBody | None:
    if b.scan_value < prefs.min_scan or b.mapping_value < prefs.min_mapping:
        return None
    if prefs.planets == "prime" and not (b.terraformable or b.subtype in PRIME_SUBTYPES):
        return None
    return RouteBody(
        b.id64, b.name, b.type, b.subtype, b.distance_ls, b.scan_value, b.mapping_value, b.terraformable,
        scan_hit=True, species=(), mapped=done.is_mapped(b.id64),
    )  # fmt: skip


def _bio_body(b: ScoutBody, prefs: ScoutPrefs, done: Explored) -> RouteBody | None:
    species = tuple(s for s in b.species if s.value >= prefs.min_species)
    if not species or sum(s.value for s in species) < prefs.min_bio_body:
        return None
    analysed = tuple(s.species for s in species if done.is_analysed(b.id64, s.species))
    left = sum(s.value for s in species if s.species not in analysed)
    return RouteBody(
        b.id64, b.name, b.type, b.subtype, b.distance_ls, b.scan_value, b.mapping_value, b.terraformable,
        scan_hit=False, species=species, mapped=done.is_mapped(b.id64), done_species=analysed,
        bio_counted=left > 0 and left >= prefs.min_bio_body,
    )  # fmt: skip


def _merge(a: RouteBody, b: RouteBody) -> RouteBody:
    bio = a if a.species else b
    return replace(
        a, scan_hit=a.scan_hit or b.scan_hit, species=bio.species, mapped=a.mapped or b.mapped,
        done_species=bio.done_species, bio_counted=a.bio_counted or b.bio_counted,
    )  # fmt: skip


def build_route(
    results: list[ScoutResult], prefs: ScoutPrefs, done: Explored = NOTHING, *, keep: int | None = None
) -> Route:
    """Merge the finished searches (same from/to/range) into one ordered list of stops; what is `done`
    is not counted. With the prime planet filter and `keep`, only the `keep` stops whose scan bodies are
    worth the most stay (stops with bio bodies stay anyway); without `keep` (the bigger search for
    replacements) all stay."""
    if not results:
        raise ValueError("no search results to build a route from")
    ends = next((r.systems for r in results if len(r.systems) >= 2), None)
    if ends is None:
        raise ValueError("search result without start and destination")
    start, dest = ends[0], ends[-1]
    a, b = _pos(start), _pos(dest)
    line = _sub(b, a)
    total = math.sqrt(_dot(line, line))

    systems: dict[int, ScoutSystem] = {}
    bodies: dict[int, dict[int, RouteBody]] = {}
    for result in results:
        pick = _riches_body if result.kind == "riches" else _bio_body
        for system in result.systems[1:-1]:  # the first and last rows are start and destination
            if system.id64 in (start.id64, dest.id64):
                continue
            systems.setdefault(system.id64, system)
            kept = bodies.setdefault(system.id64, {})
            for body in system.bodies:
                rb = pick(body, prefs, done)
                if rb is not None:
                    kept[rb.id64] = _merge(kept[rb.id64], rb) if rb.id64 in kept else rb

    stops: list[RouteStop] = []
    for id64, system in systems.items():
        if not bodies[id64]:
            continue
        rel = _sub(_pos(system), a)
        along = _dot(rel, line) / total if total > 0 else 0.0
        off = math.sqrt(max(0.0, _dot(rel, rel) - along * along))
        ordered = tuple(sorted(bodies[id64].values(), key=lambda rb: (rb.distance_ls, rb.id64)))
        stops.append(RouteStop(id64, system.name, _pos(system), along, off, 0.0, ordered))
    if prefs.planets == "prime" and keep is not None:
        stops = _best_scan_stops(stops, keep)
    route = Route(
        from_name=start.name,
        to_name=dest.name,
        range_ly=results[0].query.range_ly,
        total_ly=total,
        final_leg_ly=0.0,
        stops=(),
        from_cache=all(r.from_cache for r in results),
        from_id64=start.id64,
        to_id64=dest.id64,
        from_pos=a,
        to_pos=b,
        min_bio_body=prefs.min_bio_body,
    )
    return _ordered(route, stops)


def _best_scan_stops(stops: list[RouteStop], keep: int) -> list[RouteStop]:
    """The `keep` stops whose scan bodies are worth the most (ties: id64), plus every stop with bio
    bodies. Ranked like Spansh ranks them — bodies you mapped count too — so a stop that is yours already
    stays and is left to `swap_spent` and its detour limit, exactly as without the filter."""
    scan = sorted(
        (s for s in stops if any(b.scan_hit for b in s.bodies)),
        key=lambda s: (-sum(max(b.scan_value, b.mapping_value) for b in s.bodies if b.scan_hit), s.id64),
    )
    best = {s.id64 for s in scan[:keep]}
    return [s for s in stops if s.id64 in best or any(b.species for b in s.bodies)]


def _ordered(route: Route, stops: list[RouteStop]) -> Route:
    """The stops in order along the from→to line, each leg measured from the one before."""
    out: list[RouteStop] = []
    prev = route.from_pos
    for stop in sorted(stops, key=lambda s: (s.along_ly, s.id64)):
        out.append(replace(stop, leg_ly=_dist(prev, stop.pos)))
        prev = stop.pos
    return replace(route, stops=tuple(out), final_leg_ly=_dist(prev, route.to_pos))


def spent(route: Route) -> int:
    """How many stops hold nothing left to earn."""
    return sum(not s.counted for s in route.stops)


def _trip(route: Route, stops: list[RouteStop]) -> float:
    """Straight legs start → stops in order along the line → destination."""
    points = [
        route.from_pos,
        *(s.pos for s in sorted(stops, key=lambda s: (s.along_ly, s.id64))),
        route.to_pos,
    ]
    return sum(_dist(a, b) for a, b in pairwise(points))


def swap_spent(route: Route, pool: Route, max_detour_ly: float) -> Route:
    """Replace each spent stop with the most valuable unused stop of `pool` (the same searches asked for
    more results, built with the same `done`) whose detour — the whole trip with it (stops in order
    along the line again) minus the trip with the spent stop — is at most `max_detour_ly`. 0 = never
    swap. A spent stop with no such alternative stays."""
    if max_detour_ly <= 0:
        return route
    used = {s.id64 for s in route.stops}
    spare = [s for s in pool.stops if s.id64 not in used and s.counted]
    stops = list(route.stops)
    swapped = False
    for i, stop in enumerate(stops):
        if stop.counted or not spare:
            continue
        now = _trip(route, stops)
        fits = [(c, _trip(route, [*stops[:i], c, *stops[i + 1 :]]) - now) for c in spare]
        fits = [(c, extra) for c, extra in fits if extra <= max_detour_ly + 1e-9]
        if not fits:
            continue
        best, extra = max(fits, key=lambda f: (f[0].worth, -f[1], -f[0].id64))
        spare.remove(best)
        stops[i] = replace(best, replaces=stop.name, detour_ly=max(0.0, extra))
        swapped = True
    return _ordered(route, stops) if swapped else route
