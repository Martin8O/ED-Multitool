"""What the EDMC panel and the web page show — pure data, testable and dumpable without tkinter."""

import math
from dataclasses import dataclass, field, replace
from typing import Literal

from edmultitool.catalog.powers import POWERS
from edmultitool.catalog.ships import ship_display_name, ship_pad
from edmultitool.core.arrival import UiArrival
from edmultitool.core.career import UiCareer
from edmultitool.core.commander import UiCommander
from edmultitool.core.earnings import UiEarnings
from edmultitool.core.explored import Explored
from edmultitool.core.factions import UiFactions
from edmultitool.core.fleet import Fleet, StoredShip
from edmultitool.core.gear_view import UiGear, with_distances
from edmultitool.core.market import UiMarket
from edmultitool.core.market import with_distances as market_distances
from edmultitool.core.missions import UiMissions
from edmultitool.core.nearby import UiEngineer, UiServices, ui_engineers
from edmultitool.core.nearby import with_distances as service_distances
from edmultitool.core.plot import PlotResult
from edmultitool.core.powerplay import UiPowerplay
from edmultitool.core.powerplay import with_distances as power_distances
from edmultitool.core.route import Route, RouteStop, min_jumps
from edmultitool.core.sell import UiSell
from edmultitool.core.sell import with_distances as sell_distances
from edmultitool.core.settings import ROUTE_BASIS_LABELS, RouteBasis, ScoutPrefs, Settings
from edmultitool.core.state import GameState, plan_state
from edmultitool.core.stats import UiStats
from edmultitool.core.trade import UiTrade
from edmultitool.core.updates import UiUpdate


@dataclass(frozen=True, slots=True)
class PanelText:
    scout: str
    ship: str
    range: str
    routing: str
    next: str = "—"  # the followed route's next point (UiFollow.text)
    here: str = "—"  # what the system you are in is worth (UiArrival.text)
    earned: str = "—"  # net credits this session / today, unsold data (UiEarnings.text)
    missions: str = "—"  # open missions, their reward, the next to end (UiMissions.text)
    note: str = ""  # what the panel must tell outside the page: the page could not start, moved port…
    update: str = ""  # "Version X is out…" when a newer version exists (UiUpdate.text)
    warning: str = ""  # a minor faction here holds you hostile (UiFactions.warning)


def routing_range(state: GameState, settings: Settings) -> float | None:
    """The jump range routes will be planned with: chosen basis minus the safety margin."""
    base = {
        "full": state.full_tank_range,
        "now": state.current_range,
        "best": state.best_case_range,
    }[settings.route_basis]()
    if base is None:
        return None
    return base * (1.0 - settings.margin_pct / 100.0)


def panel_text(state: GameState, settings: Settings, *, game_seen: bool) -> PanelText:
    if state.in_other_ship:
        scout = "position unknown (crew in another ship)"
    elif state.closed:
        scout = f"game closed · last in {state.system}" if state.system else "game closed"
    elif state.system is not None:
        scout = state.system
    else:
        scout = "waiting for the game…" if not game_seen else "position unknown"

    if state.ship is None:
        ship = "—"
    elif state.ship.fsd is None:
        ship = f"{state.ship_display} · no FSD data"
    else:
        fsd = state.ship.fsd
        tags = (" SCO" if fsd.overcharge else "") + (" eng." if fsd.engineered else "")
        ship = f"{state.ship_display} · FSD {fsd.size}{fsd.rating}{tags}"
    if state.ship is not None and (state.closed or not game_seen):
        ship += " · last flown"  # remembered from an earlier session, not what the game says now

    now, full, best = state.current_range(), state.full_tank_range(), state.best_case_range()
    if now is None or full is None or best is None:
        rng = "—"
    else:
        rng = f"now {now:.2f} · full {full:.2f} · max {best:.2f} ly"

    route = routing_range(state, settings)
    if route is None:
        routing = "—"
    else:
        margin = f", -{settings.margin_pct:g} %" if settings.margin_pct else ""
        routing = f"{route:.2f} ly ({ROUTE_BASIS_LABELS[settings.route_basis]}{margin})"
    return PanelText(scout=scout, ship=ship, range=rng, routing=routing)


@dataclass(frozen=True, slots=True)
class UiRanges:
    """Jump ranges in ly, rounded to 2 decimals like the panel shows them; None = unknown."""

    now: float | None
    full: float | None
    best: float | None
    routing: float | None


ScoutStatus = Literal["idle", "running", "done", "error"]


@dataclass(frozen=True, slots=True)
class UiSpecies:
    name: str  # e.g. "Frutexa Acus"
    value: int  # base sale value, Cr
    done: bool = False  # you analysed it on this body already: not counted


@dataclass(frozen=True, slots=True)
class UiBody:
    id64: str
    name: str
    short: str  # the name without its system's name, e.g. "A 5"
    subtype: str
    distance_ls: float
    scan_value: int
    mapping_value: int
    terraformable: bool
    scan_hit: bool  # counted by the scan search
    bio_value: int  # species not analysed yet, while they still reach the per-body bio total (else 0)
    species: tuple[UiSpecies, ...]
    wealth: int = 0  # 1-5 green "$" for what this one body is still worth (see `wealth`); 0 = nothing
    done: bool = False  # you mapped it already: its scan / mapping value is not counted


@dataclass(frozen=True, slots=True)
class UiStop:
    id64: str
    name: str
    leg_ly: float  # straight distance from the previous stop (or the start), 2 decimals
    leg_jumps: int  # fewest jumps for that leg at the route's range
    along_ly: float  # progress along the from→to line, 1 decimal
    off_line_ly: float  # distance from that line, 1 decimal
    scan_value: int  # counted scan bodies you have not mapped yet
    mapping_value: int
    bio_value: int  # species not analysed yet
    bodies: tuple[UiBody, ...]
    wealth: int = 0  # 1-5 green "$": its BEST body's (one rich planet beats many small ones)
    replaces: str = ""  # took the place of this stop, whose bodies were all yours already
    detour_ly: float = 0.0  # what that swap added to the trip, 1 decimal
    spent: bool = False  # nothing left to earn here: every body on it is yours already


# Wealth of a body in "$": ≤ 5M → 1, ≤ 10M → 2, ≤ 20M → 3, ≤ 50M → 4, more → 5.
WEALTH_STEPS = (5_000_000, 10_000_000, 20_000_000, 50_000_000)


def stop_worth(scan_value: int, mapping_value: int, bio_value: int) -> int:
    """What a stop is worth: its counted bodies mapped (or only scanned, when Spansh gives no mapping
    value) plus its counted species. Spansh's mapping value already includes the scan."""
    return max(scan_value, mapping_value) + bio_value


def body_worth(scan_hit: bool, scan_value: int, mapping_value: int, bio_value: int) -> int:
    """What one body is worth on this route: mapped (or scanned) when the scan search counted it, plus
    its counted species."""
    return (max(scan_value, mapping_value) if scan_hit else 0) + bio_value


def wealth(value: int) -> int:
    """1-5 "$" for a body's value; 0 for a body worth nothing."""
    return 1 + sum(value > step for step in WEALTH_STEPS) if value > 0 else 0


@dataclass(frozen=True, slots=True)
class UiWaypoint:
    """One row of a route-only plot."""

    id64: str
    name: str
    leg_ly: float  # from the previous waypoint, 2 decimals (0 for the start)
    jumps: int  # from the previous waypoint: 1 each (0 for the start)
    neutron: bool  # supercharge at this neutron star (the next jump is the boosted one)
    refuel: bool  # fill up here
    scoopable: bool | None  # the star can be scooped (K, G, B, F, O, A, M); None = not known (start, end)


RouteMode = Literal["tour", "exact", "neutron", "game"]
RangeBasis = Literal["routing", "full"]


@dataclass(frozen=True, slots=True)
class UiRoute:
    from_name: str
    to_name: str
    range_ly: float  # the jump range the route was planned with (see range_basis)
    total_ly: float  # straight from → to
    final_leg_ly: float  # tour: last stop → destination (plots: 0)
    final_leg_jumps: int
    from_cache: bool
    finished: str  # ISO time the route was built
    stops: tuple[UiStop, ...]
    mode: RouteMode = "tour"  # tour = scan / bio stops; exact / neutron = route only; game = from the game
    # which of the page's ranges `range_ly` is compared with for the re-plan notice: the routing range
    # (tour), or the full-tank range (plots: Spansh models the ship itself, the margin is not used)
    range_basis: RangeBasis = "routing"
    total_jumps: int = 0  # plots only
    waypoints: tuple[UiWaypoint, ...] = ()  # plots only, start first, destination last
    from_id64: str = ""  # tour: start and destination ids for following ("" = not known)
    to_id64: str = ""
    # tour: the form's per-body bio total — species not analysed yet count only while they still reach it
    # (the same rule as `core.route`; 0 for routes saved by older versions)
    min_bio_body: int = 0
    # tour: what all stops are still worth, Cr, set by `regrade`: worth_map = bodies mapped (or only
    # scanned where Spansh gives no mapping value; mapping includes the scan), worth_scan = the same bodies
    # only FSS-scanned, worth_bio = species. The trip's total is worth_map + worth_bio.
    worth_map: int = 0
    worth_bio: int = 0
    worth_scan: int = 0


@dataclass(frozen=True, slots=True)
class UiFollow:
    """Progress along the shown route (`core.follow`). `next_index` counts the start, then the stops or
    waypoints, then the destination (a plot's waypoints already hold both ends)."""

    active: bool = False  # a route is shown and followed
    next_index: int = 0
    count: int = 0  # number of points, start and destination included
    finished: bool = False  # arrived at the destination
    next_name: str = ""  # what "Copy" puts on the clipboard; "" when finished
    next_kind: str = ""  # "start" | "stop" | "waypoint" | "destination" | ""
    next_wealth: int = 0  # 1-5 "$" for a tour stop, else 0
    stops_left: int = 0  # tour: stops not yet reached
    ly_left: float = 0.0  # tour: straight legs still ahead (from the point before next), 1 decimal
    jumps_left: int = 0  # plots: jumps still ahead
    value_ahead: int = 0  # tour: what the stops not yet reached are worth, Cr
    text: str = "—"  # the EDMC panel's "Next:" row


@dataclass(frozen=True, slots=True)
class UiScout:
    status: ScoutStatus = "idle"
    message: str = ""  # error text, or a note such as "cancelled"
    route: UiRoute | None = None
    handled: int = 0  # scout / cancel commands carried out so far: the page knows its click was answered


def _short(body: str, system: str) -> str:
    if body.startswith(system + " "):
        return body[len(system) :].strip()
    return body


def _ui_stop(stop: RouteStop, range_ly: float) -> UiStop:
    """Values, "$" and `spent` are filled in by `regrade`."""
    return UiStop(
        id64=str(stop.id64),
        name=stop.name,
        leg_ly=round(stop.leg_ly, 2),
        leg_jumps=min_jumps(stop.leg_ly, range_ly),
        along_ly=round(stop.along_ly, 1),
        off_line_ly=round(stop.off_line_ly, 1),
        scan_value=0,
        mapping_value=0,
        bio_value=0,
        bodies=tuple(
            UiBody(
                id64=str(b.id64),
                name=b.name,
                short=_short(b.name, stop.name),
                subtype=b.subtype,
                distance_ls=round(b.distance_ls, 2),
                scan_value=b.scan_value,
                mapping_value=b.mapping_value,
                terraformable=b.terraformable,
                scan_hit=b.scan_hit,
                bio_value=0,
                species=tuple(UiSpecies(s.species, s.value, s.species in b.done_species) for s in b.species),
                done=b.mapped,
            )
            for b in stop.bodies
        ),
        replaces=stop.replaces,
        detour_ly=round(stop.detour_ly, 1),
    )


def _regrade_body(b: UiBody, explored: Explored | None, min_bio_body: int) -> UiBody:
    if explored is not None:  # what is done now, both ways: a death can make a body open again
        species = tuple(replace(s, done=explored.is_analysed(int(b.id64), s.name)) for s in b.species)
        b = replace(b, done=explored.is_mapped(int(b.id64)), species=species)
    left = sum(s.value for s in b.species if not s.done)
    bio = left if left > 0 and left >= min_bio_body else 0
    worth = body_worth(b.scan_hit and not b.done, b.scan_value, b.mapping_value, bio)
    return replace(b, bio_value=bio, wealth=wealth(worth))


def _regrade_stop(stop: UiStop, explored: Explored | None, min_bio_body: int) -> UiStop:
    bodies = tuple(_regrade_body(b, explored, min_bio_body) for b in stop.bodies)
    scan = sum(b.scan_value for b in bodies if b.scan_hit and not b.done)
    mapping = sum(b.mapping_value for b in bodies if b.scan_hit and not b.done)
    bio = sum(b.bio_value for b in bodies)
    return replace(
        stop, bodies=bodies, scan_value=scan, mapping_value=mapping, bio_value=bio,
        wealth=max((b.wealth for b in bodies), default=0), spent=stop_worth(scan, mapping, bio) == 0,
    )  # fmt: skip


def regrade(route: UiRoute, explored: Explored | None = None) -> UiRoute:
    """Values and "$" of a tour's stops from what is left to earn. With `explored`, the done marks are
    first set from it (a route built earlier, a body mapped or a death since); without, they are kept."""
    if route.mode != "tour":
        return route
    stops = tuple(_regrade_stop(s, explored, route.min_bio_body) for s in route.stops)
    return replace(
        route, stops=stops, worth_map=sum(max(s.scan_value, s.mapping_value) for s in stops),
        worth_bio=sum(s.bio_value for s in stops), worth_scan=sum(s.scan_value for s in stops),
    )  # fmt: skip


def ui_route(route: Route, finished: str) -> UiRoute:
    return regrade(
        UiRoute(
            from_name=route.from_name,
            to_name=route.to_name,
            range_ly=round(route.range_ly, 2),
            total_ly=round(route.total_ly, 2),
            final_leg_ly=round(route.final_leg_ly, 2),
            final_leg_jumps=min_jumps(route.final_leg_ly, route.range_ly),
            from_cache=route.from_cache,
            finished=finished,
            stops=tuple(_ui_stop(s, route.range_ly) for s in route.stops),
            from_id64=str(route.from_id64 or ""),
            to_id64=str(route.to_id64 or ""),
            min_bio_body=route.min_bio_body,
        )
    )


def ui_plot(plot: PlotResult, range_ly: float, finished: str) -> UiRoute:
    """A route-only plot. `range_ly` = the full-tank range at planning time (Spansh starts the route with
    full tanks)."""
    first, last = plot.waypoints[0], plot.waypoints[-1]
    straight = math.dist((first.x, first.y, first.z), (last.x, last.y, last.z))
    return UiRoute(
        from_name=first.name,
        to_name=last.name,
        range_ly=round(range_ly, 2),
        total_ly=round(straight, 2),
        final_leg_ly=0.0,
        final_leg_jumps=0,
        from_cache=plot.from_cache,
        finished=finished,
        stops=(),
        mode=plot.kind,
        range_basis="full",
        total_jumps=plot.total_jumps,
        waypoints=tuple(
            UiWaypoint(
                id64=str(w.id64),
                name=w.name,
                leg_ly=round(w.leg_ly, 2),
                jumps=w.jumps,
                neutron=w.neutron,
                refuel=w.refuel,
                scoopable=w.scoopable,
            )
            for w in plot.waypoints
        ),
        from_id64=str(first.id64),
        to_id64=str(last.id64),
    )


@dataclass(frozen=True, slots=True)
class UiShip:
    """One remembered ship for the page's ship picker."""

    id: str  # the game's ShipID, as a string
    label: str  # e.g. "Type-6 Transporter · XC-07Q"
    fsd: str  # e.g. "5A SCO", "" when unknown
    full: float | None  # ly, full tanks and an empty hold — the same footing for every ship
    current: bool  # the ship you sit in (or, on foot, last flew)
    seen: str  # ISO time of the build shown


@dataclass(frozen=True, slots=True)
class UiPlan:
    """The ship routes are planned with: the one you sit in unless another was picked on the page."""

    ship_id: str | None = None  # None = the ship you sit in
    ship: str | None = None  # label of the planning ship
    routing: float | None = None  # its routing range (basis + margin), 2 decimals
    full: float | None = None  # its full-tank range (current cargo for the ship you are in, else empty hold)
    text: str = "—"  # what the page's "Routes are planned with" card shows


@dataclass(frozen=True, slots=True)
class UiGameRoute:
    """The route plotted in the game's galaxy map, offered to be followed (`core.follow.game_offer`)."""

    offer: bool = False  # show "Follow it": a plotted route the followed one does not already cover
    to_name: str = ""
    jumps: int = 0
    plotted: str = ""  # ISO time it was plotted


@dataclass(frozen=True, slots=True)
class UiState:
    """The one shape the web page renders (`GET /api/state` → "state"). Grow it additively only;
    the matching JS reader is documented at the top of `web/app.js`."""

    game_seen: bool
    system: str | None
    address: str | None  # id64 as a decimal string (may exceed the 2^53 a JS number holds exactly)
    in_other_ship: bool
    ship: str | None  # display name, e.g. "Mandalay"
    ranges: UiRanges
    route_basis: RouteBasis
    margin_pct: float
    panel: PanelText  # the exact strings the EDMC panel shows
    scout_prefs: ScoutPrefs = field(default_factory=ScoutPrefs)  # the remembered scout form
    recent_to: tuple[str, ...] = ()  # destinations scouted, newest first
    scout: UiScout = field(default_factory=UiScout)  # the last scout run
    fleet: tuple[UiShip, ...] = ()  # remembered ships, most recently flown first
    plan: UiPlan = field(default_factory=UiPlan)
    follow: UiFollow = field(default_factory=UiFollow)  # progress along `scout.route`
    game_route: UiGameRoute = field(default_factory=lambda: UiGameRoute())  # plotted in the galaxy map
    gear: UiGear = field(default_factory=UiGear)  # the gear post from the forum decoded last
    market: UiMarket = field(default_factory=UiMarket)  # the commodity search shown last
    trade: UiTrade = field(default_factory=UiTrade)  # the best trade found last (Trade tab)
    engineers: tuple[UiEngineer, ...] = ()  # every engineer, nearest first
    services: UiServices = field(default_factory=UiServices)  # the station service search shown last
    ship_pad: str = ""  # landing pad the planning ship needs ("S" / "M" / "L"; "" = no ship known)
    powerplay: UiPowerplay = field(default_factory=UiPowerplay)  # the Powerplay system search shown last
    pledged: str = ""  # the Powerplay power you are pledged to ("" = none or not known yet)
    powers: tuple[str, ...] = POWERS  # the 12 powers, for the Powerplay card's choice
    notices: tuple[str, ...] = ()  # standing problems the page shows above everything (journal unreadable…)
    commander: UiCommander = field(default_factory=UiCommander)  # balance + warnings (top row, Route tab)
    career: UiCareer = field(default_factory=UiCareer)  # standings, navy ranks, Powerplay (Commander tab)
    factions: UiFactions = field(default_factory=UiFactions)  # minor factions you have a standing with
    arrival: UiArrival = field(default_factory=UiArrival)  # what the system you are in is worth (Route tab)
    earnings: UiEarnings = field(default_factory=UiEarnings)  # earned / spent, data unsold (Earnings tab)
    missions: UiMissions = field(default_factory=UiMissions)  # open missions (Missions tab)
    stats: UiStats = field(default_factory=UiStats)  # the game's lifetime statistics (Stats tab)
    sell: UiSell = field(default_factory=UiSell)  # where to sell the hold (Trade tab)
    flash: bool = True  # flash the page on arriving where one body is worth ≥ flash_over Cr
    flash_over: int = 30_000_000
    update: UiUpdate = field(default_factory=UiUpdate)  # a newer version is out (page strip, panel line)
    update_check: bool = True  # the setting: ask GitHub about once a day


def _r2(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def ship_label(ship: StoredShip | None, state: GameState) -> str | None:
    """Type, the player's own name if any, registration: "Type-6 Transporter · XC-07Q"."""
    if ship is None:
        return None
    lo = ship.loadout
    label = ship_display_name(lo.ship_type, state.ship_names)
    if lo.ship_name.strip():
        label += f" “{lo.ship_name.strip()}”"
    return f"{label} · {lo.ship_ident}" if lo.ship_ident else label


def _fsd_label(ship: StoredShip) -> str:
    fsd = ship.loadout.fsd
    if fsd is None:
        return ""
    return (
        f"{fsd.size}{fsd.rating}" + (" SCO" if fsd.overcharge else "") + (" eng." if fsd.engineered else "")
    )


def ui_fleet(fleet: Fleet, state: GameState) -> tuple[UiShip, ...]:
    current = state.ship.ship_id if state.ship is not None and not state.in_other_ship else None
    rows = []
    for ship in fleet.ordered():
        empty = GameState(ship=ship.loadout)  # full tanks, empty hold
        rows.append(
            UiShip(
                id=str(ship.loadout.ship_id),
                label=ship_label(ship, state) or "",
                fsd=_fsd_label(ship),
                full=_r2(empty.full_tank_range()),
                current=ship.loadout.ship_id == current,
                seen=ship.seen,
            )
        )
    return tuple(rows)


def ui_plan(state: GameState, settings: Settings, fleet: Fleet, pick: int | None) -> UiPlan:
    planning = plan_state(state, fleet, pick)
    picked = planning is not state
    route = routing_range(planning, settings)
    if route is None:
        text = "—"
    else:
        margin = f", -{settings.margin_pct:g} %" if settings.margin_pct else ""
        text = f"{route:.2f} ly ({ROUTE_BASIS_LABELS[settings.route_basis]}{margin})"
    label = ship_label(fleet.get(pick), state) if picked else None
    if picked and label:
        text += f" · {label}"
    return UiPlan(
        ship_id=str(pick) if picked else None,
        ship=label,
        routing=_r2(route),
        full=_r2(planning.full_tank_range()),
        text=text,
    )


def ui_state(
    state: GameState,
    settings: Settings,
    *,
    game_seen: bool,
    scout: UiScout | None = None,
    fleet: Fleet | None = None,
    pick: int | None = None,
    follow: UiFollow | None = None,
    game_route: UiGameRoute | None = None,
    gear: UiGear | None = None,
    market: UiMarket | None = None,
    trade: UiTrade | None = None,
    services: UiServices | None = None,
    powerplay: UiPowerplay | None = None,
    pledged: str = "",
    commander: UiCommander | None = None,
    career: UiCareer | None = None,
    factions: UiFactions | None = None,
    arrival: UiArrival | None = None,
    earnings: UiEarnings | None = None,
    missions: UiMissions | None = None,
    stats: UiStats | None = None,
    sell: UiSell | None = None,
    update: UiUpdate | None = None,
    notices: tuple[str, ...] = (),
    panel_note: str = "",
) -> UiState:
    fleet = fleet or Fleet()
    here = None if state.in_other_ship else state.star_pos
    routing = routing_range(plan_state(state, fleet, pick), settings)
    follow = follow or UiFollow()
    arrival = arrival or UiArrival()
    earnings = earnings or UiEarnings()
    missions = missions or UiMissions()
    update = update or UiUpdate()
    factions = factions or UiFactions()
    panel = replace(
        panel_text(state, settings, game_seen=game_seen),
        next=follow.text,
        here=arrival.text,
        earned=earnings.text,
        missions=missions.text,
        note=panel_note,
        update=update.text,
        warning=factions.warning,
    )
    return UiState(
        game_seen=game_seen,
        system=state.system,
        address=None if state.address is None else str(state.address),
        in_other_ship=state.in_other_ship,
        ship=state.ship_display,
        ranges=UiRanges(
            now=_r2(state.current_range()),
            full=_r2(state.full_tank_range()),
            best=_r2(state.best_case_range()),
            routing=_r2(routing_range(state, settings)),
        ),
        route_basis=settings.route_basis,
        margin_pct=settings.margin_pct,
        panel=panel,
        scout_prefs=settings.scout,
        recent_to=settings.recent_to,
        scout=scout or UiScout(),
        fleet=ui_fleet(fleet, state),
        plan=ui_plan(state, settings, fleet, pick),
        follow=follow,
        game_route=game_route or UiGameRoute(),
        gear=with_distances(gear or UiGear(), None if state.in_other_ship else state.star_pos),
        market=market_distances(market or UiMarket(), here, routing),
        trade=trade or UiTrade(),
        engineers=ui_engineers(here, routing),
        services=service_distances(services or UiServices(), here, routing),
        ship_pad=_ship_pad(plan_state(state, fleet, pick)),
        powerplay=power_distances(powerplay or UiPowerplay(), here, routing, pledged),
        pledged=pledged,
        notices=notices,
        commander=commander or UiCommander(),
        career=career or UiCareer(),
        factions=factions,
        arrival=arrival,
        earnings=earnings,
        missions=missions,
        stats=stats or UiStats(),
        sell=sell_distances(sell or UiSell(), here, routing),
        flash=settings.flash,
        flash_over=settings.flash_over,
        update=update,
        update_check=settings.update_check,
    )


def _ship_pad(planning: GameState) -> str:
    ship = None if planning.in_other_ship else planning.ship
    return ship_pad(ship.ship_type) if ship is not None else ""


def route_from_doc(doc: object) -> UiRoute:
    """A UiRoute back from its `asdict` form (the saved route). Raises ValueError / TypeError / KeyError
    on anything that is not one."""
    if not isinstance(doc, dict):
        raise ValueError("route is not an object")
    stops = []
    for s in doc["stops"]:
        bodies = tuple(
            UiBody(**{**b, "species": tuple(UiSpecies(**sp) for sp in b["species"])}) for b in s["bodies"]
        )
        stops.append(UiStop(**{**s, "bodies": bodies}))
    waypoints = tuple(UiWaypoint(**w) for w in doc["waypoints"])
    route = UiRoute(**{**doc, "stops": tuple(stops), "waypoints": waypoints})
    followable = ("tour", "exact", "neutron", "game")
    if route.mode not in followable or (route.mode != "tour" and len(waypoints) < 2):
        raise ValueError(f"not a followable route: {route.mode}")
    # Values and "$" are graded again on every load: a route saved by an older version (or with older
    # steps) shows today's, never stale ones. The plugin then sets the done marks from the store.
    return regrade(route)
