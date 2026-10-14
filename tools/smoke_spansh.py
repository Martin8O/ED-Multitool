"""Live smoke test against Spansh — MANUAL ONLY, never part of the gate. Polite: ~10 calls, ≤ 1/s.

Checks: system-name autocomplete, one riches (FSS/DSS) tour, one exobiology tour, the error replies we
rely on, and cancelling a tour mid-poll through the real worker thread. No disk cache, so every call
is live.

Usage (from repo root, after `. tools/env.ps1`):
    .venv/Scripts/python tools/smoke_spansh.py [--from Sol] [--to Colonia] [--range 22] [--record]
    .venv/Scripts/python tools/smoke_spansh.py --plots [--record]
--record rewrites the trimmed replies in tests/data/spansh/ (the gate's recorded replies).
--plots checks the route-only galaxy plotter instead (Sol → Maia with a fixed ship build, normal jumps and
with neutron boosts) and, with --record, rewrites only its files.
--gear checks the gear-post lookups instead (system names, the system search with its stations, the station
search) on two real forum posts and, with --record, rewrites only the gear_*.json files.
--market checks the commodity search instead: Tritium near Sol at Spansh (carriers in and out, two minimum
amounts) and at Ardent, and with --record rewrites market_*.json (tests/data/spansh/, tests/data/ardent/).
--stations checks the station search instead (near Sol: pad, settlements and carriers each at two values, two
services that must both be there, a material trader of one kind, a broker of one kind) and, with --record,
rewrites tests/data/spansh/stations_*.json.
--arrival checks the body search of one system instead (the "This system" card: a rich system, one with
sampled species, one with life left unsampled, one Spansh does not know), checks the value estimate against
Spansh's own on every body, and with --record rewrites tests/data/spansh/bodies_*.json.
--trade checks the trade router instead (one hop from Abraham Lincoln in Sol: a trade, one with credits too
few to fill the hold, none within 1 ly, a station that does not exist; and the stations with a market in Sol)
and, with --record, rewrites tests/data/spansh/trade_*.json.
--sell checks the sell search instead (Painite, Platinum and Tritium carried near Sol: best price first,
within 20 and 50 ly, demand at least the tonnes carried, large pads) and, with --record, rewrites
tests/data/spansh/sell_*.json.
--powerplay checks the Powerplay system search instead (Li Yong-Rui near Sol: held, others hold, can acquire;
held also in one power state) and, with --record, rewrites tests/data/spansh/power_*.json.
Prints a short summary; redirect big output to a file.
"""

import argparse
import json
import logging
import math
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "EDMultitool"))

from edmultitool import USER_AGENT
from edmultitool.core.arrival import planet_values
from edmultitool.core.gear import locate, parse_post
from edmultitool.core.market import MarketQuery, MarketRequest
from edmultitool.core.nearby import TRADER_KINDS, ServiceQuery, ServiceRequest
from edmultitool.core.plot import PlotQuery, PlotResult, ShipBuild
from edmultitool.core.powerplay import PowerMode, PowerQuery, PowerRequest
from edmultitool.core.scout import ScoutQuery, ScoutResult
from edmultitool.core.sell import Good, SellQuery, SellRequest
from edmultitool.core.trade import TradeQuery, TradeRequest
from edmultitool.gear_lookup import lookup
from edmultitool.sources import ardent, spansh
from edmultitool.sources.http import HttpError, PoliteClient, Request, Response, Transport, requests_transport
from edmultitool.worker import Worker

RECORD_DIR = Path(__file__).resolve().parents[1] / "tests" / "data" / "spansh"
ARDENT_DIR = RECORD_DIR.parent / "ardent"
MAX_ARDENT_ROWS = 30
MAX_BODIES = 3  # per system, in recorded result files
MAX_NAMES = 5

# A real Mandalay (SCO 5A FSD, unladen 572.8 t, 32 + 0.75 t tanks): a fixed ship build, so
# the plot smoke never depends on the journals of this PC.
SMOKE_SHIP = ShipBuild(
    optimal_mass=1175,
    max_fuel_per_jump=5.2,
    fuel_multiplier=0.013,
    fuel_power=2.45,
    base_mass=573.549988,
    tank_size=32,
    reservoir_size=0.75,
    range_boost=0,
    supercharge_multiplier=4,
)


@dataclass
class Exchange:
    request: Request
    response: Response
    at: float  # when the request was sent


class Recorder:
    """Wraps the real transport and keeps every exchange."""

    def __init__(self, inner: Transport) -> None:
        self.inner = inner
        self.log: list[Exchange] = []
        self.on_reply: list[Any] = []

    def __call__(self, request: Request, timeout: float) -> Response:
        sent = time.monotonic()
        resp = self.inner(request, timeout)
        self.log.append(Exchange(request, resp, sent))
        for hook in self.on_reply:
            hook(request, resp)
        return resp


def summary(result: ScoutResult) -> str:
    lines = [f"{result.kind}: {len(result.systems)} rows"]
    for s in result.systems:
        best = max(s.bodies, key=lambda b: b.scan_value + b.mapping_value + b.bio_value, default=None)
        extra = ""
        if best is not None:
            extra = (
                f" | best {best.name}: scan {best.scan_value:,} map {best.mapping_value:,} "
                f"bio {best.bio_value:,} ({len(best.species)} sp) @ {best.distance_ls:,.0f} ls"
            )
        lines.append(f"  {s.id64} {s.name} jumps={s.jumps} bodies={len(s.bodies)}{extra}")
    return "\n".join(lines)


def trimmed(doc: Any) -> Any:
    if isinstance(doc, dict) and isinstance(doc.get("result"), list):
        rows = doc["result"]  # tour systems keep MAX_BODIES bodies; trade hops have none
        doc = {
            **doc,
            "result": [{**s, "bodies": s["bodies"][:MAX_BODIES]} if "bodies" in s else s for s in rows],
        }
    if isinstance(doc, dict) and isinstance(doc.get("results"), list):  # a system / station search
        rows = doc["results"]
        filters = doc.get("search", {}).get("filters", {})
        market = filters.get("market")
        if "system_id64" in filters:  # the body search of one system
            trimmed_rows = [recorded_body(r) for r in rows]
        elif "controlling_power" in filters or "power" in filters:  # a Powerplay system search
            trimmed_rows = [spansh.trim_power_row(r) for r in rows]
        elif market:  # a market search: of each market, only the commodity asked
            trimmed_rows = [spansh.trim_market_row(r, market[0]["name"]) for r in rows]
        elif "type" in filters and "name" not in filters:  # the station search
            trimmed_rows = [spansh.trim_station_row(r) for r in rows]
        else:
            trimmed_rows = [spansh.trim_row(r, "stations" if "system_name" in r else "systems") for r in rows]
        doc = {**{k: v for k, v in doc.items() if k != "search"}, "results": trimmed_rows}
    if isinstance(doc, list):  # Ardent's nearby market rows
        doc = doc[:MAX_ARDENT_ROWS]
    if isinstance(doc, dict) and isinstance(doc.get("min_max"), list):
        doc = {**doc, "min_max": doc["min_max"][:MAX_NAMES]}
    return doc


def plot_summary(result: PlotResult) -> str:
    w = result.waypoints
    longest = max(x.leg_ly for x in w)
    return (
        f"{result.kind}: {len(w)} waypoints, {result.total_jumps} jumps, "
        f"{sum(x.neutron for x in w)} neutron, {sum(x.refuel for x in w)} refuel, "
        f"longest leg {longest:.2f} ly, {w[0].name} → {w[-1].name}"
    )


def smoke_plots(client: PoliteClient, rec: "Recorder", args: argparse.Namespace) -> list[str]:
    """The galaxy plotter twice on the same trip: normal jumps, then with neutron boosts."""
    cancel = threading.Event()
    failures: list[str] = []
    jobs: dict[str, list[Exchange]] = {}
    maia = spansh.resolve_system(client, "Maia", cancel)
    sol = spansh.resolve_system(client, "sol", cancel)  # letter case does not matter
    normal_max = 23.5  # this ship's longest normal jump is ~22.5 ly

    for neutrons in (False, True):
        start = len(rec.log)
        plot = spansh.plot(client, PlotQuery(sol.id64, maia.id64, SMOKE_SHIP, 8, neutrons), cancel)
        jobs[plot.kind] = rec.log[start:]
        print(plot_summary(plot))
        w = plot.waypoints
        if w[0].id64 != sol.id64 or w[-1].id64 != maia.id64 or any(x.jumps != 1 for x in w[1:]):
            failures.append(f"{plot.kind}: not a jump-by-jump route Sol → Maia")
        if not all(x.scoopable for x in w if x.refuel):
            failures.append(f"{plot.kind}: refuel at a star that cannot be scooped")
        boosted = [i for i in range(1, len(w)) if w[i].leg_ly > normal_max]
        charged = [i + 1 for i in range(len(w)) if w[i].neutron]
        if not neutrons and (boosted or charged):
            failures.append("exact: a neutron boost on a normal route")
        if neutrons and (not charged or boosted != charged):
            failures.append("neutron: neutron marks do not sit right before the boosted jumps")

    if args.record:
        for kind, exchanges in jobs.items():
            record(f"{kind}_submit", exchanges[0])
            record(f"{kind}_done", exchanges[-1])
        print(f"recorded into {RECORD_DIR}")
    return failures


GEAR_POSTS = {
    # the system named, the station only matched in its list (tests/data/gear_posts.txt p2264-5)
    "keian": "Hahn Terminal - Keian Gu\nG3 Maverick | Added Melee Damage | Reduced Tool Battery Consumption",
    # a station only: the station search finds its system
    "walters": "Walters Port\nG3 maverick suit with extra backpack capacity",
}


def smoke_gear(client: PoliteClient, rec: "Recorder", args: argparse.Namespace) -> list[str]:
    """Decode two forum posts against live Spansh: one names its system, one only its station."""
    cancel = threading.Event()
    failures: list[str] = []
    calls: dict[str, list[Exchange]] = {}
    want = {"keian": ("Keian Gu", "Hahn Terminal"), "walters": ("Condovichs", "Walters Port")}
    for key, text in GEAR_POSTS.items():
        start = len(rec.log)
        post = parse_post(text)
        places = locate(post, lookup(client, post, cancel))
        calls[key] = rec.log[start:]
        got = [(p.system.name if p.system else None, p.station.name if p.station else None) for p in places]
        print(f"{key}: {got} in {len(calls[key])} calls")
        if got != [want[key]]:
            failures.append(f"{key}: expected {want[key]}, got {got}")
    if args.record:
        record("gear_names_keian", calls["keian"][0])
        record("gear_systems_keian", calls["keian"][-1])
        record("gear_stations_walters", calls["walters"][-1])
        print(f"recorded into {RECORD_DIR}")
    return failures


def smoke_market(client: PoliteClient, rec: "Recorder", args: argparse.Namespace) -> list[str]:
    """Tritium near Sol: each filter is checked at two values (probe a filter at two values)."""
    cancel = threading.Event()
    failures: list[str] = []
    now = datetime.now(UTC)
    sol = (0.0, 0.0, 0.0)

    def page(req: MarketRequest) -> tuple[Any, ...]:
        rows, total = spansh.market_page(client, MarketQuery(req, "Sol", 10477373803, sol, now), 0, cancel)
        print(f"{req.side} min {req.min_qty} carriers={req.carriers}: {len(rows)} of {total}")
        return rows

    with_carriers = page(MarketRequest("Tritium", "sell", 10, "L", True, 3.0))
    spansh_ex = rec.log[-1]
    without = page(MarketRequest("Tritium", "sell", 10, "L", False, 3.0))
    big = page(MarketRequest("Tritium", "sell", 5000, "M", False, 1.0))
    if not any(r.carrier for r in with_carriers) or any(r.carrier for r in without):
        failures.append("carriers filter: carriers expected only when asked")
    if any(r.quantity < 5000 for r in big) or any(r.pad != "L" for r in with_carriers):
        failures.append("demand / large pad filter not applied by Spansh")
    oldest = (now - timedelta(days=1)).isoformat()
    if any(r.updated < oldest[:19] for r in big):
        failures.append("data age filter not applied by Spansh")
    q = MarketQuery(MarketRequest("Tritium", "sell", 10, "L", False, 3.0), "Sol", 10477373803, sol, now)
    rows = ardent.nearby(client, q, cancel)
    ardent_ex = rec.log[-1]
    print(f"ardent: {len(rows)} rows, nearest {rows[0].system if rows else None}")
    if not rows or any(r.carrier for r in rows) or any(r.system == "Sol" for r in rows):
        failures.append("ardent: no rows, a carrier, or the start system")
    if args.record:
        record("market_sol_tritium", spansh_ex)
        record("market_sol_tritium", ardent_ex, ARDENT_DIR)
        print(f"recorded into {RECORD_DIR} and {ARDENT_DIR}")
    return failures


def smoke_trade(client: PoliteClient, rec: "Recorder", args: argparse.Namespace) -> list[str]:
    """One hop from Abraham Lincoln (Sol): the arithmetic of the reply, each limit at two values."""
    cancel = threading.Event()
    failures: list[str] = []
    now = datetime.now(UTC)
    base = TradeRequest("Sol", "Abraham Lincoln", pad="L", max_hop_ly=30.0, max_age_days=3.0)

    def run(q: TradeQuery) -> Any:
        found = spansh.trade(client, q, cancel)
        if found.source is None or found.destination is None:
            print(f"cargo {q.cargo} capital {q.capital} hop {q.request.max_hop_ly}: no trade")
            return found
        goods = ", ".join(f"{g.name} {g.amount} t x {g.profit}" for g in found.goods)
        print(
            f"cargo {q.cargo} capital {q.capital:,}: {found.source.station} -> {found.destination.system} / "
            f"{found.destination.station} {found.distance_ly:.1f} ly: {goods} = {found.total_profit:,}"
        )
        for g in found.goods:
            if g.profit != g.sell_price - g.buy_price or g.total_profit != g.profit * g.amount:
                failures.append(f"{g.name}: profit is not sell - buy, or total not profit x amount")
        load = sum(g.amount for g in found.goods)
        if load > q.cargo or sum(g.amount * g.buy_price for g in found.goods) > q.capital:
            failures.append("the load is more than the hold or costs more than the credits")
        if found.total_profit != sum(g.total_profit for g in found.goods):
            failures.append("the hop's total is not the sum of its commodities")
        if found.distance_ly > q.request.max_hop_ly:
            failures.append("the station sold at is farther than asked")
        oldest = (now - timedelta(days=q.request.max_age_days)).isoformat()
        if any(st.updated < oldest[:19] for st in (found.source, found.destination)):
            failures.append("market data older than asked")
        return found

    start = len(rec.log)
    full = run(TradeQuery(base, 400, 50_000_000))
    jobs = rec.log[start:]
    start = len(rec.log)
    poor = run(TradeQuery(base, 400, 20_000))
    poor_jobs = rec.log[start:]
    if full.source is None or poor.source is None:
        failures.append("no trade from Abraham Lincoln")
    elif sum(g.amount for g in poor.goods) >= sum(g.amount for g in full.goods):
        failures.append("credits too few did not shrink the load")
    start = len(rec.log)
    none = run(TradeQuery(TradeRequest("Sol", "Abraham Lincoln", max_hop_ly=1.0), 400, 50_000_000))
    none_jobs = rec.log[start:]
    if none.source is not None:
        failures.append("a trade within 1 ly of Sol")
    try:
        run(TradeQuery(TradeRequest("Sol", "Nonexistent Port"), 400, 50_000_000))
        failures.append("an unknown station was not refused")
    except HttpError as exc:
        print(f"unknown station: HTTP {exc.status} {exc.said!r}")
        if exc.status != 400 or "station" not in exc.said:
            failures.append("an unknown station: not HTTP 400 naming the station")
    unknown_ex = rec.log[-1]
    stations = spansh.market_stations(client, "Sol", cancel) or ()
    stations_ex = rec.log[-1]
    names = [st.name for st in stations]
    print(f"Sol: {len(names)} stations with a market, nearest first: {names[:5]}")
    if "Abraham Lincoln" not in names or not all(st.market for st in stations):
        failures.append("Sol's stations: Abraham Lincoln missing, or one without a market listed")
    if spansh.market_stations(client, "Nowhere Such System", cancel) is not None:
        failures.append("stations listed for a system that does not exist")
    if args.record:
        record("trade_stations_sol", stations_ex)
        record("trade_submit", jobs[0])
        record("trade_done", jobs[-1])
        record("trade_poor", poor_jobs[-1])
        record("trade_none", none_jobs[-1])
        record("error_trade_station", unknown_ex)
        print(f"recorded into {RECORD_DIR}")
    return failures


def smoke_stations(client: PoliteClient, rec: "Recorder", args: argparse.Namespace) -> list[str]:
    """Stations near Sol: large pad without settlements, medium pad with them (each filter at two values),
    then fleet carriers asked for, two services that must both be there, a raw material trader and a
    Guardian technology broker."""
    cancel = threading.Event()
    failures: list[str] = []
    sol = (0.0, 0.0, 0.0)

    def page(req: ServiceRequest, name: str) -> tuple[Any, ...]:
        rows, total = spansh.stations_page(client, ServiceQuery(req, "Sol", 10477373803, sol), 0, cancel)
        kinds = sorted({r.station_type for r in rows})
        asked = f"pad {req.pad} settlements={req.settlements} carriers={req.carriers}"
        print(f"{asked}: {len(rows)} of {total}, {kinds}")
        if args.record and name:
            record(name, rec.log[-1])
        return rows

    large = page(ServiceRequest(pad="L", settlements=False), "stations_sol_large")
    medium = page(ServiceRequest(pad="M", settlements=True), "stations_sol_medium")
    carriers = page(ServiceRequest(pad="S", carriers=True, settlements=False), "")
    if not large or any(r.pad != "L" for r in large):
        failures.append("large pad filter not applied by Spansh")
    if any("Settlement" in r.station_type for r in large) or not any(
        "Settlement" in r.station_type for r in medium
    ):
        failures.append("settlements filter: settlements expected only when asked")
    if any(r.carrier for r in large + medium):
        failures.append("carriers came back without being asked")
    print(f"carriers asked: {sum(1 for r in carriers if r.carrier)} carriers in the first page")
    wanted = ("Black Market", "Interstellar Factors Contact")
    page(ServiceRequest(pad="L", settlements=False, services=wanted), "stations_sol_services")
    raw = json.loads(rec.log[-1].response.body).get("results") or []
    lacking = [
        r.get("name") for r in raw if not set(wanted) <= {s.get("name") for s in r.get("services") or []}
    ]
    print(f"services {wanted}: {len(raw) - len(lacking)} of {len(raw)} have every one")
    if not raw or lacking:
        failures.append(f"services filter: not every station has {wanted} ({lacking[:3]})")
    for req, name, economies in (
        (ServiceRequest(pad="M", services=("Material Trader",), trader_kind="Raw"), "stations_sol_trader_raw",
         TRADER_KINDS["Raw"]),
        (ServiceRequest(pad="M", services=("Technology Broker",), broker_kind="Guardian"), "",
         ("High Tech",)),
    ):  # fmt: skip
        rows = page(req, name)
        raw = json.loads(rec.log[-1].response.body).get("results") or []
        off = [r.get("name") for r in raw if r.get("primary_economy") not in economies]
        lacking = [
            r.get("name")
            for r in raw
            if not set(req.services) <= {s.get("name") for s in r.get("services") or []}
        ]
        print(
            f"{req.services} {req.trader_kind or req.broker_kind}: {len(raw)} rows, "
            f"nearest {rows[0].system if rows else None}"
        )
        if not raw or off or lacking:
            failures.append(
                f"{req.services} kind filter: wrong economy {off[:3]} or service missing {lacking[:3]}"
            )
    if args.record:
        print(f"recorded into {RECORD_DIR}")
    return failures


def smoke_powerplay(client: PoliteClient, rec: "Recorder", args: argparse.Namespace) -> list[str]:
    """Li Yong-Rui near Sol: every way of asking, held also in one state (a filter at two values)."""
    cancel = threading.Event()
    failures: list[str] = []
    power = "Li Yong-Rui"
    modes: tuple[tuple[PowerMode, str], ...] = (
        ("held", ""), ("held", "Fortified"), ("others", ""), ("acquire", ""),
    )  # fmt: skip
    for mode, state in modes:
        found = spansh.power_systems(
            client, PowerQuery(PowerRequest(power, mode, state), "Sol", (0, 0, 0)), cancel
        )
        rows = found.rows
        dists = [math.dist((0, 0, 0), (r.x, r.y, r.z)) for r in rows]
        print(f"{mode} {state or 'any'}: {len(rows)} of {found.count}")
        for r, d in zip(rows, dists, strict=True):
            print(
                f"  {r.system} {d:.2f} ly held={r.held_by or '-'} {r.state} {r.progress} "
                f"+{r.reinforcement} -{r.undermining} {len(r.powers)} powers {r.updated}"
            )
        if len(rows) != 10 or dists != sorted(dists):
            failures.append(f"{mode} {state}: not 10 rows nearest first")
        if mode == "held" and any(
            r.held_by != power or r.state not in ("Exploited", "Fortified", "Stronghold") for r in rows
        ):
            failures.append(f"held {state}: a row not held by {power}")
        if mode != "held" and any(power not in r.powers for r in rows):
            failures.append(f"{mode} {state}: a row without {power} present")
        if mode == "others" and any(not r.held_by or r.held_by == power for r in rows):
            failures.append(f"others {state}: a row not held by another power")
        if mode == "acquire" and any(r.state != "Unoccupied" or r.held_by for r in rows):
            failures.append("acquire: a row that is not Unoccupied")
        if state and any(r.state != state for r in rows):
            failures.append(f"{mode} {state}: the state filter was not applied")
        if args.record and not state:
            record(f"power_sol_lyr_{mode}", rec.log[-1])
    if args.record:
        print(f"recorded into {RECORD_DIR}")
    return failures


_BODY_RECORDED = (
    "body_id", "name", "type", "subtype", "distance_to_arrival", "estimated_scan_value",
    "estimated_mapping_value", "terraforming_state", "is_landable", "earth_masses", "solar_masses",
    "signals", "genuses",
)  # fmt: skip


def recorded_body(row: Any) -> Any:
    """A body row as recorded: the fields we read plus the masses (the value estimate is checked against
    them); landmarks keep their shape but lose their coordinates."""
    if not isinstance(row, dict):
        return row
    out = {k: row[k] for k in _BODY_RECORDED if k in row}
    if isinstance(row.get("landmarks"), list):
        out["landmarks"] = [
            {k: v for k, v in m.items() if k not in ("latitude", "longitude")}
            for m in row["landmarks"]
            if isinstance(m, dict)
        ]
    return out


# (record name, system id64, what must hold)
ARRIVAL_SYSTEMS = (
    ("bodies_boelts", 180665307059, "rich"),  # Boelts KT-Q d5-5: Earth-likes and terraformables
    ("bodies_qawakana", 4786481006963, "species"),  # Qa'wakana: sampled species with their values
    ("bodies_plaa_eurk", 642232231025, "life"),  # Plaa Eurk QM-C b13-0: species, and life left unsampled
    ("bodies_unknown", 1234567, "unknown"),  # no such system
)


def smoke_arrival(client: PoliteClient, rec: "Recorder", args: argparse.Namespace) -> list[str]:
    cancel = threading.Event()
    failures: list[str] = []
    for name, id64, expect in ARRIVAL_SYSTEMS:
        found = spansh.system_bodies(client, id64, cancel)
        reply = json.loads(rec.log[-1].response.body)
        rows = {r["body_id"]: r for r in reply.get("results", [])}
        wrong = 0
        for b in found:
            r = rows[b.body_id]
            if b.star:
                continue  # star types come as Spansh's words; the planets carry the estimate's weight
            scan, mapping = planet_values(b.subtype, r.get("earth_masses") or 0.0, b.terraformable)
            wrong += (scan, mapping) != (b.scan_value, b.mapping_value)
        species = sum(len(b.species) for b in found)
        unsampled = sum(1 for b in found if b.bio_signals > len(b.species))
        elw = sum(1 for b in found if b.subtype == "Earth-like world")
        print(
            f"{name}: {len(found)} bodies (count {reply.get('count')}), {elw} Earth-like, {species} species, "
            f"{unsampled} with life unsampled, estimate off on {wrong}"
        )
        if wrong:
            failures.append(f"{name}: the value estimate differs from Spansh's on {wrong} planets")
        if expect == "rich" and elw < 2:
            failures.append(f"{name}: expected 2 Earth-like worlds")
        if expect in ("species", "life") and not species:
            failures.append(f"{name}: no species with a value")
        if expect == "life" and not unsampled:
            failures.append(f"{name}: no body with life left unsampled")
        if expect == "unknown" and found:
            failures.append(f"{name}: Spansh knows bodies in a system that does not exist")
        if args.record:
            record(name, rec.log[-1])
    if args.record:
        print(f"recorded into {RECORD_DIR}")
    return failures


def smoke_sell(client: PoliteClient, rec: "Recorder", args: argparse.Namespace) -> list[str]:
    """A mined hold near Sol: each filter checked at two values, the order checked on every page."""
    cancel = threading.Event()
    failures: list[str] = []
    now = datetime.now(UTC)
    sol = (0.0, 0.0, 0.0)
    goods = (Good("Painite", 30), Good("Platinum", 12), Good("Tritium", 4))

    def page(req: SellRequest, good: Good) -> tuple[Any, ...]:
        q = SellQuery(req, goods, "Sol", sol, now)
        rows, total = spansh.sell_page(client, q, good, 0, cancel)
        prices = [o.price for r in rows if (o := r.offer(good.name)) is not None]
        far = max((math.dist(sol, (r.x, r.y, r.z)) for r in rows), default=0.0)
        print(f"{good.name} within {req.max_ly:g} ly pad {req.pad}: {len(rows)} of {total}")
        print(f"  best {prices[:3]}")
        print(f"  farthest {far:.1f} ly")
        if prices != sorted(prices, reverse=True) or len(prices) != len(rows):
            failures.append(f"{good.name}: not best price first, or a row without its price")
        if far > req.max_ly + 0.01:
            failures.append(f"{good.name}: a station beyond {req.max_ly:g} ly")
        if any((o := r.offer(good.name)) is None or o.demand < good.tonnes for r in rows):
            failures.append(f"{good.name}: a station buying less than {good.tonnes} t")
        if req.pad == "L" and any(r.pad != "L" for r in rows):
            failures.append(f"{good.name}: a station without a large pad")
        return rows

    wide = page(SellRequest(50.0, "M"), goods[0])
    painite_ex = rec.log[-1]
    near = page(SellRequest(20.0, "M"), goods[0])
    page(SellRequest(50.0, "L"), goods[1])
    page(SellRequest(50.0, "M"), goods[2])
    best_near = near[0].offer("Painite") if near else None
    best_wide = wide[0].offer("Painite") if wide else None
    if best_near and best_wide and best_near.price > best_wide.price:
        failures.append("Painite: a nearer search found a better price than the wider one")
    others = sum(1 for r in wide if r.offer("Platinum") or r.offer("Tritium"))
    print(f"Painite stations that also buy Platinum or Tritium: {others} of {len(wide)}")
    if not others:
        failures.append("the other commodities of the hold are not in the station rows")
    if args.record:
        body = json.loads(painite_ex.response.body)
        names = sorted(g.name for g in goods)
        rows = [spansh.trim_sell_row(r, names) for r in body["results"]]
        trimmed_body = {"count": body.get("count"), "results": rows}
        record("sell_sol_painite", painite_ex, body=trimmed_body)
        print(f"recorded into {RECORD_DIR}")
    return failures


def record(name: str, ex: Exchange, folder: Path = RECORD_DIR, body: Any = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    doc = {
        "request": {
            "method": ex.request.method,
            "url": ex.request.url,
            "params": list(ex.request.params),
            "form": list(ex.request.form) if ex.request.form is not None else None,
            **({"json": json.loads(ex.request.json_body)} if ex.request.json_body is not None else {}),
        },
        "status": ex.response.status,
        "body": body if body is not None else trimmed(json.loads(ex.response.body)),
    }
    (folder / f"{name}.json").write_text(
        json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", default="Sol")
    ap.add_argument("--to", dest="dst", default="Colonia")
    ap.add_argument("--range", type=float, default=22.0)
    ap.add_argument("--record", action="store_true")
    ap.add_argument(
        "--plots",
        action="store_true",
        help="check the route-only galaxy plotter instead (normal and with neutrons)",
    )
    ap.add_argument("--gear", action="store_true", help="check the gear-post lookups instead")
    ap.add_argument("--market", action="store_true", help="check the commodity search instead")
    ap.add_argument("--powerplay", action="store_true", help="check the Powerplay system search instead")
    ap.add_argument("--trade", action="store_true", help="check the trade router instead")
    ap.add_argument("--sell", action="store_true", help="check the sell search instead")
    ap.add_argument("--arrival", action="store_true", help="check the body search of one system instead")
    ap.add_argument("--stations", action="store_true", help="check the station search instead")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log = logging.getLogger("smoke")

    rec = Recorder(requests_transport())
    client = PoliteClient(rec, user_agent=USER_AGENT, cache_dir=None, log=log)
    if args.arrival:
        arrival_failures = smoke_arrival(client, rec, args)
        print("SMOKE PASS" if not arrival_failures else "SMOKE FAIL: " + "; ".join(arrival_failures))
        return 0 if not arrival_failures else 1
    if args.stations:
        station_failures = smoke_stations(client, rec, args)
        print("SMOKE PASS" if not station_failures else "SMOKE FAIL: " + "; ".join(station_failures))
        return 0 if not station_failures else 1
    if args.sell:
        sell_failures = smoke_sell(client, rec, args)
        print("SMOKE PASS" if not sell_failures else "SMOKE FAIL: " + "; ".join(sell_failures))
        return 0 if not sell_failures else 1
    if args.trade:
        trade_failures = smoke_trade(client, rec, args)
        print("SMOKE PASS" if not trade_failures else "SMOKE FAIL: " + "; ".join(trade_failures))
        return 0 if not trade_failures else 1
    if args.powerplay:
        pp_failures = smoke_powerplay(client, rec, args)
        print("SMOKE PASS" if not pp_failures else "SMOKE FAIL: " + "; ".join(pp_failures))
        return 0 if not pp_failures else 1

    if args.market:
        market_failures = smoke_market(client, rec, args)
        print("SMOKE PASS" if not market_failures else "SMOKE FAIL: " + "; ".join(market_failures))
        return 0 if not market_failures else 1
    if args.gear:
        gear_failures = smoke_gear(client, rec, args)
        print("SMOKE PASS" if not gear_failures else "SMOKE FAIL: " + "; ".join(gear_failures))
        return 0 if not gear_failures else 1
    if args.plots:
        plot_failures = smoke_plots(client, rec, args)
        print("SMOKE PASS" if not plot_failures else "SMOKE FAIL: " + "; ".join(plot_failures))
        return 0 if not plot_failures else 1
    cancel = threading.Event()
    failures: list[str] = []

    # 1. autocomplete
    hits = spansh.system_names(client, "Col 285 Sector", cancel)
    print(f"names: {len(hits)} hits, first {hits[0] if hits else None}")
    if not hits:
        failures.append("autocomplete returned nothing")
    names_ex = rec.log[-1]

    # 2 + 3. one riches and one bio tour
    query = ScoutQuery(args.src, args.dst, args.range, radius_ly=25, max_results=5, min_value=1_000_000)
    tours: dict[str, list[Exchange]] = {}
    for kind in ("riches", "bio"):
        start = len(rec.log)
        t0 = time.monotonic()
        result = spansh.scout(client, kind, query, cancel)
        tours[kind] = rec.log[start:]
        print(f"{summary(result)}\n  ({len(tours[kind])} calls, {time.monotonic() - t0:.1f} s)")
        if len(result.systems) < 3 or not any(s.bodies for s in result.systems):
            failures.append(f"{kind}: no stops with bodies")

    # 4. error replies we rely on
    try:
        spansh.scout(client, "riches", ScoutQuery("Nowhere Xyzzy Qq", args.dst, args.range), cancel)
        failures.append("unknown start system did not fail")
    except HttpError as exc:
        print(f"unknown system -> HTTP {exc.status}: {exc}")
    bad_submit_ex = rec.log[-1]
    resp = client.get(f"{spansh.BASE}/results/00000000-0000-0000-0000-000000000000", cancel)
    print(f"unknown job -> HTTP {resp.status} {resp.body[:60]!r}")
    bad_job_ex = rec.log[-1]

    # 5. cancel mid-poll through the real worker: cancel the moment Spansh accepts the job (it is then
    #    queued/running on Spansh and we are inside the polling loop). Spansh often finishes on the
    #    first poll, so waiting for a "still running" reply would rarely get a chance to cancel.
    worker = Worker(log)
    worker.start()
    cancelled_at: list[float] = []

    def on_reply(request: Request, response: Response) -> None:
        if request.method == "POST" and response.status == 202 and not cancelled_at:
            cancelled_at.append(time.monotonic())
            worker.cancel("scout")

    rec.on_reply.append(on_reply)
    big = ScoutQuery(args.src, args.dst, args.range, radius_ly=50, max_results=40, min_value=500_000)
    calls_before = len(rec.log)
    worker.submit("scout", lambda c: spansh.scout(client, "riches", big, c))
    finished = None
    deadline = time.monotonic() + 120
    while finished is None and time.monotonic() < deadline:
        time.sleep(0.05)
        done = worker.drain()
        finished = done[0] if done else None
    worker.stop()
    if finished is None or not cancelled_at:
        failures.append("cancel test: no result / job never accepted within 120 s")
    else:
        took = time.monotonic() - cancelled_at[0]
        polls_after = sum(1 for e in rec.log[calls_before:] if e.at > cancelled_at[0])
        print(
            f"cancel mid-job: cancelled={finished.cancelled}, ended {took:.2f} s after cancel, "
            f"{polls_after} calls to Spansh after cancel"
        )
        if not finished.cancelled or took > 1.0 or polls_after:
            failures.append("cancel test: job not cancelled within 1 s")

    # pacing: no two calls closer than the client's interval
    gaps = [b.at - a.at for a, b in zip(rec.log, rec.log[1:], strict=False)]
    print(f"calls: {len(rec.log)}, closest gap between sends {min(gaps):.2f} s")

    if args.record:
        record("names", names_ex)
        for kind, exchanges in tours.items():
            record(f"{kind}_submit", exchanges[0])
            running = [e for e in exchanges[1:] if e.response.status == 202]
            if running:
                record(f"{kind}_running", running[0])
            record(f"{kind}_done", exchanges[-1])
        record("error_unknown_system", bad_submit_ex)
        record("error_unknown_job", bad_job_ex)
        print(f"recorded into {RECORD_DIR}")

    print("SMOKE PASS" if not failures else "SMOKE FAIL: " + "; ".join(failures))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
