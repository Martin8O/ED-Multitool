"""The plugin's core between EDMC hooks and the tk panel. No tkinter, no EDMC imports.

`load.py` forwards every hook here and redraws the panel when a call returns True (text changed).
Every change is also written to `data/debug/panel.json` so the panel can be checked without screenshots.
Dashboard-driven changes (Status.json, ~1/s while fuel scooping) are dumped at most every
DASHBOARD_DUMP_INTERVAL seconds; journal-driven changes are dumped at once.

The web page reads `snapshot`: ready-made JSON bytes, rebuilt on the main thread whenever the UI state
changes and swapped in one assignment, so the server thread never sees a half-built value.

Online lookups run on one background `Worker`; `load.py` calls `pump()` from a tk timer on the main
thread to collect finished jobs.

The page talks back through `api_get` / `api_post`, called on the server's request threads:
- a POST never touches plugin state — it is checked, then queued as a command that `pump()` carries out
  on the main thread (so settings, state and the worker are only ever driven from there);
- system-name suggestions are answered on the request thread itself: they must not wait behind a long
  route search on the worker, and the shared client's pacing still keeps all calls to Spansh at ≤ 1/s.
  A newer suggestion request cancels the one before it.
"""

import json
import logging
import math
import os
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from edmultitool import USER_AGENT, __version__, station_lookup
from edmultitool.catalog.commodities import COMMODITIES
from edmultitool.core.arrival import Arrival, survey, ui_arrival
from edmultitool.core.arrival import arrive as arrive_here
from edmultitool.core.career import Career, UiHold, hold_message, ui_career
from edmultitool.core.commander import Commander, apply_commander, ui_commander
from edmultitool.core.earnings import Earnings, ui_earnings
from edmultitool.core.events import (
    Arrived,
    BodyMapped,
    CareerEvent,
    CargoHeld,
    CommanderEvent,
    CreditsKnown,
    CrimeCommitted,
    DebtPaid,
    Died,
    ExplorationSold,
    FleetListed,
    GameRoute,
    LegalStateKnown,
    LoadoutChanged,
    MissionEnded,
    MissionEvent,
    MoneyEvent,
    OrganicSold,
    PowerPledged,
    ShipSold,
    SpeciesAnalysed,
    StarPos,
    cargo_from_edmc_state,
    edmc_ship_differs,
    entry_time,
    events_from_edmc_state,
    loadout_from_edmc_state,
    nav_route_pending,
    parse_career,
    parse_cargo,
    parse_commander,
    parse_death,
    parse_event,
    parse_mission,
    parse_money,
    parse_nav_route,
    parse_statistics,
    parse_survey,
)
from edmultitool.core.explored import Located
from edmultitool.core.factions import Factions, UiFactions, hostile_here, ui_factions
from edmultitool.core.fleet import (
    Fleet,
    ScanResult,
    forget_sold,
    from_doc,
    keep_owned,
    merge_scan,
    remember,
    to_doc,
)
from edmultitool.core.follow import arrive, game_offer, game_route, start_index, step, ui_follow
from edmultitool.core.gear import (
    MAX_TEXT,
    GearRequest,
    Lookup,
    ParsedPost,
    locate,
    parse_fields,
    parse_post,
    station_like,
)
from edmultitool.core.gear_view import (
    GearStatus,
    UiGear,
    UiGearFields,
    item_text,
    push_earlier,
    ui_place,
    with_distances,
)
from edmultitool.core.market import (
    MarketFound,
    MarketQuery,
    MarketRequest,
    MarketStatus,
    Standings,
    UiMarket,
    nearest,
    parse_market_request,
    split,
    standing,
    ui_rows,
)
from edmultitool.core.missions import Missions, UiMissions, ui_missions
from edmultitool.core.nearby import (
    STATIONS_SHOWN,
    ServiceQuery,
    ServiceRequest,
    ServiceRow,
    ServiceStatus,
    UiServices,
    parse_service_request,
    split_barred,
    ui_service_rows,
    what_asked,
)
from edmultitool.core.plot import PlotQuery, PlotResult, ship_build
from edmultitool.core.powerplay import (
    PowerFound,
    PowerQuery,
    PowerRequest,
    PowerStatus,
    UiPowerplay,
    describe,
    found_message,
    parse_power_request,
    ui_power_rows,
)
from edmultitool.core.route import Route, build_route, min_jumps, pool_size, scout_queries, spent, swap_spent
from edmultitool.core.scout import ScoutKind, ScoutQuery
from edmultitool.core.sell import (
    SellFound,
    SellQuery,
    SellRequest,
    SellStatus,
    UiRefined,
    UiSell,
    mined_names,
    parse_sell_request,
    sell_result,
    ui_refined,
)
from edmultitool.core.sell import hold as sell_hold
from edmultitool.core.sell import load_of as sell_load
from edmultitool.core.settings import (
    ScoutPrefs,
    Settings,
    load_settings,
    parse_flash,
    parse_scout_prefs,
    parse_update_check,
    remember_destination,
    save_settings,
)
from edmultitool.core.state import GameState, apply, plan_state
from edmultitool.core.stats import Stats, UiStats, ui_stats
from edmultitool.core.trade import (
    TradeFound,
    TradeQuery,
    TradeRequest,
    TradeStatus,
    UiTrade,
    parse_trade_request,
    ui_station,
)
from edmultitool.core.updates import Release, UiUpdate, is_newer
from edmultitool.core.view import (
    PanelText,
    ScoutStatus,
    UiRoute,
    UiScout,
    UiState,
    regrade,
    route_from_doc,
    routing_range,
    ship_label,
    ui_plot,
    ui_route,
    ui_state,
)
from edmultitool.explored import ExploredStore
from edmultitool.gear_lookup import lookup as gear_lookup
from edmultitool.history import (
    scan_career,
    scan_commander,
    scan_earnings,
    scan_explored,
    scan_history,
    scan_missions,
    scan_standings,
    scan_stats,
)
from edmultitool.market_lookup import find as market_find
from edmultitool.sell_lookup import find as sell_find
from edmultitool.server import DEFAULT_PORT, ApiReply, ScoutServer
from edmultitool.sources import github, spansh
from edmultitool.sources.http import (
    Cancelled,
    PoliteClient,
    SourceError,
    Transport,
    brief,
    explain,
    requests_transport,
)
from edmultitool.sources.spansh import CACHE_MAX_AGE
from edmultitool.worker import INTERNAL_ERROR, JobResult, Worker

QUEUED = "Waiting for the route search to finish, then: "
INTERNAL_MESSAGE = "ED Multitool hit an internal error; the details are in the EDMC log."
DASHBOARD_DUMP_INTERVAL = 5.0
TICK_SECONDS = 30.0  # rebuild the state this often with nothing else happening (a mission ran out)
SCOUT_JOB = "scout"
FLEET_JOB = "fleet-scan"
EXPLORE_JOB = "explored-scan"
GEAR_JOB = "gear"
GEAR_JUMPS_JOB = "gear-jumps"
MARKET_JOB = "market"
TRADE_JOB = "trade"
SELL_JOB = "sell"  # where to sell the hold
TRADE_CLEARED: dict[str, Any] = {
    "source": None, "destination": None, "distance_ly": None, "jumps": None, "laden_ly": None, "goods": (),
    "cost": 0, "total_profit": 0,
}  # fmt: skip
SERVICES_JOB = "services"
POWER_JOB = "powerplay"
HISTORY = "history"  # the notice about the journal history
# the card a page command belongs to, where it is not named after it (jobs are named after their card)
_CARD = {"cancel": "scout", "plan-ship": "ship", "follow": "follow", "follow-game": "follow"}
STANDINGS_JOB = "standings-scan"
COMMANDER_JOB = "commander-scan"
CAREER_JOB = "career-scan"
EARNINGS_JOB = "earnings-scan"
MISSIONS_JOB = "missions-scan"
STATS_JOB = "stats-scan"
PLACES_JOB = "mission-places"  # where the missions' systems are (Spansh, by name: a mission names no id)
ARRIVAL_JOB = "arrival"  # the bodies Spansh knows in the system you just arrived in
HOLD_JOB = "career"  # how many systems your power holds (the Commander tab asks when it is shown)
HOLD_TTL = 15 * 60  # seconds a count stands before the tab asks Spansh again
UPDATE_JOB = "update-check"  # asks GitHub whether a newer version is out
UPDATE_EVERY = 24 * 3600.0  # seconds between two asks
UPDATE_RETRY = 3600.0  # seconds to wait before asking again after a failed ask (offline, rate limit)
MAX_GEAR_PLOTS = 6  # jump plots for one post, nearest first
_NOT_PLOTTED = f"jumps not counted: only the {MAX_GEAR_PLOTS} nearest systems are plotted"
MAX_FIELD_GEAR = 2000  # characters of the separate gear field
_EXPLORE = (BodyMapped, SpeciesAnalysed, ExplorationSold, OrganicSold, Died)

ROUTE_DOC_VERSION = 1

# ("scout", prefs) · ("cancel", None) · ("plan-ship", ShipID or None = the ship you sit in) ·
# ("follow", +1 skip / -1 back) · ("gear", GearRequest, or None = clear the card) · ("market", MarketRequest)
# ("trade", TradeRequest) · ("sell", SellRequest) · ("services", ServiceRequest) · ("powerplay", PowerRequest)
Command = tuple[
    str,
    ScoutPrefs
    | GearRequest
    | MarketRequest
    | TradeRequest
    | SellRequest
    | ServiceRequest
    | PowerRequest
    | tuple[bool, int]
    | int
    | None,
]


def plugin_logger(plugin_dir: Path) -> logging.Logger:
    """The logger EDMC sets up for a plugin: "<appname>.<plugin folder name>"."""
    return logging.getLogger(f"EDMarketConnector.{plugin_dir.name}")


def _friendly(error: str, start: str, dest: str) -> str:
    """Spansh's two everyday refusals in player words; anything else as Spansh said it."""
    if "Could not find starting system" in error:
        return f"Spansh does not know the start system '{start}'. Pick a name from the suggestions."
    if "Could not find finishing system" in error:
        return f"Spansh does not know the destination '{dest}'. Pick a name from the suggestions."
    if error.startswith(("Spansh does not know", "Spansh knows")):  # spansh.resolve_system
        return f"{error}. Pick a name from the suggestions."
    return _failed("Spansh", error)


def _asked(req: ServiceRequest) -> dict[str, Any]:
    """What a station search was asked with, for the page to show again after a reload (`UiServices`)."""
    return {
        "asked": req.services, "trader_kind": req.trader_kind, "broker_kind": req.broker_kind, "pad": req.pad,
        "carriers": req.carriers, "settlements": req.settlements, "hide_barred": req.hide_barred,
    }  # fmt: skip


def _failed(service: str, error: str | None) -> str:
    """A failed job in player words: "Spansh: <reason>." — or, for a bug in ED Multitool, which service is
    not to blame, the internal-error message."""
    if error == INTERNAL_ERROR:
        return INTERNAL_MESSAGE
    return f"{service}: {error or 'no answer'}."


def _gear_request(doc: object) -> GearRequest | str | None:
    """POST /api/gear: {"text": post} | {"system", "station", "gear"} | {"clear": true} (→ None);
    a message when the body is none of these."""
    if not isinstance(doc, dict):
        return "send a JSON object"
    if doc.get("clear") is True:
        return None
    if "text" in doc:
        text = doc["text"]
        if not isinstance(text, str) or not text.strip():
            return "paste a post first"
        return GearRequest(text=text[:MAX_TEXT])
    values = [doc.get(k, "") for k in ("system", "station", "gear")]
    if not all(isinstance(v, str) for v in values):
        return "system, station and gear must be text"
    system, station = (str(v)[:200] for v in values[:2])
    gear = str(values[2])[:MAX_FIELD_GEAR]
    if not (system.strip() or station.strip()):
        return "type a system or a station"
    return GearRequest(system=system, station=station, gear=gear, fields=True)


def _fields_from(post: ParsedPost, req: GearRequest) -> UiGearFields:
    """The separate fields, shown: what the user typed there, else what the decoding found."""
    if req.fields:
        return UiGearFields(True, req.system, req.station, req.gear)
    system = next((p for p in post.pieces if not station_like(p)), "")
    station = next((p for p in post.pieces if station_like(p)), "")
    gear = "\n".join(item_text(i) for i in post.items if not i.taken)  # what the post calls gone stays out
    return UiGearFields(True, system, station, gear)


class ScoutPlugin:
    def __init__(self, plugin_dir: Path, journal_dir: Path | None = None) -> None:
        self.log = plugin_logger(plugin_dir)
        self.data_dir = plugin_dir / "data"
        self.settings_path = self.data_dir / "settings.json"
        self.fleet_path = self.data_dir / "ships.json"
        self.route_path = self.data_dir / "route.json"
        self.explored_path = self.data_dir / "explored.db"
        self.debug_path = self.data_dir / "debug" / "panel.json"
        self.journal_dir = journal_dir  # read-only: the fleet's and the explored bodies' history passes
        self.settings = load_settings(self.settings_path)
        self.explored = ExploredStore.open(self.explored_path, self.log)  # main thread only
        # live exploration events seen while the history pass runs: applied again after its result
        self._explore_live: list[Located] | None = None
        self.fleet = self._load_fleet()
        self.plan_pick: int | None = None  # ship picked on the page; never saved
        self.state = GameState()
        last = self.fleet.last_flown
        if last is not None:  # until the game says otherwise: the ship flown last (logging in on foot)
            self.state = apply(self.state, last.loadout)
        self.game_seen = False
        self.scout = UiScout()
        self.follow_next = 0  # index of the next point of `scout.route` (core.follow)
        self.game: GameRoute | None = self._read_nav_route(None)  # plotted in the galaxy map
        self._load_route()
        self.ui: UiState = ui_state(
            self.state, self.settings, game_seen=False, scout=self.scout, fleet=self.fleet,
            follow=ui_follow(self.scout.route, self.follow_next),
            game_route=game_offer(self.game, self.scout.route, self.follow_next),
        )  # fmt: skip
        self.seq = 0
        self.snapshot = self._snapshot_bytes()
        self.server: ScoutServer | None = None
        self._server_note = ""  # the panel's line from starting the page server
        self.panel_note = ""  # the panel's own line: page not running / on another port / no browser
        self.notices: dict[str, str] = {}  # standing problems the page shows, by what they are about
        self.worker = Worker(self.log)
        self.client: PoliteClient | None = None
        self.jobs_done = 0
        self.last_job: JobResult | None = None
        self._commands: queue.Queue[Command] = queue.Queue()
        self._scout_job: tuple[int, ScoutPrefs, str, float] | None = None  # job id, form, start, range
        self._handled = 0  # page commands carried out (UiScout.handled)
        self.gear = UiGear()  # the gear post decoded last (not saved)
        self._gear_handled = 0
        self._gear_job: tuple[int, ParsedPost, GearRequest] | None = None
        self._gear_jumps_job: int | None = None
        self._gear_replot = False  # jumps could not be counted yet (no position / ship): count them later
        self._gear_jumps_ship: int | None = None  # the ShipID the jumps were counted with
        self._gear_req: GearRequest | None = None  # the request the newest find answers
        self.market = UiMarket()  # the commodity search shown last (not saved)
        self.now: Callable[[], datetime] = lambda: datetime.now(UTC)  # market data age is measured from it
        self._market_handled = 0
        self._market_job: tuple[int, MarketQuery, str] | None = None
        self.trade = UiTrade()  # the best trade found last (not saved)
        self._trade_handled = 0
        self._trade_job: tuple[int, TradeQuery] | None = None
        self.cargo: CargoHeld | None = None  # the hold line by line (None = the game has not listed it yet)
        self.sell = UiSell()  # where to sell the hold, found last (not saved)
        self._sell_handled = 0
        self._sell_job: tuple[int, SellQuery, str] | None = None
        self.services = UiServices()  # the station service search shown last (not saved)
        self._services_handled = 0
        self._services_job: tuple[int, ServiceQuery] | None = None
        self.powerplay = UiPowerplay()  # the Powerplay system search shown last (not saved)
        self._power_handled = 0
        self._power_job: tuple[int, PowerQuery] | None = None
        # who you are to the stations: every minor faction met (standing, where, missions) and the
        # Powerplay pledge, from the journal history pass, then live entries (not saved: the pass runs at
        # every start)
        self.factions = Factions()
        self.power: str | None = None  # None = not known yet
        # live entries (with their time) seen while the history pass runs
        self._standing_live: list[tuple[object, str]] | None = None
        self.commander = Commander()  # money and legal standing
        # live crimes, payments, deaths and notoriety until the history pass has answered (balance and legal
        # state are kept from `commander` itself: the live value always wins)
        self._commander_live: list[CommanderEvent] | None = []
        # superpower standings, ranks, Powerplay career: live entries only until the history pass hands
        # over its own, then that one with the live entries it had not read yet
        self.career = Career()
        self._career_live: list[CareerEvent] | None = []
        # credits earned and spent, data carried unsold: the same hand-over as the career
        self.earnings = Earnings()
        self._earnings_live: list[MoneyEvent] | None = []
        # open missions: the same hand-over; their systems' places by name, from Spansh and your arrivals
        self.missions = Missions()
        self._missions_live: list[MissionEvent] | None = []
        # the game's lifetime statistics: one entry per login; the pass and live entries meet in one list
        self.stats = Stats()
        self._ui_stats_for: tuple[object, ...] = ()  # what `_ui_stats` was built from
        self._stats_reading = False  # the history pass is reading the journals
        self._ui_stats_cache = UiStats()
        self.places: dict[str, StarPos] = {}
        self._places_job: tuple[int, list[str]] | None = None  # job id, the names asked
        self._places_asked: set[str] = set()  # names asked (found or not): not asked again until you jump
        self._places_error = ""  # why the last lookup failed ("" = it did not)
        self.hold = UiHold()  # the pledged power's systems, by state (asked when the Commander tab is shown)
        self._hold_job: tuple[int, str] | None = None
        self._hold_at = 0.0  # monotonic time the last count was asked (the page re-asks on the same clock)
        self.update = UiUpdate()  # a newer version is out (asked about once a day; not saved)
        self._update_job: int | None = None
        self._update_at: float | None = None  # monotonic time of the last ask (None = not asked yet)
        self.arrival = Arrival()  # the system you are in: its bodies, from Spansh and your own scans
        self._arrival_job: int | None = None
        self._suggest_lock = threading.Lock()
        self._suggest_cancel = threading.Event()
        self._last_dump = 0.0
        self._last_tick = time.monotonic()  # time alone changes what is shown: missions run out
        self._dump_failed = False

    @property
    def text(self) -> PanelText:
        return self.ui.panel

    # --- web server ------------------------------------------------------------------------------
    @property
    def next_name(self) -> str:
        """What the panel's "Copy" button puts on the clipboard: the followed route's next point."""
        return self.ui.follow.next_name

    def start_server(self, port: int = DEFAULT_PORT) -> str | None:
        """Start the localhost page server; returns its URL (None if it could not listen)."""
        if self.server is None:
            self.server = ScoutServer(lambda: self.snapshot, self.log, port=port, api=self)
        url = self.server.start()
        if url is None:
            self.panel_note = "The page could not start: no free local port (details in the EDMC log)."
        elif self.server.port != port:
            self.panel_note = f"Port {port} is busy (a second EDMC?): this page is at {url}"
        else:
            self.panel_note = ""
        self._server_note = self.panel_note
        self._refresh()
        return url

    def page_opened(self, how: str) -> None:
        """The "Open ED Multitool" button's outcome (`browser.open_page`): a failure goes to the panel."""
        url = self.server_url
        if how == "failed" and url is not None:
            self.panel_note = f"No browser could be opened: open {url} yourself."
        else:  # opened after all: back to what the server start said (a busy port stays told)
            self.panel_note = self._server_note
        self._refresh()

    def stop_server(self) -> None:
        if self.server is not None:
            self.server.stop()
            self.server = None

    @property
    def server_url(self) -> str | None:
        return self.server.url if self.server is not None else None

    # --- background work ------------------------------------------------------------------------
    def start_worker(self, transport: Transport | None = None) -> None:
        """Start the worker thread and the HTTP client (`transport` defaults to the real network)."""
        if self.client is None:
            self.client = PoliteClient(
                transport or requests_transport(),
                user_agent=USER_AGENT,
                cache_dir=self.data_dir / "cache" / "http",
                log=self.log,
            )
        self.worker.start()
        client = self.client
        self.worker.submit("cache-prune", lambda _cancel: client.prune_cache(CACHE_MAX_AGE))
        journal_dir, newer_than = self.journal_dir, self.fleet.scanned_mtime
        if journal_dir is None or not journal_dir.is_dir():
            where = "is not known" if journal_dir is None else f"({journal_dir}) cannot be read"
            self._notice(
                HISTORY, f"The game's journal folder {where}: bodies you mapped, your other ships and your "
                "Powerplay pledge are not known until the game reports them.",
            )  # fmt: skip
            self._career_live = None  # no pass will hand over: live entries alone make the career
            self._earnings_live = None
            self._missions_live = None
        else:
            self.worker.submit(FLEET_JOB, lambda cancel: scan_history(journal_dir, newer_than, cancel))
            explored_since = self.explored.scanned_mtime
            self._explore_live = []
            self.worker.submit(EXPLORE_JOB, lambda cancel: scan_explored(journal_dir, explored_since, cancel))
            self._standing_live = []
            self.worker.submit(STANDINGS_JOB, lambda cancel: scan_standings(journal_dir, cancel))
            self.worker.submit(COMMANDER_JOB, lambda cancel: scan_commander(journal_dir, cancel))
            self.worker.submit(CAREER_JOB, lambda cancel: scan_career(journal_dir, cancel))
            self.worker.submit(EARNINGS_JOB, lambda cancel: scan_earnings(journal_dir, cancel))
            self.worker.submit(MISSIONS_JOB, lambda cancel: scan_missions(journal_dir, cancel))
            self._stats_reading = True
            self.worker.submit(STATS_JOB, lambda cancel: scan_stats(journal_dir, cancel))
        if self.arrival.status == "looking" and self._arrival_job is None:
            self._start_arrival()  # arrived before the worker ran (a replayed journal)
        self._want_places()  # missions taken before the worker ran

    def stop_worker(self) -> None:
        self.worker.stop()

    def close(self) -> None:
        """EDMC is closing: the worker is stopped first (no job left that could use the store)."""
        self.explored.close()

    def pump(self) -> bool:
        """Main thread: carry out the page's commands, then collect finished jobs. True when the panel
        text changed (the page may change without it). Each command and each finished job runs guarded:
        a bug in one marks its own card failed instead of leaving it "running" and losing the rest."""
        busy = False
        while True:
            try:
                name, prefs = self._commands.get_nowait()
            except queue.Empty:
                break
            busy = True
            self._guarded(_CARD.get(name, name), self._command, name, prefs)
        results = self.worker.drain()
        for r in results:
            if r.error is None:
                self.log.debug("job %s %s", r.name, "cancelled" if r.cancelled else "done")
            self.jobs_done += 1
            self.last_job = r
            self._guarded(r.name, self._finish, r)
        if (busy or results) and self._scout_job is None:
            self._unqueue()
        tick = time.monotonic() - self._last_tick >= TICK_SECONDS
        if tick:
            self._last_tick = time.monotonic()
            self._guarded(UPDATE_JOB, self._want_update)
        changed = self._refresh() if busy or results or tick else False
        if results:
            self._dump(self.ui.panel)
        return changed

    def _queued(self, message: str) -> str:
        """One worker runs the jobs in turn: a lookup asked during a route search (minutes) waits for it,
        and says so rather than look stuck."""
        if self._scout_job is None:
            return message
        return QUEUED + message[0].lower() + message[1:]

    def _unqueue(self) -> None:
        """The route search is over: the lookups that waited for it are now on their way."""

        def now(message: str) -> str:
            rest = message.removeprefix(QUEUED)
            return rest[0].upper() + rest[1:] if rest != message else message

        if self.gear.status == "running":
            self.gear = replace(self.gear, message=now(self.gear.message))
        if self.market.status == "running":
            self.market = replace(self.market, message=now(self.market.message))
        if self.trade.status == "running":
            self.trade = replace(self.trade, message=now(self.trade.message))
        if self.sell.status == "running":
            self.sell = replace(self.sell, message=now(self.sell.message))
        if self.services.status == "running":
            self.services = replace(self.services, message=now(self.services.message))
        if self.powerplay.status == "running":
            self.powerplay = replace(self.powerplay, message=now(self.powerplay.message))
        if self.hold.status == "running":
            self.hold = replace(self.hold, message=now(self.hold.message))
        if self.arrival.status == "looking" and self.arrival.message:
            self.arrival = replace(self.arrival, message="")

    def _command(self, name: str, prefs: object) -> None:
        if name == "plan-ship":  # not a scout command: the page's `handled` counter skips it
            self._pick_ship(prefs if isinstance(prefs, int) else None)
        elif name == "flash":  # the "This system" card's flash setting
            if isinstance(prefs, tuple):
                on, over = prefs
                self.set_settings(replace(self.settings, flash=on, flash_over=over))
        elif name == "update-check":  # the page footer's setting
            self.set_update_check(bool(prefs))
        elif name == "follow-game":  # nor this
            self._take_game_route()
        elif name == "follow":  # not a scout command either
            self._step_follow(prefs if isinstance(prefs, int) else 0)
        elif name == "market":  # the commodity card counts its own requests (UiMarket.handled)
            self._market_handled += 1
            if isinstance(prefs, MarketRequest):
                self._start_market(prefs)
        elif name == "trade":  # the trade card counts its own requests (UiTrade.handled)
            self._trade_handled += 1
            if isinstance(prefs, TradeRequest):
                self._start_trade(prefs)
        elif name == "sell":  # the sell card counts its own requests (UiSell.handled)
            self._sell_handled += 1
            if isinstance(prefs, SellRequest):
                self._start_sell(prefs)
        elif name == "services":  # the services card counts its own requests (UiServices.handled)
            self._services_handled += 1
            if isinstance(prefs, ServiceRequest):
                self._start_services(prefs)
        elif name == "powerplay":  # the Powerplay card counts its own requests (UiPowerplay.handled)
            self._power_handled += 1
            if isinstance(prefs, PowerRequest):
                self._start_power(prefs)
        elif name == "career":  # the Commander tab was shown: count your power's systems if not fresh
            self._start_hold()
        elif name == "gear":  # the gear card counts its own requests (UiGear.handled)
            self._gear_handled += 1
            self._start_gear(prefs if isinstance(prefs, GearRequest) else None)
        else:
            self._handled += 1
            if name == "scout" and isinstance(prefs, ScoutPrefs):
                self._start_scout(prefs)
            elif name == "cancel":
                self._cancel_scout()
                self.scout = replace(self.scout, handled=self._handled)

    def _finish(self, r: JobResult) -> None:
        if (
            r.name in (FLEET_JOB, EXPLORE_JOB, STANDINGS_JOB, CAREER_JOB, MISSIONS_JOB, STATS_JOB)
            and r.error is not None
        ):
            self._notice(
                HISTORY, "Reading the game's journal history failed (details in the EDMC log): bodies you "
                "mapped, your other ships, your Powerplay pledge, missions taken earlier and the statistics "
                "of earlier logins may be missing.",
            )  # fmt: skip
        if r.name == SCOUT_JOB:
            self._finish_scout(r)
        elif r.name == FLEET_JOB and r.ok and isinstance(r.value, tuple):
            self._finish_fleet_scan(*r.value)
        elif r.name == EXPLORE_JOB:
            self._finish_explored_scan(r)
        elif r.name == GEAR_JOB:
            self._finish_gear(r)
        elif r.name == GEAR_JUMPS_JOB:
            self._finish_gear_jumps(r)
        elif r.name == MARKET_JOB:
            self._finish_market(r)
        elif r.name == TRADE_JOB:
            self._finish_trade(r)
        elif r.name == SELL_JOB:
            self._finish_sell(r)
        elif r.name == SERVICES_JOB:
            self._finish_services(r)
        elif r.name == POWER_JOB:
            self._finish_power(r)
        elif r.name == STANDINGS_JOB:
            self._finish_standings(r)
        elif r.name == COMMANDER_JOB:
            self._finish_commander(r)
        elif r.name == CAREER_JOB:
            self._finish_career(r)
        elif r.name == EARNINGS_JOB:
            self._finish_earnings(r)
        elif r.name == MISSIONS_JOB:
            self._finish_missions(r)
        elif r.name == STATS_JOB:
            self._finish_stats(r)
        elif r.name == PLACES_JOB:
            self._finish_places(r)
        elif r.name == HOLD_JOB:
            self._finish_hold(r)
        elif r.name == ARRIVAL_JOB:
            self._finish_arrival(r)
        elif r.name == UPDATE_JOB:
            self._finish_update(r)

    # --- "a new version is out" -------------------------------------------------------------------
    def _want_update(self) -> None:
        """Ask GitHub for the newest release when the setting is on and the last ask is a day old (or there
        was none). Called from the 30-second tick, so the first ask comes shortly after start."""
        if not self.settings.update_check or self._update_job is not None:
            return
        if self._update_at is not None and time.monotonic() - self._update_at < UPDATE_EVERY:
            return
        client = self.client
        if client is None or not self.worker.alive:
            return
        self._update_at = time.monotonic()
        self._update_job = self.worker.submit(
            UPDATE_JOB, lambda cancel: github.latest_release(client, cancel)
        )

    def _finish_update(self, r: JobResult) -> None:
        """Quiet by design: a failed or empty answer (no release yet, repository private, offline, rate
        limit) only goes to the log; what was shown before stays until the next answer."""
        if self._update_job != r.job_id:
            return
        self._update_job = None
        if r.cancelled:
            return
        if r.error is not None:
            self.log.info("update check: %s", r.error)
            self._update_at = time.monotonic() - UPDATE_EVERY + UPDATE_RETRY
            return
        release = r.value
        if isinstance(release, Release) and is_newer(__version__, release.tag):
            self.update = UiUpdate(release.version, release.url)
        else:
            self.update = UiUpdate()
        self._refresh()

    def set_update_check(self, on: bool) -> None:
        if on == self.settings.update_check:
            return
        self._update_at = None  # switched on: ask at the next tick; off: the strip goes away
        if not on:
            self.update = UiUpdate()
        self.set_settings(replace(self.settings, update_check=on))
        if on:
            self._want_update()

    def _notice(self, key: str, text: str) -> None:
        self.notices[key] = text
        self._refresh()

    def _guarded(self, card: str, action: Callable[..., None], *args: Any) -> None:
        try:
            action(*args)
        except Exception:  # a bug: logged with its traceback, the card says so, the panel keeps running
            self.log.exception("%s failed", card)
            try:
                self._broken(card)
            except Exception:  # even showing the error failed: the log has it, the batch goes on
                self.log.exception("%s: could not show the failure", card)

    def _broken(self, card: str) -> None:
        """The card whose command or result hit a bug: its job is forgotten and it shows the error."""
        if card == "scout":
            self._scout_job = None
            self._set_scout("error", INTERNAL_MESSAGE)
        elif card == "gear":
            self._gear_job = self._gear_jumps_job = None
            self._set_gear("error", INTERNAL_MESSAGE, jumps_status="idle")
        elif card == "gear-jumps":
            self._gear_jumps_job = None
            g = self.gear
            origin_id = int(g.jumps_from_id64) if g.jumps_from_id64 else None
            self._set_gear_jumps("error", origin_id, g.jumps_from, INTERNAL_MESSAGE)
        elif card == "market":
            self._market_job = None
            self._set_market("error", INTERNAL_MESSAGE)
        elif card == "trade":
            self._trade_job = None
            self._set_trade("error", INTERNAL_MESSAGE)
        elif card == "sell":
            self._sell_job = None
            self._set_sell("error", INTERNAL_MESSAGE)
        elif card == "services":
            self._services_job = None
            self._set_services("error", INTERNAL_MESSAGE)
        elif card == "powerplay":
            self._power_job = None
            self._set_power("error", INTERNAL_MESSAGE)
        elif card == HOLD_JOB:
            self._hold_job = None
            self.hold = UiHold(self.hold.power, "error", INTERNAL_MESSAGE)
        elif card == ARRIVAL_JOB:
            self._arrival_job = None
            self.arrival = replace(self.arrival, status="error", message=INTERNAL_MESSAGE)
        elif card == UPDATE_JOB:
            self._update_job = None
        elif card == PLACES_JOB:
            self._places_job = None
            self._places_error = INTERNAL_MESSAGE
        # the background passes (fleet, explored, standings) and follow steps have no card: log only

    # --- route scouting (main thread) -------------------------------------------------------------
    def _start_scout(self, prefs: ScoutPrefs) -> None:
        self.set_settings(replace(self.settings, scout=prefs))
        start = prefs.from_system or self.state.system
        if not start:
            self._set_scout("error", "Start system unknown: type one into From, or jump in the game first.")
            return
        if not (prefs.riches or prefs.bio):
            self._start_plot(prefs, start)
            return
        range_ly = routing_range(self._plan_state(), self.settings)
        if range_ly is None or range_ly <= 0:
            self._set_scout("error", "No jump range yet: load your ship in the game first.")
            return
        client = self.client
        if client is None or not self.worker.alive:
            self._set_scout("error", "Online lookups are not running; restart EDMC.")
            return
        queries = scout_queries(prefs, start, range_ly)
        pool = scout_queries(prefs, start, range_ly, max_results=pool_size(prefs.max_stops))
        done = self.explored.snapshot()  # immutable: safe on the worker

        def build(
            asked: list[tuple[ScoutKind, ScoutQuery]], cancel: threading.Event, keep: int | None = None
        ) -> Route:
            try:
                results = [spansh.scout(client, k, q, cancel) for k, q in asked]
                return build_route(results, prefs, done, keep=keep)
            except ValueError as exc:  # a reply without start and destination
                raise SourceError(str(exc)) from exc

        def run(cancel: threading.Event) -> Route:
            route = build(queries, cancel, keep=prefs.max_stops)
            if spent(route) and prefs.max_detour_ly > 0 and pool_size(prefs.max_stops) > prefs.max_stops:
                # Some stops hold only what is yours already: the same searches with more results give
                # the next-best stops to swap in (asked only now, so a normal search stays as it was).
                route = swap_spent(route, build(pool, cancel), prefs.max_detour_ly)
            return route

        job_id = self.worker.submit(SCOUT_JOB, run)
        self._scout_job = (job_id, prefs, start, range_ly)
        self._set_scout("running", f"Searching {start} → {prefs.to_system} at {range_ly:.2f} ly…")

    def _start_plot(self, prefs: ScoutPrefs, start: str) -> None:
        """Route only: Spansh's galaxy plotter with the real ship build (full tanks at the start, current
        cargo; the safety margin does not apply), normal jumps or with neutron supercharges."""
        s = self._plan_state()
        ship = None if s.in_other_ship else s.ship
        build = ship_build(ship) if ship is not None else None
        range_ly = s.full_tank_range()
        if build is None or range_ly is None or range_ly <= 0:
            self._set_scout("error", "No jump range yet: load your ship in the game first.")
            return
        client = self.client
        if client is None or not self.worker.alive:
            self._set_scout("error", "Online lookups are not running; restart EDMC.")
            return
        dest = prefs.to_system
        if start.casefold() == dest.casefold():  # Spansh would answer with a 0 ly "route"
            self._set_scout("error", "Start and destination are the same system.")
            return

        # The galaxy plotter wants system ids: the current system's is known, others are looked up.
        here = s.address if s.system is not None and start.casefold() == s.system.casefold() else None
        cargo, neutrons = s.cargo, prefs.plotter == "neutron"

        def run(cancel: threading.Event) -> PlotResult:
            src = here if here is not None else spansh.resolve_system(client, start, cancel).id64
            dst = spansh.resolve_system(client, dest, cancel).id64
            return spansh.plot(client, PlotQuery(src, dst, build, cargo, neutrons), cancel)

        how = "with neutron boosts" if neutrons else "jump by jump"
        picked = ship_label(self.fleet.get(self.plan_pick), self.state) if s is not self.state else None
        with_ship = f" with {picked}" if picked else ""
        message = f"Plotting {start} → {dest} {how}{with_ship} ({cargo} t cargo, full tanks)…"
        job_id = self.worker.submit(SCOUT_JOB, run)
        self._scout_job = (job_id, prefs, start, range_ly)
        self._set_scout("running", message)

    def _cancel_scout(self) -> None:
        self.worker.cancel(SCOUT_JOB)
        if self._scout_job is not None:
            self._scout_job = None
            self._set_scout("idle", "Search cancelled.")

    def _finish_scout(self, r: JobResult) -> None:
        if self._scout_job is None or self._scout_job[0] != r.job_id:
            return  # replaced or cancelled meanwhile
        _, prefs, start, range_ly = self._scout_job
        self._scout_job = None
        if r.cancelled:
            self._set_scout("idle", "Search cancelled.")
            return
        if r.error is not None:
            self._set_scout("error", _friendly(r.error, start, prefs.to_system))
            return
        finished = datetime.now(UTC).isoformat(timespec="seconds")
        if isinstance(r.value, PlotResult):
            route = ui_plot(r.value, range_ly, finished)
        elif isinstance(r.value, Route):
            route = ui_route(r.value, finished)
        else:
            self._set_scout("error", "Spansh: the search returned no route")
            return
        # only a destination Spansh found is offered again, under its own spelling (a typo never is)
        recent = remember_destination(self.settings.recent_to, route.to_name)
        self.set_settings(replace(self.settings, recent_to=recent))
        self._follow_new(route)

    # --- gear posts from the forum (main thread) -----------------------------------------------------
    def _set_gear(self, status: GearStatus, message: str, **changes: Any) -> None:
        self.gear = replace(self.gear, status=status, message=message, handled=self._gear_handled, **changes)

    def _start_gear(self, req: GearRequest | None) -> None:
        """Decode a pasted post (or the separate fields) here, then ask Spansh for its places on the
        worker. A new request replaces the one before (a job with the same name cancels the old one)."""
        # whatever runs for the post before is dropped, also when this one is refused right here
        self.worker.cancel(GEAR_JOB)
        self.worker.cancel(GEAR_JUMPS_JOB)
        self._gear_job = None
        self._gear_jumps_job = None
        self._gear_replot = False
        if req is None:  # "Clear": the earlier finds go too
            self.gear = UiGear(handled=self._gear_handled)
            self._gear_req = None
            return
        if req != self._gear_req:  # the same post again replaces its find instead of piling up
            self.gear = push_earlier(self.gear)
        self._gear_req = req
        post = parse_fields(req.system, req.station, req.gear) if req.fields else parse_post(req.text)
        if not req.fields and post.reply:
            offer_like = bool(post.items and post.pieces)  # it might be an offer after all: let it be typed
            message = (
                "This reads like a reply or a request (thanks / taken / looking for), not an offer of gear."
            )
            if offer_like:
                message += " If it is an offer, check the fields below."
            self._set_gear(
                "error", message, places=(), fields=_fields_from(post, req) if offer_like else UiGearFields(),
                jumps_status="idle",
            )  # fmt: skip
            return
        if not post.pieces:
            what = "No system or station in the text" if post.items else "No gear and no place in the text"
            self._set_gear(
                "error", f"{what}: type them into the fields below.", places=(),
                fields=_fields_from(post, req), jumps_status="idle",
            )  # fmt: skip
            return
        client = self.client
        if client is None or not self.worker.alive:
            self._set_gear("error", "Online lookups are not running; restart EDMC.", places=())
            return
        job_id = self.worker.submit(GEAR_JOB, lambda cancel: gear_lookup(client, post, cancel))
        self._gear_job = (job_id, post, req)
        n = len(post.pieces)
        fields = _fields_from(post, req) if req.fields else UiGearFields()
        self._set_gear(
            "running", self._queued(f"Looking up {n} name{'s' if n != 1 else ''} at Spansh…"), places=(),
            fields=fields,
            jumps_status="idle",
        )  # fmt: skip

    def _finish_gear(self, r: JobResult) -> None:
        if self._gear_job is None or self._gear_job[0] != r.job_id:
            return  # replaced or cleared meanwhile
        _, post, req = self._gear_job
        self._gear_job = None
        if r.cancelled:
            self._set_gear("idle", "Cancelled.")
            return
        if r.error is not None or not isinstance(r.value, Lookup):
            self._set_gear("error", _failed("Spansh", r.error))
            return
        places = tuple(ui_place(p, post.all_gone) for p in locate(post, r.value))
        found = [p for p in places if p.found]
        if not found:
            names = ", ".join(post.pieces[:4])
            self._set_gear(
                "error", f"Spansh knows none of these places: {names}. Check the names in the fields below.",
                places=places, fields=_fields_from(post, req),
            )  # fmt: skip
            return
        items = sum(len(p.items) for p in places)
        missing = len(places) - len(found)
        message = (
            f"{len(found)} place{'s' if len(found) != 1 else ''}, {items} item{'s' if items != 1 else ''}"
        )
        if not items:
            message += " (the post names no suit or weapon)"
        if missing:
            message += f"; {missing} place{'s' if missing != 1 else ''} not found"
        decoded = datetime.now(UTC).isoformat(timespec="seconds")
        self._set_gear("done", message + ".", places=places, decoded=decoded)
        self._start_gear_jumps()

    def _start_gear_jumps(self) -> None:
        """Jumps to every place found, plotted with the planning ship from where you are (nearest first,
        the galaxy plotter of "Route only": normal jumps, full tanks, current cargo)."""
        s = self._plan_state()
        here, origin = s.address, s.system
        ship = None if s.in_other_ship else s.ship
        build = ship_build(ship) if ship is not None else None
        client = self.client
        self._gear_replot = False
        self._gear_jumps_ship = ship.ship_id if ship is not None else None
        if s.in_other_ship:
            self._gear_replot = True  # counted again once you are back in your own ship
            self._set_gear_jumps("idle", None, "", "jumps not counted: you are crew in another ship")
            return
        if here is None or not origin:
            self._gear_replot = True  # counted as soon as the game says where you are
            self._set_gear_jumps(
                "idle", None, "", "jumps not counted: jump once in the game so it knows where you are"
            )
            return
        if build is None:
            self._gear_replot = True
            why = "no FSD data for this ship"
            if ship is None:
                why = "no ship known yet (load your ship in the game)"
            self._set_gear_jumps("idle", here, origin, f"jumps not counted: {why}")
            return
        if client is None or not self.worker.alive:
            return
        gear = with_distances(self.gear, s.star_pos)
        targets: list[int] = []
        for p in sorted((p for p in gear.places if p.found), key=lambda p: p.distance_ly or 0.0):
            if int(p.system_id64) not in targets:
                targets.append(int(p.system_id64))
        targets = targets[:MAX_GEAR_PLOTS]
        cargo = s.cargo

        def run(cancel: threading.Event) -> dict[int, int | str]:
            out: dict[int, int | str] = {}
            for dst in targets:
                if dst == here:
                    out[dst] = 0
                    continue
                try:
                    out[dst] = spansh.plot(client, PlotQuery(here, dst, build, cargo), cancel).total_jumps
                except Cancelled:
                    raise
                except SourceError as exc:
                    out[dst] = brief(explain(exc))
            return out

        self._gear_jumps_job = self.worker.submit(GEAR_JUMPS_JOB, run)
        self._set_gear_jumps("running", here, origin, "")

    def _gear_ship_changed(self) -> bool:
        if self.gear.jumps_status == "idle" or not any(p.found for p in self.gear.places):
            return False
        s = self._plan_state()
        ship = None if s.in_other_ship else s.ship
        return ship is not None and ship.ship_id != self._gear_jumps_ship

    def _set_gear_jumps(self, status: GearStatus, origin_id: int | None, origin: str, note: str) -> None:
        places = tuple(replace(p, jumps=None, jumps_note=note if p.found else "") for p in self.gear.places)
        self.gear = replace(
            self.gear, jumps_status=status, jumps_from=origin, places=places,
            jumps_from_id64="" if origin_id is None else str(origin_id),
        )  # fmt: skip

    def _finish_gear_jumps(self, r: JobResult) -> None:
        if self._gear_jumps_job != r.job_id:
            return  # replaced or cleared meanwhile
        self._gear_jumps_job = None
        if r.cancelled:
            return
        if r.error is not None or not isinstance(r.value, dict):
            g = self.gear
            origin_id = int(g.jumps_from_id64) if g.jumps_from_id64 else None
            why = (
                INTERNAL_MESSAGE if r.error == INTERNAL_ERROR else f"Spansh: {brief(r.error or 'no answer')}"
            )
            self._set_gear_jumps("error", origin_id, g.jumps_from, f"jumps not counted: {why}")
            return
        places = []
        for p in self.gear.places:
            got = r.value.get(int(p.system_id64)) if p.found else None
            if isinstance(got, int):
                places.append(replace(p, jumps=got, jumps_note=""))
            elif isinstance(got, str):
                places.append(replace(p, jumps=None, jumps_note=f"jumps not counted: {got}"))
            else:
                places.append(replace(p, jumps=None, jumps_note=_NOT_PLOTTED if p.found else ""))
        self.gear = replace(self.gear, jumps_status="done", places=tuple(places))

    # --- nearest commodity market (main thread) ----------------------------------------------------
    def _set_market(self, status: MarketStatus, message: str, **changes: Any) -> None:
        self.market = replace(
            self.market, status=status, message=message, handled=self._market_handled, **changes
        )

    def _standings(self) -> Standings:
        return Standings(self.factions.reputation(), self.power or "")

    def _start_market(self, req: MarketRequest) -> None:
        """Search from where you are now (never from a picked ship's place); a new search replaces the one
        before. The rows shown stay until the new search finishes."""
        self.worker.cancel(MARKET_JOB)
        self._market_job = None
        s = self.state
        if s.in_other_ship or s.star_pos is None or not s.system:
            self._set_market("error", "Where you are is not known yet: jump once in the game first.")
            return
        client = self.client
        if client is None or not self.worker.alive:
            self._set_market("error", "Online lookups are not running; restart EDMC.")
            return
        query = MarketQuery(req, s.system, s.address, s.star_pos, self.now())
        you = self._standings()
        job_id = self.worker.submit(MARKET_JOB, lambda cancel: market_find(client, query, you, cancel))
        self._market_job = (job_id, query, s.system)
        where = "selling" if req.side == "buy" else "buying"
        self._set_market(
            "running", self._queued(f"Looking for stations {where} {req.commodity} near {s.system}…")
        )

    def _finish_market(self, r: JobResult) -> None:
        if self._market_job is None or self._market_job[0] != r.job_id:
            return  # replaced meanwhile
        _, query, origin = self._market_job
        self._market_job = None
        req = query.request
        if r.cancelled:
            self._set_market("idle", "Cancelled.")
            return
        if r.error is not None or not isinstance(r.value, MarketFound):
            self._set_market("error", _failed("Spansh", r.error))
            return
        # who may dock is decided now: the journal history pass may have finished while the search ran
        you = self._standings()
        found = r.value
        kept, barred = split(list(found.rows), req, you)
        result = nearest(query, kept, barred, found.source, found.note)
        where = "sell" if req.side == "buy" else "buy"
        n = len(result.rows)
        if n:
            message = f"{n} station{'s' if n != 1 else ''} that {where} {req.commodity}, nearest first."
        else:
            message = (
                f"No station {where}s {req.commodity} with these filters: try older data, a smaller amount "
                "or fleet carriers."
            )
        if result.barred:
            b = result.barred
            them = "stations hidden: they" if b != 1 else "station hidden: it"
            message += f" {b} {them} would refuse you docking."
        if result.note:
            message += f" Spansh failed ({result.note.removeprefix('Spansh: ')}), so these come from Ardent."
        self._set_market(
            "done", message, commodity=req.commodity, side=req.side, rows=ui_rows(result, you),
            source=result.source, searched=self.now().isoformat(timespec="seconds"),
            searched_from=origin, barred=result.barred, note=result.note,
        )  # fmt: skip

    # --- where to sell the hold (main thread) -------------------------------------------------------
    def _set_sell(self, status: SellStatus, message: str, **changes: Any) -> None:
        self.sell = replace(self.sell, status=status, message=message, handled=self._sell_handled, **changes)

    def _start_sell(self, req: SellRequest) -> None:
        """One search per commodity in the hold now, from where you are now; a new search replaces the one
        before, and the offers shown stay until it finishes."""
        self.worker.cancel(SELL_JOB)
        self._sell_job = None
        s = self.state
        if s.in_other_ship or s.star_pos is None or not s.system:
            self._set_sell("error", "Where you are is not known yet: jump once in the game first.")
            return
        goods, _ = sell_hold(self.cargo)
        if not goods:
            if self.cargo is None:
                self._set_sell(
                    "error", "What your hold holds is not known yet: it shows once the game lists it."
                )
            else:
                self._set_sell("error", "Nothing in your hold to sell at a market.")
            return
        client = self.client
        if client is None or not self.worker.alive:
            self._set_sell("error", "Online lookups are not running; restart EDMC.")
            return
        query = SellQuery(req, goods, s.system, s.star_pos, self.now())
        you = self._standings()
        job_id = self.worker.submit(SELL_JOB, lambda cancel: sell_find(client, query, you, cancel))
        self._sell_job = (job_id, query, s.system)
        what = goods[0].name if len(goods) == 1 else f"{len(goods)} commodities"
        self._set_sell(
            "running",
            self._queued(f"Looking for the best price for {what} within {req.max_ly:g} ly of {s.system}…"),
        )

    def _finish_sell(self, r: JobResult) -> None:
        if self._sell_job is None or self._sell_job[0] != r.job_id:
            return  # replaced meanwhile
        _, query, origin = self._sell_job
        self._sell_job = None
        if r.cancelled:
            self._set_sell("idle", "Cancelled.")
            return
        if r.error is not None or not isinstance(r.value, SellFound):
            self._set_sell("error", _failed("Spansh", r.error))
            return
        # who may dock is decided now: the journal history pass may have finished while the search ran
        found, you = r.value, self._standings()
        goods, best = sell_result(found, query, you, mined_names(self._ui_refined()))
        within = f"{query.request.max_ly:g} ly"
        unsold = [g.name for g in goods if not g.offers]
        if len(unsold) == len(goods):
            message = (
                f"No station within {within} buys all you carry of any of these with these filters: try "
                "farther, older data or fleet carriers."
            )
        elif unsold:
            message = f"Best prices within {within}. No station there buys all your {', '.join(unsold)}."
        else:
            message = f"Best prices within {within}."
        # each station once, though several commodities found it
        rows = [st for _, found_rows in found.by_good for st in found_rows]
        hidden = {(st.system.casefold(), st.station.casefold()) for st in rows if you.barred(st)}
        barred = len(hidden) if query.request.hide_barred else 0
        if barred:
            them = "stations left out: they" if barred != 1 else "station left out: it"
            message += f" {barred} {them} would refuse you docking."
        self._set_sell(
            "done", message, searched_load=sell_load(query.goods), goods=goods, best=best,
            max_ly=query.request.max_ly, searched=self.now().isoformat(timespec="seconds"),
            searched_from=origin,
        )  # fmt: skip

    def _ui_refined(self) -> UiRefined:
        e = self.earnings
        return ui_refined(e.refined, e.session_start, e.latest)

    def _ui_sell(self) -> UiSell:
        """The last search with the hold as it is now, and what you mined this session."""
        goods, lines = sell_hold(self.cargo)
        sell = self.sell
        changed = bool(sell.searched_load) and sell_load(goods) != sell.searched_load
        rough = self.cargo is not None and not self.cargo.detailed and bool(lines)
        return replace(sell, hold=lines, hold_rough=rough, changed=changed, refined=self._ui_refined())

    # --- best trade from a station (main thread) ----------------------------------------------------
    def _set_trade(self, status: TradeStatus, message: str, **changes: Any) -> None:
        self.trade = replace(
            self.trade, status=status, message=message, handled=self._trade_handled, **changes
        )

    def _trade_ship(self) -> tuple[GameState, int | None]:
        """The planning ship (the one the page plans with) and its hold in t (None = no ship known)."""
        s = self._plan_state()
        ship = None if s.in_other_ship else s.ship
        return s, (ship.cargo_capacity if ship is not None else None)

    def _ui_trade(self) -> UiTrade:
        """The card as shown: the form starts from the station you last docked at, your hold, your balance."""
        system, station = self.missions.docked
        _, hold = self._trade_ship()
        return replace(
            self.trade,
            docked_system=system,
            docked_station=station,
            hold=hold,
            balance=self.commander.credits,
        )

    def _start_trade(self, req: TradeRequest) -> None:
        """A new search replaces the one before; the trade shown stays until the new one finishes."""
        self.worker.cancel(TRADE_JOB)
        self._trade_job = None
        _, hold = self._trade_ship()
        cargo = req.cargo if req.cargo is not None else hold
        capital = req.capital if req.capital is not None else self.commander.credits
        if cargo is None:
            self._set_trade("error", "Your ship's cargo hold is not known yet: enter the tonnes to fill.")
            return
        if cargo <= 0:
            self._set_trade("error", "Your ship has no cargo racks: enter the tonnes to fill.")
            return
        if capital is None:
            self._set_trade("error", "Your balance is not known yet: enter the credits to spend.")
            return
        if capital <= 0:
            self._set_trade("error", "Your balance is 0 Cr: enter the credits to spend.")
            return
        client = self.client
        if client is None or not self.worker.alive:
            self._set_trade("error", "Online lookups are not running; restart EDMC.")
            return
        query = TradeQuery(req, cargo, capital)
        job_id = self.worker.submit(TRADE_JOB, lambda cancel: spansh.trade(client, query, cancel))
        self._trade_job = (job_id, query)
        self._set_trade("running", self._queued(f"Looking for the best trade from {req.station}…"))

    def _finish_trade(self, r: JobResult) -> None:
        if self._trade_job is None or self._trade_job[0] != r.job_id:
            return  # replaced meanwhile
        _, query = self._trade_job
        self._trade_job = None
        req = query.request
        if r.cancelled:
            self._set_trade("idle", "Cancelled.")
            return
        if r.error is not None or not isinstance(r.value, TradeFound):
            message = _failed("Spansh", r.error)
            if r.error and "find station" in r.error:  # also a station its trade router has no market for
                message = (
                    f"Spansh's trade router does not know {req.station} in {req.system}, or knows no market "
                    "there: check the station's name, or start from another station."
                )
            # the trade shown before started elsewhere: it no longer answers what was asked
            self._set_trade("error", message, **TRADE_CLEARED)
            return
        found = r.value
        searched = self.now().isoformat(timespec="seconds")
        if found.source is None or found.destination is None or not found.goods:
            self._set_trade(
                "done",
                f"No trade from {req.station} pays within {req.max_hop_ly:g} ly with these filters: try a "
                "longer distance, older data or a longer arrival distance.",
                **TRADE_CLEARED, cargo=query.cargo, capital=query.capital, searched=searched,
            )  # fmt: skip
            return
        load = sum(g.amount for g in found.goods)
        planning, _ = self._trade_ship()
        # with the load aboard and full tanks, less the safety margin, whatever range routes are planned
        # with: the empty-hold best case would count too few jumps for a full hold
        full = replace(planning, cargo=load).full_tank_range()
        laden = None if full is None else full * (1.0 - self.settings.margin_pct / 100.0)
        distance = round(found.distance_ly, 2)
        jumps = min_jumps(distance, laden) if laden is not None and laden > 0 else None
        names = " and ".join(g.name for g in found.goods)
        self._set_trade(
            "done",
            f"Buy {names} at {found.source.station}, sell at {found.destination.station} "
            f"({found.destination.system}): {found.total_profit:,} Cr profit.",
            source=ui_station(found.source), destination=ui_station(found.destination), distance_ly=distance,
            jumps=jumps, laden_ly=None if laden is None else round(laden, 2), goods=found.goods,
            cost=sum(g.amount * g.buy_price for g in found.goods), total_profit=found.total_profit,
            cargo=query.cargo, capital=query.capital, searched=searched,
        )  # fmt: skip

    # --- nearest station service (main thread) -----------------------------------------------------
    def _set_services(self, status: ServiceStatus, message: str, **changes: Any) -> None:
        self.services = replace(
            self.services, status=status, message=message, handled=self._services_handled, **changes
        )

    def _start_services(self, req: ServiceRequest) -> None:
        """Search from where you are now; a new search replaces the one before."""
        self.worker.cancel(SERVICES_JOB)
        self._services_job = None
        s = self.state
        if s.in_other_ship or s.star_pos is None or not s.system:
            self._set_services("error", "Where you are is not known yet: jump once in the game first.")
            return
        if req.economies() == ():
            self._set_services(
                "done", f"No station can have {what_asked(req)}: those kinds follow different economies.",
                **_asked(req), rows=(),
                source="", searched=self.now().isoformat(timespec="seconds"), searched_from=s.system,
                barred=0,
            )  # fmt: skip
            return
        client = self.client
        if client is None or not self.worker.alive:
            self._set_services("error", "Online lookups are not running; restart EDMC.")
            return
        query = ServiceQuery(req, s.system, s.address, s.star_pos)
        you = self._standings()
        job_id = self.worker.submit(
            SERVICES_JOB, lambda cancel: station_lookup.find(client, query, you, cancel)
        )
        self._services_job = (job_id, query)
        self._set_services(
            "running",
            self._queued(f"Looking for the nearest stations with {what_asked(req)} near {s.system}…"),
        )

    def _finish_services(self, r: JobResult) -> None:
        """Who may dock is decided now (the history pass may have finished meanwhile)."""
        if self._services_job is None or self._services_job[0] != r.job_id:
            return  # replaced meanwhile
        _, query = self._services_job
        self._services_job = None
        if r.cancelled:
            self._set_services("idle", "Cancelled.")
            return
        if r.error is not None or not isinstance(r.value, list):
            self._set_services("error", _failed("Spansh", r.error))
            return
        found: list[ServiceRow] = r.value
        req = query.request
        you = self._standings()
        kept, barred = split_barred(found, req, you)
        shown = kept[:STATIONS_SHOWN]
        if len(shown) < STATIONS_SHOWN:
            hidden = len(barred)
        else:
            reach = math.dist(query.star_pos, (shown[-1].x, shown[-1].y, shown[-1].z))
            hidden = sum(1 for b in barred if math.dist(query.star_pos, (b.x, b.y, b.z)) <= reach)
        n = len(shown)
        if n:
            message = f"The {n} nearest place{'s' if n != 1 else ''} with {what_asked(req)}, nearest first."
        else:
            fewer = "fewer services, " if req.services else ""
            message = (
                f"No station with {what_asked(req)} near you with these filters: "
                f"try {fewer}fleet carriers or settlements."
            )
        if hidden:
            them = "stations hidden: they" if hidden != 1 else "station hidden: it"
            message += f" {hidden} {them} would refuse you docking."
        self._set_services(
            "done", message, **_asked(req),
            rows=ui_service_rows(req.services, shown, you), source="Spansh",
            searched=self.now().isoformat(timespec="seconds"), searched_from=query.system, barred=hidden,
        )  # fmt: skip

    # --- nearest system by Powerplay power (main thread) ---------------------------------------------
    def _set_power(self, status: PowerStatus, message: str, **changes: Any) -> None:
        self.powerplay = replace(
            self.powerplay, status=status, message=message, handled=self._power_handled, **changes
        )

    def _start_power(self, req: PowerRequest) -> None:
        """Search from where you are now; a new search replaces the one before."""
        self.worker.cancel(POWER_JOB)
        self._power_job = None
        s = self.state
        if s.in_other_ship or s.star_pos is None or not s.system:
            self._set_power("error", "Where you are is not known yet: jump once in the game first.")
            return
        client = self.client
        if client is None or not self.worker.alive:
            self._set_power("error", "Online lookups are not running; restart EDMC.")
            return
        query = PowerQuery(req, s.system, s.star_pos)
        job_id = self.worker.submit(POWER_JOB, lambda cancel: spansh.power_systems(client, query, cancel))
        self._power_job = (job_id, query)
        self._set_power("running", self._queued(f"Looking for the nearest {describe(req)} near {s.system}…"))

    def _finish_power(self, r: JobResult) -> None:
        if self._power_job is None or self._power_job[0] != r.job_id:
            return  # replaced meanwhile
        _, query = self._power_job
        self._power_job = None
        req = query.request
        if r.cancelled:
            self._set_power("idle", "Cancelled.")
            return
        if r.error is not None or not isinstance(r.value, PowerFound):
            self._set_power("error", _failed("Spansh", r.error))
            return
        found = r.value
        rows = ui_power_rows(req.power, found.rows)
        nearest = min((math.dist(query.star_pos, (r.x, r.y, r.z)) for r in rows), default=None)
        message = found_message(req, len(rows), found.count, nearest)
        self._set_power(
            "done", message, power=req.power, mode=req.mode, state=req.state, rows=rows, count=found.count,
            source="Spansh", searched=self.now().isoformat(timespec="seconds"), searched_from=query.system,
        )  # fmt: skip

    def _commander_event(self, event: CommanderEvent) -> None:
        if self._commander_live is not None and not isinstance(event, CreditsKnown | LegalStateKnown):
            self._commander_live.append(event)
        self.commander = apply_commander(self.commander, event)

    def _finish_commander(self, r: JobResult) -> None:
        live, self._commander_live = self._commander_live or [], None
        if not r.ok or not isinstance(r.value, tuple):
            return
        found, last = r.value
        if not isinstance(found, Commander) or not isinstance(last, str):
            return
        # crimes and payments the pass already read (up to `last`) are not counted twice; balance and legal
        # state said live win over the pass's
        c = found
        for event in live:
            if isinstance(event, CrimeCommitted | DebtPaid | Died) and event.when and event.when <= last:
                continue
            c = apply_commander(c, event)
        now = self.commander
        self.commander = Commander(
            now.credits if now.credits is not None else c.credits, now.legal or c.legal, c.notoriety, c.debts
        )
        self.log.info(
            "commander: notoriety %s, %d factions with unpaid fines / bounties", c.notoriety, len(c.debts)
        )

    def _career_event(self, event: CareerEvent) -> None:
        if self._career_live is not None:
            self._career_live.append(event)
        self.career.add(event)

    def _finish_career(self, r: JobResult) -> None:
        live, self._career_live = self._career_live or [], None
        if not r.ok or not isinstance(r.value, Career):
            return
        c = r.value
        # the entries the pass already read (up to its newest) are not counted twice; one written in that
        # same second may be, which only the merits' own sum would feel (the game's total wins)
        for event in live:
            if event.when >= c.last:
                c.add(event)
        self.career = c
        self.log.info(
            "career: since %s, %d standings, pledged to %s", c.first[:10] or "-", len(c.standings),
            c.power or "no power",
        )  # fmt: skip

    def _earnings_event(self, event: MoneyEvent) -> None:
        if self._earnings_live is not None:
            self._earnings_live.append(event)
        self.earnings.add(event)

    def _finish_earnings(self, r: JobResult) -> None:
        live, self._earnings_live = self._earnings_live or [], None
        e = r.value if r.ok and isinstance(r.value, Earnings) else None
        if e is None:
            self.log.warning("earnings: the journals could not be read; only what came since is counted")
            return
        e.add_live(live)
        e.saw(self.earnings.latest)
        self.earnings = e
        self.log.info(
            "earnings: %d credit moves, %d bodies and %d species unsold", len(e.moves), len(e.bodies),
            len(e.species),
        )  # fmt: skip

    # --- the game's lifetime statistics (main thread) -----------------------------------------------
    def _finish_stats(self, r: JobResult) -> None:
        self._stats_reading = False
        if not r.ok or not isinstance(r.value, Stats):
            return
        s = r.value
        s.merge(self.stats)  # logins seen live meanwhile (one read twice is kept once)
        self.stats = s
        self.log.info(
            "stats: %d logins with statistics, newest %s; %d deaths",
            len(s.logins),
            s.last or "-",
            len(s.deaths.died),
        )

    def _ui_stats(self) -> UiStats:
        """Built again only when another login's statistics or a death came in (or the reading ended)."""
        key = (len(self.stats.logins), self.stats.last, self.stats.deaths.key, self._stats_reading)
        if key != self._ui_stats_for:
            self._ui_stats_cache = replace(ui_stats(self.stats), reading=self._stats_reading)
            self._ui_stats_for = key
        return self._ui_stats_cache

    # --- open missions (main thread) ----------------------------------------------------------------
    def _mission_event(self, event: MissionEvent) -> None:
        if self._missions_live is not None:
            self._missions_live.append(event)
        self.missions.add(event)

    def _finish_missions(self, r: JobResult) -> None:
        live, self._missions_live = self._missions_live or [], None
        if not r.ok or not isinstance(r.value, Missions):
            self.log.warning("missions: the journals could not be read; only missions taken since are known")
        else:
            m = r.value
            for event in live:  # the entries the pass already read are not applied twice
                if event.when >= m.last:
                    m.add(event)
            self.missions = m
            self.log.info("missions: %d open", len(m.open))
        self._want_places()

    def _want_places(self) -> None:
        """Ask Spansh where the missions' systems are that are not known yet (one search for all of them,
        cached a day); one lookup at a time, each name once until you jump again."""
        if self._places_job is not None or self.client is None or not self.worker.alive:
            return
        wanted = {m.system for m in self.missions.open.values() if m.system}
        names = sorted(wanted - self.places.keys() - self._places_asked)[: spansh.MAX_SEARCH_NAMES]
        if not names:
            return
        self._places_asked.update(names)
        client = self.client
        job = self.worker.submit(PLACES_JOB, lambda cancel: spansh.systems_by_name(client, names, cancel))
        self._places_job = (job, names)

    def _finish_places(self, r: JobResult) -> None:
        if self._places_job is None or self._places_job[0] != r.job_id:
            return
        asked = {name.casefold(): name for name in self._places_job[1]}
        self._places_job = None
        if r.cancelled:
            return
        if r.error is not None or not isinstance(r.value, tuple):
            self._places_error = _failed("Spansh", r.error)
            return
        self._places_error = ""
        for system in r.value:  # kept under the journal's spelling, whatever case Spansh answers in
            name = asked.get(system.name.casefold(), system.name)
            self.places[name] = (system.x, system.y, system.z)
        self._want_places()  # more names than one search takes

    def _start_hold(self) -> None:
        """Count the pledged power's systems by state, unless a count of it is fresh or on its way."""
        power = self.career.power
        if not power:
            return
        h = self.hold
        if h.power == power and (
            h.status == "running" or (h.status == "done" and time.monotonic() - self._hold_at < HOLD_TTL)
        ):
            return
        client = self.client
        if client is None or not self.worker.alive:
            self.hold = UiHold(power, "error", "Online lookups are not running; restart EDMC.")
            return

        def count(cancel: threading.Event) -> tuple[object, object]:
            counts = spansh.power_hold(client, power, cancel)
            try:  # every power's total is extra: its failure leaves your power's count standing
                powers: tuple[tuple[str, int], ...] = spansh.powers_held(client, cancel)
            except SourceError as exc:
                self.log.warning("counting every power's systems failed: %s", exc)
                powers = ()
            return counts, powers

        job_id = self.worker.submit(HOLD_JOB, count)
        self._hold_job = (job_id, power)
        self._hold_at = time.monotonic()
        self.hold = UiHold(power, "running", self._queued("Counting the systems every power holds…"))

    def _finish_hold(self, r: JobResult) -> None:
        if self._hold_job is None or self._hold_job[0] != r.job_id:
            return
        _, power = self._hold_job
        self._hold_job = None
        if r.cancelled:
            self.hold = UiHold(power)
            return
        if r.error is not None or not isinstance(r.value, tuple) or len(r.value) != 2:
            self.hold = UiHold(power, "error", _failed("Spansh", r.error))
            return
        counts, powers = r.value
        searched = self.now().isoformat(timespec="seconds")
        self.hold = UiHold(power, "done", hold_message(power, counts), counts, searched, powers)

    def _standing_event(self, event: object, when: str) -> None:
        """A live arrival listing factions, a mission ended, or a Powerplay pledge change."""
        if not isinstance(event, Arrived | PowerPledged | MissionEnded):
            return
        if self._standing_live is not None:
            self._standing_live.append((event, when))
        self._apply_standing(event, when)

    def _apply_standing(self, event: object, when: str) -> None:
        if isinstance(event, PowerPledged):
            self.power = event.power
        else:
            self._apply_to(self.factions, event, when)

    def _finish_standings(self, r: JobResult) -> None:
        live, self._standing_live = self._standing_live or [], None
        factions, power = r.value if r.ok and isinstance(r.value, tuple) else (None, None)
        if isinstance(factions, Factions):
            # the pass read the files at some moment while live entries kept coming: they are applied again,
            # a mission only when newer than the pass's newest (counted once); an arrival only overwrites
            for event, when in live:
                if when > factions.last or (isinstance(event, Arrived) and when >= factions.last):
                    self._apply_to(factions, event, when)
            self.factions = factions
        else:
            self.log.warning("standings: the journals could not be read; only factions met since are known")
        if isinstance(power, str):
            self.power = power
        for event, _when in live:
            if isinstance(event, PowerPledged):
                self.power = event.power
        reputation = self.factions.reputation()
        hostile = sum(1 for v in reputation.values() if standing(v) == "hostile")
        self.log.info(
            "standings: %d factions known (%d hostile to you), pledged to %s",
            len(reputation), hostile, self.power or "no power",
        )  # fmt: skip

    @staticmethod
    def _apply_to(factions: Factions, event: object, when: str) -> None:
        if isinstance(event, Arrived):
            factions.arrived(event, when)
        elif isinstance(event, MissionEnded):
            factions.mission(event)

    # --- the system you just arrived in (main thread) ----------------------------------------------
    def _start_arrival(self) -> None:
        """A new system: ask Spansh for the bodies it knows there (one search, cached for an hour)."""
        address, system = self.state.address, self.state.system
        if address is None or system is None or self.state.in_other_ship:
            self.worker.cancel(ARRIVAL_JOB)
            self._arrival_job = None
            self.arrival = Arrival()
            return
        # your scans of this system stay when it is asked again (the worker started after you arrived)
        here = self.arrival if self.arrival.address == address else arrive_here(address, system)
        client = self.client
        if client is None:  # asked once the worker starts (`start_worker`)
            self.arrival = replace(here, status="looking", message="")
            return
        if not self.worker.alive:
            stopped = "Online lookups are not running; restart EDMC."
            self.arrival = replace(here, status="error", message=stopped)
            return
        wait = "Waiting for the route search to finish." if self._scout_job is not None else ""
        self.arrival = replace(here, status="looking", message=wait)
        # a newer arrival cancels the lookup still running for the system before
        self._arrival_job = self.worker.submit(
            ARRIVAL_JOB, lambda cancel: spansh.system_bodies(client, address, cancel)
        )

    def _finish_arrival(self, r: JobResult) -> None:
        if self._arrival_job != r.job_id:
            return  # a lookup for a system you have left
        self._arrival_job = None
        if r.cancelled:
            return
        if r.error is not None or not isinstance(r.value, tuple):
            self.arrival = replace(self.arrival, status="error", message=_failed("Spansh", r.error))
            return
        self.arrival = replace(self.arrival, status="done", message="", known=r.value)

    def _survey(self, entry: dict[str, Any]) -> None:
        """An FSS / DSS result of the system you are in (kept while Spansh is still being asked)."""
        e = parse_survey(entry)
        if e is not None and not self.state.in_other_ship:
            self.arrival = survey(self.arrival, e)

    # --- following the route (main thread) ---------------------------------------------------------
    def _follow_new(self, route: UiRoute) -> None:
        """A finished search replaces the followed route; it starts past the system you are in."""
        self.scout = UiScout("done", "", regrade(route, self.explored.snapshot()), self._handled)
        self.follow_next = start_index(route, self._address())
        self._save_route()

    def _address(self) -> str | None:
        return None if self.state.address is None else str(self.state.address)

    def _arrived(self) -> None:
        route = self.scout.route
        if route is None:
            return
        n = arrive(route, self.follow_next, self._address())
        if n != self.follow_next:
            self.follow_next = n
            self._save_route()

    def _step_follow(self, by: int) -> None:
        route = self.scout.route
        if route is None or by not in (1, -1):
            return
        n = step(route, self.follow_next, by)
        if n != self.follow_next:
            self.follow_next = n
            self._save_route()

    def _read_nav_route(self, entry: dict[str, Any] | None) -> GameRoute | None:
        """The galaxy map's route from the game's NavRoute.json (read-only) — at start, or for a journal
        NavRoute entry that names no route; never an older file than the entry."""
        if self.journal_dir is None:
            return None
        path = self.journal_dir / "NavRoute.json"
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            self.log.warning("could not read %s: %s", path, exc)
            return None
        if not isinstance(doc, dict):
            return None
        if entry is not None and entry_time(doc) < entry_time(entry):
            self.log.info("%s is older than the NavRoute entry; route not taken", path)
            return None
        return parse_nav_route(doc)

    def _nav_route(self, entry: dict[str, Any]) -> None:
        game = self._read_nav_route(entry) if nav_route_pending(entry) else parse_nav_route(entry)
        if game is not None:
            self.game = game

    def _take_game_route(self) -> None:
        """The page's "Follow it": the galaxy map's route becomes the followed route."""
        game = self.game
        if game is None or len(game.points) < 2:
            return
        finished = datetime.now(UTC).isoformat(timespec="seconds")
        self._follow_new(game_route(game, self._plan_state().full_tank_range() or 0.0, finished))
        self.log.info("following the galaxy map's route to %s", game.points[-1].name)

    def _load_route(self) -> None:
        """The route followed before EDMC closed, where it was left; nothing on a missing or bad file."""
        try:
            doc = json.loads(self.route_path.read_text(encoding="utf-8"))
            if doc.get("version") != ROUTE_DOC_VERSION or not isinstance(doc.get("next"), int):
                raise ValueError("unknown route file version")
            route = route_from_doc(doc["route"])
        except FileNotFoundError:
            return
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            self.log.warning("could not read %s (%s); no route to follow", self.route_path, exc)
            return
        self.scout = UiScout("done", "", regrade(route, self.explored.snapshot()), 0)
        self.follow_next = step(route, doc["next"], 0)

    def _save_route(self) -> None:
        route = self.scout.route
        if route is None:
            return
        doc = {"version": ROUTE_DOC_VERSION, "next": self.follow_next, "route": asdict(route)}
        try:
            self.route_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.route_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, self.route_path)
        except OSError:
            self.log.exception("could not save the route to %s", self.route_path)

    # --- ships (main thread) -----------------------------------------------------------------------
    def _plan_state(self) -> GameState:
        return plan_state(self.state, self.fleet, self.plan_pick)

    def _pick_ship(self, ship_id: int | None) -> None:
        """The page picked the ship to plan with; unknown ids and the ship you sit in mean "no pick"."""
        current = self.state.ship.ship_id if self.state.ship is not None else None
        self.plan_pick = ship_id if ship_id in self.fleet.ships and ship_id != current else None
        if any(p.found for p in self.gear.places):
            self._start_gear_jumps()  # the gear card's jumps follow the planning ship

    def _load_fleet(self) -> Fleet:
        try:
            return from_doc(json.loads(self.fleet_path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return Fleet()
        except (OSError, ValueError):
            self.log.warning("could not read %s; reading the journal history again", self.fleet_path)
            return Fleet()

    def _set_fleet(self, fleet: Fleet) -> None:
        if fleet is self.fleet:
            return
        self.fleet = fleet
        if self.plan_pick not in fleet.ships:
            self.plan_pick = None
        try:
            self.fleet_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.fleet_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(to_doc(fleet), indent=1) + "\n", encoding="utf-8")
            os.replace(tmp, self.fleet_path)
        except OSError:
            self.log.exception("could not save the fleet to %s", self.fleet_path)

    def _fleet_event(self, event: object, when: str) -> None:
        match event:
            case LoadoutChanged():
                if not self.state.in_other_ship:  # crew in someone else's ship: never remember the host's
                    self._set_fleet(remember(self.fleet, event, when))
            case ShipSold():
                self._set_fleet(forget_sold(self.fleet, event.ship_id, when))
            case FleetListed():
                current = self.state.ship.ship_id if self.state.ship is not None else None
                self._set_fleet(keep_owned(self.fleet, event.ship_ids, current, when))

    def _finish_fleet_scan(self, found: object, newest: object) -> None:
        if not isinstance(found, ScanResult) or not isinstance(newest, float):
            return
        self._set_fleet(merge_scan(self.fleet, found, newest))
        self.log.info("fleet: %d ship(s) remembered after reading journal history", len(self.fleet.ships))
        last = self.fleet.last_flown
        if self.state.ship is None and last is not None:
            self.state = apply(self.state, last.loadout)

    # --- what you explored (main thread) ----------------------------------------------------------
    def _explored_event(self, event: object) -> None:
        """A body mapped, a species analysed, a sale or a death, live from the journal."""
        if not isinstance(event, _EXPLORE):
            return
        located: Located = (event, "" if self.state.in_other_ship else self.state.system or "")
        if self._explore_live is not None:
            self._explore_live.append(located)
        if self.explored.apply([located]):
            self._explored_changed()

    def _finish_explored_scan(self, r: JobResult) -> None:
        live, self._explore_live = self._explore_live or [], None
        if not r.ok or not isinstance(r.value, tuple):
            return
        found, newest = r.value
        # The pass read the files at some moment while live events kept coming: those are applied again
        # after it, so a death seen live stays applied after older entries of the same file.
        changed = self.explored.apply(found, scanned_mtime=newest)
        changed = self.explored.apply(live) or changed
        mapped, analysed = self.explored.counts()
        self.log.info("explored: %d bodies mapped and %d species analysed are yours", mapped, analysed)
        if changed:
            self._explored_changed()

    def _explored_changed(self) -> None:
        """Mark the shown tour with what is done now (no new search: the stops stay)."""
        route = self.scout.route
        if route is None or route.mode != "tour":
            return
        marked = regrade(route, self.explored.snapshot())
        if marked != route:
            self.scout = replace(self.scout, route=marked)
            self._save_route()

    def _set_scout(self, status: ScoutStatus, message: str) -> None:
        """The followed route stays until a new search finishes: a failed or cancelled search keeps it."""
        self.scout = UiScout(status, message, self.scout.route, self._handled)

    # --- the page's API (request threads) ---------------------------------------------------------
    def api_get(self, path: str, params: dict[str, str]) -> ApiReply | None:
        if path == "/api/commodities":  # the commodity card's suggestions: the bundled names
            return 200, {"names": list(COMMODITIES)}
        if path == "/api/stations":
            return self._market_stations(params.get("system", "").strip()[:100])
        if path != "/api/systems":
            return None
        text = params.get("q", "").strip()[:100]
        client = self.client
        if client is None:
            return 503, {"error": "online lookups are not running"}
        with self._suggest_lock:
            self._suggest_cancel.set()  # a newer keystroke wins
            cancel = self._suggest_cancel = threading.Event()
        try:
            hits = spansh.system_names(client, text, cancel)
        except Cancelled:
            hits = ()
        except SourceError as exc:
            return 502, {"error": f"Spansh: {explain(exc)}"}
        if cancel.is_set():  # replaced while waiting its turn, or while Spansh answered
            return 200, {"q": text, "systems": [], "superseded": True}
        rows = [{"name": h.name, "id64": str(h.id64), "x": h.x, "y": h.y, "z": h.z} for h in hits]
        return 200, {"q": text, "systems": rows}

    def _market_stations(self, system: str) -> ApiReply:
        """GET /api/stations?system=: the stations with a market there, for the trade card's station choice
        (server thread, like the name suggestions; a newer request does not cancel an older one)."""
        client = self.client
        if client is None:
            return 503, {"error": "online lookups are not running"}
        if not system:
            return 400, {"error": "name a system"}
        try:
            stations = spansh.market_stations(client, system, threading.Event())
        except SourceError as exc:
            return 502, {"error": f"Spansh: {explain(exc)}"}
        if stations is None:
            return 404, {"error": f"Spansh knows no system spelled '{system}': pick it from the suggestions"}
        rows = [
            {"name": st.name, "type": st.type, "pad": st.pad, "arrival_ls": st.arrival_ls} for st in stations
        ]
        return 200, {"system": system, "stations": rows}

    def api_post(self, path: str, doc: object) -> ApiReply | None:
        if path == "/api/scout":
            prefs = parse_scout_prefs(doc)
            if isinstance(prefs, str):
                return 400, {"error": prefs}
            self._commands.put(("scout", prefs))
            return 202, {"accepted": "scout"}
        if path == "/api/scout/cancel":
            self._commands.put(("cancel", None))
            return 202, {"accepted": "cancel"}
        if path == "/api/plan-ship":
            raw = doc.get("ship_id", "?") if isinstance(doc, dict) else "?"
            if raw is not None and not (
                isinstance(raw, str) and raw.isascii() and raw.isdigit() and len(raw) <= 20
            ):
                return 400, {"error": "ship_id must be a ShipID string or null"}
            self._commands.put(("plan-ship", None if raw is None else int(raw)))
            return 202, {"accepted": "plan-ship"}
        if path == "/api/gear":
            req = _gear_request(doc)
            if isinstance(req, str):
                return 400, {"error": req}
            self._commands.put(("gear", req))
            return 202, {"accepted": "gear"}
        if path == "/api/market":
            market = parse_market_request(doc)
            if isinstance(market, str):
                return 400, {"error": market}
            self._commands.put(("market", market))
            return 202, {"accepted": "market"}
        if path == "/api/trade":
            trade = parse_trade_request(doc)
            if isinstance(trade, str):
                return 400, {"error": trade}
            self._commands.put(("trade", trade))
            return 202, {"accepted": "trade"}
        if path == "/api/sell":
            sell = parse_sell_request(doc)
            if isinstance(sell, str):
                return 400, {"error": sell}
            self._commands.put(("sell", sell))
            return 202, {"accepted": "sell"}
        if path == "/api/services":
            service = parse_service_request(doc)
            if isinstance(service, str):
                return 400, {"error": service}
            self._commands.put(("services", service))
            return 202, {"accepted": "services"}
        if path == "/api/flash":
            flash = parse_flash(doc)
            if isinstance(flash, str):
                return 400, {"error": flash}
            self._commands.put(("flash", flash))
            return 202, {"accepted": "flash"}
        if path == "/api/update-check":
            check = parse_update_check(doc)
            if isinstance(check, str):
                return 400, {"error": check}
            self._commands.put(("update-check", check))
            return 202, {"accepted": "update-check"}
        if path == "/api/career":
            self._commands.put(("career", None))
            return 202, {"accepted": "career"}
        if path == "/api/powerplay":
            power = parse_power_request(doc)
            if isinstance(power, str):
                return 400, {"error": power}
            self._commands.put(("powerplay", power))
            return 202, {"accepted": "powerplay"}
        if path == "/api/follow":
            if isinstance(doc, dict) and doc.get("take") == "game":
                self._commands.put(("follow-game", None))
                return 202, {"accepted": "follow-game"}
            by = doc.get("step") if isinstance(doc, dict) else None
            if type(by) is not int or by not in (1, -1):
                return 400, {"error": "step must be 1 (skip) or -1 (back)"}
            self._commands.put(("follow", by))
            return 202, {"accepted": "follow"}
        return None

    # --- hooks -----------------------------------------------------------------------------------
    def on_journal(self, entry: dict[str, Any], edmc_state: dict[str, Any] | None) -> bool:
        self.game_seen = True
        when = entry_time(entry)
        was = self.state.address
        if entry.get("event") == "StartUp" and edmc_state is not None:
            # EDMC started mid-session: rebuild from its cumulative state.
            for ev in events_from_edmc_state(edmc_state):
                self.state = apply(self.state, ev)
                self._fleet_event(ev, when)
            self.cargo = cargo_from_edmc_state(edmc_state) or self.cargo
            for cev in parse_commander({"event": "LoadGame", "Credits": edmc_state.get("Credits")}):
                self._commander_event(cev)
            if when:
                self.earnings.saw(when)
        else:
            event = parse_event(entry)
            if event is not None:
                self.state = apply(self.state, event)
                self._fleet_event(event, when)
                self._explored_event(event)
                self._standing_event(event, when)
            for cev in parse_commander(entry):
                self._commander_event(cev)
            for career_event in parse_career(entry):
                self._career_event(career_event)
            for money_event in parse_money(entry):
                self._earnings_event(money_event)
            mission_event = parse_mission(entry)
            if mission_event is not None:
                self._mission_event(mission_event)
                if isinstance(mission_event, MissionEnded):
                    self._standing_event(mission_event, when)
            statistics = parse_statistics(entry)
            if statistics is not None:
                self.stats.add(statistics)
            death = parse_death(entry)
            if death is not None:
                self.stats.deaths.add(death)
            self._cargo(entry, edmc_state)
            if when:
                self.earnings.saw(when)
            self._survey(entry)
            if (
                edmc_state is not None
                and not self.state.in_other_ship
                and edmc_ship_differs(edmc_state, self.state.ship)
            ):
                # EDMC knows a ship we never got: its own ship-loadout export failed on that `Loadout`, so
                # the entry never reached plugins, but EDMC's state already holds the ship.
                loadout = loadout_from_edmc_state(edmc_state)
                if loadout is not None:
                    self.log.info(
                        "ship taken from EDMC's state: %s id %s", loadout.ship_type, loadout.ship_id
                    )
                    self.state = apply(self.state, loadout)
                    self._fleet_event(loadout, when)
        ship = self.state.ship
        if self.plan_pick is not None and ship is not None and ship.ship_id == self.plan_pick:
            self.plan_pick = None  # you now sit in the picked ship: plan with its real cargo and fuel
        self._nav_route(entry)  # a route plotted or cleared in the galaxy map; any other entry: nothing
        if self.state.address != was:
            self._arrived()
            self._start_arrival()
            if self.state.system and self.state.star_pos is not None and not self.state.in_other_ship:
                self.places[self.state.system] = self.state.star_pos
            self._places_asked.clear()  # a failed lookup is tried again after a jump (cached answers stay)
        self._want_places()
        if self._gear_replot and self.state.address is not None and self.state.ship is not None:
            self._start_gear_jumps()
        elif self._gear_ship_changed():
            self._start_gear_jumps()  # another ship now: its own jump counts (plots are cached)
        return self._refresh()

    def _cargo(self, entry: dict[str, Any], edmc_state: dict[str, Any] | None) -> None:
        """The game rewrites Cargo.json on every change of the hold and writes a `Cargo` entry that lists it
        only at login; EDMC reads the file for every other one."""
        if entry.get("event") != "Cargo" or entry.get("Vessel", "Ship") != "Ship":
            return
        held = parse_cargo(entry)
        if held is None and edmc_state is not None:
            held = cargo_from_edmc_state(edmc_state)
        if held is not None:
            self.cargo = held

    def on_dashboard(self, entry: dict[str, Any]) -> bool:
        status = {**entry, "event": "Status"}
        for cev in parse_commander(status):  # balance and legal state: also on foot
            self._commander_event(cev)
            if isinstance(cev, CreditsKnown) and (when := entry_time(status)):
                self.earnings.live = (when, cev.credits)
        ev = parse_event(status)
        if ev is not None:
            self.state = apply(self.state, ev)
        return self._refresh(throttle=True)

    def set_settings(self, settings: Settings) -> bool:
        self.settings = settings
        try:
            save_settings(self.settings_path, settings)
        except OSError:
            self.log.exception("could not save settings to %s", self.settings_path)
        return self._refresh()

    # --- internals -------------------------------------------------------------------------------
    def _refresh(self, *, throttle: bool = False) -> bool:
        """Rebuild the UI state; True when the panel text changed (the page may change without it)."""
        ui = ui_state(
            self.state,
            self.settings,
            game_seen=self.game_seen,
            scout=self.scout,
            fleet=self.fleet,
            pick=self.plan_pick,
            follow=ui_follow(self.scout.route, self.follow_next),
            game_route=game_offer(self.game, self.scout.route, self.follow_next),
            gear=self.gear,
            market=self.market,
            trade=self._ui_trade(),
            services=self.services,
            powerplay=self.powerplay,
            pledged=self.power or "",
            commander=ui_commander(self.commander),
            career=ui_career(self.career, self.now(), self.hold),
            factions=self._ui_factions(),
            earnings=ui_earnings(
                self.earnings,
                self.now().astimezone(),
                self._earnings_live is None,
                lambda t: t.astimezone(),
            ),
            arrival=ui_arrival(self.arrival, self.settings.scout, self.explored.snapshot()),
            missions=self._ui_missions(),
            stats=self._ui_stats(),
            sell=self._ui_sell(),
            update=self.update if self.settings.update_check else None,
            notices=tuple(self.notices.values()),
            panel_note=self.panel_note,
        )
        if ui == self.ui:
            return False
        text_changed = ui.panel != self.ui.panel
        self.ui = ui
        self.seq += 1
        self.snapshot = self._snapshot_bytes()
        now = time.monotonic()
        if not throttle or now - self._last_dump >= DASHBOARD_DUMP_INTERVAL:
            self._last_dump = now
            self._dump(ui.panel)
        return text_changed

    def _ui_factions(self) -> UiFactions:
        s = self.state
        if self._standing_live is not None:  # the history pass is still reading: only a live warning
            return UiFactions(warning=hostile_here(self.factions, s.address))
        return ui_factions(self.factions, None if s.in_other_ship else s.star_pos, s.address)

    def _ui_missions(self) -> UiMissions:
        s = self.state
        here = None if s.in_other_ship else s
        here_name = here.system if here is not None else None
        missing = sorted(
            {m.system for m in self.missions.open.values() if m.system}
            - self.places.keys()
            - {here_name or ""}
        )
        lookup = ""
        if missing and self._places_job is None:
            unknown = [n for n in missing if n in self._places_asked]
            if self._places_error:
                lookup = self._places_error
            elif self.client is None or not self.worker.alive:
                lookup = "Online lookups are not running: distances are not known."
            elif unknown:
                lookup = f"Spansh does not know {', '.join(unknown)}."
        return ui_missions(
            self.missions,
            self.now(),
            known=self._missions_live is None,
            places=self.places,
            star_pos=here.star_pos if here is not None else None,
            here=here.system if here is not None else None,
            range_ly=routing_range(self._plan_state(), self.settings),
            lookup=lookup,
            local=lambda t: t.astimezone(),
        )

    def _snapshot_bytes(self) -> bytes:
        doc = {
            "version": __version__,
            "seq": self.seq,
            "updated": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "state": asdict(self.ui),
        }
        return (json.dumps(doc, ensure_ascii=False) + "\n").encode("utf-8")

    def _dump(self, text: PanelText) -> None:
        s = self.state
        route = routing_range(s, self.settings)
        doc = {
            "updated": datetime.now(UTC).isoformat(timespec="seconds"),
            "version": __version__,
            "panel": asdict(text),
            "web": self.server_url,
            "state": {
                "system": s.system,
                "address": s.address,
                "star_pos": s.star_pos,
                "ship": s.ship_display,
                "fsd": s.ship.fsd.item if s.ship and s.ship.fsd else None,
                "fuel_main": s.fuel_main,
                "cargo": s.cargo,
                "in_other_ship": s.in_other_ship,
                "range_now": s.current_range(),
                "range_full": s.full_tank_range(),
                "range_best": s.best_case_range(),
                "game_max_jump_range": s.ship.game_max_jump_range if s.ship else None,
                "routing_range": route,
                "ship_id": s.ship.ship_id if s.ship else None,
            },
            "fleet": {
                "ships": [f"{x.loadout.ship_id}:{x.loadout.ship_type}" for x in self.fleet.ordered()],
                "scanned_mtime": self.fleet.scanned_mtime,
                "plan_pick": self.plan_pick,
                "plan_routing": self.ui.plan.routing,
            },
            "settings": asdict(self.settings),
            "scout": {
                "status": self.scout.status,
                "message": self.scout.message,
                "stops": None if self.scout.route is None else len(self.scout.route.stops),
                "mode": None if self.scout.route is None else self.scout.route.mode,
                "waypoints": None if self.scout.route is None else len(self.scout.route.waypoints),
            },
            "follow": asdict(self.ui.follow),
            "arrival": {
                "status": self.arrival.status,
                "system": self.arrival.system,
                "known": None if self.arrival.known is None else len(self.arrival.known),
                "scanned": len(self.arrival.scanned),
                "signals": len(self.arrival.signals),
                "worth": self.ui.arrival.worth,
            },
            "explored": dict(zip(("mapped", "analysed"), self.explored.counts(), strict=True))
            | {"scanned_mtime": self.explored.scanned_mtime, "pass_running": self._explore_live is not None},
            "worker": {
                "alive": self.worker.alive,
                "jobs_done": self.jobs_done,
                "last_job": None
                if self.last_job is None
                else {
                    "name": self.last_job.name,
                    "ok": self.last_job.ok,
                    "cancelled": self.last_job.cancelled,
                    "error": self.last_job.error,
                },
            },
        }
        try:
            self.debug_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.debug_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
            os.replace(tmp, self.debug_path)
            self._dump_failed = False
        except OSError as exc:
            if not self._dump_failed:  # log once per failure streak, not on every tick
                self.log.warning("could not write %s: %s", self.debug_path, exc)
            self._dump_failed = True
