// ED Multitool page: polls GET /api/state and renders it; the scout form posts to /api/scout. No build step,
// no dependencies.
"use strict";

/**
 * Reply of GET /api/systems?q=… (built in `edmultitool.plugin.ScoutPlugin.api_get`); `superseded` = a newer
 * request for the same page replaced this one before Spansh answered.
 * @typedef {{q: string, systems: {name: string, id64: string, x: number, y: number, z: number}[],
 *   superseded?: boolean}} SystemsReply
 *
 * Reader for the server's reply — mirrors `edmultitool.core.view.UiState` (grow both additively).
 * @typedef {{scout: string, ship: string, range: string, routing: string, next: string, here: string,
 *   earned: string, missions: string, note: string, update: string, warning: string}} PanelText
 *   note = the EDMC panel's own line (page on another port, no browser…); the page does not show it
 * @typedef {{now: ?number, full: ?number, best: ?number, routing: ?number}} UiRanges  ly, 2 decimals
 * @typedef {{
 *   from_system: string, to_system: string, radius_ly: number, max_distance_ls: number, max_stops: number,
 *   riches: boolean, bio: boolean, min_scan: number, min_mapping: number, min_species: number,
 *   min_bio_body: number, plotter: "exact"|"neutron", max_detour_ly: number, planets: "any"|"prime"
 * }} ScoutPrefs  mirrors `edmultitool.core.settings.ScoutPrefs`; also the body of POST /api/scout.
 * Neither riches nor bio = route only, plotted by `plotter`. planets "prime" = the scan search keeps only
 * Earth-like, water and ammonia worlds and terraformables.
 * @typedef {{name: string, value: number, done: boolean}} UiSpecies  done = analysed by you already
 * @typedef {{
 *   id64: string, name: string, short: string, subtype: string, distance_ls: number, scan_value: number,
 *   mapping_value: number, terraformable: boolean, scan_hit: boolean, bio_value: number,
 *   species: UiSpecies[], wealth: number, done: boolean
 * }} UiBody  done = mapped by you already; bio_value / wealth count only what is left to earn
 * @typedef {{
 *   id64: string, name: string, leg_ly: number, leg_jumps: number, along_ly: number, off_line_ly: number,
 *   scan_value: number, mapping_value: number, bio_value: number, bodies: UiBody[], wealth: number,
 *   replaces: string, detour_ly: number, spent: boolean
 * }} UiStop  replaces = the spent stop this one took the place of; spent = nothing left to earn here
 * @typedef {{
 *   id64: string, name: string, leg_ly: number, jumps: number, neutron: boolean, refuel: boolean,
 *   scoopable: ?boolean
 * }} UiWaypoint  one row of a route-only plot; `neutron` = supercharge at this star (the next jump is boosted)
 * @typedef {{
 *   from_name: string, to_name: string, range_ly: number, total_ly: number, final_leg_ly: number,
 *   final_leg_jumps: number, from_cache: boolean, finished: string, stops: UiStop[],
 *   mode: "tour"|"exact"|"neutron"|"game", range_basis: "routing"|"full", total_jumps: number,
 *   waypoints: UiWaypoint[], from_id64: string, to_id64: string, min_bio_body: number,
 *   worth_map: number, worth_bio: number, worth_scan: number
 * }} UiRoute  tour = `stops`; exact / neutron = route only, `waypoints` (start first, destination last).
 * `worth_map` / `worth_bio` = what all the tour's stops are still worth, Cr (mapped bodies / species);
 * `worth_scan` = those bodies only FSS-scanned (mapping includes the scan, so it is not added).
 * `range_basis` names the UiRanges field `range_ly` is compared with for the re-plan notice.
 * @typedef {{status: "idle"|"running"|"done"|"error", message: string, route: ?UiRoute, handled: number}} UiScout
 * `handled` counts the scout / cancel commands the server has carried out.
 * @typedef {{id: string, label: string, fsd: string, full: ?number, current: boolean, seen: string}} UiShip
 * one remembered ship; `full` = ly with full tanks and an empty hold (the same footing for every ship)
 * @typedef {{ship_id: ?string, ship: ?string, routing: ?number, full: ?number, text: string}} UiPlan
 * the ship routes are planned with; `ship_id` null = the ship you sit in. POST /api/plan-ship
 * {ship_id: string|null} picks one (not remembered across EDMC restarts).
 * @typedef {{
 *   active: boolean, next_index: number, count: number, finished: boolean, next_name: string,
 *   next_kind: ""|"start"|"stop"|"waypoint"|"destination", next_wealth: number, stops_left: number,
 *   ly_left: number, jumps_left: number, value_ahead: number, text: string
 * }} UiFollow  progress along `scout.route`. `next_index` counts the start (0), then the stops (tour) or
 * the waypoints after the start (plots), then the destination; `count` = all of them; `next_index` ==
 * `count` = arrived. POST /api/follow {step: 1|-1} skips / goes back one point; {take: "game"} follows the
 * route plotted in the galaxy map.
 * @typedef {{offer: boolean, to_name: string, jumps: number, plotted: string}} UiGameRoute  that route;
 * `offer` = the followed route does not already lead there.
 * @typedef {{
 *   name: string, kind: ""|"suit"|"weapon", klass: string, grade: ?number, slots: ?number, mods: string[],
 *   empty: boolean, count: number, price: ?number, taken: boolean, stars: number, why: string, suits: string[]
 * }} UiGearItem  one offered suit / weapon; stars 1-5 (0 = cannot rate), `why` = what the rating rests on,
 * `suits` = who it is for (combat / salvage / exobiology / stealth)
 * @typedef {{
 *   found: boolean, system: string, system_id64: string, station: string, station_type: string, pad: string,
 *   arrival_ls: ?number, text: string, note: string, items: UiGearItem[], stars: number,
 *   x: number, y: number, z: number, distance_ly: ?number, jumps: ?number, jumps_note: string
 * }} UiGearPlace  `distance_ly` from where you are now; `jumps` plotted from `UiGear.jumps_from`
 * @typedef {{show: boolean, system: string, station: string, gear: string}} UiGearFields  the separate fields
 * @typedef {{
 *   places: UiGearPlace[], message: string, decoded: string, jumps_from: string, jumps_from_id64: string
 * }} UiGearFind  an earlier find, as it was (jumps counted from where you were then)
 * @typedef {{
 *   status: "idle"|"running"|"done"|"error", message: string, places: UiGearPlace[], fields: UiGearFields,
 *   jumps_status: "idle"|"running"|"done"|"error", jumps_from: string, jumps_from_id64: string,
 *   handled: number, decoded: string, earlier: UiGearFind[]
 * }} UiGear  the gear post decoded last, and up to two finds before it (newest first). POST /api/gear {text} decodes a pasted post, {system, station, gear}
 * the separate fields, {clear: true} empties the card; `handled` counts those requests.
 * @typedef {{
 *   system: string, system_id64: string, station: string, station_type: string, pad: string,
 *   arrival_ls: ?number, carrier: boolean, faction: string,
 *   standing: ""|"hostile"|"unfriendly"|"neutral"|"cordial"|"friendly"|"allied", barred: boolean, power: string,
 *   price: number, quantity: number, updated: string, x: number, y: number, z: number,
 *   distance_ly: ?number, jumps: ?number
 * }} UiMarketRow  one station's market for the commodity: `price` Cr/t you pay (buy) or get (sell), `quantity`
 * t of stock (buy) or demand (sell), `updated` ISO time of the data; `barred` = it would refuse you docking
 * (Hostile faction, another power's Stronghold Carrier, a carrier closed to visitors), `power` = a Stronghold
 * Carrier's power
 * @typedef {{
 *   status: "idle"|"running"|"done"|"error", message: string, commodity: string, side: "buy"|"sell",
 *   rows: UiMarketRow[], source: string, searched: string, searched_from: string, barred: number,
 *   note: string, handled: number
 * }} UiMarket  the commodity search shown last. POST /api/market {commodity, side, min_qty, pad, carriers,
 * max_age_days, hide_barred}; GET /api/commodities → {names: string[]} the names it takes.
 * @typedef {{name: string, grade: number}} UiModule
 * @typedef {{
 *   name: string, ship: boolean, system: string, system_id64: string, base: string, base_type: string,
 *   modules: UiModule[], distance_ly: ?number, jumps: ?number
 * }} UiEngineer  one engineer from the bundled catalog; `modules` = what a ship engineer improves, highest
 * grade first
 * @typedef {{
 *   system: string, system_id64: string, station: string, station_type: string, pad: ""|"S"|"M"|"L",
 *   arrival_ls: ?number, kind: string, updated: string, x: number, y: number, z: number,
 *   distance_ly: ?number, jumps: ?number, faction: string, standing: string, barred: boolean,
 *   carrier: boolean, power: string
 * }} UiServiceRow  `kind` = with a trader / broker asked, its kind ("Raw", "Guardian", both joined by " · ");
 * "" otherwise
 * @typedef {{
 *   status: "idle"|"running"|"done"|"error", message: string, asked: string[], trader_kind: string,
 *   broker_kind: string, pad: ""|"S"|"M"|"L", carriers: boolean, settlements: boolean, hide_barred: boolean,
 *   rows: UiServiceRow[],
 *   source: string, searched: string, searched_from: string, handled: number, barred: number
 * }} UiServices  the station search shown last. POST /api/services {pad, carriers, settlements, hide_barred,
 * services: Spansh's service names (every one wanted), trader_kind, broker_kind}
 * @typedef {{system: string, system_id64: string, station: string, arrival_ls: ?number, updated: string}} UiTradeStation
 * @typedef {{
 *   name: string, amount: number, buy_price: number, sell_price: number, supply: number, demand: number,
 *   profit: number, total_profit: number
 * }} TradeGood  one commodity of the trade; profit = Cr/t, total_profit = Cr for `amount`
 * @typedef {{
 *   status: "idle"|"running"|"done"|"error", message: string, source: ?UiTradeStation,
 *   destination: ?UiTradeStation, distance_ly: ?number, jumps: ?number, laden_ly: ?number, goods: TradeGood[],
 *   cost: number, total_profit: number, cargo: number, capital: number, searched: string, handled: number,
 *   docked_system: string, docked_station: string, hold: ?number, balance: ?number
 * }} UiTrade  the best trade found last; docked_* / hold / balance = what the form starts from. POST
 * /api/trade {system, station, cargo, capital, max_hop_ly, max_arrival_ls, max_age_days, pad, carriers,
 * planetary} (cargo / capital null = the ship's hold / your balance)
 * @typedef {{
 *   system: string, system_id64: string, station: string, station_type: string, pad: string,
 *   arrival_ls: ?number, carrier: boolean, faction: string, standing: string, barred: boolean, power: string,
 *   updated: string, x: number, y: number, z: number, distance_ly: ?number, jumps: ?number
 * }} UiSellPlace  a station that buys from you; `barred` = it would refuse you docking
 * @typedef {{place: UiSellPlace, price: number, demand: number, value: number}} UiSellOffer  price Cr/t,
 *   demand t, value = Cr for your whole load of it
 * @typedef {{name: string, tonnes: number, mined: boolean, offers: UiSellOffer[], barred: number}} UiSellGood
 *   one commodity of the hold: its best offers (best price first); mined = refined this session; barred =
 *   stations left out that would refuse you docking
 * @typedef {{name: string, tonnes: number, price: number, value: number}} UiSellLine
 * @typedef {{place: UiSellPlace, lines: UiSellLine[], total: number, left: string[], richer: ?UiSellBest}} UiSellBest
 *   one stop: the best-paying station that buys all of the load, else the one paying most for part (left = the
 *   rest); never one that would refuse you docking. richer = a station paying more for part of the load
 * @typedef {{name: string, tonnes: number, note: string}} UiHoldLine  note "" = searched in full
 * @typedef {{tonnes: number, by: [string, number][], per_hour: ?number, since: string}} UiRefined  mined this session
 * @typedef {{
 *   status: "idle"|"running"|"done"|"error", message: string, hold: UiHoldLine[], hold_rough: boolean,
 *   searched_load: [string, number][],
 *   changed: boolean, goods: UiSellGood[], best: ?UiSellBest, max_ly: number, searched: string,
 *   searched_from: string, refined: UiRefined, handled: number
 * }} UiSell  the "Sell cargo" card: the hold now (live) and the last search for it; changed = the hold is not
 *   what it searched for. POST /api/sell {max_ly, pad, carriers, max_age_days, hide_barred}
 * @typedef {{
 *   system: string, system_id64: string, held_by: string, others: string[], state: string,
 *   progress_pct: ?number, reinforcement: ?number, undermining: ?number, updated: string,
 *   powers: string[], x: number, y: number, z: number, distance_ly: ?number, jumps: ?number,
 *   effect: "reinforce"|"undermine"|"acquire"|"out-of-reach"|""
 * }} UiPowerRow  one system; `held_by` = the controlling power ("" = none), `others` = the other powers present
 * besides the one searched, `powers` = all of them; `effect` = what your merits do there for your pledged
 * power ("" = not pledged)
 * @typedef {{
 *   status: "idle"|"running"|"done"|"error", message: string, power: string, mode: string, state: string,
 *   rows: UiPowerRow[], count: number, source: string, searched: string, searched_from: string, handled: number
 * }} UiPowerplay  the Powerplay system search shown last. POST /api/powerplay {power, mode: "held"|"others"|"acquire", state}
 * @typedef {{level: "danger"|"warn", text: string, detail: string}} UiWarning
 * @typedef {{name: string, value: number, tier: string, next_tier: string, to_next: number, tier_pct: number}} UiStanding
 *   a superpower: value -100..100; next_tier "" = Allied already
 * @typedef {{
 *   name: string, standing: UiStanding, hostile: boolean, state: string, system: string, seen: string,
 *   controls: boolean, influence: ?number, nearest: string, nearest_ly: ?number, places: number, missions: number
 * }} UiFaction  a minor faction as at your last visit where it is present; `nearest` = the nearest system you
 *   saw it in (`nearest_ly` null = your position is not known); `influence` in % there
 * @typedef {{known: boolean, rows: UiFaction[], neutral: number, warning: string}} UiFactions  rows = standing
 *   not 0 (or missions done), best first; neutral = met at exactly 0 and not listed; warning = hostile where you are
 * @typedef {{title: string, unlocks: string}} UiRankStep  one rank of a ladder, and what reaching it opens
 * @typedef {{kind: string, rank: number, top: number, title: string, next_title: string, progress: number,
 *   ladder: UiRankStep[], how: string, note: string}} UiRank  `how` = what moves you up
 *   a rank the game keeps (kind = its journal name: Combat … CQC); next_title "" = the top rank
 * @typedef {{
 *   power: string, known: boolean, merits: ?number, week: number, week_start: string, rank: ?number,
 *   next_rank_merits: ?number, since: string
 * }} UiPledge  power "" = not pledged; known false = no Powerplay entry at all; rank worked out from merits
 * @typedef {{power: string, status: "idle"|"running"|"done"|"error", message: string, counts: [string, number][], searched: string,
 *   powers: [string, number][]}} UiHold  powers = all 12 with the systems each holds, most first
 *   how many systems the pledged power holds, by state. POST /api/career {} asks (when the tab is shown)
 * @typedef {{day: string, values: (?number)[]}} UiPoint
 * @typedef {{start: string, merits: number}} UiWeek  a Powerplay week (from Thursday 07:00 UTC)
 * @typedef {{
 *   known: boolean, since: string, standings: UiStanding[], ranks: UiRank[], pledge: UiPledge, hold: UiHold,
 *   standing_history: UiPoint[], navy_history: UiPoint[], weeks: UiWeek[]
 * }} UiCareer  history values: standings in Federation/Empire/Alliance/Independent order, navies in
 *   Federation/Empire order as rank + progress / 100; weeks = the last 12 since the first
 *   with merits, oldest first
 * @typedef {{
 *   short: string, subtype: string, distance_ls: number, terraformable: boolean, mapping_value: number,
 *   bio_value: number, species: [string, number][], signals: number, unknown: number, genera: string[],
 *   new: boolean, first: boolean, worth: number
 * }} UiSpot  a body worth something in the system you are in: mapping_value / bio_value = what counts
 * (0 = not counted); unknown = biological signals no known species accounts for; new = found by your scan,
 * unknown to Spansh; first = your scan says nobody discovered it before you
 * @typedef {{
 *   status: "none"|"looking"|"done"|"error", system: string, message: string, known: number, new: number,
 *   body_count: ?number, all_found: boolean, unknown_system: boolean, first: number, spots: UiSpot[],
 *   map_value: number, map_count: number, bio_value: number, bio_count: number, unknown_bio: number,
 *   yours: number, worth: number, best: number, text: string
 * }} UiArrival  the "This system" card (`edmultitool.core.arrival.UiArrival`); text = the panel's Here: row
 * @typedef {{kind: string, label: string, session: number, today: number, week: number}} UiMoney  one row, Cr;
 *   kind "total" closes each list, "net" = earned minus spent
 * @typedef {{when: string, credits: number}} UiBalance  one point of the balance line, oldest first
 * @typedef {{name: string, value: ?number}} UiCarriedSpecies  value null = not in the catalogue
 * @typedef {{
 *   known: boolean, session_start: string, session_hours: number, running: boolean, per_hour: ?number,
 *   week_start: string, earned: UiMoney[], spent: UiMoney[], net: UiMoney, data_value: number,
 *   data_bodies: number, data_systems: number, data_since: string, bio_value: number,
 *   bio_species: UiCarriedSpecies[], bio_since: string, died: string, lost: number, balance: UiBalance[],
 *   text: string
 * }} UiEarnings  the Earnings tab (`edmultitool.core.earnings.UiEarnings`); known false = journals not read yet;
 *   died "" = no death this week; text = the panel's Earned: row
 * @typedef {{
 *   id: string, kind: string, title: string, faction: string, system: string, station: string, expiry: string,
 *   reward: number, wing: boolean, taken: string, done: boolean, here: boolean, distance_ly: ?number,
 *   jumps: ?number
 * }} UiMission  one open mission; expiry "" = no limit known; done = redirected, hand it in at system /
 *   station; jumps = the fewest at the routing range
 * @typedef {{known: boolean, missions: UiMission[], reward: number, lookup: string, text: string}} UiMissions
 *   the Missions tab (`edmultitool.core.missions.UiMissions`), soonest to end first; known false = journals
 *   not read yet; lookup = why some distances are missing; text = the panel's Missions: row
 * @typedef {{key: string, label: string, value: number, unit: string, week: ?number, derived: boolean}} UiStatRow
 *   unit "cr" | "cr/h" | "ly" | "m" | "s" | "ratio" | "" (a count); week = grown since `UiStats.week_from` (null = no
 *   login that old); derived = worked out from the game's totals
 * @typedef {{key: string, title: string, rows: UiStatRow[], zeros: number}} UiStatCard  zeros = figures at 0, left out
 * @typedef {{when: string, value: number}} UiStatPoint  one login's total
 * @typedef {{key: string, label: string, unit: string, value: number, week: ?number, points: UiStatPoint[]}} UiStatHeadline
 * @typedef {{label: string, value: number}} UiStatBar  credits
 * @typedef {{
 *   known: boolean, reading: boolean, count: number, first: string, at: string, week_from: string, headlines: UiStatHeadline[],
 *   profits: UiStatBar[], spending: UiStatBar[], cards: UiStatCard[], empty: string[], deaths: UiDeaths
 * }} UiStats  the Stats tab (`edmultitool.core.stats.UiStats`): the game's lifetime statistics at the newest
 *   login; empty = titles of groups with nothing in them yet; deaths = read one by one (the totals count none)
 * @typedef {{name: string, what: string, rank: string, station: boolean}} UiKiller  what = the ship or the station
 *   type; station = its defences killed you (no rank)
 * @typedef {{when: string, killers: UiKiller[], outcome: string, cost: number}} UiDeath  killers empty = the game
 *   named none; outcome "rebuy" | "free" | "bankrupt" | another game word | "" (no resurrection read yet)
 * @typedef {{count: number, paid: number, rows: UiDeath[]}} UiDeaths  paid = credits to come back; rows newest first
 * @typedef {{credits: ?number, warnings: UiWarning[]}} UiCommander  balance in millions (1 decimal); warnings
 *   only when there is one (legal state here, notoriety, unpaid fines / bounties)
 * @typedef {{
 *   game_seen: boolean, system: ?string, address: ?string, in_other_ship: boolean, ship: ?string,
 *   ranges: UiRanges, route_basis: "full"|"now"|"best", margin_pct: number, panel: PanelText,
 *   scout_prefs: ScoutPrefs, scout: UiScout, fleet: UiShip[], plan: UiPlan, follow: UiFollow,
 *   game_route: UiGameRoute, recent_to: string[], gear: UiGear, market: UiMarket, trade: UiTrade, engineers: UiEngineer[],
 *   services: UiServices, ship_pad: ""|"S"|"M"|"L", powerplay: UiPowerplay, pledged: string, powers: string[],
 *   notices: string[], commander: UiCommander, career: UiCareer, factions: UiFactions, arrival: UiArrival, flash: boolean,
 *   flash_over: number, earnings: UiEarnings, missions: UiMissions, stats: UiStats, sell: UiSell,
 *   update: {version: string, url: string}, update_check: boolean
 * }} UiState  recent_to = destinations scouted, newest first; ship_pad = the pad the planning ship needs;
 * pledged = your Powerplay power ("" = none / not known yet); powers = the 12 powers; notices = standing
 * problems shown above everything (the journal folder unreadable…); update.version = "" when no newer
 * version is known, update_check = the setting
 * @typedef {{version: string, seq: number, updated: string, state: UiState}} StateReply
 * `address` and every `id64` are decimal strings: id64 can exceed 2^53, which a JS number cannot hold
 * exactly, so compare them as strings, never as numbers.
 */

const POLL_MS = 1000;
const SUGGEST_DELAY_MS = 300;
const SUGGEST_MIN_CHARS = 2;

/** Debug hook for checks without screenshots: last reply, fetch counters, last error, scout traffic. */
const debug = {
  tab: "", // the tab shown
  reply: /** @type {?StateReply} */ (null),
  fetches: 0,
  failures: 0,
  error: "",
  posts: /** @type {{path: string, status: number, body: unknown}[]} */ ([]),
  flashes: 0, // window flashes on arriving somewhere rich
  copied: /** @type {?{name: string, ok: boolean}} */ (null), // the last "Copy" click
  follow: { rows: 0, passed: 0, hidden: 0, next: "" }, // what the route table shows of the progress
  suggest: { fetches: 0, aborted: 0, stale: 0, last: /** @type {?{q: string, names: string[]}} */ (null) },
  view: { order: "", show: "", shown: 0, rows: /** @type {string[]} */ ([]) }, // the tour table as ordered
  best: { stop: "", body: "" }, // the id64s framed in the tour table: the most valuable system and body
  gear: { renders: 0, places: 0, items: 0, earlier: 0, copied: /** @type {?{name: string, ok: boolean}} */ (null) },
  // the commodity card: names loaded for the suggestions, the last search sent, the table as shown
  market: {
    names: 0,
    sent: /** @type {?object} */ (null),
    renders: 0,
    rows: /** @type {{system: string, station: string, age: string, step: number, cells: string[]}[]} */ ([]),
    copied: /** @type {?{name: string, ok: boolean}} */ (null),
  },
  // the best-trade card: the last search sent, the two legs and the table as shown
  trade: {
    sent: /** @type {?object} */ (null),
    renders: 0,
    stations: /** @type {string[]} */ ([]), // the station choice as filled last
    legs: /** @type {string[]} */ ([]),
    rows: /** @type {string[][]} */ ([]),
    total: "",
    copied: /** @type {?{name: string, ok: boolean}} */ (null),
  },
  // the sell-cargo card: the last search sent, the hold, the offers and the one-stop total as shown
  sell: {
    sent: /** @type {?object} */ (null),
    renders: 0,
    hold: /** @type {string[][]} */ ([]),
    rows: /** @type {string[][]} */ ([]),
    best: /** @type {string[][]} */ ([]),
    total: "",
    richer: "",
    refined: "",
    rough: false, // the hold is known only from EDMC's summary (the card says so)
    copied: /** @type {?{name: string, ok: boolean}} */ (null),
  },
  // the engineers card (filtered on the page) and the services card, as shown
  engineers: {
    renders: 0,
    matched: 0,
    rows: /** @type {{name: string, cells: string[]}[]} */ ([]),
    copied: /** @type {?{name: string, ok: boolean}} */ (null),
  },
  services: {
    sent: /** @type {?object} */ (null),
    renders: 0,
    hidden: 0,
    rows: /** @type {{system: string, station: string, cells: string[]}[]} */ ([]),
    copied: /** @type {?{name: string, ok: boolean}} */ (null),
  },
  // the Commander tab as shown: rows, pledge facts, the hold line, chart points; POSTs asking for the count
  career: {
    renders: 0,
    asked: 0,
    standings: /** @type {{name: string, value: number, tier: string, next: string}[]} */ ([]),
    ranks: /** @type {{kind: string, title: string, next: string, stage: string, bar: string}[]} */ ([]),
    pledge: /** @type {Record<string, string>} */ ({}),
    hold: "",
    powers: /** @type {string[]} */ ([]), // every power as listed, "*" = yours
    charts: /** @type {{standing: number, navy: number, weeks: string[]}} */ ({ standing: 0, navy: 0, weeks: [] }),
  },
  // the Minor factions card as shown: the summary, the warning, each row's cells; the last name copied
  factions: {
    renders: 0,
    sum: "",
    warning: "",
    rows: /** @type {{name: string, cells: string[]}[]} */ ([]),
    copied: /** @type {?{name: string, ok: boolean}} */ (null),
  },
  // the Earnings tab as shown: each row's cells, the facts, the unsold species
  earnings: {
    renders: 0,
    rows: /** @type {{kind: string, label: string, cells: string[], values: number[]}[]} */ ([]),
    session: "",
    unsold: /** @type {Record<string, string>} */ ({}),
    species: /** @type {string[]} */ ([]),
    died: "",
    balance: /** @type {{points: number, first: string, last: string, top: string}} */ ({ points: 0, first: "", last: "", top: "" }),
    hover: /** @type {?{when: string, credits: number, text: string}} */ (null), // the point the pointer marks
  },
  // the Missions tab as shown: the status line, each row's cells (time left as counted), the last copy
  missions: {
    renders: 0,
    ticks: 0, // Time left recounts
    status: "",
    rows: /** @type {{id: string, cells: string[], soon: boolean}[]} */ ([]),
    copied: /** @type {?{name: string, ok: boolean}} */ (null),
  },
  // the Stats tab as shown: each headline (value, change, points in its line), each card's rows
  stats: {
    renders: 0,
    lines: /** @type {{key: string, value: string, delta: string, points: number}[]} */ ([]),
    cards: /** @type {{key: string, rows: string[]}[]} */ ([]),
    deaths: /** @type {{when: string, who: string, cost: string}[]} */ ([]),
  },
  // the (i) bubble: whose text it shows, the text, pinned by a click, where it sits in the window
  info: {
    shows: 0,
    for: "",
    text: "",
    pinned: false,
    box: /** @type {?{left: number, top: number, right: number, bottom: number}} */ (null),
  },
  // the Powerplay card: the last search sent, the table as shown
  powerplay: {
    sent: /** @type {?object} */ (null),
    renders: 0,
    rows: /** @type {{system: string, age: string, step: number, cells: string[], effect: string}[]} */ ([]),
    copied: /** @type {?{name: string, ok: boolean}} */ (null),
  },
};
window.edmultitool = debug;

const $ = (id) => /** @type {HTMLElement} */ (document.getElementById(id));
const input = (id) => /** @type {HTMLInputElement} */ (document.getElementById(id));

/** @param {?number} ly */
function fmt(ly) {
  return ly === null || ly === undefined ? "—" : ly.toFixed(2);
}

const CR = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
// distances in whole light years; a jump range keeps two decimals (RANGE: the ship's reach, not a distance)
const LY = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const RANGE = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** @param {number} n */
const cr = (n) => CR.format(n);

/**
 * One rule for every number field: a comma only ever groups thousands ("100,000"), a dot is the decimal
 * point ("1.5M"). Anything else with a comma ("1,5M", "25,5") is refused, never guessed.
 * Spaces and underscores are ignored ("1 000 000"); k / M suffixes multiply.
 * @param {string} text @returns {number} NaN when it is not a number
 */
function parseNum(text) {
  const t = text.trim().toLowerCase().replace(/[\s_]/g, "");
  const m = /^(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?([km]?)$/.exec(t);
  if (!m) return NaN;
  const mult = m[3] === "m" ? 1e6 : m[3] === "k" ? 1e3 : 1;
  // rounded to 6 decimals so "1.005k" is exactly 1005, not 1004.9999999999999
  return Math.round(Number(m[1].replace(/,/g, "") + (m[2] || "")) * mult * 1e6) / 1e6;
}

/** @param {string} text whole credits */
const parseAmount = (text) => Math.round(parseNum(text));

/**
 * @param {string} tag @param {string} [cls] @param {string} [text]
 * @returns {HTMLElement}
 */
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

// --- live state --------------------------------------------------------------------------------------

/** Standing problems above everything; redrawn only when they change. @param {string[]} notices */
function renderNotices(notices) {
  const box = $("notices");
  const key = JSON.stringify(notices);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.replaceChildren(...notices.map((n) => el("li", "notice", n)));
  box.hidden = notices.length === 0;
}

let warningsKey = "";

/** Balance next to the ship; warning chips in the top row and on the route card. @param {UiState} s */
function renderCommander(s) {
  const c = s.commander || { credits: null, warnings: [] };
  $("credits").textContent =
    c.credits === null ? "—" : `${c.credits.toLocaleString("en-US", { minimumFractionDigits: 1 })}M`;
  const key = JSON.stringify(c.warnings);
  if (key === warningsKey) return;
  warningsKey = key;
  for (const id of ["warnings", "route-warnings"]) {
    const box = $(id);
    box.replaceChildren(
      ...c.warnings.map((w) => {
        const chip = el("li", `warning ${w.level}`, w.text);
        chip.title = w.detail;
        return chip;
      }),
    );
    box.hidden = c.warnings.length === 0;
  }
  $("warnings-item").hidden = c.warnings.length === 0;
}

// --- the system you are in ---------------------------------------------------------------------------

let hereKey = "";

/** @param {UiArrival | undefined} a */
function renderHere(a) {
  a = a || /** @type {UiArrival} */ ({ status: "none", spots: [] });
  const key = JSON.stringify(a);
  if (key === hereKey) return;
  hereKey = key;
  $("here-name").textContent = a.system ? `· ${a.system}` : "";
  const head = $("here-head");
  const status = $("here-status");
  const facts = $("here-facts");
  facts.replaceChildren();
  status.hidden = true;
  head.className = "here-head";
  head.replaceChildren();
  if (a.status === "none") {
    head.textContent = "Jump somewhere and this card tells you what the system is worth.";
    head.classList.add("quiet");
  } else if (a.worth) {
    const steps = a.bio_value > a.map_value ? VALUE_STEPS.stopBio : VALUE_STEPS.stopMapping;
    head.append(el("strong", valueTier(a.worth, steps), `${money(a.worth)} Cr`), " to earn here");
  } else if (a.status === "looking") {
    head.textContent = "Asking Spansh what it knows here…";
    head.classList.add("quiet");
  } else if (a.status === "error") {
    head.textContent = "Could not ask Spansh about this system.";
    head.classList.add("quiet");
  } else if (a.unknown_system && !a.new) {
    head.textContent = "Spansh knows no body here: first discovery possible.";
    head.classList.add("first");
  } else if (a.unknown_bio) {
    head.textContent = "Life nobody has sampled yet: its worth shows once someone does.";
  } else {
    head.textContent = a.yours ? "Everything over your minimums is yours already." : "Nothing here over your minimums.";
    head.classList.add("quiet");
  }
  const ownOnly = a.status !== "done" && a.spots.length ? " Only your own scans are counted so far." : "";
  const said = a.message || (a.status === "looking" && ownOnly ? "Still asking Spansh." : "");
  if (said) {
    status.textContent = said + ownOnly;
    status.hidden = false;
  }
  if (a.status !== "none") {
    /** @type {string[]} */
    const parts = [];
    if (a.map_count) parts.push(`map ${a.map_count} ${a.map_count === 1 ? "body" : "bodies"} ${money(a.map_value)}`);
    if (a.bio_count) parts.push(`exobiology on ${a.bio_count} ${a.bio_count === 1 ? "body" : "bodies"} ${money(a.bio_value)}`);
    if (a.unknown_bio) parts.push(`life nobody sampled on ${a.unknown_bio}`);
    if (a.status === "done")
      parts.push(a.known ? `Spansh knows ${a.known} ${a.known === 1 ? "body" : "bodies"}` : "unknown to Spansh");
    if (a.new) parts.push(`${a.new} new from your scans`);
    if (a.first) parts.push(`${a.first} undiscovered`);
    if (a.body_count !== null) parts.push(a.all_found ? `all ${a.body_count} scanned` : `${a.body_count} in the system`);
    if (a.yours) parts.push(`${a.yours} yours already`);
    facts.textContent = parts.join(" · ");
  }
  const spots = a.spots;
  $("here-wrap").hidden = spots.length === 0;
  $("here-rows").replaceChildren(...spots.map(spotRow));
}

const UPDATE_HIDDEN_KEY = "edmultitool.update-hidden"; // the version whose strip was closed (per viewer)
let updateCheckFilled = false; // the checkbox was filled in from the server once (the user's edits win after)
let updateClosedFor = ""; // the version closed in this page (kept even when storage is blocked)

/**
 * "Version X is out": a quiet strip above everything, closable until the next version; and the footer's
 * switch for the check itself.
 * @param {UiState} s
 */
function renderUpdate(s) {
  if (!updateCheckFilled) {
    updateCheckFilled = true;
    input("update-check").checked = s.update_check;
  }
  const version = s.update ? s.update.version : "";
  let hidden = "";
  try {
    hidden = localStorage.getItem(UPDATE_HIDDEN_KEY) || "";
  } catch {
    // storage blocked: the strip simply cannot be remembered as closed
  }
  const strip = $("update");
  strip.hidden = !version || hidden === version || updateClosedFor === version;
  if (!version) return;
  const link = /** @type {HTMLAnchorElement} */ ($("update-link"));
  link.textContent = `Version ${version} is out: what's new and the download`;
  link.href = s.update.url;
  strip.dataset.version = version;
}

let flashFilled = false; // the flash setting was filled in from the server once (the user's edits win after)
let flashSeen = /** @type {?string} */ (null); // the system the flash was decided for (null = page just opened)

/**
 * Flash the window once per system when one body there is worth at least the set amount; not for the
 * system the page opened in.
 * @param {UiState} s
 */
function renderFlash(s) {
  if (!flashFilled) {
    flashFilled = true;
    input("flash-on").checked = s.flash;
    input("flash-over").value = String(Math.round(s.flash_over / 1e6));
  }
  const a = s.arrival;
  if (!a || !a.system) return;
  if (flashSeen === null) flashSeen = a.system;
  if (flashSeen === a.system || !s.flash || !a.best || a.best < s.flash_over) return;
  flashSeen = a.system;
  flashWindow();
}

function flashWindow() {
  debug.flashes += 1;
  document.body.classList.remove("flash");
  void document.body.offsetWidth; // restart the animation
  document.body.classList.add("flash");
}

function saveFlash() {
  const over = Math.round(Number(input("flash-over").value));
  if (!(over >= 1 && over <= 1000)) return;
  post("/api/flash", { on: input("flash-on").checked, over: over * 1e6 });
}

/** @param {UiSpot} b */
function spotRow(b) {
  const tr = el("tr", "spot-row");
  const name = el("td", "body");
  const line = el("div", "bline");
  line.append(el("span", "bname", b.short));
  if (b.terraformable) line.append(el("span", "badge terra", "terraformable"));
  if (b.first) {
    const mark = el("span", "badge first", "undiscovered");
    mark.title = "Your scan says nobody discovered this body before you";
    line.append(mark);
  } else if (b.new) {
    const mark = el("span", "badge new", "new");
    mark.title = "Found by your scan; Spansh did not know it";
    line.append(mark);
  }
  name.append(line, el("div", `btype ${CLASS_COLOURS[b.subtype] || ""}`, b.subtype));
  const arrival = el("td", `n ${costTier(b.distance_ls, COST_STEPS.arrival)}`, CR.format(Math.round(b.distance_ls)));
  const map = el("td", `n strong ${valueTier(b.mapping_value, VALUE_STEPS.mapping)}`, b.mapping_value ? money(b.mapping_value) : "");
  const bio = el("td", "bio");
  if (b.bio_value) {
    bio.append(el("div", `btotal ${valueTier(b.bio_value, VALUE_STEPS.bioBody)}`, `${money(b.bio_value)} Cr`));
    const ul = el("ul", "species");
    for (const [sp, value] of b.species) {
      const li = el("li", `sp ${valueTier(value, VALUE_STEPS.species)}`);
      li.title = `${sp}: ${cr(value)} Cr`;
      li.append(el("span", "sp-name", sp), el("span", "sp-value", money(value)));
      ul.append(li);
    }
    bio.append(ul);
  }
  if (b.unknown) {
    const what = b.genera.length ? `: ${b.genera.join(", ")}` : "";
    const life = el("div", "unsampled", `${b.unknown} ${b.unknown === 1 ? "signal" : "signals"} nobody sampled${what}`);
    life.title = "Biological signals no known species accounts for: their worth shows once sampled";
    bio.append(life);
  }
  tr.append(name, arrival, map, bio);
  return tr;
}

/** @param {StateReply} reply */
function render(reply) {
  const s = reply.state;
  renderNotices(s.notices || []);
  renderCommander(s);
  renderHere(s.arrival);
  renderFlash(s);
  renderUpdate(s);
  $("scout").textContent = s.panel.scout;
  $("ship").textContent = s.panel.ship;
  $("range-now").textContent = fmt(s.ranges.now);
  $("range-full").textContent = fmt(s.ranges.full);
  $("range-best").textContent = fmt(s.ranges.best);
  $("routing").textContent = s.plan.text;
  renderPicker(s);
  $("version").textContent = `v${reply.version}`;
  $("about-version").textContent = `v${reply.version}`;
  $("updated").textContent = `updated ${new Date(reply.updated).toLocaleTimeString()}`;
  document.body.dataset.seq = String(reply.seq);
  document.title = s.system ? `ED Multitool · ${s.system}` : "ED Multitool";
  if (!formFilled) fillForm(s.scout_prefs);
  input("f-from").placeholder = s.system ? `current: ${s.system}` : "current system";
  renderScout(s.scout);
  renderFollow(s);
  renderGameOffer(s);
  renderReplan(s);
  renderGear(s.gear, s.address, s.plan.ship ?? "your current ship");
  renderMarket(s);
  renderTrade(s);
  renderSell(s);
  renderEngineers(s);
  renderServices(s);
  renderPowerplay(s);
  renderCareer(s);
  renderFactions(s.factions);
  renderEarnings(s.earnings);
  renderMissions(s);
  renderStats(s.stats);
}

/**
 * A route keeps the range it was planned with. When the ship's routing range has moved since (margin,
 * cargo, fuel, another ship), say so and offer to plan again — never automatically: with "current fuel &
 * cargo" the range changes on every scoop, and each re-plan is two Spansh searches.
 * @param {UiState} s
 */
function renderReplan(s) {
  const box = $("replan");
  const shown = s.scout.status === "done" ? s.scout.route : null;
  const route = shown && shown.mode !== "game" ? shown : null; // the game plotted that one, not us
  const now = route && route.range_basis === "full" ? s.plan.full : s.plan.routing;
  const stale = route !== null && now !== null && Math.abs(now - route.range_ly) >= 0.01;
  box.hidden = !stale;
  if (stale && route && now !== null) {
    const basis = route.range_basis === "full" ? " (full tank)" : "";
    $("replan-text").textContent =
      `This route was planned at ${route.range_ly.toFixed(2)} ly per jump${basis}; your range is now ${now.toFixed(2)} ly.`;
  }
}

/**
 * The ship picker: "the ship I'm in" plus every other remembered ship. Rebuilt only when the list
 * changes, and never while it is open or focused (a rebuild would close it under the mouse).
 * @param {UiState} s
 */
function renderPicker(s) {
  const sel = /** @type {HTMLSelectElement} */ ($("plan-ship"));
  const current = s.fleet.find((x) => x.current);
  const others = s.fleet.filter((x) => !x.current);
  const here = current ? `The ship I'm in (${shipOption(current)})` : "The ship I'm in";
  const rows = [["", here], ...others.map((x) => [x.id, shipOption(x)])];
  const key = JSON.stringify(rows);
  // never rebuild or reset it under the player's hand (a focused select in a focused window)
  const inHand = document.hasFocus() && document.activeElement === sel;
  if (sel.dataset.key !== key && !inHand) {
    const opt = ([v, t]) => Object.assign(document.createElement("option"), { value: v, textContent: t });
    sel.replaceChildren(...rows.map(opt));
    sel.dataset.key = key;
  }
  // a posted pick shows until the server's state carries it (or 3 s pass: the server refused it)
  if (pickPending !== null && ((s.plan.ship_id ?? "") === pickPending || Date.now() - pickSent > 3000)) {
    pickPending = null;
  }
  if (!inHand && pickPending === null) sel.value = s.plan.ship_id ?? "";
  sel.disabled = others.length === 0;
  const note = $("plan-note");
  note.hidden = s.plan.ship_id === null;
  note.textContent = s.plan.ship
    ? `Planning with ${s.plan.ship} — full tanks, empty hold. Not the ship you are in.`
    : "";
}

/** @param {UiShip} x */
function shipOption(x) {
  return `${x.label} · ${fmt(x.full)} ly`;
}

/** value the page last posted, until the server's state shows it (keeps the select from jumping back) */
let pickPending = /** @type {?string} */ (null);
let pickSent = 0;

async function onPickShip() {
  const sel = /** @type {HTMLSelectElement} */ ($("plan-ship"));
  pickPending = sel.value;
  pickSent = Date.now();
  sel.blur(); // let the list follow the game again (boarding or selling the picked ship ends the pick)
  const r = await post("/api/plan-ship", { ship_id: sel.value === "" ? null : sel.value });
  if (!r.ok) {
    pickPending = null;
    showStatus(r.error, "error");
  }
}

async function onReplan() {
  const prefs = debug.reply ? debug.reply.state.scout_prefs : null;
  if (!prefs) return;
  localStatus = "Sending…";
  showStatus(localStatus, "running");
  awaitHandled = debug.reply ? debug.reply.state.scout.handled : 0;
  const r = await post("/api/scout", prefs);
  if (!r.ok) {
    awaitHandled = -1;
    localStatus = r.error;
    showStatus(r.error, "error");
  }
}

/** @param {"live"|"down"} state @param {string} text */
function setConn(state, text) {
  const el = $("conn");
  el.dataset.state = state;
  el.textContent = text;
  document.body.dataset.stale = String(state !== "live");
}

async function poll() {
  debug.fetches += 1;
  try {
    // The timeout keeps one stalled request from stopping the polling loop for good.
    const resp = await fetch("/api/state", { cache: "no-store", signal: AbortSignal.timeout(3000) });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    /** @type {StateReply} */
    const reply = await resp.json();
    if (!debug.reply || reply.seq !== debug.reply.seq || reply.updated !== debug.reply.updated) {
      render(reply);
    }
    debug.reply = reply;
    debug.error = "";
    setConn("live", "live");
  } catch (err) {
    debug.failures += 1;
    debug.error = String(err);
    setConn("down", "EDMC not reachable");
  } finally {
    fitBalance();
    setTimeout(poll, POLL_MS);
  }
}

// --- scout form --------------------------------------------------------------------------------------

let formFilled = false;
/** A local message (bad field, "Sending…", failed post) shown until the server reports something new. */
let localStatus = "";
let lastScoutKey = "";
/** The server's command count our last accepted post must exceed before its answer is on screen. */
let awaitHandled = -1;

const AMOUNTS = /** @type {const} */ ([
  ["f-min-scan", "min_scan"],
  ["f-min-mapping", "min_mapping"],
  ["f-min-species", "min_species"],
  ["f-min-bio-body", "min_bio_body"],
]);

/** @param {ScoutPrefs} p */
function fillForm(p) {
  formFilled = true;
  input("f-from").value = p.from_system;
  input("f-to").value = p.to_system;
  const plan = p.riches && p.bio ? "both" : p.riches ? "riches" : p.bio ? "bio" : "route";
  radio("plan", plan).checked = true;
  radio("plotter", p.plotter).checked = true;
  syncPlan();
  input("f-radius").value = String(p.radius_ly);
  input("f-maxdist").value = String(p.max_distance_ls);
  input("f-stops").value = String(p.max_stops);
  input("f-detour").value = String(p.max_detour_ly);
  select("f-planets").value = p.planets;
  for (const [id, key] of AMOUNTS) input(id).value = cr(p[key]);
}

/** @param {string} id */
const select = (id) => /** @type {HTMLSelectElement} */ ($(id));

/**
 * Presets set the whole search (plan, planets, Search and Minimum values); From, To and the detour stay
 * as you had them. You still press Scout. Fields a preset does not name take these values — the same as
 * core ScoutPrefs' defaults (change both together).
 * @type {[string, string][]}
 */
const PRESET_BASE = /** @type {[string, string][]} */ ([
  ["f-radius", "25"],
  ["f-maxdist", "50,000"],
  ["f-stops", "10"],
  ["f-min-scan", "0"],
  ["f-min-mapping", "1M"],
  ["f-min-species", "0"],
  ["f-min-bio-body", "1M"],
]);
/** @type {Record<string, {label: string, plan: string, planets: "any"|"prime", fields: [string, string][]}>} */
const PRESETS = {
  elw: { label: "ELW hunt", plan: "riches", planets: "prime", fields: [] },
  bio: { label: "Bio rich", plan: "bio", planets: "any", fields: [["f-min-species", "2M"], ["f-min-bio-body", "10M"]] },
  quick: {
    label: "Quick trip",
    plan: "both",
    planets: "any",
    fields: [["f-stops", "5"], ["f-radius", "10"], ["f-maxdist", "5,000"]],
  },
};

/** @param {string} name */
function applyPreset(name) {
  const p = PRESETS[name];
  if (!p) return;
  radio("plan", p.plan).checked = true;
  syncPlan();
  select("f-planets").value = p.planets;
  for (const [id, value] of [...PRESET_BASE, ...p.fields]) input(id).value = value;
  localStatus = `${p.label} filled in: press Scout.`;
  showStatus(localStatus, "idle");
}

/** @param {string} name @param {string} value */
const radio = (name, value) =>
  /** @type {HTMLInputElement} */ (document.querySelector(`input[name="${name}"][value="${value}"]`));

/** @param {string} name @returns {string} the checked radio's value */
function checked(name) {
  const r = /** @type {?HTMLInputElement} */ (document.querySelector(`input[name="${name}"]:checked`));
  return r ? r.value : "";
}

/** Route only shows the plotter choice; a search shows its own fields instead. */
function syncPlan() {
  const routeOnly = checked("plan") === "route";
  $("fs-plotter").hidden = !routeOnly;
  $("fs-search").hidden = routeOnly;
  $("fs-minimums").hidden = routeOnly;
}

/** @returns {ScoutPrefs | string} the form as the server wants it, or what is wrong with it */
function readForm() {
  const plan = checked("plan");
  const radius = parseNum(input("f-radius").value);
  const maxdist = parseNum(input("f-maxdist").value);
  const stops = parseNum(input("f-stops").value);
  const detour = parseNum(input("f-detour").value);
  if (!input("f-to").value.trim()) return "Choose a destination.";
  /** @type {Record<string, number>} */
  const amounts = {};
  const problems = [];
  if (!(radius >= 1 && radius <= 1000)) problems.push("Within … ly: a number from 1 to 1000.");
  if (!(Number.isInteger(maxdist) && maxdist >= 1 && maxdist <= 1000000))
    problems.push("Bodies within … ls: a whole number from 1 to 1,000,000.");
  if (!(Number.isInteger(stops) && stops >= 1 && stops <= 100)) problems.push("Stops per search: 1 to 100.");
  if (!(detour >= 0 && detour <= 1000)) problems.push("Swap a done stop: a detour from 0 to 1000 ly.");
  for (const [id, key] of AMOUNTS) {
    const v = parseAmount(input(id).value || "0");
    if (!(v >= 0 && v <= 1e10))
      problems.push(`"${input(id).value}" is not an amount (try 1500000, 1,500,000, 1.5M or 500k).`);
    amounts[key] = v;
  }
  // A route only hides the search fields: a bad one there is no error, the remembered values go back.
  const kept = debug.reply ? debug.reply.state.scout_prefs : null;
  if (problems.length && !(plan === "route" && kept)) return problems[0];
  const search =
    problems.length && kept
      ? {
          radius_ly: kept.radius_ly,
          max_distance_ls: kept.max_distance_ls,
          max_stops: kept.max_stops,
          max_detour_ly: kept.max_detour_ly,
          min_scan: kept.min_scan,
          min_mapping: kept.min_mapping,
          min_species: kept.min_species,
          min_bio_body: kept.min_bio_body,
          planets: kept.planets,
        }
      : {
          radius_ly: radius,
          max_distance_ls: maxdist,
          max_stops: stops,
          max_detour_ly: detour,
          min_scan: amounts.min_scan,
          min_mapping: amounts.min_mapping,
          min_species: amounts.min_species,
          min_bio_body: amounts.min_bio_body,
          planets: select("f-planets").value === "prime" ? "prime" : "any",
        };
  return {
    from_system: input("f-from").value.trim(),
    to_system: input("f-to").value.trim(),
    ...search,
    riches: plan === "riches" || plan === "both",
    bio: plan === "bio" || plan === "both",
    plotter: checked("plotter") === "neutron" ? "neutron" : "exact",
  };
}

/** @param {string} path @param {unknown} body */
async function post(path, body) {
  try {
    const resp = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    const doc = await resp.json().catch(() => ({}));
    debug.posts.push({ path, status: resp.status, body: doc });
    return { ok: resp.ok, error: typeof doc.error === "string" ? doc.error : `HTTP ${resp.status}` };
  } catch (err) {
    debug.posts.push({ path, status: 0, body: String(err) });
    return { ok: false, error: "EDMC not reachable" };
  }
}

/**
 * A search's answer line colour: a finished search that found nothing reads quiet grey, never like success.
 * @param {"idle"|"running"|"done"|"error"} status @param {boolean} found
 * @returns {"idle"|"running"|"done"|"error"|"empty"}
 */
function answerKind(status, found) {
  return status === "done" && !found ? "empty" : status;
}

/** @param {string} text @param {"idle"|"running"|"done"|"error"|"empty"} kind */
function showStatus(text, kind) {
  const s = $("scout-status");
  s.textContent = text;
  s.dataset.status = kind;
}

async function onScout(/** @type {SubmitEvent} */ ev) {
  ev.preventDefault();
  closeAllSuggestions();
  const form = readForm();
  if (typeof form === "string") {
    localStatus = form;
    showStatus(form, "error");
    return;
  }
  localStatus = "Sending…";
  showStatus(localStatus, "running");
  awaitHandled = debug.reply ? debug.reply.state.scout.handled : 0;
  const r = await post("/api/scout", form);
  if (!r.ok) {
    awaitHandled = -1;
    localStatus = r.error;
    showStatus(r.error, "error");
  }
}

async function onCancel() {
  const r = await post("/api/scout/cancel", {});
  if (!r.ok) showStatus(r.error, "error");
}

/** @param {UiScout} sc */
function renderScout(sc) {
  const r = sc.route;
  const key = `${sc.handled}|${sc.status}|${sc.message}|${r ? `${r.finished}|${r.worth_map}|${r.worth_bio}` : ""}`;
  const changed = key !== lastScoutKey;
  lastScoutKey = key;
  /** @type {HTMLButtonElement} */ ($("b-cancel")).disabled = sc.status !== "running";
  if (awaitHandled >= 0 && sc.handled <= awaitHandled) return; // our click is not answered yet
  awaitHandled = -1;
  if (changed) localStatus = "";
  if (localStatus) return; // a local message stays until the server reports something new
  if (sc.status === "done" && sc.route && sc.route.mode === "game") {
    const n = sc.route.total_jumps;
    showStatus(`Following the route from the galaxy map: ${CR.format(n)} jump${n === 1 ? "" : "s"}.`, "done");
  } else if (sc.status === "done" && sc.route && sc.route.mode !== "tour") {
    showStatus(plotSummary(sc.route), "done");
  } else if (sc.status === "done" && sc.route) {
    showStatus(tourSummary(sc.route), "done");
  } else {
    showStatus(sc.message, answerKind(sc.status, false));
  }
  if (changed) renderRoute(sc.route);
}

/** @param {UiRoute} route a tour: how many stops, and what happened to stops already yours */
function tourSummary(route) {
  const n = route.stops.length;
  if (!n) return "No stops pass your minimum values.";
  const swapped = route.stops.filter((s) => s.replaces).length;
  const spent = route.stops.filter((s) => s.spent).length;
  let text = `${n} stop${n === 1 ? "" : "s"} found.`;
  if (swapped) text += ` ${swapped} swapped for one nearby: you had done the first choice already.`;
  if (spent) text += ` ${spent} ${spent === 1 ? "stop holds" : "stops hold"} nothing left for you to earn.`;
  return text;
}

const MILLIONS = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
/** @param {number} n credits → earnings in tenths of a million: "19.0M", "1.1M", "<0.1M" */
function money(n) {
  if (n <= 0) return "0";
  if (n < 50e3) return "<0.1M";
  return `${MILLIONS.format(n / 1e6)}M`;
}

/** Planet classes worth a colour of their own (Spansh's subtype names). */
const CLASS_COLOURS = /** @type {Record<string, string>} */ ({
  "Earth-like world": "cls-elw",
  "Water world": "cls-ww",
  "Ammonia world": "cls-aw",
});

/**
 * Colour steps. A value is graded against [mid, high, top]: below mid it is "low" (grey), then mid
 * (plain), high (gold), top (green). A cost is graded against [ok, far, very far]: below ok it is
 * "near" (teal), then ok (plain), far (orange), very far (red).
 */
const VALUE_STEPS = {
  scan: [100e3, 250e3, 500e3], // Cr per body, FSS
  mapping: [500e3, 1e6, 2e6], // Cr per body, DSS
  stopScan: [250e3, 500e3, 1e6],
  stopMapping: [1e6, 2e6, 4e6],
  species: [2e6, 7e6, 15e6], // Cr per species
  bioBody: [5e6, 15e6, 30e6], // Cr per body
  stopBio: [15e6, 50e6, 100e6],
};
const COST_STEPS = {
  arrival: [1000, 5000, 20000], // ls to fly in supercruise
  jumps: [4, 11, 41], // jumps for one leg
};

/** @param {number} v @param {number[]} steps */
function valueTier(v, steps) {
  return v >= steps[2] ? "v-top" : v >= steps[1] ? "v-high" : v >= steps[0] ? "v-mid" : "v-low";
}

/** @param {number} v @param {number[]} steps */
function costTier(v, steps) {
  return v >= steps[2] ? "c-vfar" : v >= steps[1] ? "c-far" : v >= steps[0] ? "c-ok" : "c-near";
}

/**
 * The route card's head: "From → To" as its title, then one quiet line of facts.
 * @param {UiRoute} route @param {[string, string][]} facts [value shown bold, words after it]
 */
function routeHead(route, facts) {
  const title = el("div", "route-title");
  title.append(el("span", "", route.from_name), el("span", "to-arrow", " → "), el("span", "", route.to_name));
  const meta = factLine(facts, "route-meta");
  if (route.mode === "tour") meta.title = "In all = mapping + exobiology. Mapping includes the scan, so the scan is not added.";
  $("route-head").replaceChildren(title, meta);
}

/** @param {[string, string][]} facts @param {string} cls */
function factLine(facts, cls) {
  const line = el("div", `facts ${cls}`);
  for (const [value, words] of facts) {
    const f = el("span", "fact");
    if (value) f.append(el("strong", "", value), " ");
    f.append(words);
    line.append(f);
  }
  return line;
}

/** @param {UiRoute} route a route-only plot */
function plotSummary(route) {
  const plural = (n, word) => `${CR.format(n)} ${word}${n === 1 ? "" : "s"}`;
  const refuels = route.waypoints.filter((w) => w.refuel).length;
  const parts = [plural(route.total_jumps, "jump")];
  if (route.mode === "neutron") parts.push(plural(route.waypoints.filter((w) => w.neutron).length, "neutron boost"));
  parts.push(`refuel at ${plural(refuels, "star")}`);
  return `${parts.join("; ")}.`;
}

/** Row icons (inline SVG, drawn in the current text colour). */
const ICONS = {
  // lightning bolt: supercharge at this neutron star
  neutron: '<path d="M9.5 1 3 9.2h4.2L6 15l7-8.6H8.6z"/>',
  // fuel pump: the star can be scooped
  scoop:
    '<path d="M2.5 2.5A1 1 0 0 1 3.5 1.5h5a1 1 0 0 1 1 1v12h-7zM4 3.5v3h4v-3z"/>' +
    '<path d="M9.5 5.5h1.2c.7 0 1.3.6 1.3 1.3v4.7a1 1 0 0 0 2 0V5.2L12.3 3.6" fill="none" stroke="currentColor"' +
    ' stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/><path d="M1.5 14h9v1.2h-9z"/>',
  // fuel nozzle (spout, grip, trigger, hose): fill up here
  refuel:
    '<path d="M5 3.8h6.8a1.2 1.2 0 0 1 1.2 1.2v2.2H5z"/><g fill="none" stroke="currentColor" stroke-linecap="round">' +
    '<path d="M5.2 5.2 2.4 6.6 1.6 9.6" stroke-width="1.7" stroke-linejoin="round"/>' +
    '<path d="M9 7.2v4.3c0 .9.7 1.6 1.6 1.6h.3c.9 0 1.6-.7 1.6-1.6V7.2" stroke-width="1.4" stroke-linecap="butt"/>' +
    '<path d="M6.3 7.2 8 9.6" stroke-width="1.3"/><path d="M13 5.3h.9c.6 0 1.1.5 1.1 1.1V15" stroke-width="1.3"/></g>',
};
const ICON_TITLES = {
  neutron: "Neutron star: supercharge here (the next jump is boosted)",
  scoop: "Scoopable star (K, G, B, F, O, A, M)",
  refuel: "Refuel here: scoop before you go on",
};

/** @param {"neutron"|"scoop"|"refuel"} name */
function icon(name) {
  const box = el("span", `icon icon-${name}`);
  box.title = ICON_TITLES[name];
  box.setAttribute("role", "img");
  box.setAttribute("aria-label", ICON_TITLES[name]);
  box.innerHTML = `<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true">${ICONS[name]}</svg>`;
  return box;
}

// the (i) text of a plotted route (fixed markup, nothing from outside)
const GAME_HINT =
  "<p>The route you plotted in the game's galaxy map, one row per jump.</p>" +
  "<ul><li><strong>Pump</strong>: a scoopable star.</li></ul>" +
  '<p class="info-foot">The game does not say where to refuel or supercharge.</p>';

const PLOT_HINT =
  "<p>One row per jump, for your ship's build: full tanks at the start, your current cargo.</p>" +
  "<ul><li><strong>Lightning</strong>: a neutron star; supercharge here.</li>" +
  "<li><strong>Pump</strong>: a scoopable star (not said for the start and the destination).</li>" +
  "<li><strong>Nozzle at the pump</strong>: refuel here before you go on.</li></ul>" +
  `<p class="info-foot">Data: Spansh's galaxy plotter.</p>`;

// --- route view: Order / Show (this browser only) ---------------------------------------------------

const VIEW_KEY = "edmultitool.routeView";
/** @type {{order: "route"|"value", show: "all"|"open"|"map"|"bio"}} */
const view = { order: "route", show: "all" };
try {
  const saved = JSON.parse(localStorage.getItem(VIEW_KEY) || "{}");
  if (saved.order === "value") view.order = "value";
  if (["open", "map", "bio"].includes(saved.show)) view.show = saved.show;
} catch {
  // no storage (private window, blocked): the defaults
}

/** @param {UiStop} stop what is left to earn there: mapped (or scanned) bodies plus species */
const stopWorth = (stop) => Math.max(stop.scan_value, stop.mapping_value) + stop.bio_value;
/** What is left to earn on a body: its scan or mapping (where the scan search counted it and you have not
 * mapped it) plus its species. @param {UiBody} b */
const bodyWorth = (b) => (b.scan_hit && !b.done ? Math.max(b.scan_value, b.mapping_value) : 0) + b.bio_value;

/** The id64s of the stop and of the body worth the most on the whole route (the first on a tie; none when
 * nothing is left to earn). @param {UiStop[]} stops @returns {{stop: string, body: string}} */
function bestOnRoute(stops) {
  let stop = "", body = "", stopTop = 0, bodyTop = 0;
  for (const s of stops) {
    const w = stopWorth(s);
    if (w > stopTop) [stop, stopTop] = [s.id64, w];
    for (const b of s.bodies) {
      const v = bodyWorth(b);
      if (v > bodyTop) [body, bodyTop] = [b.id64, v];
    }
  }
  return { stop, body };
}

/** @param {UiStop} stop */
function showsStop(stop) {
  if (view.show === "open") return !stop.spent;
  if (view.show === "map") return Math.max(stop.scan_value, stop.mapping_value) > 0;
  if (view.show === "bio") return stop.bio_value > 0;
  return true;
}

function onView() {
  view.order = select("v-order").value === "value" ? "value" : "route";
  const show = select("v-show").value;
  view.show = show === "open" || show === "map" || show === "bio" ? show : "all";
  try {
    localStorage.setItem(VIEW_KEY, JSON.stringify(view));
  } catch {
    // not remembered; still applied
  }
  const s = debug.reply && debug.reply.state;
  if (!s) return;
  renderRoute(s.scout.route);
  renderFollow(s);
}

/** @param {?UiRoute} route */
function renderRoute(route) {
  const box = $("route");
  const table = $("route-table");
  for (const old of table.querySelectorAll("tbody")) old.remove();
  for (const old of $("plot-table").querySelectorAll("tbody")) old.remove();
  lastFollowKey = ""; // fresh rows: mark the progress again
  $("view-note").hidden = true;
  if (!route) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.dataset.mode = route.mode;
  const plot = route.mode !== "tour";
  $("tour-wrap").hidden = plot;
  $("tour-hint").hidden = plot;
  $("plot-wrap").hidden = !plot;
  $("plot-hint").hidden = !plot;
  if (plot) {
    renderPlot(route);
    return;
  }
  const worth = route.worth_map + route.worth_bio;
  routeHead(route, [
    [String(route.stops.length), route.stops.length === 1 ? "stop" : "stops"],
    [`${money(worth)} Cr`, "in all"],
    [money(route.worth_scan), "scan"],
    [money(route.worth_map), "mapping"],
    [money(route.worth_bio), "exobiology"],
    [`${LY.format(route.total_ly)} ly`, "straight"],
    [`${route.range_ly.toFixed(2)} ly`, "per jump"],
    ...(route.from_cache ? [/** @type {[string, string]} */ (["", "saved result"])] : []),
  ]);

  // Order and Show change only what you see: the stop numbers, the route and its following stay.
  const best = bestOnRoute(route.stops);
  debug.best = best;
  const order = route.stops.map((_, i) => i);
  if (view.order === "value") order.sort((a, b) => stopWorth(route.stops[b]) - stopWorth(route.stops[a]) || a - b);
  let shown = 0;
  order.forEach((i) => {
    const stop = route.stops[i];
    const group = el("tbody", stop.spent ? "stop spent" : "stop");
    group.dataset.id64 = stop.id64;
    group.dataset.point = String(i + 1);
    const visible = showsStop(stop);
    group.classList.toggle("filtered", !visible);
    if (visible) shown += 1;
    if (stop.id64 === best.stop) group.classList.add("best-stop"); // the whole system, its bodies too
    const row = el("tr", "stop-row");
    if (stop.id64 === best.stop) row.title = `The most valuable system on this route: ${cr(stopWorth(stop))} Cr`;
    const num = el("td", "num");
    num.append(el("span", "stop-no", String(i + 1)));
    const legTier = costTier(stop.leg_jumps, COST_STEPS.jumps);
    const sys = el("th", "sys");
    sys.append(copyName("sys-name", stop.name, (ok) => (debug.copied = { name: stop.name, ok })));
    if (stop.replaces) {
      const extra = LY.format(stop.detour_ly);
      const note = el("div", "swap-note", `instead of ${stop.replaces} · +${extra} ly`);
      note.title = `Your bodies at ${stop.replaces} are done already; this stop adds ${extra} ly to the trip.`;
      sys.append(note);
    }
    // One body only: its own row already shows the same numbers, so the stop row leaves them out.
    const only = stop.bodies.length === 1 ? stop.bodies[0] : null;
    const sameScan = !!only && only.scan_hit && only.scan_value === stop.scan_value;
    const sameMap = !!only && only.scan_hit && only.mapping_value === stop.mapping_value;
    const sameBio = !!only && only.species.length > 0 && only.bio_value === stop.bio_value;
    row.append(
      num,
      sys,
      el("td", `n leg ${legTier}`, `${LY.format(stop.leg_ly)}`),
      el("td", `n leg ${legTier}`, String(stop.leg_jumps)),
      el("td", "n", ""),
      el(
        "td",
        `n total ${valueTier(stop.scan_value, VALUE_STEPS.stopScan)}`,
        stop.scan_value && !sameScan ? money(stop.scan_value) : "",
      ),
      el(
        "td",
        `n total ${valueTier(stop.mapping_value, VALUE_STEPS.stopMapping)}`,
        stop.mapping_value && !sameMap ? money(stop.mapping_value) : "",
      ),
      el("td", "", ""), // body worth: bodies only
      el(
        "td",
        `total ${valueTier(stop.bio_value, VALUE_STEPS.stopBio)}`,
        stop.bio_value && !sameBio ? `${money(stop.bio_value)} Cr` : "",
      ),
      el("td", "worth", ""),
    );
    // The stop's "$" (its best body) stands out on its own, right of every value column.
    if (stop.wealth) /** @type {HTMLElement} */ (row.lastElementChild).append(wealthMark(stop.wealth));
    else if (stop.spent) /** @type {HTMLElement} */ (row.lastElementChild).append(yoursMark("Everything here is yours already"));
    /** @type {HTMLTableCellElement} */ (row.children[1]).scope = "row";
    group.append(row);
    for (const b of stop.bodies) {
      const tr = bodyRow(b);
      if (b.id64 === best.body) {
        tr.classList.add("best-body");
        tr.title = `The most valuable body on this route: ${cr(bodyWorth(b))} Cr`;
      }
      group.append(tr);
    }
    table.append(group);
  });
  const hiddenNote = $("view-note");
  const left = route.stops.length - shown;
  hiddenNote.hidden = left === 0;
  hiddenNote.textContent = left ? `${CR.format(left)} of ${CR.format(route.stops.length)} stops hidden by Show.` : "";
  debug.view = { order: view.order, show: view.show, shown, rows: order.map((i) => route.stops[i].name) };

  const last = el("tbody", "stop dest");
  last.dataset.point = String(route.stops.length + 1);
  const row = el("tr", "stop-row");
  const flag = el("td", "num");
  flag.append(el("span", "stop-no", "⚑"));
  const lastTier = costTier(route.final_leg_jumps, COST_STEPS.jumps);
  row.append(
    flag,
    (() => {
      const th = el("th", "sys");
      th.append(copyName("sys-name", route.to_name, (ok) => (debug.copied = { name: route.to_name, ok })));
      return th;
    })(),
    el("td", `n leg ${lastTier}`, LY.format(route.final_leg_ly)),
    el("td", `n leg ${lastTier}`, String(route.final_leg_jumps)),
    el("td", "", ""),
    el("td", "", ""),
    el("td", "", ""),
    el("td", "", ""),
    el("td", "dest-note", "destination"),
    el("td", "", ""),
  );
  last.append(row);
  table.append(last);
}

/** @param {UiRoute} route a route-only plot: one row per waypoint, start and destination included */
function renderPlot(route) {
  const exact = route.mode === "exact";
  routeHead(route, [
    ["", route.mode === "game" ? "from the galaxy map" : exact ? "normal jumps" : "with neutron boosts"],
    [CR.format(route.total_jumps), route.total_jumps === 1 ? "jump" : "jumps"],
    [`${LY.format(route.total_ly)} ly`, "straight"],
    [`${route.range_ly.toFixed(2)} ly`, "per jump, full tank"],
    ...(route.from_cache ? [/** @type {[string, string]} */ (["", "saved result"])] : []),
  ]);
  $("plot-hint").innerHTML = route.mode === "game" ? GAME_HINT : PLOT_HINT;

  const body = el("tbody");
  const last = route.waypoints.length - 1;
  route.waypoints.forEach((w, i) => {
    const tr = el("tr", i === 0 || i === last ? "wp ends" : "wp");
    tr.dataset.id64 = w.id64;
    const sys = /** @type {HTMLTableCellElement} */ (el("th", "sys", w.name));
    sys.scope = "row";
    const note = el("span", "note");
    if (w.neutron) note.append(icon("neutron"));
    if (w.scoopable) note.append(icon("scoop"));
    if (w.refuel) note.append(icon("refuel"));
    if (i === 0) note.append(el("span", "dest-note", "start"));
    if (i === last) note.append(el("span", "dest-note", "destination"));
    tr.append(
      el("td", "n wp-no", i === 0 ? "" : String(i)),
      sys,
      el("td", "n", i === 0 ? "" : LY.format(w.leg_ly)),
      el("td", ""),
    );
    /** @type {HTMLElement} */ (tr.lastElementChild).append(note);
    body.append(tr);
  });
  $("plot-table").append(body);
}

// --- following the route -----------------------------------------------------------------------------

const WEALTH_TITLES = ["", "up to 5M Cr", "5–10M Cr", "10–20M Cr", "20–50M Cr", "over 50M Cr"];

/** @param {number} n 1–5 */
function wealthMark(n) {
  const w = el("span", `wealth w${n}`, "$".repeat(n));
  w.title = `Worth ${WEALTH_TITLES[n]}`;
  w.setAttribute("aria-label", `${n} of 5: worth ${WEALTH_TITLES[n]}`);
  return w;
}

/** Points behind you are folded away unless the player opens them. */
let showPassed = false;
let lastFollowKey = "";

/**
 * Where you are on the route, as separate facts: [value shown bold, words after it].
 * @param {UiFollow} f @param {UiRoute} route @returns {[string, string][]}
 */
function followProgress(f, route) {
  const s = (n, word) => `${word}${n === 1 ? "" : "s"}`;
  if (f.finished) return [["", `Arrived at ${route.to_name}: route done`]];
  if (route.mode !== "tour") {
    const left = /** @type {[string, string]} */ ([CR.format(f.jumps_left), `${s(f.jumps_left, "jump")} left`]);
    if (f.next_kind === "start") return [["", "Fly to the start first"], left];
    if (f.next_kind === "destination") return [["", "Last leg to the destination"], left];
    return [[`${f.next_index} / ${f.count - 1}`, "waypoint"], left];
  }
  /** @type {[string, string][]} */
  const out = [];
  const n = route.stops.length;
  if (f.next_kind === "start") out.push(["", "Fly to the start first"]);
  else if (f.next_kind === "stop") out.push([`${f.next_index} / ${n}`, "stop"]);
  else out.push(["", "All stops behind you"]);
  if (f.stops_left) {
    out.push([CR.format(f.stops_left), `${s(f.stops_left, "stop")} ahead`]);
    out.push([`${money(f.value_ahead)} Cr`, "to earn"]);
  }
  out.push([`≈ ${CR.format(Math.round(f.ly_left))} ly`, "to the destination"]);
  return out;
}

/** @param {UiState} s */
function renderFollow(s) {
  const f = s.follow;
  const route = s.scout.route;
  const box = $("follow");
  if (!route || !f.active) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.querySelector(".follow-label").textContent = f.finished ? "Arrived" : "Next";
  $("follow-name").textContent = f.finished ? route.to_name : f.next_name;
  $("follow-wealth").replaceChildren(...(f.next_wealth ? [wealthMark(f.next_wealth)] : []));
  /** @type {HTMLButtonElement} */ ($("b-copy")).disabled = !f.next_name;
  /** @type {HTMLButtonElement} */ ($("b-skip")).disabled = f.finished;
  /** @type {HTMLButtonElement} */ ($("b-back")).disabled = f.next_index === 0;
  $("follow-progress").replaceChildren(...factLine(followProgress(f, route), "").childNodes);

  const key = `${route.finished}|${f.next_index}|${showPassed}`;
  if (key === lastFollowKey) return;
  lastFollowKey = key;
  markRows(route, f);
}

/**
 * Grey out and fold the points behind you, mark the next one. A tour stop's row carries its point
 * (stop i is point i + 1; point 0 is the start, which has no row) — Order may move it; row i of the plot
 * table is point i.
 * @param {UiRoute} route @param {UiFollow} f
 */
function markRows(route, f) {
  const tour = route.mode === "tour";
  const table = $(tour ? "route-table" : "plot-table");
  for (const old of table.querySelectorAll(".behind")) old.remove();
  /** @type {HTMLElement[]} */
  const rows = Array.from(table.querySelectorAll(tour ? "tbody.stop" : "tbody tr.wp"));
  let passed = 0;
  rows.forEach((row, i) => {
    const point = tour ? Number(row.dataset.point) : i;
    const behind = point < f.next_index;
    if (behind && !row.classList.contains("filtered")) passed += 1; // Show hides it either way
    row.classList.toggle("passed", behind);
    row.classList.toggle("is-next", point === f.next_index);
    row.hidden = behind && !showPassed;
  });
  table.classList.toggle("follow-done", f.finished);
  if (passed) {
    const word = tour ? (passed === 1 ? "stop" : "stops") : passed === 1 ? "system" : "systems";
    const button = el(
      "button",
      "behind-toggle",
      `${showPassed ? "▾ Hide" : "▸ Show"} ${CR.format(passed)} ${word} behind you`,
    );
    /** @type {HTMLButtonElement} */ (button).type = "button";
    button.setAttribute("aria-expanded", String(showPassed));
    button.addEventListener("click", () => {
      showPassed = !showPassed;
      if (debug.reply) renderFollow(debug.reply.state);
    });
    const cell = el("td", "behind-cell");
    /** @type {HTMLTableCellElement} */ (cell).colSpan = tour ? 10 : 4;
    cell.append(button);
    const tr = el("tr", "behind");
    tr.append(cell);
    if (tour) {
      const group = el("tbody", "behind");
      group.append(tr);
      rows[0].before(group);
    } else {
      rows[0].before(tr);
    }
  }
  debug.follow = {
    rows: rows.length,
    passed,
    hidden: rows.filter((r) => r.hidden || r.classList.contains("filtered")).length,
    next: (table.querySelector(".is-next .sys") || { textContent: "" }).textContent || "",
  };
}

let copyLabelTimer = 0;

async function onCopy() {
  const f = debug.reply && debug.reply.state.follow;
  if (!f || !f.next_name) return;
  const name = f.next_name;
  const ok = await copyText(name);
  debug.copied = { name, ok };
  const b = $("b-copy");
  b.innerHTML = ok ? COPIED_SVG : FAILED_SVG;
  b.classList.toggle("copied", ok);
  b.classList.toggle("copy-failed", !ok);
  window.clearTimeout(copyLabelTimer);
  copyLabelTimer = window.setTimeout(() => {
    b.innerHTML = COPY_SVG;
    b.classList.remove("copied", "copy-failed");
  }, 1500);
}

const COPY_SVG =
  '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">' +
  '<rect x="5.5" y="5.5" width="8" height="8.5" rx="1.5"/><path d="M10.5 5.5V3.5a1.5 1.5 0 0 0-1.5-1.5H4A1.5 1.5 0 0 0 2.5 3.5v6A1.5 1.5 0 0 0 4 11h1.5"/></svg>';
const COPIED_SVG =
  '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
  '<path d="M3 8.5l3.2 3L13 4.5"/></svg>';
const FAILED_SVG =
  '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
  '<path d="M4 4l8 8M12 4l-8 8"/></svg>';

/** The copy icon button put right after a name: copies `name` on a click, shows a tick (or a cross) for
 * a moment. `done` records the click. @param {string} name @param {(ok: boolean) => void} done
 * @returns {HTMLButtonElement} */
function copyIcon(name, done) {
  const b = /** @type {HTMLButtonElement} */ (el("button", "copy-icon"));
  b.type = "button";
  b.title = `Copy ${name} to the clipboard`;
  b.setAttribute("aria-label", `Copy ${name}`);
  b.innerHTML = COPY_SVG;
  let timer = 0;
  b.addEventListener("click", async (e) => {
    e.stopPropagation();
    const ok = await copyText(name);
    done(ok);
    b.innerHTML = ok ? COPIED_SVG : FAILED_SVG;
    b.classList.toggle("copied", ok);
    b.classList.toggle("copy-failed", !ok);
    b.title = ok ? `Copied ${name}` : "Copy failed";
    window.clearTimeout(timer);
    timer = window.setTimeout(() => {
      b.innerHTML = COPY_SVG;
      b.classList.remove("copied", "copy-failed");
      b.title = `Copy ${name} to the clipboard`;
    }, 1500);
  });
  return b;
}

/** A name with its copy icon right after it. @param {string} cls @param {string} name
 * @param {(ok: boolean) => void} done */
function copyName(cls, name, done) {
  return named(copyIcon(name, done), el("span", cls, name));
}

/** @param {HTMLElement} icon @param {HTMLElement} label */
function named(icon, label) {
  const line = el("span", "named");
  line.append(label, icon);
  return line;
}

/** Put `name` on the clipboard — only ever on a click (the clipboard is the player's).
 * @param {string} name @returns {Promise<boolean>} */
async function copyText(name) {
  let ok = true;
  try {
    await navigator.clipboard.writeText(name);
  } catch {
    // older engines / no permission: the hidden-textarea way
    const area = /** @type {HTMLTextAreaElement} */ (el("textarea"));
    area.value = name;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    ok = document.execCommand("copy");
    area.remove();
  }
  return ok;
}

/** @param {UiState} s */
function renderGameOffer(s) {
  const g = s.game_route;
  $("game-offer").hidden = !g.offer;
  if (!g.offer) return;
  const when = g.plotted ? ` at ${new Date(g.plotted).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "";
  const jumps = `${CR.format(g.jumps)} jump${g.jumps === 1 ? "" : "s"}`;
  $("game-offer-text").textContent = `In the galaxy map you plotted ${g.to_name} (${jumps})${when}.`;
}

async function onTakeGame() {
  const r = await post("/api/follow", { take: "game" });
  if (!r.ok) showStatus(r.error, "error");
}

/** @param {1|-1} by */
async function onStep(by) {
  const r = await post("/api/follow", { step: by });
  if (!r.ok) showStatus(r.error, "error");
}

/** @param {string} why tooltip @param {string} [text] */
function yoursMark(why, text = "already yours") {
  const mark = el("span", "badge yours", text);
  mark.title = why;
  return mark;
}

/** @param {UiBody} b */
function bodyRow(b) {
  const allAnalysed = b.species.length > 0 && b.species.every((sp) => sp.done);
  // Nothing left to earn on it: mapped where the scan search counted it, and no species value left that
  // still reaches the per-body bio minimum (bio_value is 0 then).
  const yours = (!b.scan_hit || b.done) && b.bio_value === 0;
  const tr = el("tr", `body-row${b.species.length ? " has-bio" : ""}${yours ? " done" : ""}`);
  tr.dataset.id64 = b.id64;
  const name = el("td", "body");
  const line = el("div", "bline");
  line.append(el("span", "bname", b.short));
  if (b.terraformable) line.append(el("span", "badge terra", "terraformable"));
  if (yours && b.species.length && !allAnalysed)
    line.append(yoursMark("The species you have not analysed here add up to less than your bio minimum per body", "rest below minimum"));
  else if (yours) line.append(yoursMark(b.done && b.scan_hit ? "You mapped this body already" : "You analysed every species here already"));
  else if (b.done && b.scan_hit) line.append(yoursMark("You mapped this body already: only its species are counted", "mapped"));
  name.append(line, el("div", `btype ${CLASS_COLOURS[b.subtype] || ""}`, b.subtype));

  const arrival = el("td", `n ${costTier(b.distance_ls, COST_STEPS.arrival)}`, CR.format(Math.round(b.distance_ls)));

  // Scan values only where the scan search counted the body; a bio stop's 500 Cr rock is noise.
  const scanDone = b.done ? " done-value" : "";
  const scan = el("td", `n ${valueTier(b.scan_value, VALUE_STEPS.scan)}${scanDone}`, b.scan_hit ? money(b.scan_value) : "");
  const mapping = el(
    "td",
    `n strong ${valueTier(b.mapping_value, VALUE_STEPS.mapping)}${scanDone}`,
    b.scan_hit ? money(b.mapping_value) : "",
  );

  const bio = el("td", "bio");
  // The body's "$" in a column of its own, between its scan values and its bio.
  const bodyWorth = el("td", "bworth");
  if (b.wealth) bodyWorth.append(wealthMark(b.wealth));
  if (b.species.length) {
    const head = el("div", `btotal ${valueTier(b.bio_value, VALUE_STEPS.bioBody)}`);
    head.append(allAnalysed ? "all analysed" : b.bio_value ? `${money(b.bio_value)} Cr` : "below minimum");
    bio.append(head);
    const ul = el("ul", "species");
    for (const sp of b.species) {
      const li = el("li", sp.done ? "sp done" : `sp ${valueTier(sp.value, VALUE_STEPS.species)}`);
      li.title = sp.done ? `${sp.name}: you analysed it here already` : `${sp.name}: ${cr(sp.value)} Cr`;
      li.append(el("span", "sp-name", sp.name), el("span", "sp-value", money(sp.value)));
      ul.append(li);
    }
    bio.append(ul);
  }
  const worth = el("td", "", "");
  const cells = [el("td", "", ""), name, el("td", "", ""), el("td", "", ""), arrival, scan, mapping];
  tr.append(...cells, bodyWorth, bio, worth);
  return tr;
}

// --- system-name suggestions ---------------------------------------------------------------------------

/** @type {(() => void)[]} */
const closers = [];
function closeAllSuggestions() {
  for (const close of closers) close();
}

/** Type-ahead on one input: debounced, the previous request aborted, out-of-date replies dropped.
 * With `recent`, the box offers those names on focus and while you type the first letters; matching ones
 * also lead the looked-up names. With `local`, names come from it (no request) from the first letter.
 * @param {HTMLInputElement} box @param {HTMLElement} list @param {() => string[]} [recent]
 * @param {(q: string) => string[]} [local] */
function attachSuggest(box, list, recent, local) {
  let timer = 0;
  /** @type {?AbortController} */
  let inflight = null;
  let active = -1;
  /** @type {string[]} */
  let names = [];

  const close = () => {
    list.hidden = true;
    list.replaceChildren();
    box.setAttribute("aria-expanded", "false");
    box.removeAttribute("aria-activedescendant");
    names = [];
    active = -1;
  };
  closers.push(close);

  const highlight = (/** @type {number} */ i) => {
    active = i;
    [...list.children].forEach((li, j) => li.setAttribute("aria-selected", String(j === i)));
    if (i >= 0) box.setAttribute("aria-activedescendant", `${list.id}-${i}`);
    else box.removeAttribute("aria-activedescendant");
  };

  const pick = (/** @type {number} */ i) => {
    if (i < 0 || i >= names.length) return;
    box.value = names[i];
    close();
    box.dispatchEvent(new Event("change")); // a box that acts on its value (the trade card's system)
  };

  const show = (/** @type {string[]} */ found) => {
    names = found;
    list.replaceChildren(
      ...found.map((name, i) => {
        const li = el("li", "", name);
        li.id = `${list.id}-${i}`;
        li.setAttribute("role", "option");
        li.addEventListener("mousedown", (e) => {
          e.preventDefault(); // keep focus in the input
          pick(i);
        });
        return li;
      }),
    );
    const open = found.length > 0 && document.activeElement === box;
    list.hidden = !open;
    box.setAttribute("aria-expanded", String(open));
    highlight(-1);
  };

  const lookup = async () => {
    const q = box.value.trim();
    if (local) {
      if (q) show(local(q));
      else close();
      return;
    }
    if (inflight) {
      inflight.abort();
      debug.suggest.aborted += 1;
    }
    if (q.length < SUGGEST_MIN_CHARS) {
      showRecent();
      return;
    }
    const ctl = (inflight = new AbortController());
    debug.suggest.fetches += 1;
    try {
      const resp = await fetch(`/api/systems?q=${encodeURIComponent(q)}`, {
        cache: "no-store",
        signal: AbortSignal.any([ctl.signal, AbortSignal.timeout(15000)]),
      });
      /** @type {SystemsReply} */
      const doc = await resp.json();
      if (box.value.trim() !== q || doc.superseded) {
        debug.suggest.stale += 1;
        return; // the user has typed on; a newer lookup is on its way
      }
      const found = resp.ok && Array.isArray(doc.systems) ? doc.systems.map((s) => String(s.name)) : [];
      debug.suggest.last = { q, names: found };
      // your recent destinations that start with it come first
      const mine = recentMatching(q);
      show([...mine, ...found.filter((n) => !mine.some((m) => m.toLowerCase() === n.toLowerCase()))]);
    } catch {
      // aborted, timed out or unreachable: the next keystroke tries again
    } finally {
      if (inflight === ctl) inflight = null;
    }
  };

  /** @param {string} q @returns {string[]} destinations scouted before that start with `q` */
  const recentMatching = (q) => (recent ? recent().filter((n) => n.toLowerCase().startsWith(q.toLowerCase())) : []);

  /** Destinations scouted before, starting with what is typed; nothing to offer = closed. */
  const showRecent = () => {
    const found = recentMatching(box.value.trim());
    if (found.length) show(found);
    else close();
  };

  // On entering the box: all recent destinations when it is empty or holds one of them (the remembered
  // destination), else those starting with what is there.
  box.addEventListener("focus", () => {
    const q = box.value.trim().toLowerCase();
    const all = recent ? recent() : [];
    if (all.length && (!q || all.some((n) => n.toLowerCase() === q))) show(all);
    else if (q.length < SUGGEST_MIN_CHARS) showRecent();
  });
  box.addEventListener("input", () => {
    clearTimeout(timer);
    timer = window.setTimeout(lookup, local ? 0 : SUGGEST_DELAY_MS);
  });
  box.addEventListener("keydown", (e) => {
    if (list.hidden) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      highlight((active + 1) % names.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      highlight(active <= 0 ? names.length - 1 : active - 1);
    } else if (e.key === "Enter" && active >= 0) {
      e.preventDefault(); // pick, don't submit
      pick(active);
    } else if (e.key === "Escape") {
      close();
    }
  });
  box.addEventListener("blur", () => {
    clearTimeout(timer);
    close();
  });
}

// --- gear from the forum ----------------------------------------------------------------------------

let gearAwait = -1; // the `handled` count before our last request: its answer is not in yet
let gearLocal = ""; // a local message ("Sending…", a failed post) until the server answers
let gearKey = "";
let gearFieldsHandled = -1; // the request whose fields were filled in (the user's typing is kept after)
let gearLocalAt = -1; // `handled` when the local message was set: it stays until the server moves past it

/** @param {string} text @param {"idle"|"running"|"done"|"error"|"empty"} kind */
function gearStatus(text, kind) {
  const s = $("gear-status");
  s.textContent = text;
  s.dataset.status = kind;
}

/** @param {object} body */
async function sendGear(body) {
  gearLocal = "Sending…";
  gearStatus(gearLocal, "running");
  gearAwait = debug.reply ? debug.reply.state.gear.handled : 0;
  const r = await post("/api/gear", body);
  if (!r.ok) {
    gearAwait = -1;
    gearLocal = r.error.startsWith("body over") ? "That text is too long: paste one post at a time." : r.error;
    gearLocalAt = debug.reply ? debug.reply.state.gear.handled : 0;
    gearStatus(gearLocal, "error");
  }
}

async function onGear(/** @type {SubmitEvent} */ ev) {
  ev.preventDefault();
  const text = /** @type {HTMLTextAreaElement} */ ($("g-text")).value;
  if (!text.trim()) {
    gearLocal = "Paste a post first.";
    gearLocalAt = debug.reply ? debug.reply.state.gear.handled : 0;
    gearStatus(gearLocal, "error");
    return;
  }
  await sendGear({ text });
}

async function onGearFields(/** @type {SubmitEvent} */ ev) {
  ev.preventDefault();
  const system = input("g-system").value.trim();
  const station = input("g-station").value.trim();
  const gear = /** @type {HTMLTextAreaElement} */ ($("g-gear")).value;
  if (!system && !station) {
    gearLocal = "Type a system or a station.";
    gearLocalAt = debug.reply ? debug.reply.state.gear.handled : 0;
    gearStatus(gearLocal, "error");
    return;
  }
  await sendGear({ system, station, gear });
}

async function onGearClear() {
  /** @type {HTMLTextAreaElement} */ ($("g-text")).value = "";
  await sendGear({ clear: true });
}

/** Prices as the posts write them: 11.25M, 875k. @param {number} n */
function gearPrice(n) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(3).replace(/\.?0+$/, "")}M`;
  return `${(n / 1e3).toFixed(1).replace(/\.0$/, "")}k`;
}

const STAR_TEXT = ["", "poor", "fair", "good", "very good", "excellent"];

/** @param {number} n 1-5, 0 = not rated @param {string} why */
function starsMark(n, why) {
  const s = el("span", n ? "stars" : "stars unrated");
  if (!n) {
    s.textContent = "not rated";
  } else {
    s.append("★".repeat(n));
    s.append(el("span", "off", "★".repeat(5 - n)));
    s.setAttribute("aria-label", `${n} of 5 stars, ${STAR_TEXT[n]}`);
  }
  s.title = why;
  return s;
}

/** @param {UiGearItem} i */
function gearItemRow(i) {
  const li = el("li", i.taken ? "gear-item taken" : "gear-item");
  li.append(starsMark(i.stars, i.why + (i.taken ? " — the post says it is gone" : "")));
  const name = `${i.count > 1 ? `${i.count}× ` : ""}${i.grade ? `G${i.grade} ` : ""}${i.name}`;
  const n = el("span", "g-name", name);
  n.title = i.klass || "not a suit or weapon ED Multitool knows";
  const what = el("span", i.klass ? "g-class" : "g-class unknown", i.klass || "unknown item");
  what.title = n.title;
  n.append(" ", what); // the space keeps copied text readable
  if (i.suits.length) {
    const tag = el("span", "g-for", i.suits.join(" · "));
    tag.title = "who it is for: the suit's own job and what its good mods are for";
    n.append(tag);
  }
  li.append(n);
  let mods = i.mods.join(", ");
  if (!mods) mods = i.empty ? "no mods" : "mods not stated";
  const free = i.slots !== null && i.mods.length < i.slots ? i.slots - i.mods.length : 0;
  if (i.mods.length && free) mods += ` + ${free} free slot${free > 1 ? "s" : ""}`;
  li.append(el("span", i.mods.length ? "g-mods" : "g-mods none", mods));
  li.append(el("span", "g-price", (i.taken ? "gone" : "") + (i.price ? `${i.taken ? " · " : ""}${gearPrice(i.price)} Cr` : "")));
  return li;
}

/**
 * @param {UiGearPlace} p
 * @param {{jumps_status: string, jumps_from: string, jumps_from_id64: string}} g where its jumps were counted
 * @param {?string} here id64 of the system you are in now
 * @param {?string} ship the ship the jumps are counted with (null = not known, an earlier find)
 */
function gearPlaceBox(p, g, here, ship) {
  const box = el("div", p.found ? "gear-place" : "gear-place missing");
  box.dataset.system = p.system;
  const head = el("div", "gear-head");
  if (!p.found) {
    head.append(el("span", "gear-system", p.text ? `Not found: ${p.text}` : "No place named"));
    box.append(head);
  } else {
    head.append(el("span", "gear-system", p.system));
    if (p.station) head.append(el("span", "gear-station", p.station));
    const facts = [];
    if (p.station_type) facts.push(p.station_type);
    if (p.pad) facts.push(`pad ${p.pad}`);
    if (p.arrival_ls !== null) facts.push(`${CR.format(p.arrival_ls)} ls`);
    if (facts.length) head.append(el("span", "gear-facts", facts.join(" · ")));
    const trip = el("span", "gear-trip");
    if (p.distance_ly !== null) {
      trip.append(el("strong", "", `${LY.format(p.distance_ly)} ly`));
    }
    let jumps = "";
    const moved = g.jumps_from_id64 && here !== g.jumps_from_id64; // counted before you jumped away: say from where
    if (p.jumps !== null && !moved) {
      jumps = p.jumps === 0 ? "you are here" : `${CR.format(p.jumps)} jump${p.jumps === 1 ? "" : "s"}`;
    } else if (p.jumps !== null) {
      jumps = `${CR.format(p.jumps)} jump${p.jumps === 1 ? "" : "s"} from ${g.jumps_from}`;
    } else if (g.jumps_status === "running") jumps = "plotting jumps…";
    else if (p.jumps_note) jumps = p.jumps_note;
    if (jumps) trip.append(`${p.distance_ly !== null ? " · " : ""}${jumps}`);
    if (p.jumps !== null && g.jumps_from) {
      trip.title = `Fewest jumps from ${g.jumps_from}${ship ? ` with ${ship}` : ""}, full tanks`;
    }
    head.append(trip);
    if (p.items.length && p.items.every((i) => i.taken)) head.append(el("span", "stars unrated", "all gone"));
    else if (p.items.length) head.append(starsMark(p.stars, p.stars ? "the best item still there" : "nothing here can be rated"));
    const sysName = head.querySelector(".gear-system");
    if (sysName) sysName.after(copyIcon(p.system, (ok) => (debug.gear.copied = { name: p.system, ok })));
    box.append(head);
  }
  if (p.note) box.append(el("p", "gear-note", p.note));
  if (p.items.length) {
    const ul = el("ul", "gear-items");
    for (const i of p.items) ul.append(gearItemRow(i));
    box.append(ul);
  }
  return box;
}

/** @param {UiGear} g @param {?string} here id64 of the system you are in now @param {string} ship the planning ship */
function renderGear(g, here, ship) {
  if (!g) return;
  /** @type {HTMLButtonElement} */ ($("b-gear")).disabled = g.status === "running";
  const answered = !(gearAwait >= 0 && g.handled <= gearAwait);
  if (answered && gearAwait >= 0) {
    gearAwait = -1;
    gearLocal = ""; // our request is answered: the server's message now
  }
  if (gearLocal && gearAwait < 0 && g.handled > gearLocalAt) gearLocal = ""; // a newer request answered
  if (!gearLocal) {
    let msg = g.message;
    if (g.status === "done" && g.jumps_status === "running") msg += ` Plotting jumps from ${g.jumps_from}…`;
    gearStatus(msg, answerKind(g.status, g.places.length > 0));
  }
  const fields = $("gear-fields");
  fields.hidden = !g.fields.show;
  if (g.fields.show && answered && gearFieldsHandled !== g.handled) {
    gearFieldsHandled = g.handled;
    input("g-system").value = g.fields.system;
    input("g-station").value = g.fields.station;
    /** @type {HTMLTextAreaElement} */ ($("g-gear")).value = g.fields.gear;
  }
  const key = JSON.stringify([g.places, g.jumps_status, g.jumps_from, g.earlier, here, ship]);
  if (key === gearKey) return;
  gearKey = key;
  const out = $("gear-result");
  const boxes = g.places.map((p) => gearPlaceBox(p, g, here, ship));
  if (g.places.length && g.decoded) boxes.unshift(el("p", "gear-find-head", `Newest · ${clock(g.decoded)}`));
  for (const f of g.earlier) {
    const find = el("div", "gear-earlier");
    const names = [...new Set(f.places.filter((p) => p.found).map((p) => p.system))].join(", ");
    find.append(el("p", "gear-find-head", `Earlier · ${clock(f.decoded)} · ${names}`));
    const counted = { jumps_status: "done", jumps_from: f.jumps_from, jumps_from_id64: f.jumps_from_id64 };
    for (const p of f.places) find.append(gearPlaceBox(p, counted, here, null));
    boxes.push(find);
  }
  out.replaceChildren(...boxes);
  debug.gear.renders += 1;
  debug.gear.places = g.places.length;
  debug.gear.items = g.places.reduce((n, p) => n + p.items.length, 0);
  debug.gear.earlier = g.earlier.length;
}

// --- nearest commodity market ------------------------------------------------------------------------

let commodities = /** @type {string[]} */ ([]);
let commoditiesLoading = false;
let padTouched = false; // the pad was picked by hand: it no longer follows the ship
let marketFormFilled = false; // the commodity field was filled once from the last search (a reopened page)
let marketAwait = -1; // the `handled` count before our last request: its answer is not in yet
let marketLocal = ""; // a local message until the server answers
let marketLocalAt = -1;
let marketKey = "";
const MAX_COMMODITY_SUGGESTIONS = 12;

/** The commodity names the search takes (bundled with the plugin), loaded once. */
async function loadCommodities() {
  if (commodities.length || commoditiesLoading) return;
  commoditiesLoading = true;
  try {
    const resp = await fetch("/api/commodities", { cache: "no-store", signal: AbortSignal.timeout(5000) });
    const doc = await resp.json();
    if (resp.ok && Array.isArray(doc.names)) commodities = doc.names.map(String);
    debug.market.names = commodities.length;
  } catch {
    // the next focus on the box tries again
  } finally {
    commoditiesLoading = false;
  }
}

/** Names starting with what is typed first, then names with a word starting with it, then the rest that
 * contain it. @param {string} q @returns {string[]} */
function commoditiesMatching(q) {
  const t = q.toLowerCase();
  const starts = [], words = [], inside = [];
  for (const name of commodities) {
    const n = name.toLowerCase();
    if (n.startsWith(t)) starts.push(name);
    else if (n.split(/[\s-]+/).some((w) => w.startsWith(t))) words.push(name);
    else if (n.includes(t)) inside.push(name);
  }
  return [...starts, ...words, ...inside].slice(0, MAX_COMMODITY_SUGGESTIONS);
}

/** @param {string} text @param {"idle"|"running"|"done"|"error"|"empty"} kind */
function marketStatus(text, kind) {
  const s = $("market-status");
  s.textContent = text;
  s.dataset.status = kind;
}

/** @param {string} text */
function marketError(text) {
  marketLocal = text;
  marketLocalAt = debug.reply ? debug.reply.state.market.handled : 0;
  marketStatus(text, "error");
}

async function onMarket(/** @type {SubmitEvent} */ ev) {
  ev.preventDefault();
  closeAllSuggestions();
  const typed = input("m-commodity").value.trim();
  // the list not loaded (the server restarting): the server checks the name itself
  const commodity = commodities.length ? commodities.find((n) => n.toLowerCase() === typed.toLowerCase()) : typed;
  if (!commodity) {
    marketError(typed ? `“${typed}” is not a commodity: pick one from the suggestions.` : "Type a commodity first.");
    return;
  }
  input("m-commodity").value = commodity;
  const qtyText = input("m-qty").value;
  const qty = qtyText.trim() ? parseNum(qtyText) : 0;
  if (!Number.isInteger(qty) || qty < 0) {
    marketError("“At least” must be a whole number of tonnes (0 = any).");
    return;
  }
  const body = {
    commodity,
    side: checked("side") || "buy",
    min_qty: qty,
    pad: select("m-pad").value,
    carriers: input("m-carriers").checked,
    max_age_days: Number(select("m-age").value),
    hide_barred: input("m-barred").checked,
  };
  debug.market.sent = body;
  marketLocal = "Sending…";
  marketStatus(marketLocal, "running");
  marketAwait = debug.reply ? debug.reply.state.market.handled : 0;
  const r = await post("/api/market", body);
  if (!r.ok) {
    marketAwait = -1;
    marketError(r.error);
  }
}

/** How old the market data is, in five colour steps: ≤ 1 h, ≤ 6 h, ≤ 1 day, ≤ 2 days, older.
 * @param {string} iso @param {number} now ms @returns {{text: string, step: number}} */
function marketAge(iso, now) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return { text: "?", step: 5 };
  const min = Math.max(0, Math.round((now - t) / 60000));
  const h = min / 60;
  const step = h <= 1 ? 1 : h <= 6 ? 2 : h <= 24 ? 3 : h <= 48 ? 4 : 5;
  if (min < 60) return { text: `${min} min`, step };
  if (h < 48) return { text: `${Math.ceil(h)} h`, step }; // rounded up: "1 h" is never older than an hour
  return { text: `${Math.floor(h / 24)} d ${Math.floor(h % 24)} h`, step };
}

const STANDING_TEXT = {
  hostile: "Hostile to you",
  unfriendly: "Unfriendly",
  neutral: "Neutral",
  cordial: "Cordial",
  friendly: "Friendly",
  allied: "Allied",
};

/** @param {UiMarketRow} r @param {number} now @param {?string} here */
function marketRow(r, now, here) {
  const tr = el("tr", r.barred ? "barred" : "");
  const age = marketAge(r.updated, now);
  const ageTd = el("td", "c");
  const chip = el("span", `age a${age.step}`, age.text);
  chip.title = `Market data from ${new Date(r.updated).toLocaleString()}`;
  ageTd.append(chip);
  const where = el("td");
  where.append(copyName("sys", r.system, (ok) => (debug.market.copied = { name: r.system, ok })));
  const st = el("span", "st", r.station);
  st.append(" ", el("span", "st-type", r.carrier ? "Fleet carrier" : r.station_type));
  where.append(st);
  const atHere = here && r.system_id64 === here;
  const dist = r.distance_ly === null ? "—" : atHere ? "here" : LY.format(r.distance_ly);
  const jumps = r.jumps === null ? "—" : String(r.jumps);
  const faction = factionCell(r);
  const pad = r.pad ? { S: "small", M: "medium", L: "large" }[r.pad] || r.pad : "—";
  tr.append(
    ageTd,
    where,
    el("td", "n", dist),
    el("td", "n", jumps),
    el("td", "c", pad),
    el("td", "n", r.arrival_ls === null ? "—" : CR.format(r.arrival_ls)),
    el("td", "n price key", CR.format(r.price)),
    el("td", "n", CR.format(r.quantity)),
    faction,
  );
  debug.market.rows.push({
    system: r.system,
    station: r.station,
    age: age.text,
    step: age.step,
    cells: [...tr.children].slice(0, 9).map((c) => c.textContent || ""),
  });
  return tr;
}

/**
 * Who runs the station and whether you may dock: a market row or an "Any station" row.
 * @param {{power: string, barred: boolean, faction: string, standing: string, carrier: boolean}} r
 */
function factionCell(r) {
  const faction = el("td");
  if (r.power) {
    faction.append(el("span", "faction", r.power));
    faction.append(el("span", r.barred ? "standing s-hostile" : "standing", r.barred ? "not your power: no docking" : "your power"));
  } else if (r.faction) {
    const f = el("span", "faction", r.faction);
    f.title = r.faction;
    faction.append(f);
    const text = r.standing ? STANDING_TEXT[r.standing] : "standing not known";
    faction.append(el("span", `standing s-${r.standing || "none"}`, r.standing === "hostile" ? `${text}: no docking` : text));
  } else if (r.barred && r.carrier) {
    faction.append(el("span", "standing s-hostile", "closed to visitors"));
  } else if (r.barred) {
    faction.append(el("span", "standing s-hostile", "Stronghold Carrier: pledged pilots only"));
  }
  return faction;
}


/** @param {UiState} s */
function renderMarket(s) {
  const m = s.market;
  if (!m) return;
  if (!padTouched && s.ship_pad) select("m-pad").value = s.ship_pad;
  if (!marketFormFilled) {
    // a reopened page shows the last search's rows: show what was asked with them (once, never over typing)
    marketFormFilled = true;
    if (m.commodity && !input("m-commodity").value) {
      input("m-commodity").value = m.commodity;
      const side = /** @type {?HTMLInputElement} */ (document.querySelector(`input[name="side"][value="${m.side}"]`));
      if (side) side.checked = true;
    }
  }
  /** @type {HTMLButtonElement} */ ($("b-market")).disabled = m.status === "running";
  const answered = !(marketAwait >= 0 && m.handled <= marketAwait);
  if (answered && marketAwait >= 0) {
    marketAwait = -1;
    marketLocal = "";
  }
  if (marketLocal && marketAwait < 0 && m.handled > marketLocalAt) marketLocal = "";
  if (!marketLocal) {
    let msg = m.message;
    if (m.status === "done" && m.searched_from && s.system && m.searched_from !== s.system) {
      msg += ` Searched near ${m.searched_from} at ${clock(m.searched)}; distances are from where you are now.`;
    } else if (m.status === "done" && m.searched) {
      msg += ` (${clock(m.searched)})`;
    }
    marketStatus(msg, answerKind(m.status, m.rows.length > 0));
  }
  const now = Date.now();
  const key = JSON.stringify([m.rows, m.side, s.address]);
  if (key === marketKey) {
    // the same rows: only the data ages move on (a timer calls in), so focus and "Copied" stay
    const chips = document.querySelectorAll("#market-table .age");
    m.rows.forEach((r, i) => {
      const age = marketAge(r.updated, now);
      const chip = chips[i];
      if (chip && chip.textContent !== age.text) {
        chip.textContent = age.text;
        chip.className = `age a${age.step}`;
        debug.market.rows[i].age = age.text;
        debug.market.rows[i].step = age.step;
      }
    });
    return;
  }
  marketKey = key;
  $("m-amount-head").textContent = m.side === "sell" ? "Demand t" : "Stock t";
  $("market-wrap").hidden = !m.rows.length;
  debug.market.rows = [];
  const body = el("tbody");
  for (const r of m.rows) body.append(marketRow(r, now, s.address));
  const table = $("market-table");
  for (const old of table.querySelectorAll("tbody")) old.remove();
  table.append(body);
  debug.market.renders += 1;
}

// --- best trade from a station (one hop: buy here, sell there) -----------------------------------------

const TRADE_VIEW_KEY = "edmultitool.tradeView";
let tradePadTouched = false; // the pad was picked by hand: it no longer follows the ship
let tradePlaceTouched = false; // system / station chosen by hand: they no longer follow where you dock
let stationsFor = ""; // the system the station choice was filled for
// the station to select once that system's list is in (where you docked); kept for a retry of that system
let stationsWanted = { system: "", station: "" };
let stationNote = false; // the local message is about the station choice: a list that loads clears it
let dockedAt = ""; // "system / station" the form last followed
let tradeAwait = -1; // the `handled` count before our last request: its answer is not in yet
let tradeLocal = ""; // a local message until the server answers
let tradeLocalAt = -1;
let tradeKey = "";

/** @param {"trade"|"commodity"|"sell"} view */
function showTradeView(view) {
  $("panel-trade").dataset.view = view;
  $("tv-trade").setAttribute("aria-pressed", String(view === "trade"));
  $("tv-commodity").setAttribute("aria-pressed", String(view === "commodity"));
  $("tv-sell").setAttribute("aria-pressed", String(view === "sell"));
  closeAllSuggestions();
  try {
    localStorage.setItem(TRADE_VIEW_KEY, view);
  } catch {
    // no storage: the view is simply not remembered
  }
}

/** @param {string} text @param {"idle"|"running"|"done"|"error"|"empty"} kind */
function tradeStatus(text, kind) {
  const s = $("trade-status");
  s.textContent = text;
  s.dataset.status = kind;
}

/** @param {string} text @param {"error"|"idle"} [kind] @param {boolean} [aboutStations] */
function tradeError(text, kind = "error", aboutStations = false) {
  tradeLocal = text;
  tradeLocalAt = debug.reply ? debug.reply.state.trade.handled : 0;
  stationNote = aboutStations;
  tradeStatus(text, kind);
}

/** The trouble with the station choice is over: the card's own message again. */
function clearStationNote() {
  if (!stationNote) return;
  tradeLocal = "";
  stationNote = false;
  if (debug.reply) renderTrade(debug.reply.state);
}

/** Fill the station choice with the stations that have a market in `system` (Spansh, via the plugin).
 * @param {string} system */
async function loadStations(system) {
  system = system.trim();
  if (system === stationsFor) return;
  stationsFor = system;
  const box = select("t-station");
  const keep = (stationsWanted.system === system && stationsWanted.station) || box.value;
  /** @param {string} text */
  const only = (text) => {
    box.replaceChildren(new Option(text, ""));
    box.disabled = true;
  };
  if (!system) {
    only("pick a system first");
    return;
  }
  only("loading stations…");
  /** @type {{name: string, type: string, pad: string, arrival_ls: ?number}[]} */
  let rows = [];
  let error = "";
  try {
    const resp = await fetch(`/api/stations?system=${encodeURIComponent(system)}`, { cache: "no-store", signal: AbortSignal.timeout(30000) });
    const doc = await resp.json();
    if (resp.ok && Array.isArray(doc.stations)) rows = doc.stations;
    else error = String(doc.error || `HTTP ${resp.status}`);
  } catch {
    error = "no answer from ED Multitool";
  }
  if (system !== stationsFor) return; // another system was picked meanwhile
  debug.trade.stations = rows.map((r) => r.name);
  if (!rows.length) {
    stationsFor = ""; // asked again on the next pick, Find trade or dock
    clearStationNote(); // an older system's trouble; this one's is said below
    const unknown = error.startsWith("Spansh knows no system");
    only(unknown ? "pick a system from the suggestions" : error ? "stations could not be loaded" : "no station with a market there");
    if (error) tradeError(unknown ? `${error}.` : `Stations of ${system}: ${error}`, "error", true);
    return;
  }
  box.replaceChildren(
    ...rows.map((r) => {
      const facts = [r.type, r.pad ? `pad ${r.pad}` : "", r.arrival_ls === null ? "" : `${CR.format(r.arrival_ls)} ls`].filter(Boolean);
      return new Option(`${r.name} · ${facts.join(" · ")}`, r.name);
    }),
  );
  box.disabled = false;
  clearStationNote();
  const wanted = stationsWanted.system === system ? stationsWanted.station : "";
  stationsWanted = { system: "", station: "" };
  if (rows.some((r) => r.name === keep)) box.value = keep;
  else if (wanted) {
    // where you docked has no market Spansh knows (a fleet carrier, a new station): say so, not a silent swap
    tradeError(`Spansh knows no market at ${wanted}: pick a station from the list.`, "idle", true);
  }
}

/** An optional whole number field: empty = null (the plugin fills it in). @param {string} id */
function optionalInt(id) {
  const text = input(id).value;
  return text.trim() ? parseNum(text) : null;
}

async function onTrade(/** @type {SubmitEvent} */ ev) {
  ev.preventDefault();
  closeAllSuggestions();
  const system = input("t-system").value.trim();
  const station = select("t-station").value;
  if (!system || !station || system !== stationsFor) {
    if (system !== stationsFor || !station) loadStations(system);
    tradeError("Pick the system, then the station you buy at.", "error", true);
    return;
  }
  const cargo = optionalInt("t-cargo");
  if (cargo !== null && (!Number.isInteger(cargo) || cargo < 1)) {
    tradeError("Cargo must be a whole number of tonnes, or empty for your whole hold.");
    return;
  }
  const capital = optionalInt("t-capital");
  if (capital !== null && (!Number.isInteger(capital) || capital < 1)) {
    tradeError("Credits must be a whole number (5m = 5 million), or empty for your whole balance.");
    return;
  }
  const hop = parseNum(input("t-hop").value);
  if (!(hop > 0)) {
    tradeError("“Sell within” must be a distance in ly above 0.");
    return;
  }
  const arrival = parseNum(input("t-arrival").value);
  if (!Number.isInteger(arrival) || arrival < 1) {
    tradeError("“Arrival up to” must be a whole number of ls above 0.");
    return;
  }
  const body = {
    system,
    station,
    cargo,
    capital,
    max_hop_ly: hop,
    max_arrival_ls: arrival,
    max_age_days: Number(select("t-age").value),
    pad: select("t-pad").value,
    carriers: input("t-carriers").checked,
    planetary: input("t-planetary").checked,
  };
  debug.trade.sent = body;
  tradeLocal = "Sending…";
  stationNote = false;
  tradeStatus(tradeLocal, "running");
  tradeAwait = debug.reply ? debug.reply.state.trade.handled : 0;
  const r = await post("/api/trade", body);
  if (!r.ok) {
    tradeAwait = -1;
    tradeError(r.error);
  }
}

/** One leg of the trade: the system (copy icon), the station, its arrival distance and data age.
 * @param {UiTradeStation} st @param {string[]} extra facts after the station's own @param {number} now */
function tradeLeg(st, extra, now) {
  const dd = document.createDocumentFragment();
  dd.append(copyName("sys", st.system, (ok) => (debug.trade.copied = { name: st.system, ok })));
  dd.append(el("span", "st", st.station));
  const facts = el("span", "leg-facts");
  const parts = [];
  if (st.arrival_ls !== null) parts.push(`${CR.format(st.arrival_ls)} ls from the star`);
  facts.append(parts.concat(extra).join(" · "));
  if (st.updated) {
    const age = marketAge(st.updated, now);
    const chip = el("span", `age a${age.step}`, age.text);
    chip.title = `Market data from ${new Date(st.updated).toLocaleString()}`;
    facts.append(parts.length || extra.length ? " · data " : "data ", chip);
  }
  dd.append(facts);
  return dd;
}

/** @param {UiState} s */
function renderTrade(s) {
  const t = s.trade;
  if (!t) return;
  if (!tradePadTouched && s.ship_pad) select("t-pad").value = s.ship_pad;
  const docked = `${t.docked_system} / ${t.docked_station}`;
  if (!tradePlaceTouched && t.docked_station && docked !== dockedAt) {
    dockedAt = docked; // the form follows each new dock until you choose a place yourself
    input("t-system").value = t.docked_system;
    const box = select("t-station");
    if (stationsFor === t.docked_system && !box.disabled && [...box.options].some((o) => o.value === t.docked_station)) {
      box.value = t.docked_station;
      clearStationNote();
    } else {
      stationsWanted = { system: t.docked_system, station: t.docked_station };
      if (stationsFor === t.docked_system) stationsFor = ""; // the same system, but its station is not listed
      loadStations(t.docked_system);
    }
  }
  input("t-cargo").placeholder = t.hold === null ? "your hold" : `${CR.format(t.hold)} (hold)`;
  input("t-capital").placeholder = t.balance === null ? "your balance" : `${CR.format(t.balance)} (all)`;
  /** @type {HTMLButtonElement} */ ($("b-trade")).disabled = t.status === "running";
  const answered = !(tradeAwait >= 0 && t.handled <= tradeAwait);
  if (answered && tradeAwait >= 0) {
    tradeAwait = -1;
    tradeLocal = "";
  }
  if (tradeLocal && tradeAwait < 0 && t.handled > tradeLocalAt) tradeLocal = "";
  if (!tradeLocal) {
    const msg = t.status === "done" && t.searched ? `${t.message} (${clock(t.searched)})` : t.message;
    tradeStatus(msg, answerKind(t.status, t.goods.length > 0));
  }
  const now = Date.now();
  const key = JSON.stringify([t.source, t.destination, t.goods, t.jumps, Math.floor(now / 60000)]);
  if (key === tradeKey) return; // the same trade, the same minute: focus and "Copied" stay
  tradeKey = key;
  const shown = Boolean(t.source && t.destination && t.goods.length);
  $("trade-result").hidden = !shown;
  debug.trade.legs = [];
  debug.trade.rows = [];
  debug.trade.total = "";
  if (!t.source || !t.destination || !shown) return;
  const trip = [`${LY.format(t.distance_ly || 0)} ly`];
  if (t.jumps !== null) {
    trip.push(`≥ ${t.jumps} jump${t.jumps === 1 ? "" : "s"}` + (t.laden_ly !== null ? ` at ${RANGE.format(t.laden_ly)} ly laden` : ""));
  }
  $("t-from").replaceChildren(tradeLeg(t.source, [], now));
  $("t-to").replaceChildren(tradeLeg(t.destination, trip, now));
  debug.trade.legs = [$("t-from").textContent || "", $("t-to").textContent || ""];
  const body = el("tbody");
  for (const g of t.goods) {
    const tr = el("tr");
    tr.append(
      el("td", "good", g.name),
      el("td", "n", CR.format(g.amount)),
      el("td", "n", CR.format(g.buy_price)),
      el("td", "n", CR.format(g.sell_price)),
      el("td", "n", CR.format(g.profit)),
      el("td", "n price key", CR.format(g.total_profit)),
      el("td", "n", CR.format(g.supply)),
      el("td", "n", CR.format(g.demand)),
    );
    body.append(tr);
    debug.trade.rows.push([...tr.children].map((c) => c.textContent || ""));
  }
  const table = $("trade-table");
  for (const old of table.querySelectorAll("tbody")) old.remove();
  table.append(body);
  const load = t.goods.reduce((sum, g) => sum + g.amount, 0);
  const total = $("t-total");
  total.replaceChildren(
    `Load ${CR.format(load)} t for ${CR.format(t.cost)} Cr → profit `,
    el("span", "profit key", `${CR.format(t.total_profit)} Cr`),
  );
  total.title = `Searched with ${CR.format(t.cargo)} t of hold and ${CR.format(t.capital)} Cr to spend`;
  debug.trade.total = total.textContent || "";
  debug.trade.renders += 1;
}

// --- sell cargo: the best price for each commodity in the hold, and the one station for it all ----------

let sellPadTouched = false; // the pad was picked by hand: it no longer follows the ship
let sellAwait = -1; // the `handled` count before our last request: its answer is not in yet
let sellLocal = ""; // a local message until the server answers
let sellLocalAt = -1;
let sellKey = "";

/** @param {string} text @param {"idle"|"running"|"done"|"error"|"empty"} kind */
function sellStatus(text, kind) {
  const s = $("sell-status");
  s.textContent = text;
  s.dataset.status = kind;
}

/** @param {string} text */
function sellError(text) {
  sellLocal = text;
  sellLocalAt = debug.reply ? debug.reply.state.sell.handled : 0;
  sellStatus(text, "error");
}

async function onSell(/** @type {SubmitEvent} */ ev) {
  ev.preventDefault();
  const ly = parseNum(input("sell-ly").value);
  if (!(ly > 0) || ly > 500) {
    sellError("“Sell within” must be a distance above 0 and at most 500 ly.");
    return;
  }
  const body = {
    max_ly: ly,
    pad: select("sell-pad").value,
    carriers: input("sell-carriers").checked,
    max_age_days: Number(select("sell-age").value),
    hide_barred: input("sell-barred").checked,
  };
  debug.sell.sent = body;
  sellLocal = "Sending…";
  sellStatus(sellLocal, "running");
  sellAwait = debug.reply ? debug.reply.state.sell.handled : 0;
  const r = await post("/api/sell", body);
  if (!r.ok) {
    sellAwait = -1;
    sellError(r.error);
  }
}

/** The data age chip of a station's market. @param {string} iso @param {number} now */
function ageChip(iso, now) {
  const age = marketAge(iso, now);
  const chip = el("span", `age a${age.step}`, age.text);
  chip.title = `Market data from ${new Date(iso).toLocaleString()}`;
  return chip;
}

/** @param {UiSellPlace} p @param {?string} here */
function sellDist(p, here) {
  return distText(p.distance_ly, Boolean(here && p.system_id64 === here));
}

/** @param {?string} pad */
function padText(pad) {
  return pad ? PAD_TEXT[/** @type {"S"|"M"|"L"} */ (pad)] || pad : "—";
}

/** @param {UiState} s */
function renderSell(s) {
  const m = s.sell;
  if (!m) return;
  if (!sellPadTouched && s.ship_pad) select("sell-pad").value = s.ship_pad;
  /** @type {HTMLButtonElement} */ ($("b-sell")).disabled = m.status === "running";
  const answered = !(sellAwait >= 0 && m.handled <= sellAwait);
  if (answered && sellAwait >= 0) {
    sellAwait = -1;
    sellLocal = "";
  }
  if (sellLocal && sellAwait < 0 && m.handled > sellLocalAt) sellLocal = "";
  if (!sellLocal) {
    let msg = m.message;
    if (m.status === "done" && m.searched_from && s.system && m.searched_from !== s.system) {
      msg += ` Searched near ${m.searched_from} at ${clock(m.searched)}; distances are from where you are now.`;
    } else if (m.status === "done" && m.searched) {
      msg += ` (${clock(m.searched)})`;
    }
    if (m.changed && m.status !== "running") msg += " Your hold has changed since: search again.";
    sellStatus(msg, answerKind(m.status, m.goods.some((g) => g.offers.length > 0)));
  }
  const now = Date.now();
  $("sell-rough").hidden = !m.hold_rough;
  debug.sell.rough = m.hold_rough;
  const key = JSON.stringify([m.hold, m.goods, m.best, m.refined, m.max_ly, s.address, Math.floor(now / 60000)]);
  if (key === sellKey) return; // the same answer, the same minute: focus and "Copied" stay
  sellKey = key;
  renderSellHold(m);
  renderSellBest(m, s.address, now);
  renderSellTable(m, s.address, now);
  debug.sell.renders += 1;
}

/** What the hold holds now, and what you mined this session. @param {UiSell} m */
function renderSellHold(m) {
  const box = $("sell-hold");
  debug.sell.hold = [];
  if (!m.hold.length) {
    box.replaceChildren(el("span", "quiet", "Your hold is empty, or the game has not listed it yet."));
  } else {
    const list = el("ul", "hold-list");
    for (const line of m.hold) {
      const li = el("li", line.note ? "hold-line aside" : "hold-line");
      li.append(el("span", "good", line.name), " ", el("span", "t", `${CR.format(line.tonnes)} t`));
      if (line.note) li.append(" ", el("span", "note", line.note));
      list.append(li);
      debug.sell.hold.push([line.name, String(line.tonnes), line.note]);
    }
    box.replaceChildren(el("span", "hold-head", "In your hold"), list);
  }
  const r = m.refined;
  const refined = $("sell-refined");
  refined.hidden = !r.tonnes;
  if (r.tonnes) {
    const by = r.by.map(([name, t]) => `${name} ${CR.format(t)} t`).join(", ");
    const rate = r.per_hour === null ? "" : ` · ${DEC1.format(r.per_hour)} t per hour since ${clock(r.since)}`;
    refined.textContent = `Mined this session: ${CR.format(r.tonnes)} t (${by})${rate}`;
  }
  debug.sell.refined = r.tonnes ? refined.textContent || "" : "";
}

const DEC1 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

/** The one station paying most for the whole load. @param {UiSell} m @param {?string} here @param {number} now */
function renderSellBest(m, here, now) {
  const b = m.best;
  $("sell-best").hidden = !b;
  debug.sell.best = [];
  debug.sell.total = "";
  if (!b) return;
  const p = b.place;
  const dd = document.createDocumentFragment();
  dd.append(copyName("sys", p.system, (ok) => (debug.sell.copied = { name: p.system, ok })));
  dd.append(el("span", "st", p.station));
  const d = sellDist(p, here);
  const facts = [d === "here" ? "in this system" : d === "—" ? "distance not known" : `${d} ly`];
  if (p.jumps !== null && d !== "here" && d !== "—") facts.push(`≥ ${p.jumps} jump${p.jumps === 1 ? "" : "s"}`);
  if (p.pad) facts.push(`pad ${padText(p.pad)}`);
  if (p.arrival_ls !== null) facts.push(`${CR.format(p.arrival_ls)} ls from the star`);
  const line = el("span", "leg-facts", `${facts.join(" · ")} · data `);
  line.append(ageChip(p.updated, now));
  dd.append(line);
  $("sell-best-at").replaceChildren(dd);
  const body = el("tbody");
  for (const l of b.lines) {
    const tr = el("tr");
    tr.append(el("td", "good", l.name), el("td", "n", CR.format(l.tonnes)), el("td", "n", CR.format(l.price)), el("td", "n price key", CR.format(l.value)));
    body.append(tr);
    debug.sell.best.push([...tr.children].map((c) => c.textContent || ""));
  }
  const table = $("sell-best-table");
  for (const old of table.querySelectorAll("tbody")) old.remove();
  table.append(body);
  const whole = !b.left.length;
  $("sell-best-label").textContent = whole ? "Sell it all at" : "Best one stop";
  const total = $("sell-total");
  total.replaceChildren(whole ? "All of it there: " : "There: ", el("span", "profit key", `${CR.format(b.total)} Cr`));
  if (!whole) total.append(el("span", "left", ` · none of the stations found buys all of it; not here: ${b.left.join(", ")} (see the best prices below)`));
  const richer = $("sell-richer");
  richer.hidden = !b.richer;
  if (b.richer) {
    const r = b.richer;
    const rd = sellDist(r.place, here);
    const where = rd === "here" ? "in this system" : rd === "—" ? "" : ` (${rd} ly)`;
    richer.replaceChildren(
      "Pays more for part of it: ",
      copyName("sys", r.place.system, (ok) => (debug.sell.copied = { name: r.place.system, ok })),
      el("span", "st", r.place.station),
      `${where} · ${r.lines.map((l) => l.name).join(", ")} for `,
      el("span", "profit", `${CR.format(r.total)} Cr`),
      ` · not there: ${r.left.join(", ")}`,
    );
  }
  debug.sell.total = total.textContent || "";
  debug.sell.richer = b.richer ? richer.textContent || "" : "";
}

/** Each commodity's best offers. @param {UiSell} m @param {?string} here @param {number} now */
function renderSellTable(m, here, now) {
  $("sell-wrap").hidden = !m.goods.length;
  debug.sell.rows = [];
  const body = el("tbody");
  for (const g of m.goods) {
    const head = el("td", "good");
    head.rowSpan = Math.max(1, g.offers.length);
    head.append(el("span", "good-name", g.name), el("span", "good-t", `${CR.format(g.tonnes)} t`));
    if (g.mined) head.append(el("span", "mined", "mined"));
    if (!g.offers.length) {
      const tr = el("tr", "group");
      const none = el("td", "quiet", `No station within ${DEC1.format(m.max_ly)} ly buys all ${CR.format(g.tonnes)} t with these filters.`);
      none.colSpan = 10;
      tr.append(head, none);
      body.append(tr);
      debug.sell.rows.push([g.name, none.textContent || ""]);
      continue;
    }
    g.offers.forEach((o, i) => {
      const p = o.place;
      const tr = el("tr", [i === 0 ? "group" : "", p.barred ? "barred" : ""].filter(Boolean).join(" "));
      if (i === 0) tr.append(head);
      const ageTd = el("td", "c");
      ageTd.append(ageChip(p.updated, now));
      tr.append(
        ageTd,
        sellWhere(p),
        el("td", "n", sellDist(p, here)),
        el("td", "n", p.jumps === null ? "—" : String(p.jumps)),
        el("td", "c", padText(p.pad)),
        el("td", "n", p.arrival_ls === null ? "—" : CR.format(p.arrival_ls)),
        el("td", "n price", CR.format(o.price)),
        el("td", "n", CR.format(o.demand)),
        el("td", "n value key", CR.format(o.value)),
        factionCell(p),
      );
      body.append(tr);
      const cells = [...tr.children].slice(i === 0 ? 1 : 0).map((c) => c.textContent || "");
      debug.sell.rows.push([g.name, ...cells]);
    });
  }
  const table = $("sell-table");
  for (const old of table.querySelectorAll("tbody")) old.remove();
  table.append(body);
}

/** System (copy icon) and station of a place to sell. @param {UiSellPlace} p */
function sellWhere(p) {
  const td = el("td");
  td.append(copyName("sys", p.system, (ok) => (debug.sell.copied = { name: p.system, ok })));
  const st = el("span", "st", p.station);
  st.append(" ", el("span", "st-type", p.carrier ? "Fleet carrier" : p.station_type));
  td.append(st);
  return td;
}

// --- nearest engineer · nearest station service --------------------------------------------------------

const ENG_SHOWN = 10; // nearest engineers shown unless "Show all"
const PAD_TEXT = { S: "small", M: "medium", L: "large" };
let engKey = "";
let svcKey = "";
let svcPadTouched = false; // the pad was picked by hand: it no longer follows the ship
let svcAwait = -1; // the `handled` count before our last request: its answer is not in yet
let svcLocal = ""; // a local message until the server answers
let svcLocalAt = -1;
let svcFormSet = false; // the form was set once to the list shown when the page opened

/** @param {?number} d @param {boolean} here */
function distText(d, here) {
  return d === null ? "—" : here ? "here" : LY.format(d);
}

/** The "Improves" choices, from the engineers themselves (once). @param {UiEngineer[]} rows */
function fillModules(rows) {
  const sel = select("eng-module");
  if (sel.options.length > 1 || !rows.length) return;
  const names = [...new Set(rows.flatMap((r) => r.modules.map((m) => m.name)))].sort((a, b) => a.localeCompare(b));
  for (const n of names) sel.append(new Option(n, n));
}

/** @param {UiState} s */
function renderEngineers(s) {
  const all = s.engineers || [];
  fillModules(all);
  const kind = checked("eng-kind") || "ship";
  const module = select("eng-module").value;
  const grade = Number(select("eng-grade").value);
  select("eng-grade").disabled = !module;
  const showAll = input("eng-all").checked;
  const key = JSON.stringify([all, kind, module, grade, showAll, s.address, s.system]);
  if (key === engKey) return;
  engKey = key;
  let rows = all.filter((r) => (kind === "all" ? true : kind === "ship" ? r.ship : !r.ship));
  if (module) rows = rows.filter((r) => r.modules.some((m) => m.name === module && m.grade >= grade));
  const matched = rows.length;
  const shown = showAll ? rows : rows.slice(0, ENG_SHOWN);
  debug.engineers.rows = [];
  debug.engineers.matched = matched;
  const body = el("tbody");
  for (const r of shown) {
    const tr = el("tr");
    const who = el("td");
    who.append(
      named(copyIcon(r.system, (ok) => (debug.engineers.copied = { name: r.system, ok })), el("span", "sys", r.name)),
      el("span", "st", r.ship ? "ship" : "on foot"),
    );
    const where = el("td");
    where.append(el("span", "sys", r.system));
    const st = el("span", "st", r.base);
    st.append(" ", el("span", "st-type", r.base_type));
    where.append(st);
    const mods = el("td", "mods");
    if (r.modules.length) {
      for (const m of r.modules) mods.append(el("span", m.name === module ? "mod mod-hit" : "mod", `${m.name} ${m.grade}`), " ");
    } else {
      mods.append(el("span", "st", "suits and personal weapons"));
    }
    tr.append(
      who,
      where,
      el("td", "n key", distText(r.distance_ly, !!s.address && r.system_id64 === s.address)),
      el("td", "n", r.jumps === null ? "—" : String(r.jumps)),
      mods,
    );
    debug.engineers.rows.push({ name: r.name, cells: [...tr.children].slice(0, 5).map((c) => c.textContent || "") });
    body.append(tr);
  }
  const table = $("eng-table");
  for (const old of table.querySelectorAll("tbody")) old.remove();
  table.append(body);
  const what = module ? ` that improve ${module} to grade ${grade} or more` : "";
  const many = `${matched} engineer${matched !== 1 ? "s" : ""}${what}`;
  const st = $("eng-status");
  st.textContent = !matched
    ? `No engineer${what}.`
    : !s.system
      ? `${many}. Where you are is not known yet: no distances.`
      : `${many}, nearest to ${s.system} first` + (shown.length < matched ? ` (showing ${shown.length}).` : ".");
  st.dataset.status = matched ? "done" : "error";
  debug.engineers.renders += 1;
}

/** @param {string} text @param {"idle"|"running"|"done"|"error"|"empty"} kind */
function svcStatus(text, kind) {
  const s = $("svc-status");
  s.textContent = text;
  s.dataset.status = kind;
}

const SVC_VIEW_KEY = "edmultitool.servicesView";
const SVC_HAS_KEY = "edmultitool.stationServices";
const TRADER = "Material Trader";
const BROKER = "Technology Broker";

/** @returns {"services"|"engineers"} what the card shows */
function svcView() {
  return $("services").dataset.view === "engineers" ? "engineers" : "services";
}

/** Show the stations or the engineers in the card (remembered in this browser).
 * @param {"services"|"engineers"} view */
function showSvcView(view) {
  $("services").dataset.view = view;
  $("svc-stations").setAttribute("aria-pressed", String(view === "services"));
  $("svc-engineers").setAttribute("aria-pressed", String(view === "engineers"));
  try {
    localStorage.setItem(SVC_VIEW_KEY, view);
  } catch {
    // no storage: the view is simply not remembered
  }
  if (debug.reply) renderServices(debug.reply.state);
}

/** @returns {HTMLInputElement[]} the service boxes */
function hasBoxes() {
  return /** @type {HTMLInputElement[]} */ ([...document.querySelectorAll("#svc-has input[data-has]")]);
}

/** @returns {string[]} the services ticked, Spansh's names */
function hasTicked() {
  return hasBoxes().filter((b) => b.checked).map((b) => b.dataset.has || "");
}

/** A trader's / broker's kind is offered only while it is ticked. */
function syncKinds() {
  const ticked = hasTicked();
  $("svc-trader-field").hidden = !ticked.includes(TRADER);
  $("svc-broker-field").hidden = !ticked.includes(BROKER);
}

/** What is ticked and picked, remembered in this browser. */
function saveTicks() {
  try {
    const kinds = { trader: select("svc-trader-kind").value, broker: select("svc-broker-kind").value };
    localStorage.setItem(SVC_HAS_KEY, JSON.stringify({ services: hasTicked(), ...kinds }));
  } catch {
    // storage blocked: the ticks are not remembered
  }
}

function loadTicks() {
  try {
    const had = JSON.parse(localStorage.getItem(SVC_HAS_KEY) || "[]");
    const services = Array.isArray(had) ? had : Array.isArray(had.services) ? had.services : [];
    for (const b of hasBoxes()) b.checked = services.includes(b.dataset.has);
    if (!Array.isArray(had)) {
      select("svc-trader-kind").value = typeof had.trader === "string" ? had.trader : "";
      select("svc-broker-kind").value = typeof had.broker === "string" ? had.broker : "";
    }
  } catch {
    // no storage or a damaged entry: nothing ticked
  }
  syncKinds();
}

/** @param {?Event} [e] */
async function onFind(e) {
  if (e) e.preventDefault();
  const services = hasTicked();
  const body = {
    pad: select("svc-pad").value,
    carriers: input("svc-carriers").checked,
    settlements: input("svc-settlements").checked,
    hide_barred: input("svc-barred").checked,
    services,
    trader_kind: services.includes(TRADER) ? select("svc-trader-kind").value : "",
    broker_kind: services.includes(BROKER) ? select("svc-broker-kind").value : "",
  };
  debug.services.sent = body;
  svcLocal = "Sending…";
  svcStatus(svcLocal, "running");
  svcAwait = debug.reply ? debug.reply.state.services.handled : 0;
  const r = await post("/api/services", body);
  if (!r.ok) {
    svcAwait = -1;
    svcLocal = r.error;
    svcLocalAt = debug.reply ? debug.reply.state.services.handled : 0;
    svcStatus(r.error, "error");
  }
}

let svcAgain = 0; // the timer of a search asked again after a change

/** An option changed while a list is shown: ask again with it, once the clicking stops. */
function onSvcOption() {
  syncKinds();
  saveTicks();
  clearTimeout(svcAgain);
  svcAgain = setTimeout(() => {
    const v = debug.reply && debug.reply.state.services;
    if (v && v.searched) onFind();
  }, 600);
}

/** @param {UiState} s */
function renderServices(s) {
  const v = s.services;
  if (!v) return;
  if (!svcPadTouched && s.ship_pad) select("svc-pad").value = s.ship_pad;
  if (!svcFormSet && v.searched) {
    // the page opened on a list searched before (F5, another window): tick what it was searched for
    for (const b of hasBoxes()) b.checked = v.asked.includes(b.dataset.has || "");
    select("svc-trader-kind").value = v.trader_kind;
    select("svc-broker-kind").value = v.broker_kind;
    if (v.pad) {
      select("svc-pad").value = v.pad;
      svcPadTouched = true; // the pad the list was searched with, not the ship's
    }
    input("svc-carriers").checked = v.carriers;
    input("svc-settlements").checked = v.settlements;
    input("svc-barred").checked = v.hide_barred;
    syncKinds();
    saveTicks();
  }
  svcFormSet = true;
  /** @type {HTMLButtonElement} */ ($("svc-find")).disabled = v.status === "running";
  const answered = !(svcAwait >= 0 && v.handled <= svcAwait);
  if (answered && svcAwait >= 0) {
    svcAwait = -1;
    svcLocal = "";
  }
  if (svcLocal && svcAwait < 0 && v.handled > svcLocalAt) svcLocal = "";
  const rows = v.rows;
  if (!svcLocal) {
    let msg = v.message;
    if (v.status === "done" && v.searched_from && s.system && v.searched_from !== s.system) {
      msg += ` Searched near ${v.searched_from} at ${clock(v.searched)}; distances are from where you are now.`;
    } else if (v.status === "done" && v.searched) {
      msg += ` (${clock(v.searched)})`;
    }
    svcStatus(msg, answerKind(v.status, rows.length > 0));
  }
  const kinds = v.asked.includes(TRADER) || v.asked.includes(BROKER);
  const key = JSON.stringify([rows, kinds, s.address]);
  if (key === svcKey) return;
  svcKey = key;
  $("svc-kind-col").hidden = !kinds;
  $("svc-wrap").hidden = !rows.length;
  debug.services.rows = [];
  const body = el("tbody");
  for (const r of rows) {
    const tr = el("tr");
    const where = el("td");
    where.append(copyName("sys", r.system, (ok) => (debug.services.copied = { name: r.system, ok })));
    const st = el("span", "st", r.station);
    st.append(" ", el("span", "st-type", r.station_type));
    where.append(st);
    const kind = el("td", "c", r.kind || "—");
    kind.hidden = !kinds;
    tr.append(
      where,
      el("td", "n key", distText(r.distance_ly, !!s.address && r.system_id64 === s.address)),
      el("td", "n", r.jumps === null ? "—" : String(r.jumps)),
      el("td", "c", r.pad ? PAD_TEXT[r.pad] : "—"),
      el("td", "n", r.arrival_ls === null ? "—" : CR.format(r.arrival_ls)),
      kind,
      factionCell(r),
    );
    if (r.barred) tr.classList.add("barred");
    debug.services.rows.push({
      system: r.system,
      station: r.station,
      cells: [...tr.children].filter((c) => !(/** @type {HTMLElement} */ (c).hidden)).map((c) => c.textContent || ""),
    });
    body.append(tr);
  }
  const table = $("svc-table");
  for (const old of table.querySelectorAll("tbody")) old.remove();
  table.append(body);
  debug.services.renders += 1;
}
// --- nearest system by Powerplay power --------------------------------------------------------------

// the states each way of asking can be narrowed to (edmultitool.catalog.powers)
const PP_STATES = {
  held: ["Exploited", "Fortified", "Stronghold"],
  others: ["Exploited", "Fortified", "Stronghold"],
  acquire: [], // Unoccupied only: nothing to pick
};
let ppKey = "";
let ppPowerTouched = false; // the power was picked by hand: it no longer follows the pledge
let ppAwait = -1; // the `handled` count before our last request: its answer is not in yet
let ppLocal = ""; // a local message until the server answers
let ppLocalAt = -1;
let ppKeptState = ""; // the state picked before switching to "It can acquire" (which has none)
let ppFormAt = -1; // the `handled` count whose search the form was set back to

/** @param {string} text @param {"idle"|"running"|"done"|"error"|"empty"} kind */
function ppStatus(text, kind) {
  const s = $("pp-status");
  s.textContent = text;
  s.dataset.status = kind;
}

/** The power choice (once). The form shows the search the table answers (after F5 too); before any
 * search, the pledged power is picked until one is picked by hand. @param {UiState} s */
function fillPowers(s) {
  const sel = select("pp-power");
  if (!sel.options.length && s.powers && s.powers.length) {
    for (const p of s.powers) sel.append(new Option(p, p));
  }
  const v = s.powerplay;
  if (v.power && v.status === "done" && v.handled !== ppFormAt) {
    ppFormAt = v.handled;
    ppPowerTouched = true;
    sel.value = v.power;
    const mode = radio("pp-mode", v.mode);
    if (mode) mode.checked = true;
    fillStates();
    select("pp-state").value = v.state;
  } else if (!ppPowerTouched && !v.power && s.pledged && s.powers.includes(s.pledged)) {
    sel.value = s.pledged;
  }
}

/** The "State" choices for the way of asking; keeps the choice when it still fits. */
function fillStates() {
  const mode = /** @type {keyof typeof PP_STATES} */ (checked("pp-mode") || "held");
  const states = PP_STATES[mode];
  const sel = select("pp-state");
  sel.disabled = !states.length;
  const first = states.length ? "any" : "Unoccupied"; // acquire: Unoccupied only, shown, not picked
  if ([...sel.options].map((o) => o.text).join("|") === [first, ...states].join("|")) return;
  const was = sel.value || ppKeptState;
  if (!states.length) ppKeptState = sel.value; // back from acquire, the state picked before returns
  sel.replaceChildren(new Option(first, ""), ...states.map((x) => new Option(x, x)));
  sel.value = states.includes(was) ? was : "";
}

async function onPowerplay(/** @type {SubmitEvent} */ ev) {
  ev.preventDefault();
  const body = { power: select("pp-power").value, mode: checked("pp-mode") || "held", state: select("pp-state").value };
  debug.powerplay.sent = body;
  ppLocal = "Sending…";
  ppStatus(ppLocal, "running");
  ppAwait = debug.reply ? debug.reply.state.powerplay.handled : 0;
  const r = await post("/api/powerplay", body);
  if (!r.ok) {
    ppAwait = -1;
    ppLocal = r.error;
    ppLocalAt = debug.reply ? debug.reply.state.powerplay.handled : 0;
    ppStatus(r.error, "error");
  }
}

// what your merits do in a system: mark, word, and the full sentence (title)
/** @type {Record<string, [string, string, (r: UiPowerRow, you: string) => string]>} */
const EFFECTS = {
  reinforce: ["▲", "Reinforce", (r, you) => `Reinforcement: your merits strengthen ${you}'s hold on it`],
  undermine: ["▼", "Undermine", (r, you) => `Undermining: your merits weaken ${r.held_by}'s hold on it, for ${you}`],
  acquire: ["✚", "Acquire", (r, you) => `Acquisition: your merits help ${you} take this Unoccupied system`],
  "out-of-reach": ["–", "Out of reach", (r, you) => `${you} is not present here: it cannot acquire this system yet`],
};

/** @param {UiPowerRow} r @param {string} pledged */
function effectCell(r, pledged) {
  const td = el("td", "c");
  const e = r.effect ? EFFECTS[r.effect] : null;
  if (!e) {
    const none = el("span", "st", "—");
    none.title = "Not pledged to a power: you earn no merits here";
    td.append(none);
    return td;
  }
  const chip = el("span", `pp-effect e-${r.effect}`, `${e[0]} ${e[1]}`);
  chip.title = e[2](r, pledged);
  td.append(chip);
  return td;
}

/** @param {?number} n */
const meritsText = (n) => (n === null ? "—" : CR.format(n));

/** @param {UiPowerRow} r @param {number} now @param {?string} here @param {string} pledged */
function powerRow(r, now, here, pledged) {
  const tr = el("tr");
  const age = marketAge(r.updated, now);
  const ageTd = el("td", "c");
  const chip = el("span", `age a${age.step}`, age.text);
  chip.title = `System data from ${new Date(r.updated).toLocaleString()}`;
  ageTd.append(chip);
  const where = el("td");
  where.append(copyName("sys", r.system, (ok) => (debug.powerplay.copied = { name: r.system, ok })));
  const held = el("td");
  const yours = !!r.held_by && r.held_by === pledged;
  held.append(el("span", yours ? "faction pp-yours" : "faction", r.held_by || "nobody"));
  if (r.others.length) {
    const others = el("span", "st", `+ ${r.others.length} other power${r.others.length !== 1 ? "s" : ""}`);
    others.title = `Also present: ${r.others.join(", ")}`;
    held.append(others);
  }
  tr.append(
    ageTd,
    where,
    el("td", "n key", distText(r.distance_ly, !!here && r.system_id64 === here)),
    el("td", "n", r.jumps === null ? "—" : String(r.jumps)),
    el("td", "c", r.state || "—"),
    effectCell(r, pledged),
    el("td", "n", r.progress_pct === null ? "—" : `${r.progress_pct} %`),
    el("td", "n", meritsText(r.reinforcement)),
    el("td", "n", meritsText(r.undermining)),
    held,
  );
  debug.powerplay.rows.push({
    system: r.system,
    age: age.text,
    step: age.step,
    cells: [...tr.children].slice(0, 10).map((c) => c.textContent || ""),
    effect: r.effect,
  });
  return tr;
}

/** @param {UiState} s */
function renderPowerplay(s) {
  const v = s.powerplay;
  if (!v) return;
  fillPowers(s);
  /** @type {HTMLButtonElement} */ ($("b-pp")).disabled = v.status === "running";
  const answered = !(ppAwait >= 0 && v.handled <= ppAwait);
  if (answered && ppAwait >= 0) {
    ppAwait = -1;
    ppLocal = "";
  }
  if (ppLocal && ppAwait < 0 && v.handled > ppLocalAt) ppLocal = "";
  if (!ppLocal) {
    let msg = v.message;
    if (v.status === "done" && v.searched_from && s.system && v.searched_from !== s.system) {
      msg += ` Searched near ${v.searched_from} at ${clock(v.searched)}; distances are from where you are now.`;
    } else if (v.status === "done" && v.searched) {
      msg += ` (${clock(v.searched)})`;
    }
    ppStatus(msg, answerKind(v.status, v.rows.length > 0));
  }
  const now = Date.now();
  const key = JSON.stringify([v.rows, s.address, s.pledged]);
  if (key === ppKey) {
    // the same rows: only the data ages move on (a timer calls in), so focus and "Copied" stay
    const chips = document.querySelectorAll("#pp-table .age");
    v.rows.forEach((r, i) => {
      const age = marketAge(r.updated, now);
      const chip = chips[i];
      if (chip && chip.textContent !== age.text) {
        chip.textContent = age.text;
        chip.className = `age a${age.step}`;
        debug.powerplay.rows[i].age = age.text;
        debug.powerplay.rows[i].step = age.step;
      }
    });
    return;
  }
  ppKey = key;
  $("pp-wrap").hidden = !v.rows.length;
  debug.powerplay.rows = [];
  const body = el("tbody");
  for (const r of v.rows) body.append(powerRow(r, now, s.address, s.pledged));
  const table = $("pp-table");
  for (const old of table.querySelectorAll("tbody")) old.remove();
  table.append(body);
  debug.powerplay.renders += 1;
}

/** @param {string} iso @returns {string} "16:41" */
function clock(iso) {
  return iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
}

// --- the Commander tab: superpower reputation, navy ranks, Powerplay, and how they moved ------------

const SUPERPOWER_COLOURS = { Federation: "--sp-fed", Empire: "--sp-emp", Alliance: "--sp-all", Independent: "--sp-ind" };
const NAVY_COLOURS = { Federation: "--sp-fed", Empire: "--sp-emp" };
const NAVIES = ["Federation", "Empire"];
// the ranks in the game's own panel order, two to a row, then the navies
const RANK_ORDER = ["Combat", "Explore", "Trade", "CQC", "Soldier", "Exobiologist", "Federation", "Empire"];
const SUPERPOWERS = ["Federation", "Empire", "Alliance", "Independent"];
const TIER_BOUNDS = [-90, -35, 4, 35, 90]; // where the reputation words change (the hint names them)
const SVG_NS = "http://www.w3.org/2000/svg";
let crKey = ""; // what the cards were last drawn from
let crPledgeKey = ""; // the pledge facts last drawn
let crAsked = ""; // the power whose systems this page asked to count since the tab was last shown
let crAskedAt = 0; // when (a tab left open asks again once the plugin's count has gone stale)

/** @param {string} iso @returns {string} "11 Oct 2026" */
function dayText(iso) {
  return new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso).toLocaleDateString("en-GB", {
    day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
  });
}

/** @param {string} iso @returns {string} "11 Oct" */
function shortDay(iso) {
  return new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso).toLocaleDateString("en-GB", {
    day: "numeric", month: "short", timeZone: "UTC",
  });
}

/** @param {number} ms @returns {string} "1 d 21 h" */
function spanText(ms) {
  const h = Math.max(0, Math.floor(ms / 3.6e6));
  return h < 24 ? `${h} h` : `${Math.floor(h / 24)} d ${h % 24} h`;
}

/** @param {string} name @param {Record<string, string>} attrs @returns {SVGElement} */
function svg(name, attrs = {}) {
  const e = /** @type {SVGElement} */ (document.createElementNS(SVG_NS, name));
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}

/** @param {number} value -100..100 @returns {string} percent across the bar */
const repPct = (value) => `${((value + 100) / 2).toFixed(1)}%`;

/** @param {UiStanding} st @returns {HTMLElement} */
function standingRow(st) {
  const li = el("li", "cr-row");
  li.dataset.name = st.name;
  const head = el("div", "cr-head");
  const name = el("span", "cr-name", st.name);
  name.style.setProperty("--dot", `var(${SUPERPOWER_COLOURS[st.name] || "--muted"})`);
  const sign = st.value > 0 ? "+" : "";
  head.append(name, el("strong", `cr-tier t-${st.tier.toLowerCase()}`, st.tier), el("span", "cr-value", `${sign}${st.value.toFixed(1)}`));
  const bar = el("div", "cr-bar rep");
  bar.title = st.next_tier ? `${st.to_next.toFixed(1)} to ${st.next_tier}` : "The top: Allied";
  for (const b of TIER_BOUNDS) {
    const tick = el("span", "cr-tick");
    tick.style.left = repPct(b);
    bar.append(tick);
  }
  const mark = el("span", "cr-mark");
  mark.style.left = repPct(st.value);
  bar.append(mark);
  const next = el("p", "cr-next", st.next_tier ? `${st.to_next.toFixed(1)} to ${st.next_tier}` : "Allied: the top");
  li.append(head, bar, next);
  return li;
}

/** @param {HTMLElement} dl @param {string} term @param {string} value @param {string} [title] */
function fact(dl, term, value, title) {
  const dt = el("dt", "", term);
  const dd = el("dd", "", value);
  if (title) dd.title = title;
  dl.append(dt, dd);
}

/** @param {UiPledge} p @param {number} now */
function renderPledge(p, now) {
  /** @type {[string, string, string][]} */
  const facts = [];
  const add = (term, value, title = "") => facts.push([term, value, title]);
  if (!p.known) {
    add("Power", "No Powerplay entry in your journals yet");
  } else if (!p.power) {
    add("Power", "Not pledged");
  } else {
    add("Power", p.power);
    if (p.since) add("Pledged", `${dayText(p.since)} · ${spanText(now - Date.parse(p.since))}`);
    if (p.merits !== null) add("Merits", cr(p.merits), "In all, for this power");
    add("This week", cr(p.week), `Since ${dayText(p.week_start)}, 07:00 UTC`);
    if (p.rank === 0) {
      add("Rank", "0 · rank 1 comes after the power's introductory missions");
    } else if (p.rank !== null && p.next_rank_merits !== null && p.merits !== null && p.next_rank_merits > p.merits) {
      add("Rank", `${p.rank} · ${cr(p.next_rank_merits - p.merits)} merits to rank ${p.rank + 1}`);
    } else if (p.rank !== null) {
      add("Rank", String(p.rank));
    }
  }
  const key = JSON.stringify(facts);
  if (key === crPledgeKey) return;
  crPledgeKey = key;
  const dl = $("cr-pledge");
  dl.replaceChildren();
  for (const [term, value, title] of facts) fact(dl, term, value, title);
  debug.career.pledge = Object.fromEntries(facts.map(([term, value]) => [term, value]));
}

/** @param {UiCareer} c */
function renderHold(c) {
  const h = c.hold;
  const box = $("cr-hold");
  let text = h.message;
  if (h.status === "done" && h.counts.length) {
    text += `: ${h.counts.map(([state, n]) => `${state} ${cr(n)}`).join(" · ")} (Spansh, ${clock(h.searched)})`;
  }
  box.textContent = c.pledge.power ? text : "";
  box.dataset.status = h.status;
  debug.career.hold = box.textContent;
  renderPowers(c.pledge.power ? h.powers : [], c.pledge.power);
}

let crPowersKey = "";

/** Every power by the systems it holds, most first, yours marked; bars to the largest.
 * @param {[string, number][]} powers @param {string} mine */
function renderPowers(powers, mine) {
  const key = JSON.stringify([powers, mine]);
  if (key === crPowersKey) return;
  crPowersKey = key;
  const list = $("cr-powers");
  list.hidden = !powers.length;
  const top = Math.max(1, ...powers.map(([, n]) => n));
  list.replaceChildren(
    ...powers.map(([power, n]) => {
      const li = el("li", power === mine ? "cr-pw mine" : "cr-pw");
      const bar = el("span", "cr-pw-bar");
      bar.style.width = `${((n / top) * 100).toFixed(1)}%`;
      li.append(el("span", "cr-pw-name", power), el("span", "cr-pw-n", cr(n)), bar);
      if (power === mine) li.title = "Your power";
      return li;
    }),
  );
  debug.career.powers = powers.map(([power, n]) => `${power === mine ? "*" : ""}${power}: ${n}`);
}

/**
 * A line chart over days. @param {HTMLElement} box @param {UiPoint[]} points
 * @param {{name: string, colour: string}[]} lines one per value of a point, in order
 * @param {number} lo @param {number} hi the y range @param {{v: number, label: string}[]} ticks
 * @param {(v: number) => string} say a value as the hover says it
 */
function lineChart(box, points, lines, lo, hi, ticks, say) {
  box.replaceChildren();
  if (!points.length) {
    box.append(el("p", "hint", "Nothing in your journals yet."));
    return;
  }
  const W = 360, H = 170, L = 38, R = 8, T = 8, B = 22;
  const t0 = Date.parse(`${points[0].day}T00:00:00Z`);
  const t1 = Date.parse(`${points[points.length - 1].day}T00:00:00Z`);
  const x = (day) => (t1 === t0 ? L + (W - L - R) / 2 : L + ((Date.parse(`${day}T00:00:00Z`) - t0) / (t1 - t0)) * (W - L - R));
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "cr-svg", role: "img" });
  for (const tk of ticks) {
    chart.append(svg("line", { x1: String(L), x2: String(W - R), y1: String(y(tk.v)), y2: String(y(tk.v)), class: "grid-line" }));
    const label = svg("text", { x: String(L - 7), y: String(y(tk.v) + 3), class: "axis", "text-anchor": "end" });
    label.textContent = tk.label;
    chart.append(label);
  }
  for (const [i, iso] of [[0, points[0].day], [1, points[points.length - 1].day]]) {
    if (i === 1 && t1 === t0) continue;
    const label = svg("text", { x: String(x(iso)), y: String(H - 6), class: "axis", "text-anchor": i ? "end" : "start" });
    label.textContent = shortDay(iso);
    chart.append(label);
  }
  lines.forEach((line, i) => {
    const pts = points.filter((p) => p.values[i] !== null && p.values[i] !== undefined);
    if (!pts.length) return;
    const colour = `var(${line.colour})`;
    if (pts.length > 1) {
      const d = pts.map((p, j) => `${j ? "L" : "M"}${x(p.day).toFixed(1)},${y(/** @type {number} */ (p.values[i])).toFixed(1)}`).join("");
      chart.append(svg("path", { d, class: "series", stroke: colour }));
    }
    for (const p of pts) {
      const dot = svg("circle", { cx: x(p.day).toFixed(1), cy: y(/** @type {number} */ (p.values[i])).toFixed(1), r: "2.4", fill: colour });
      const tip = svg("title");
      tip.textContent = `${line.name} · ${dayText(p.day)}: ${say(/** @type {number} */ (p.values[i]))}`;
      dot.append(tip);
      chart.append(dot);
    }
  });
  const legend = el("ul", "cr-legend");
  for (const line of lines) {
    const li = el("li", "", line.name);
    li.style.setProperty("--dot", `var(${line.colour})`);
    legend.append(li);
  }
  box.append(chart, legend);
}

/** @param {HTMLElement} box @param {UiWeek[]} weeks oldest first, the last = this week */
function weeksChart(box, weeks) {
  box.replaceChildren();
  if (!weeks.length) {
    box.append(el("p", "hint", "No merits in your journals yet."));
    return;
  }
  const W = 360, H = 170, L = 34, R = 8, T = 14, B = 22;
  const top = Math.max(...weeks.map((w) => w.merits), 1);
  const slot = (W - L - R) / 12; // twelve places, the newest week on the right
  const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "cr-svg", role: "img" });
  const y = (v) => T + (1 - v / top) * (H - T - B);
  for (const v of [0, top]) {
    chart.append(svg("line", { x1: String(L), x2: String(W - R), y1: String(y(v)), y2: String(y(v)), class: "grid-line" }));
    const label = svg("text", { x: String(L - 4), y: String(y(v) + 3), class: "axis", "text-anchor": "end" });
    label.textContent = v >= 1000 ? `${(v / 1000).toFixed(1)}k` : cr(v);
    chart.append(label);
  }
  weeks.forEach((w, i) => {
    const bx = L + (12 - weeks.length + i) * slot + slot * 0.15;
    const h = Math.max(y(0) - y(w.merits), w.merits ? 1 : 0);
    const bar = svg("rect", {
      x: bx.toFixed(1), y: (y(0) - h).toFixed(1), width: (slot * 0.7).toFixed(1), height: h.toFixed(1),
      class: i === weeks.length - 1 ? "week now" : "week",
    });
    const tip = svg("title");
    tip.textContent = `Week from ${dayText(w.start)}: ${cr(w.merits)} merits`;
    bar.append(tip);
    chart.append(bar);
    if (i % 3 === (weeks.length - 1) % 3) {
      const label = svg("text", { x: (bx + slot * 0.35).toFixed(1), y: String(H - 6), class: "axis", "text-anchor": "middle" });
      label.textContent = i === weeks.length - 1 ? "now" : shortDay(w.start);
      chart.append(label);
    }
  });
  box.append(chart);
}

/** The Commander tab is shown: ask once per power for its systems (the plugin keeps a count 15 minutes).
 * @param {string} [power] the pledged power (default: as the last reply says) */
function askHold(power) {
  if (power === undefined) {
    const s = debug.reply && debug.reply.state;
    power = s && s.career ? s.career.pledge.power : "";
  }
  if (!power || crAsked === power || $("panel-commander").hidden) return;
  crAsked = power;
  crAskedAt = Date.now();
  debug.career.asked += 1;
  post("/api/career", {});
}

/** @param {UiState} s */
function renderCareer(s) {
  const c = s.career;
  if (!c) return;
  askHold(c.pledge.power);
  const now = Date.now();
  renderHold(c);
  const key = JSON.stringify([c.standings, c.ranks, c.standing_history, c.navy_history, c.weeks, c.since]);
  renderPledge(c.pledge, now); // the pledge's age moves on with the clock
  if (key === crKey) return;
  crKey = key;
  const unknown = "Not known yet: the game writes this at login.";
  const standings = $("cr-standings");
  standings.replaceChildren(...(c.standings.length ? c.standings.map(standingRow) : [el("li", "hint", unknown)]));
  const ranks = $("cr-ranks");
  const ordered = [...c.ranks].sort((a, b) => RANK_ORDER.indexOf(a.kind) - RANK_ORDER.indexOf(b.kind));
  ranks.replaceChildren(...(ordered.length ? ordered.map(rankCard) : [el("li", "hint", unknown)]));
  dropStaleTip(); // a ladder shown for a card just replaced
  $("cr-since").textContent = c.known
    ? `From your journals since ${dayText(c.since)}: one point per day (the last of the day).`
    : "Your journals have not been read yet.";
  lineChart(
    $("cr-chart-standing"), c.standing_history,
    SUPERPOWERS.map((name) => ({ name, colour: SUPERPOWER_COLOURS[name] })), -100, 100,
    TIER_BOUNDS.map((v) => ({ v, label: v > 0 ? `+${v}` : String(v) })),
    (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)}`,
  );
  const navyTop = Math.max(2, ...c.navy_history.flatMap((p) => p.values.map((v) => Math.ceil(v || 0))));
  const navyTicks = [...new Set([0, Math.round(navyTop / 2), navyTop])].map((v) => ({ v, label: String(v) }));
  lineChart(
    $("cr-chart-navy"), c.navy_history,
    NAVIES.map((name) => ({ name, colour: NAVY_COLOURS[name] })), 0, navyTop, navyTicks,
    (v) => `rank ${Math.floor(v)}, ${Math.round((v % 1) * 100)} %`,
  );
  weeksChart($("cr-chart-weeks"), c.weeks);
  debug.career.renders += 1;
  debug.career.standings = c.standings.map((st) => ({
    name: st.name, value: st.value, tier: st.tier, next: standings.querySelector(`[data-name="${st.name}"] .cr-next`)?.textContent || "",
  }));
  debug.career.ranks = [...ranks.querySelectorAll(".rk")].map((li) => ({
    kind: li.dataset.kind || "",
    title: li.querySelector(".rk-title")?.textContent || "",
    next: li.querySelector(".rk-next")?.textContent || "",
    stage: li.querySelector(".emblem")?.getAttribute("data-stage") || "",
    bar: /** @type {HTMLElement | null} */ (li.querySelector(".rk-bar > span"))?.style.width || "",
  }));
  debug.career.charts = {
    standing: $("cr-chart-standing").querySelectorAll("circle").length,
    navy: $("cr-chart-navy").querySelectorAll("circle").length,
    weeks: [...$("cr-chart-weeks").querySelectorAll("rect")].map((r) => r.querySelector("title")?.textContent || ""),
  };
}

// --- Earnings tab ---------------------------------------------------------------------------------------

const CREDITS = new Intl.NumberFormat("en-US");
/** @param {number} n credits → "12.3M" from a million, else in full: "45,210" */
function credits(n) {
  const sign = n < 0 ? "−" : "";
  const a = Math.abs(n);
  return a >= 1e6 ? `${sign}${MILLIONS.format(a / 1e6)}M` : `${sign}${CREDITS.format(a)}`;
}

/** @param {string} iso @returns {string} "13 Oct 14:05" in this computer's time */
function whenText(iso) {
  const t = new Date(iso);
  return `${t.toLocaleDateString("en-GB", { day: "numeric", month: "short" })} ${t.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
}

let facKey = ""; // what the Minor factions card was last drawn from

/** @param {UiFaction} f @returns {HTMLTableRowElement} */
function factionRow(f) {
  const tr = /** @type {HTMLTableRowElement} */ (el("tr", f.hostile ? "fac-hostile" : ""));
  tr.dataset.name = f.name;
  const st = f.standing;
  const name = el("td", "fac-name");
  name.append(el("span", "sys", f.name));
  if (f.controls) name.append(el("span", "st", `controls ${f.system}`));
  const standing = el("td", "fac-standing");
  const head = el("span", "cr-head");
  head.append(el("strong", `cr-tier t-${st.tier.toLowerCase()}`, st.tier), el("span", "cr-value", `${st.value > 0 ? "+" : ""}${st.value.toFixed(1)}`));
  const bar = el("div", "cr-bar rep");
  bar.title = st.next_tier ? `${st.to_next.toFixed(1)} to ${st.next_tier}` : "The top: Allied";
  for (const b of TIER_BOUNDS) {
    const tick = el("span", "cr-tick");
    tick.style.left = repPct(b);
    bar.append(tick);
  }
  const mark = el("span", "cr-mark");
  mark.style.left = repPct(st.value);
  bar.append(mark);
  standing.append(head, bar);
  const met = el("td", "fac-met");
  met.append(el("span", "", f.system), el("span", "st", f.seen ? dayText(f.seen) : ""));
  if (f.influence !== null) met.title = `${f.influence} % influence there`;
  const near = el("td", "fac-near");
  near.append(copyName("sys", f.nearest, (ok) => { debug.factions.copied = { name: f.nearest, ok }; }));
  const where = f.places === 1 ? "seen in 1 system" : `seen in ${CR.format(f.places)} systems`;
  near.append(el("span", "st", f.nearest_ly === null ? where : `${LY.format(f.nearest_ly)} ly · ${where}`));
  tr.append(name, standing, el("td", "n", f.missions ? CR.format(f.missions) : "–"), el("td", "fac-state", f.state || "–"), met, near);
  return tr;
}

/** @param {UiFactions} fs */
function renderFactions(fs) {
  if (!fs) return;
  const key = JSON.stringify(fs);
  if (key === facKey) return;
  facKey = key;
  const warn = $("cr-fac-warn");
  warn.textContent = fs.warning;
  warn.hidden = !fs.warning;
  const sum = $("cr-fac-sum");
  const wrap = $("cr-fac-wrap");
  const body = /** @type {HTMLTableSectionElement} */ ($("cr-fac-table").tBodies[0]);
  if (!fs.known) {
    sum.textContent = "Reading your journals…";
    wrap.hidden = true;
    body.replaceChildren();
  } else {
    const n = fs.rows.length;
    const listed = n ? `${CR.format(n)} ${n === 1 ? "faction knows" : "factions know"} you` : "No faction knows you yet";
    const rest = fs.neutral ? `; ${CR.format(fs.neutral)} more you have met are Neutral at 0, not listed` : "";
    sum.textContent = `${listed}${rest}.`;
    wrap.hidden = !n;
    body.replaceChildren(...fs.rows.map(factionRow));
  }
  debug.factions.renders += 1;
  debug.factions.sum = sum.textContent || "";
  debug.factions.warning = warn.hidden ? "" : warn.textContent || "";
  debug.factions.rows = [...body.rows].map((tr) => ({
    name: tr.dataset.name || "",
    cells: [...tr.cells].map((td) => [...td.childNodes].map((c) => c.textContent).filter(Boolean).join(" | ")),
  }));
}

let eaKey = "";

/** @param {UiMoney} m @param {string} cls the row's look */
function moneyRow(m, cls) {
  const tr = el("tr", cls);
  tr.dataset.kind = m.kind;
  tr.append(el("th", "", m.label));
  tr.firstElementChild?.setAttribute("scope", "row");
  for (const v of [m.session, m.today, m.week]) tr.append(el("td", v ? "n" : "n zero", v ? credits(v) : "—"));
  return tr;
}

/** @param {UiMoney[]} rows @param {string} head */
function moneyBlock(rows, head) {
  const used = rows.filter((m) => m.kind === "total" || m.session || m.today || m.week);
  const title = el("tr", "ea-head");
  const th = el("th", "", head);
  th.colSpan = 4;
  title.append(th);
  return [title, ...used.map((m) => moneyRow(m, m.kind === "total" ? "ea-total" : ""))];
}

/** @param {UiEarnings | undefined} e */
function renderEarnings(e) {
  if (!e) return;
  const key = JSON.stringify(e);
  if (key === eaKey) return;
  eaKey = key;
  const session = $("ea-session");
  if (!e.known) {
    session.textContent = "Reading your journals…";
  } else if (!e.session_start) {
    session.textContent = "No game start found in your journals yet.";
  } else {
    const hours = e.session_hours;
    const length = hours >= 1 ? `${Math.floor(hours)} h ${Math.round((hours % 1) * 60)} min` : `${Math.round(hours * 60)} min`;
    const rate = e.per_hour === null ? "" : ` · earned ${credits(e.per_hour)} per hour`;
    session.textContent = `Session from ${whenText(e.session_start)}, ${length}${e.running ? " so far" : ""}${rate}`;
  }
  $("ea-week").title = e.week_start ? `From ${whenText(e.week_start)}` : "";
  $("ea-earned").replaceChildren(...moneyBlock(e.earned, "Earned"));
  $("ea-spent").replaceChildren(...moneyBlock(e.spent, "Spent"));
  $("ea-net").replaceChildren(moneyRow(e.net, "ea-net"));
  const facts = /** @type {[string, string][]} */ ([
    ["Cartographic data", e.data_bodies
      ? `≈ ${credits(e.data_value)} Cr · ${e.data_bodies} ${e.data_bodies === 1 ? "body" : "bodies"} in ${e.data_systems} ${e.data_systems === 1 ? "system" : "systems"}, since ${whenText(e.data_since)}`
      : "nothing unsold"],
    ["Exobiology", e.bio_species.length
      ? `${credits(e.bio_value)} Cr · ${e.bio_species.length} species, since ${whenText(e.bio_since)}`
      : "nothing unsold"],
  ]);
  $("ea-unsold").replaceChildren(...facts.flatMap(([term, value]) => [el("dt", "", term), el("dd", "", value)]));
  $("ea-lose").hidden = !(e.data_bodies || e.bio_species.length);
  $("ea-species").replaceChildren(
    ...e.bio_species.map((sp) => {
      const li = el("li", "ea-sp");
      li.append(el("span", "", sp.name), el("span", "n", sp.value === null ? "value not known" : `${credits(sp.value)} Cr`));
      li.dataset.name = sp.name;
      return li;
    }),
  );
  const died = $("ea-died");
  died.hidden = !e.died;
  died.textContent = e.died ? `You died on ${whenText(e.died)}: about ${credits(e.lost)} Cr of unsold data was lost.` : "";
  debug.earnings.renders += 1;
  const all = [...e.earned.map((m) => ({ ...m, side: "earned" })), ...e.spent.map((m) => ({ ...m, side: "spent" })), { ...e.net, side: "net" }];
  const tbodies = { earned: "ea-earned", spent: "ea-spent", net: "ea-net" };
  debug.earnings.rows = all
    .map((m) => {
      const tr = $(tbodies[m.side]).querySelector(`tr[data-kind="${m.kind}"]`);
      return tr ? {
        kind: `${m.side}:${m.kind}`, label: tr.querySelector("th")?.textContent || "",
        cells: [...tr.querySelectorAll("td")].map((td) => td.textContent || ""), values: [m.session, m.today, m.week],
      } : null;
    })
    .filter((r) => r !== null);
  debug.earnings.session = session.textContent || "";
  debug.earnings.unsold = Object.fromEntries(facts);
  debug.earnings.species = [...$("ea-species").children].map((li) => `${/** @type {HTMLElement} */ (li).dataset.name}: ${li.lastChild?.textContent}`);
  debug.earnings.died = died.textContent || "";
  balanceChart($("ea-balance"), e.balance);
}

/** A round step for the balance axis: 1, 2 or 5 × a power of ten. @param {number} span */
function niceStep(span) {
  const raw = span / 3;
  const p = 10 ** Math.floor(Math.log10(raw || 1));
  return [1, 2, 5, 10].map((m) => m * p).find((s) => s >= raw) || p * 10;
}

let balanceShown = /** @type {UiBalance[]} */ ([]);
let balanceWidth = 0;
/** The chart is drawn at its box's own width, so the labels keep their size on a narrow window: drawn again
 * when that width changes (a window resized, the tab shown after being drawn hidden). Asked on every poll
 * too, as a window that is not on screen reports no resizes. */
function fitBalance() {
  const box = $("ea-balance");
  if (box.clientWidth && box.clientWidth !== balanceWidth) balanceChart(box, balanceShown);
}
new ResizeObserver(fitBalance).observe($("ea-balance"));

/** Pointing anywhere over the balance chart marks the nearest point: a guide line, its dot and a label with
 * its time and balance. @param {SVGElement} chart @param {UiBalance[]} points @param {number[]} xs
 * @param {number[]} ys each point's place @param {number} W @param {number} H the chart's size @param {number} top
 * @param {number} bottom the plot's top and bottom */
function balanceHover(chart, points, xs, ys, W, H, top, bottom) {
  const mark = svg("g", { class: "ea-hover", visibility: "hidden" });
  const guide = svg("line", { y1: String(top), y2: String(bottom), class: "ea-guide" });
  const dot = svg("circle", { r: "3.5", class: "ea-mark" });
  const back = svg("rect", { rx: "3", class: "ea-tip-back", height: "30" });
  const line1 = svg("text", { class: "ea-tip" });
  const line2 = svg("text", { class: "ea-tip ea-tip-value" });
  mark.append(guide, dot, back, line1, line2);
  const area = svg("rect", { x: "0", y: "0", width: String(W), height: String(H), class: "ea-area" });
  chart.append(mark, area);
  /** @param {number} i */
  const show = (i) => {
    const px = xs[i], py = ys[i];
    guide.setAttribute("x1", px.toFixed(1));
    guide.setAttribute("x2", px.toFixed(1));
    dot.setAttribute("cx", px.toFixed(1));
    dot.setAttribute("cy", py.toFixed(1));
    line1.textContent = whenText(points[i].when);
    line2.textContent = `${CREDITS.format(points[i].credits)} Cr`;
    const w = Math.max(line1.getComputedTextLength?.() || 0, line2.getComputedTextLength?.() || 0, 60) + 12;
    const left = px + 8 + w > W ? px - 8 - w : px + 8; // beside the line, inside the chart
    const ty = Math.min(Math.max(py - 15, top), bottom - 30);
    back.setAttribute("x", left.toFixed(1));
    back.setAttribute("y", ty.toFixed(1));
    back.setAttribute("width", w.toFixed(1));
    line1.setAttribute("x", (left + 6).toFixed(1));
    line1.setAttribute("y", (ty + 12).toFixed(1));
    line2.setAttribute("x", (left + 6).toFixed(1));
    line2.setAttribute("y", (ty + 25).toFixed(1));
    mark.setAttribute("visibility", "visible");
    debug.earnings.hover = { when: points[i].when, credits: points[i].credits, text: `${line1.textContent} ${line2.textContent}` };
  };
  area.addEventListener("pointermove", (e) => {
    const r = chart.getBoundingClientRect();
    if (!r.width) return;
    const at = ((e.clientX - r.left) / r.width) * W;
    let best = 0;
    for (let i = 1; i < xs.length; i++) if (Math.abs(xs[i] - at) < Math.abs(xs[best] - at)) best = i;
    show(best);
  });
  area.addEventListener("pointerleave", () => {
    mark.setAttribute("visibility", "hidden");
    debug.earnings.hover = null;
  });
}

/** Your balance as a step line over real time. @param {HTMLElement} box @param {UiBalance[]} points */
function balanceChart(box, points) {
  balanceShown = points;
  balanceWidth = box.clientWidth;
  debug.earnings.hover = null; // a new chart: nothing marked until the pointer moves
  box.replaceChildren();
  debug.earnings.balance = { points: points.length, first: "", last: "", top: "" };
  if (points.length < 2) {
    box.append(el("p", "hint", "Your balance shows here once your journals have a login in them."));
    return;
  }
  const W = Math.max(300, box.clientWidth || 720), H = 200, L = 46, R = 10, T = 10, B = 22;
  const times = points.map((p) => Date.parse(p.when));
  const t0 = times[0], t1 = times[times.length - 1];
  const values = points.map((p) => p.credits);
  const step = niceStep(Math.max(...values) - Math.min(0, ...values));
  const low = Math.min(...values);
  const lo = low >= -step / 10 ? 0 : Math.floor(low / step) * step; // a dip of a few credits keeps 0 at the bottom
  const hi = Math.ceil(Math.max(...values) / step) * step || step;
  const x = (t) => (t1 === t0 ? L : L + ((t - t0) / (t1 - t0)) * (W - L - R));
  const y = (v) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  const chart = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "cr-svg", role: "img", "aria-label": "Balance over time" });
  for (let v = lo; v <= hi + step / 2; v += step) {
    chart.append(svg("line", { x1: String(L), x2: String(W - R), y1: y(v).toFixed(1), y2: y(v).toFixed(1), class: "grid-line" }));
    const label = svg("text", { x: String(L - 6), y: (y(v) + 3).toFixed(1), class: "axis", "text-anchor": "end" });
    label.textContent = credits(v);
    chart.append(label);
  }
  for (const [i, t] of [[0, t0], [1, t1]]) {
    const label = svg("text", { x: x(t).toFixed(1), y: String(H - 6), class: "axis", "text-anchor": i ? "end" : "start" });
    label.textContent = whenText(new Date(t).toISOString());
    chart.append(label);
  }
  // a balance holds until the next change: a step, not a slope
  const d = points.map((p, i) => i ? `H${x(times[i]).toFixed(1)}V${y(p.credits).toFixed(1)}` : `M${x(times[0]).toFixed(1)},${y(p.credits).toFixed(1)}`).join("");
  chart.append(svg("path", { d, class: "series", stroke: "var(--accent)" }));
  balanceHover(chart, points, times.map(x), points.map((p) => y(p.credits)), W, H, T, H - B);
  box.append(chart);
  debug.earnings.balance = {
    points: points.length,
    first: `${points[0].when} ${points[0].credits}`,
    last: `${points[points.length - 1].when} ${points[points.length - 1].credits}`,
    top: credits(hi),
  };
}

// --- Missions tab: what is open, where, until when --------------------------------------------------

const MI_SORT_KEY = "edmultitool.missionSort";
const DUE_SOON_MS = 2 * 3600 * 1000; // time left turns red under this
let miKey = "";

/** @param {number} ms time left → "2 d 4 h", "3 h 12 min", "12 min", "< 1 min" */
function timeLeft(ms) {
  const min = Math.floor(ms / 60000);
  if (min < 1) return "< 1 min";
  const d = Math.floor(min / 1440);
  const h = Math.floor((min % 1440) / 60);
  if (d) return `${d} d ${h} h`;
  return h ? `${h} h ${min % 60} min` : `${min} min`;
}

/** The Time left cell, counted from this computer's clock. @param {HTMLElement} td
 * @param {string} expiry ISO, "" = no limit known */
function countDown(td, expiry) {
  if (!expiry) {
    td.textContent = "—";
    td.title = "No time limit known";
    return;
  }
  const ms = new Date(expiry).getTime() - Date.now();
  td.textContent = ms > 0 ? timeLeft(ms) : "ran out";
  td.classList.toggle("due-soon", ms < DUE_SOON_MS);
  td.title = `Ends ${whenText(expiry)}`;
}

/** Recount every Time left cell: on each render and every few seconds between (the state only comes again
 * when something changes). */
function tickMissions() {
  for (const td of document.querySelectorAll("#mi-table td.mi-left")) {
    const cell = /** @type {HTMLElement} */ (td);
    countDown(cell, cell.dataset.expiry || "");
  }
  debug.missions.ticks += 1;
}
window.setInterval(tickMissions, 5000);

/** @param {UiMission[]} rows @param {string} by "time" | "distance" @returns {UiMission[]} */
function sortMissions(rows, by) {
  if (by !== "distance") return rows; // the server sends them soonest to end first
  const far = (/** @type {UiMission} */ m) => (m.here ? 0 : m.distance_ly ?? Infinity);
  return [...rows].sort((a, b) => far(a) - far(b));
}

/** @param {UiState} s */
function renderMissions(s) {
  const v = s.missions;
  if (!v) return;
  const by = checked("mi-sort") || "time";
  const rows = sortMissions(v.missions, by);
  const status = $("mi-status");
  let msg = "";
  if (!v.known && !rows.length) msg = "Reading your journals…";
  else if (!rows.length) msg = "No open missions.";
  else {
    const n = rows.length === 1 ? "1 open mission" : `${rows.length} open missions`;
    msg = v.reward ? `${n}, ${credits(v.reward)} Cr in rewards.` : `${n}.`;
    if (v.lookup) msg += ` Some distances are not known: ${v.lookup}`;
  }
  status.textContent = msg;
  status.dataset.status = v.lookup ? "error" : v.known || rows.length ? "done" : "running";
  const key = JSON.stringify([rows, s.address, by]);
  if (key !== miKey) {
    miKey = key;
    $("mi-wrap").hidden = !rows.length;
    const body = el("tbody");
    for (const m of rows) {
      const tr = el("tr", m.done ? "mi-done" : "");
      tr.dataset.id = m.id;
      const what = el("td", "mi-what");
      what.append(el("span", "mi-kind", m.kind || "Mission"));
      if (m.wing) what.append(" ", el("span", "mi-tag", "wing"));
      if (m.title) what.append(el("span", "mi-title", m.title));
      if (m.faction) what.append(el("span", "mi-faction", m.faction));
      const where = el("td");
      if (m.done) where.append(el("span", "mi-ready", "Done → hand in"));
      if (m.system) {
        where.append(copyName("sys", m.system, (ok) => (debug.missions.copied = { name: m.system, ok })));
      } else {
        where.append(el("span", "st", "not named"));
      }
      if (m.station) where.append(el("span", "st", m.station));
      const left = el("td", "n mi-left");
      left.dataset.expiry = m.expiry;
      tr.append(
        what,
        where,
        el("td", "n key", m.reward ? credits(m.reward) : "—"),
        left,
        el("td", "n", distText(m.distance_ly, m.here)),
        el("td", "n", m.here ? "0" : m.jumps === null ? "—" : String(m.jumps)),
      );
      body.append(tr);
    }
    const table = $("mi-table");
    for (const old of table.querySelectorAll("tbody")) old.remove();
    table.append(body);
    debug.missions.renders += 1;
  }
  tickMissions();
  debug.missions.status = msg;
  debug.missions.rows = [...document.querySelectorAll("#mi-table tbody tr")].map((tr) => ({
    id: /** @type {HTMLElement} */ (tr).dataset.id || "",
    cells: [...tr.children].map((c) => c.textContent || ""),
    soon: !!tr.querySelector(".due-soon"),
  }));
}

for (const r of document.querySelectorAll('input[name="mi-sort"]')) {
  r.addEventListener("change", () => {
    try {
      localStorage.setItem(MI_SORT_KEY, checked("mi-sort"));
    } catch {
      // no storage: the order is simply not remembered
    }
    if (debug.reply) renderMissions(debug.reply.state);
  });
}
try {
  const sort = localStorage.getItem(MI_SORT_KEY);
  const pick = /** @type {?HTMLInputElement} */ (document.querySelector(`input[name="mi-sort"][value="${sort}"]`));
  if (pick) pick.checked = true;
} catch {
  // no storage: time left first
}

// --- Stats tab: the game's own lifetime statistics, as at the last login ------------------------------

let stKey = "";
const KM = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });

/** @param {number} v @param {string} unit @returns {string} */
function statText(v, unit) {
  if (unit === "cr") return `${credits(Math.round(v))} Cr`;
  if (unit === "cr/h") return `${credits(Math.round(v))} Cr/h`;
  if (unit === "ly") return `${v >= 100 ? CR.format(v) : LY.format(v)} ly`;
  if (unit === "ratio") return v.toFixed(2);
  if (unit === "m") return v >= 1000 ? `${KM.format(v / 1000)} km` : `${CR.format(v)} m`;
  if (unit === "s") {
    const min = Math.round(v / 60);
    return `${CR.format(Math.floor(min / 60))} h ${min % 60} min`;
  }
  return CR.format(v);
}

/** @param {?number} d @param {string} unit @returns {string} "+148", "" when nothing to say */
function statDelta(d, unit) {
  if (d === null || d === undefined || !d) return "";
  return `${d > 0 ? "+" : "−"}${statText(Math.abs(d), unit)}`;
}

/** the total at every login as a small line; each point's value on hover
 * @param {UiStatPoint[]} points @param {string} unit @returns {SVGElement} */
function sparkline(points, unit) {
  const W = 120;
  const H = 30;
  // stretched to the tile's width (the line keeps its thickness)
  const box = svg("svg", { viewBox: `0 0 ${W} ${H}`, class: "spark", role: "img", preserveAspectRatio: "none" });
  const ends = `from ${statText(points[0]?.value ?? 0, unit)} to ${statText(points[points.length - 1]?.value ?? 0, unit)}`;
  box.setAttribute("aria-label", points.length > 1 ? ends : "one login so far");
  if (points.length < 2) return box;
  const lo = Math.min(...points.map((p) => p.value));
  const hi = Math.max(...points.map((p) => p.value));
  const t0 = new Date(points[0].when).getTime();
  const span = Math.max(1, new Date(points[points.length - 1].when).getTime() - t0);
  const x = (p) => 2 + ((new Date(p.when).getTime() - t0) / span) * (W - 4);
  const y = (p) => H - 3 - (hi === lo ? 0.5 : (p.value - lo) / (hi - lo)) * (H - 6);
  box.append(svg("polyline", { points: points.map((p) => `${x(p).toFixed(1)},${y(p).toFixed(1)}`).join(" "), class: "spark-line" }));
  // one hover strip per login, as wide as the gap to its neighbours
  points.forEach((p, i) => {
    const left = i ? (x(points[i - 1]) + x(p)) / 2 : 0;
    const right = i < points.length - 1 ? (x(p) + x(points[i + 1])) / 2 : W;
    const hit = svg("rect", { x: left.toFixed(1), y: "0", width: Math.max(0.5, right - left).toFixed(1), height: String(H), class: "spark-hit" });
    const tip = svg("title");
    tip.textContent = `${whenText(p.when)}: ${statText(p.value, unit)}`;
    hit.append(tip);
    box.append(hit);
  });
  return box;
}

/** @param {UiStatBar[]} bars @param {HTMLElement} list */
function statBars(bars, list) {
  const total = bars.reduce((sum, b) => sum + b.value, 0);
  const top = bars.length ? bars[0].value : 1;
  list.replaceChildren(
    ...(bars.length ? bars.map((b) => {
      const li = el("li", "st-bar");
      li.append(el("span", "st-bar-label", b.label));
      const track = el("span", "st-bar-track");
      const fill = el("span", "st-bar-fill");
      fill.style.width = `${Math.max(0.5, (b.value / top) * 100).toFixed(1)}%`;
      track.append(fill);
      li.append(track, el("span", "st-bar-value", `${credits(b.value)} Cr`), el("span", "st-bar-share", b.value / total < 0.005 ? "<1 %" : `${Math.round((b.value / total) * 100)} %`));
      li.title = `${b.label}: ${CREDITS.format(b.value)} Cr`;
      return li;
    }) : [el("li", "hint", "Nothing yet.")]),
  );
}

/** @param {UiStatCard} c @returns {HTMLElement} */
function statCard(c) {
  const card = el("section", "card st-card");
  card.dataset.card = c.key;
  const h = el("h2", "", c.title);
  const info = el("button", "info", "i");
  Object.assign(info, { type: "button" });
  info.dataset.info = "i-st-card";
  info.setAttribute("aria-describedby", "i-st-card");
  info.setAttribute("aria-label", `About ${c.title}`);
  h.append(" ", info);
  const dl = el("dl", "cr-facts st-rows");
  for (const r of c.rows) {
    const dt = el("dt", r.derived ? "derived" : "", r.label);
    const dd = el("dd", "", statText(r.value, r.unit));
    const delta = statDelta(r.week, r.unit);
    if (delta) dd.append(" ", el("span", r.week && r.week < 0 ? "st-delta down" : "st-delta", delta));
    if (r.unit === "cr" && Math.abs(r.value) >= 1e6) dd.title = `${CREDITS.format(Math.round(r.value))} Cr`;
    dl.append(dt, dd);
  }
  card.append(h, dl);
  if (c.zeros) card.append(el("p", "hint", `${c.zeros} more at 0, not shown.`));
  return card;
}

/** @param {UiKiller} k @returns {string} "Name: ship, rank" / "Name: Coriolis starport defences" */
function killerText(k) {
  const what = k.station ? `${k.what || "station"} defences` : [k.what, k.rank].filter(Boolean).join(", ");
  return k.name && what ? `${k.name}: ${what}` : k.name || what;
}

/** @param {UiDeath} d @returns {[string, boolean]} the text, and whether it went badly */
function deathCost(d) {
  if (d.outcome === "bankrupt") return ["Bankrupt: ship lost", true];
  if (d.outcome === "free") return ["Free", false];
  if (d.outcome === "rebuy") return [`Rebuy ${CREDITS.format(d.cost)} Cr`, false];
  if (!d.outcome) return ["–", false];
  const word = d.outcome[0].toUpperCase() + d.outcome.slice(1);
  return [d.cost ? `${word} ${CREDITS.format(d.cost)} Cr` : word, false];
}

/** @param {UiDeaths} ds @param {boolean} reading */
function renderDeaths(ds, reading) {
  const sum = $("st-deaths-sum");
  const list = $("st-deaths-list");
  if (!ds || !ds.count) {
    sum.textContent = reading ? "Reading your journals…" : "No deaths in your journals.";
    list.replaceChildren();
  } else {
    const n = ds.count === 1 ? "1 death" : `${CR.format(ds.count)} deaths`;
    sum.textContent = `${n}; ${CREDITS.format(ds.paid)} Cr paid to come back.${reading ? " Still reading older journals…" : ""}`;
    list.replaceChildren(
      ...ds.rows.map((d) => {
        const li = el("li", "st-death");
        const who = el("span", "st-death-who");
        if (!d.killers.length) who.append(el("small", "", "No killer named (a crash, a self-destruct…)"));
        else if (d.killers.length > 1) {
          who.append(el("small", "", `A wing of ${d.killers.length}:`));
          who.append(...d.killers.map((k) => el("span", "", killerText(k))));
        } else who.append(el("span", "", killerText(d.killers[0])));
        const [cost, bad] = deathCost(d);
        const t = new Date(d.when);
        // an earlier year: the date with its year, in this computer's time like the rest of the list
        const when = t.getFullYear() === new Date().getFullYear() ? whenText(d.when)
          : t.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
        li.append(el("span", "st-death-when", when), who, el("span", bad ? "st-death-cost bad" : "st-death-cost", cost));
        return li;
      }),
    );
  }
  debug.stats.deaths = [...list.querySelectorAll(".st-death")].map((li) => ({
    when: li.querySelector(".st-death-when")?.textContent || "",
    who: [...li.querySelectorAll(".st-death-who > *")].map((x) => x.textContent).join(" | "),
    cost: li.querySelector(".st-death-cost")?.textContent || "",
  }));
}

/** @param {UiStats} st */
function renderStats(st) {
  if (!st) return;
  const key = JSON.stringify(st);
  if (key === stKey) return;
  stKey = key;
  renderDeaths(st.deaths, st.reading);
  const since = $("st-since");
  if (!st.known) {
    since.textContent = st.reading ? "Reading your journals…" : "Not known yet: the game writes its statistics at every login.";
    $("st-lines").replaceChildren();
    statBars([], $("st-profits"));
    statBars([], $("st-spending"));
    $("st-cards").replaceChildren();
    $("st-empty").hidden = true;
    return;
  }
  const week = st.week_from ? ` "+" = since ${shortDay(st.week_from)}, the newest login a week or more before.` : "";
  const logins = st.count === 1 ? "1 login" : `${CR.format(st.count)} logins`;
  const reading = st.reading ? " Still reading older journals…" : "";
  since.textContent = `As at your last login, ${whenText(st.at)}; ${logins} since ${dayText(st.first)}.${week}${reading}`;
  $("st-lines").replaceChildren(
    ...st.headlines.map((h) => {
      const li = el("li", "st-line");
      li.dataset.key = h.key;
      li.append(el("span", "st-label", h.label), el("strong", "st-value", statText(h.value, h.unit)));
      const delta = statDelta(h.week, h.unit);
      li.append(el("span", h.week && h.week < 0 ? "st-delta down" : "st-delta", delta || " "));
      li.append(sparkline(h.points, h.unit));
      return li;
    }),
  );
  statBars(st.profits, $("st-profits"));
  statBars(st.spending, $("st-spending"));
  $("st-cards").replaceChildren(...st.cards.map(statCard));
  $("st-empty").hidden = !st.empty.length;
  $("st-empty").textContent = st.empty.length ? `Nothing yet in: ${st.empty.join(", ")}.` : "";
  dropStaleTip();
  debug.stats.renders += 1;
  debug.stats.lines = [...$("st-lines").querySelectorAll(".st-line")].map((li) => ({
    key: li.dataset.key || "",
    value: li.querySelector(".st-value")?.textContent || "",
    delta: (li.querySelector(".st-delta")?.textContent || "").trim(),
    points: li.querySelectorAll(".spark-hit").length,
  }));
  debug.stats.cards = [...$("st-cards").querySelectorAll(".st-card")].map((c) => ({
    key: /** @type {HTMLElement} */ (c).dataset.card || "",
    rows: [...c.querySelectorAll("dt")].map((dt) => `${dt.textContent}: ${dt.nextElementSibling?.textContent || ""}`),
  }));
}

// --- (i): one bubble for every explanation -----------------------------------------------------------------
// A `button.info` names the hidden element holding its text (`data-info`); the bubble shows a copy of it on
// hover or keyboard focus, and stays after a click or tap until a second one, Escape or a click elsewhere.

const tip = el("div", "info-tip");
tip.id = "info-tip";
tip.hidden = true;
tip.setAttribute("aria-hidden", "true"); // screen readers get the same text from the button's description
document.body.append(tip);
/** @type {?HTMLElement} */
let tipFor = null;
let tipPinned = false;
let tipTimer = 0;

/** @param {HTMLElement} trigger the (i) */
function placeTip(trigger) {
  const r = trigger.getBoundingClientRect();
  tip.style.maxHeight = "";
  const w = tip.offsetWidth;
  let h = tip.offsetHeight;
  const vw = document.documentElement.clientWidth;
  const vh = window.innerHeight;
  const gap = 6;
  let left = Math.max(8, Math.min(r.left, vw - w - 8)); // flips left near the right edge, never out
  // below the (i), else above it; too tall for both: beside it when the window is wide enough, else the
  // roomier side, scrolling inside; an (i) button is never covered
  const below = vh - 8 - (r.bottom + gap);
  const above = r.top - gap - 8;
  const right = vw - 8 - (r.right + gap);
  const leftRoom = r.left - gap - 8;
  let top;
  if (h > below && h > above && Math.max(right, leftRoom) >= w) {
    left = right >= w ? r.right + gap : r.left - gap - w;
    if (h > vh - 16) {
      h = vh - 16;
      tip.style.maxHeight = `${Math.round(h)}px`;
    }
    top = Math.max(8, Math.min(r.top + r.height / 2 - h / 2, vh - 8 - h));
  } else if (h > below && h > above && trigger.tagName !== "BUTTON") {
    // a whole card: on its roomier side, overlapping the card rather than scrolling (the ladder says it all)
    if (h > vh - 16) {
      h = vh - 16;
      tip.style.maxHeight = `${Math.round(h)}px`;
    }
    top = below >= above ? Math.min(r.bottom + gap, vh - 8 - h) : Math.max(r.top - gap - h, 8);
  } else {
    const under = h <= below || (h > above && below >= above);
    if (h > (under ? below : above)) {
      h = Math.max(under ? below : above, 80);
      tip.style.maxHeight = `${Math.round(h)}px`;
    }
    top = under ? Math.min(r.bottom + gap, vh - 8 - h) : Math.max(r.top - gap - h, 8); // a tiny window: inside
  }
  tip.style.left = `${Math.round(left)}px`;
  tip.style.top = `${Math.round(top)}px`;
  const b = tip.getBoundingClientRect();
  debug.info.box = { left: Math.round(b.left), top: Math.round(b.top), right: Math.round(b.right), bottom: Math.round(b.bottom) };
}

/** @param {HTMLElement} trigger @param {boolean} pin */
function showTip(trigger, pin) {
  const source = document.getElementById(trigger.dataset.info || "");
  if (!source) return;
  window.clearTimeout(tipTimer);
  if (tipFor && tipFor !== trigger) markOpen(tipFor, false);
  const parts = [...source.children].filter((c) => !(/** @type {HTMLElement} */ (c).hidden)).map((c) => {
    const copy = /** @type {HTMLElement} */ (c.cloneNode(true));
    for (const n of [copy, ...copy.querySelectorAll("[id]")]) n.removeAttribute("id"); // ids stay unique
    return copy;
  });
  tip.replaceChildren(...parts);
  tip.hidden = false;
  tipFor = trigger;
  tipPinned = pin;
  markOpen(trigger, pin);
  placeTip(trigger);
  Object.assign(debug.info, { shows: debug.info.shows + 1, for: source.id, text: tip.textContent.replace(/\s+/g, " ").trim(), pinned: pin });
}

function hideTip() {
  window.clearTimeout(tipTimer);
  if (tipFor) markOpen(tipFor, false);
  tip.hidden = true;
  tipFor = null;
  tipPinned = false;
  Object.assign(debug.info, { for: "", text: "", pinned: false, box: null });
}

/** a shown bubble whose (i) was rebuilt or hidden (a list redrawn, another tab) goes too */
function dropStaleTip() {
  if (tipFor && (!tipFor.isConnected || !tipFor.getClientRects().length)) hideTip();
}

/** the pointer left the (i) or the bubble: close soon, unless it went into the other one */
function hideTipSoon() {
  if (tipPinned) return;
  window.clearTimeout(tipTimer);
  tipTimer = window.setTimeout(hideTip, 200);
}

/** @param {EventTarget | null} t @returns {?HTMLElement} the (i), or the whole rank card, the event happened on */
const infoOf = (t) => (t instanceof Element ? /** @type {?HTMLElement} */ (t.closest("[data-info]")) : null);
/** @param {HTMLElement} trigger @returns {?HTMLElement} its (i) button (a rank card's is its emblem) */
const buttonOf = (trigger) => (trigger.tagName === "BUTTON" ? trigger : trigger.querySelector(":scope > button.info"));
/** @param {HTMLElement} trigger @param {boolean} open the bubble is pinned open by it (marks its (i)) */
function markOpen(trigger, open) {
  buttonOf(trigger)?.classList.toggle("open", open);
}
/** @type {?HTMLElement} closed by a click while the pointer is still on it: no hover until it leaves */
let tipQuiet = null;

document.addEventListener("pointerover", (e) => {
  const trigger = infoOf(e.target);
  if (!trigger || e.pointerType !== "mouse" || trigger === tipQuiet) return;
  if (tipFor === trigger) window.clearTimeout(tipTimer); // moving within it, or back in before it closed
  else if (!tipPinned) showTip(trigger, false); // a pinned bubble stays until closed
});
document.addEventListener("pointerout", (e) => {
  const trigger = infoOf(e.target);
  const to = /** @type {Node} */ (e.relatedTarget);
  if (!trigger || tip.contains(to) || trigger.contains(to)) return;
  if (trigger === tipQuiet) tipQuiet = null;
  hideTipSoon();
});
tip.addEventListener("pointerenter", () => window.clearTimeout(tipTimer));
tip.addEventListener("pointerleave", hideTipSoon);
document.addEventListener("focusin", (e) => {
  const trigger = infoOf(e.target);
  if (trigger && tipFor !== trigger) showTip(trigger, false);
});
document.addEventListener("focusout", (e) => {
  if (infoOf(e.target) && !tipPinned) hideTip();
});
document.addEventListener("click", (e) => {
  const trigger = infoOf(e.target);
  if (trigger) {
    if (tipPinned && tipFor === trigger) {
      hideTip();
      tipQuiet = trigger;
    } else showTip(trigger, true);
  } else if (tipFor && !tip.contains(/** @type {Node} */ (e.target))) hideTip();
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !tipFor) return;
  const trigger = tipFor;
  const back = trigger.contains(document.activeElement) || tip.contains(document.activeElement);
  hideTip();
  if (back) buttonOf(trigger)?.focus({ preventScroll: true }); // never out of a field you are typing in
});
// the page moves under a shown bubble: keep it beside its (i), or close it when the (i) is gone
/** @param {Event} [e] */
const followTip = (e) => {
  if (!tipFor || (e && e.target instanceof Node && tip.contains(e.target))) return; // scrolling the bubble itself
  dropStaleTip();
  if (tipFor) placeTip(tipFor);
};
window.addEventListener("scroll", followTip, true);
window.addEventListener("resize", followTip);

// --- tabs: Route · Trade (best trade, commodity) · Services (with the engineers) · Powerplay · Gear · Commander · Earnings · Missions (the one shown is remembered in this browser) --------------------

const TAB_KEY = "edmultitool.tab";
const TABS = /** @type {HTMLButtonElement[]} */ ([...document.querySelectorAll('.tabs [role="tab"]')]);

/** @param {string} id the tab button's id, e.g. "tab-trade" @param {boolean} [focus] */
function showTab(id, focus = false) {
  const tab = TABS.find((t) => t.id === id) || TABS[0];
  for (const t of TABS) {
    const on = t === tab;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    $(t.getAttribute("aria-controls") || "").hidden = !on;
  }
  if (focus) tab.focus();
  closeAllSuggestions();
  dropStaleTip();
  debug.tab = tab.id;
  if (tab.id === "tab-commander") {
    crAsked = ""; // shown again: the plugin decides whether its count is still fresh
    askHold();
  }
  try {
    localStorage.setItem(TAB_KEY, tab.id);
  } catch {
    // no storage: the tab is simply not remembered
  }
}

for (const t of TABS) {
  t.addEventListener("click", () => showTab(t.id));
  t.addEventListener("keydown", (e) => {
    const i = TABS.indexOf(t);
    const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: TABS.length - 1 }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    showTab(TABS[(to + TABS.length) % TABS.length].id, true);
  });
}
// a link may name the tab to open (…/#trade); otherwise the one last shown in this browser
// the engineers are a view of the Services card, the commodity search and the sell card views of the Trade tab, and the Route tab
// was called Search: older links and remembered tabs name them as before
const TAB_ALIASES = { "tab-engineers": "tab-services", "tab-commodity": "tab-trade", "tab-sell": "tab-trade", "tab-search": "tab-route" };
/** @param {string} hash e.g. "#trade" @returns {string} the tab button's id */
const tabOf = (hash) => TAB_ALIASES[`tab-${hash.slice(1)}`] || `tab-${hash.slice(1)}`;
const linkedTab = location.hash ? tabOf(location.hash) : "";
let storedTab = "";
try {
  storedTab = localStorage.getItem(TAB_KEY) || ""; // read before showTab stores the tab shown now
} catch {
  // no storage: nothing remembered
}
window.addEventListener("hashchange", () => {
  const id = tabOf(location.hash);
  if (location.hash === "#engineers") showSvcView("engineers");
  if (location.hash === "#commodity") showTradeView("commodity");
  if (location.hash === "#sell") showTradeView("sell");
  if (TABS.some((t) => t.id === id)) showTab(id);
});
try {
  showTab(TABS.some((t) => t.id === linkedTab) ? linkedTab : TAB_ALIASES[localStorage.getItem(TAB_KEY) || ""] || localStorage.getItem(TAB_KEY) || "tab-route");
} catch {
  showTab(linkedTab || "tab-route");
}

$("gear-form").addEventListener("submit", onGear);
$("market-form").addEventListener("submit", onMarket);
$("trade-form").addEventListener("submit", onTrade);
$("t-pad").addEventListener("change", () => (tradePadTouched = true));
$("t-system").addEventListener("input", () => (tradePlaceTouched = true));
$("t-system").addEventListener("change", () => loadStations(input("t-system").value)); // typed, then left
$("t-station").addEventListener("change", () => (tradePlaceTouched = true));
$("tv-trade").addEventListener("click", () => showTradeView("trade"));
$("tv-commodity").addEventListener("click", () => showTradeView("commodity"));
$("tv-sell").addEventListener("click", () => showTradeView("sell"));
$("sell-form").addEventListener("submit", onSell);
$("sell-pad").addEventListener("change", () => (sellPadTouched = true));
try {
  // an older version remembered the commodity search as a tab of its own
  const older = !location.hash && storedTab === "tab-commodity";
  const linked = location.hash === "#commodity" || older ? "commodity" : location.hash === "#sell" ? "sell" : "";
  const wanted = linked || localStorage.getItem(TRADE_VIEW_KEY);
  showTradeView(wanted === "commodity" || wanted === "sell" ? wanted : "trade");
} catch {
  showTradeView("trade");
}
$("m-pad").addEventListener("change", () => (padTouched = true));
input("flash-on").addEventListener("change", saveFlash);
input("flash-over").addEventListener("change", saveFlash);
$("update-hide").addEventListener("click", () => {
  updateClosedFor = $("update").dataset.version || "";
  const version = updateClosedFor;
  try {
    localStorage.setItem(UPDATE_HIDDEN_KEY, version);
  } catch {
    // storage blocked: closed for this page only
  }
  $("update").hidden = true;
});
input("update-check").addEventListener("change", () => post("/api/update-check", { on: input("update-check").checked }));
$("svc-pad").addEventListener("change", () => {
  svcPadTouched = true;
  onSvcOption();
});
loadTicks();
$("svc-form").addEventListener("submit", onFind);
for (const box of [input("svc-carriers"), input("svc-settlements"), input("svc-barred"), ...hasBoxes()]) {
  box.addEventListener("change", onSvcOption);
}
for (const id of ["svc-trader-kind", "svc-broker-kind"]) $(id).addEventListener("change", onSvcOption);
$("pp-form").addEventListener("submit", onPowerplay);
$("pp-power").addEventListener("change", () => (ppPowerTouched = true));
for (const r of document.querySelectorAll('input[name="pp-mode"]')) r.addEventListener("change", fillStates);
fillStates();
$("svc-stations").addEventListener("click", () => showSvcView("services"));
$("svc-engineers").addEventListener("click", () => showSvcView("engineers"));
try {
  const wanted = location.hash === "#engineers" ? "engineers" : localStorage.getItem(SVC_VIEW_KEY);
  showSvcView(wanted === "engineers" ? "engineers" : "services");
} catch {
  showSvcView("services");
}
for (const id of ["eng-module", "eng-grade", "eng-all"]) {
  $(id).addEventListener("change", () => {
    if (!debug.reply) return;
    renderEngineers(debug.reply.state);
    renderServices(debug.reply.state);
  });
}
for (const r of document.querySelectorAll('input[name="eng-kind"]')) {
  r.addEventListener("change", () => debug.reply && renderEngineers(debug.reply.state));
}
$("gear-fields").addEventListener("submit", onGearFields);
$("b-gear-clear").addEventListener("click", onGearClear);
$("scout-form").addEventListener("submit", onScout);
$("b-cancel").addEventListener("click", onCancel);
$("b-replan").addEventListener("click", onReplan);
$("plan-ship").addEventListener("change", onPickShip);
$("b-copy").innerHTML = COPY_SVG;
$("b-copy").addEventListener("click", onCopy);
$("b-skip").addEventListener("click", () => onStep(1));
$("b-back").addEventListener("click", () => onStep(-1));
$("b-take-game").addEventListener("click", onTakeGame);
select("v-order").value = view.order;
select("v-show").value = view.show;
select("v-order").addEventListener("change", onView);
select("v-show").addEventListener("change", onView);
for (const b of document.querySelectorAll("button[data-preset]")) {
  b.addEventListener("click", () => applyPreset(/** @type {HTMLElement} */ (b).dataset.preset || ""));
}
for (const r of document.querySelectorAll('input[name="plan"]')) r.addEventListener("change", syncPlan);
attachSuggest(input("f-from"), $("s-from"));
attachSuggest(input("f-to"), $("s-to"), () => (debug.reply ? debug.reply.state.recent_to || [] : []));
attachSuggest(input("m-commodity"), $("s-commodity"), undefined, commoditiesMatching);
attachSuggest(input("t-system"), $("s-t-system"));
input("m-commodity").addEventListener("focus", loadCommodities);
loadCommodities();
// the data ages keep counting while nothing else changes (the table is rebuilt once a minute at most)
window.setInterval(() => {
  if (debug.reply) renderMarket(debug.reply.state);
  if (debug.reply) renderTrade(debug.reply.state);
  if (debug.reply) renderSell(debug.reply.state);
  if (debug.reply) renderPowerplay(debug.reply.state);
  if (Date.now() - crAskedAt > 15 * 60 * 1000) crAsked = "";
  if (debug.reply) renderCareer(debug.reply.state);
}, 15000);

// --- day / night: one button under the connection state, a sun by day and a moon by night; it switches to
// the other. Until it is pressed the page follows the system's setting; the choice is remembered in this
// browser (theme.js applies it before paint).
const THEME_KEY = "edmultitool.theme";
const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
/** The look shown now: chosen, or the system's. */
function shownTheme() {
  return document.documentElement.dataset.theme || (systemDark.matches ? "dark" : "light");
}
/** Puts the sun or the moon on the button. */
function showTheme() {
  const shown = shownTheme();
  const b = $("theme");
  b.dataset.shown = shown;
  b.querySelector("use")?.setAttribute("href", `#ic-theme-${shown}`);
  const label = shown === "dark" ? "Night look: press for day" : "Day look: press for night";
  b.setAttribute("aria-label", label);
  b.title = label;
}
$("theme").addEventListener("click", () => {
  const next = shownTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch {
    // storage blocked: the choice lasts until the page is reloaded
  }
  showTheme();
});
systemDark.addEventListener("change", showTheme);
showTheme();

// --- About: a window over the page; Esc, × or a click beside it closes it
const about = /** @type {HTMLDialogElement} */ ($("about"));
$("about-open").addEventListener("click", () => about.showModal());
$("about-close").addEventListener("click", () => about.close());
about.addEventListener("click", (ev) => {
  if (ev.target === about) about.close(); // the backdrop, not the card
});
if (location.hash === "#about") about.showModal(); // a link straight to it (#about)

poll();
