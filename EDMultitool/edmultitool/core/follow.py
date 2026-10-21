"""Following a route: which point is next, what is left, how rich the stops ahead are. Pure — no I/O.

A route's points are its start, then its stops (tour) or waypoints (plots), then its destination, each
known by id64. `next` indexes them: 0 = go to the start first, len(points) = arrived. Arriving in ANY
later point moves `next` past it, so skipping or overshooting a stop is safe; arriving in an earlier
point, or anywhere off the route, changes nothing — unless the route is followed by your position: then a
tour's stops you are clearly beyond along the start → destination line count as behind you too.
"""

import math
from dataclasses import replace

from edmultitool.core.events import GameRoute
from edmultitool.core.view import UiFollow, UiGameRoute, UiRoute, UiStop, UiWaypoint, stop_worth


def stop_value(stop: UiStop) -> int:
    return stop_worth(stop.scan_value, stop.mapping_value, stop.bio_value)


def points(route: UiRoute) -> tuple[str, ...]:
    """The id64s a route is followed by, start first, destination last ("" = not known)."""
    if route.mode != "tour":
        return tuple(w.id64 for w in route.waypoints)
    return (route.from_id64, *(s.id64 for s in route.stops), route.to_id64)


def start_index(route: UiRoute, address: str | None) -> int:
    """Where a new route starts: past the point you are in (usually the start), else at the start."""
    return arrive(route, 0, address)


def arrive(route: UiRoute, next_index: int, address: str | None) -> int:
    """`next_index` after arriving in `address`: past the furthest point there from `next_index` on."""
    if not address:
        return next_index
    ids = points(route)
    for i in range(len(ids) - 1, next_index - 1, -1):
        if ids[i] == address:
            return i + 1
    return next_index


def step(route: UiRoute, next_index: int, by: int) -> int:
    """Skip (+1) or go back (-1) by hand, kept within the route."""
    return max(0, min(len(points(route)), next_index + by))


def along(route: UiRoute, pos: tuple[float, float, float] | None) -> float | None:
    """How far along a tour's start → destination line `pos` is, ly: its projection, kept within the line.
    None for a plot, or when the route's ends or your position are not known."""
    a, b = route.from_pos, route.to_pos
    if route.mode != "tour" or a is None or b is None or pos is None:
        return None
    line = math.dist(a, b)
    if line == 0:
        return 0.0
    t = sum((pos[i] - a[i]) * (b[i] - a[i]) for i in range(3)) / (line * line)
    return max(0.0, min(1.0, t)) * line


def pass_by_position(route: UiRoute, next_index: int, pos: tuple[float, float, float] | None) -> int:
    """`next_index` moved past every tour stop you are more than one jump (the route's range) beyond along
    the line: flown past, not visited. Never back, never past the destination — that one you arrive in."""
    here = along(route, pos)
    if here is None:
        return next_index
    margin = max(route.range_ly, 1.0)
    passed = sum(1 for s in route.stops if s.along_ly + margin < here)  # stops are in order along the line
    return max(next_index, passed + 1) if passed else next_index  # the start stays until a stop is passed


def ui_follow(
    route: UiRoute | None,
    next_index: int,
    by: str = "position",
    pos: tuple[float, float, float] | None = None,
) -> UiFollow:
    """`by` = how the route is followed ("position" / "stops"); `pos` = where you are."""
    if route is None:
        return UiFollow(by=by)
    ids = points(route)
    count = len(ids)
    n = max(0, min(count, next_index))
    here = along(route, pos) if by == "position" else None
    where = round(here, 1) if here is not None else None
    if n >= count:
        return UiFollow(True, n, count, True, text=f"arrived at {route.to_name}", by=by, along_ly=where)
    f = _tour(route, n, count) if route.mode == "tour" else _plot(route, n, count)
    return replace(f, by=by, along_ly=where)


def _tour(route: UiRoute, n: int, count: int) -> UiFollow:
    stops = route.stops
    ahead = stops[max(0, n - 1) :]  # point i (1..len) is stop i-1
    legs = round(sum(s.leg_ly for s in ahead) + route.final_leg_ly, 1)
    value = sum(stop_value(s) for s in ahead)
    if n == 0:
        name, kind, rich, where = route.from_name, "start", 0, "start of the route"
    elif n <= len(stops):
        stop = stops[n - 1]
        rich = stop.wealth
        dollars = f" · {'$' * rich}" if rich else ""
        name, kind, where = stop.name, "stop", f"stop {n}/{len(stops)}{dollars}"
    else:
        name, kind, rich, where = route.to_name, "destination", 0, "destination"
    return UiFollow(
        True, n, count, False, name, kind, rich,
        stops_left=len(ahead), ly_left=legs, value_ahead=value, text=f"{name} · {where}",
    )  # fmt: skip


def _plot(route: UiRoute, n: int, count: int) -> UiFollow:
    wps = route.waypoints
    left = sum(w.jumps for w in wps[max(1, n) :])
    name = wps[n].name
    if n == 0:
        kind, where = "start", "start of the route"
    elif n == count - 1:
        kind, where = "destination", f"destination · {left} jump{'s' if left != 1 else ''} left"
    else:
        kind = "waypoint"
        boost = " · neutron" if wps[n].neutron else ""
        where = f"{n}/{count - 1}{boost} · {left} jump{'s' if left != 1 else ''} left"
    return UiFollow(True, n, count, False, name, kind, 0, jumps_left=left, text=f"{name} · {where}")


# Star classes a fuel scoop works on (main-sequence and giants of K G B F O A M): "K", "M_RedGiant", …
_SCOOPABLE = frozenset("KGBFOAM")


def game_offer(game: GameRoute | None, route: UiRoute | None, next_index: int) -> UiGameRoute:
    """Offer the galaxy map's route unless the followed route already leads there: its destination is
    one of the points still ahead (plotting in the game to the next stop must not nag)."""
    if game is None or len(game.points) < 2:
        return UiGameRoute()
    dest = game.points[-1]
    ahead = points(route)[next_index:] if route is not None else ()
    return UiGameRoute(
        offer=str(dest.address) not in ahead,
        to_name=dest.name,
        jumps=len(game.points) - 1,
        plotted=game.plotted,
    )


def game_route(game: GameRoute, range_ly: float, finished: str) -> UiRoute:
    """The galaxy map's route as a followable plot: one waypoint per jump, the system it was plotted in
    first. The game does not say where to refuel or supercharge; only which stars can be scooped."""
    pts = game.points
    first, last = pts[0], pts[-1]
    waypoints = tuple(
        UiWaypoint(
            id64=str(p.address),
            name=p.name,
            leg_ly=round(math.dist(pts[i - 1].pos, p.pos), 2) if i else 0.0,
            jumps=1 if i else 0,
            neutron=False,
            refuel=False,
            scoopable=p.star_class.split("_")[0] in _SCOOPABLE if p.star_class else None,
        )
        for i, p in enumerate(pts)
    )
    return UiRoute(
        from_name=first.name,
        to_name=last.name,
        range_ly=round(range_ly, 2),
        total_ly=round(math.dist(first.pos, last.pos), 2),
        final_leg_ly=0.0,
        final_leg_jumps=0,
        from_cache=False,
        finished=finished,
        stops=(),
        mode="game",
        range_basis="full",
        total_jumps=len(pts) - 1,
        waypoints=waypoints,
        from_id64=str(first.address),
        to_id64=str(last.address),
    )
