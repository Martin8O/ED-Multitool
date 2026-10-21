# API notes — the exact request shapes ED Multitool relies on

Spansh has no official API documentation. Every shape below was observed live and is replayed by the
tests from `tests/data/spansh/` (trimmed real replies, re-recorded with
`tools/smoke_spansh.py --record`). If Spansh changes one of these, the smoke script is the first thing to
run. Code: `EDMultitool/edmultitool/sources/spansh.py`; all calls go through `sources/http.py`.

## Spansh — `https://spansh.co.uk/api`  (verified 2026-10-12)

Common to every call:
- `User-Agent: EDMultitool/<version> (EDMC plugin; +https://github.com/Martin8O/ED-Multitool)`, `Accept: application/json`.
- Our pace: at most one request per second (polling included), 20 s timeout. GETs are retried on
  429/5xx/network failure, job submits (POST) only on 429/503 — after a timeout or 502 Spansh may already
  have the job, and a retry would start a duplicate. Waits: Retry-After if given, else 2 / 4 / 8 s; a
  Retry-After over 60 s means we give up at once instead of knocking early.
- Errors come back as JSON `{"error": "<message>"}` with a 4xx status; we show the message.
- **id64 arrives as a string** in route results (`"id64": "10477373803"`) and as a number in the
  autocomplete reply. Both are parsed to an exact int. The numeric `id` field in bodies is the same value
  as a float-unsafe number — never read it.
- No CORS: only our Python side calls Spansh, never the web page.

### System-name autocomplete
`GET /systems/field_values/system_names?q=<text>` → `200`
```json
{"min_max": [{"id64": 525823134027, "name": "Col 285 Sector VE-Z d15", "x": -235.125, "y": 66.90625, "z": -280.65625}, …]}
```
- Up to 20 rows, prefix matches. We skip texts shorter than 2 characters. Cached 24 h.

### Tour jobs: riches (FSS/DSS) and exobiology
Submit, form-encoded (`application/x-www-form-urlencoded`):

| field | riches `POST /riches/route` | bio `POST /exobiology/route` | our value |
|---|---|---|---|
| `from` | ✓ | ✓ | system name |
| `to` | ✓ | ✓ | system name |
| `range` | ✓ | ✓ | laden jump range, ly, 2 decimals |
| `radius` | ✓ | ✓ | ly from the from→to line |
| `max_results` | ✓ | ✓ | stops wanted (Spansh may return fewer) |
| `max_distance` | ✓ | ✓ | max distance to arrival, ls |
| `min_value` | ✓ | ✓ | riches: min scan (or mapping) value **per body**; bio: min value **per species** (see below) |
| `loop` | ✓ | ✓ | `0` / `1` |
| `avoid_thargoids` | ✓ | ✓ | `0` / `1` |
| `use_mapping_value` | ✓ | — (Spansh ignores it; not sent) | `1` = rank by DSS value |

- Submit reply: `202 {"job": "<UUID>", "status": "queued"}`.
- Unknown system: `400 {"error": "Could not find starting system"}`.
- **`max_results` picks the best N, then orders them along the route** (probed live 2026-10-12, Sol → Colonia,
  riches min 1 M and bio, 10 vs 30): the 30-stop answer holds all 10 of the 10-stop answer plus 20 more, but
  not as a prefix — the extra stops sit in tour order among them. Swapping a done stop relies on this: the
  replacement for a stop that is already yours comes from the same search asked for 3× the stops
  (≤ 100), sent only when a stop is spent.
- Body `id64` = the system's `id64` + the journal's `BodyID` << 55 (body `id` is the same number again);
  that is how journal `SAAScanComplete` / `ScanOrganic` bodies are matched to Spansh bodies.

Poll: `GET /results/<job>`
- still running → `202` with `{"job", "state": "started", "status": "queued", "parameters": {…}, …}`;
- done → `200 {"status": "ok", "state": "completed", "job", "parameters", "result": [ … ]}`;
  often already on the first poll (1–2 s for a Sol→Colonia tour);
- unknown / expired job → `404 {"error": "Unable to find route"}`.
- We stop polling after 300 s. Completed results are cached by the exact submit form for 6 h.

`result` rows (in tour order; the **first row is `from` and the last is `to`**, both with no bodies):
```json
{"id64": "180665307059", "name": "Boelts KT-Q d5-5", "jumps": 497, "x": …, "y": …, "z": …, "bodies": [ … ]}
```
- `jumps` = jumps from the previous row at the given range.

Riches body:
```json
{"id64": "396316947873910707", "name": "Boelts KT-Q d5-5 A 5", "type": "Planet",
 "subtype": "High metal content world", "distance_to_arrival": 1177.85, "is_terraformable": true,
 "estimated_scan_value": 191807, "estimated_mapping_value": 696898, "landmark_value": null}
```
Bio body — same, without `is_terraformable`, plus species:
```json
{"landmark_value": 34841800, "landmarks": [{"type": "Frutexa", "subtype": "Frutexa Acus", "count": 16, "value": 7774700}, …]}
```
- `value` = base sale value of the species (no first-logged bonus); `count` = sightings Spansh knows of.
- **Bio `min_value` is a per-species filter** (verified 2026-10-12, Sol→Colonia at 22.38 ly): with 5,000,000
  every returned `landmarks[]` entry was ≥ 5,000,000 and cheaper species were simply left out of the list;
  with 20,000,000 no system came back at all (the dearest species on that line is 19,010,800), although
  bodies worth 34.8 M in total exist there. So ED Multitool sends the *species* minimum as `min_value` and
  applies its per-body minimum itself.
- `landmark_value` is the body's total over **all** its species, unfiltered — that is why it "did not
  match the sum" of a filtered list. We sum the returned `landmarks[].value` ourselves and never read it.
- Riches `min_value` filters per body: every returned body has its ranked value (mapping when
  `use_mapping_value=1`, else scan) ≥ `min_value`.

Checked by hand on 2026-10-12 against spansh.co.uk's own results pages for the recorded jobs:
riches 7 systems / 11 bodies / scan 3,354,293 / mapping 12,187,252 / 1,114 jumps — identical; bio
per-body species values and sums identical (e.g. Qa'wakana 12 f: 9 species, 34,841,800 Cr).

A tour job's `from` must be a system Spansh has a record of: from an undiscovered one it answers 400
`{"error": "Could not find starting system"}`. When that start is the system you are in, the search starts
instead from the nearest system Spansh knows (next section) and says so.

### The nearest system Spansh knows to a point  (verified 2026-10-18)
`POST /systems/search` with no filter at all, nearest first, one row:
```json
{"filters": {}, "sort": [{"distance": {"direction": "asc"}}],
 "reference_coords": {"x": -75.0, "y": -150.0, "z": -340.0}, "size": 1, "page": 0}
```
→ `{"count": 10000, "results": [{"id64", "name", "x", "y", "z", "distance", …}]}` (Merope, 3.65 ly; from its
own coordinates: Merope at 0.0 ly). Recorded reply: `tests/data/spansh/systems_nearest_merope.json`
(trimmed). Cached like the other searches.

### Route-only plots: the galaxy plotter  (verified 2026-10-12)
Both route-only choices — "Normal" and "With neutron boosts" — use Spansh's **galaxy plotter**, so both show
every jump with fuel (the same table either way). Shape read off spansh.co.uk's own page
code (form-encoded like the tours, same `/results/<job>` polling), then each parameter we vary probed live at
two values. Recorded replies: `tools/smoke_spansh.py --plots --record` (Sol → Maia, both choices).

`POST /generic/route` (spansh.co.uk/exact-plotter) — wants system **ids**, not names (we look names up with
the autocomplete, exact name match, any letter case; a name two systems share is refused). The autocomplete
puts the exact name **first**, even when 20+ names start with it (checked 2026-10-12: "Sol",
"Col 285 Sector KZ-C b27-1", "Synuefe EN-H d11-11", the last two with a full list of 20), so far a real
system was always among the 20 rows (observed, not documented by Spansh). Ship constants as Spansh's own
journal-Loadout import computes them:

| field | our value |
|---|---|
| `source`, `destination` | id64 |
| `optimal_mass`, `max_fuel_per_jump` | FSD after engineering (journal modifiers) |
| `fuel_multiplier`, `fuel_power` | FSD constants, e.g. `0.013`, `2.45` |
| `base_mass` | Loadout `UnladenMass` + reservoir fuel |
| `tank_size`, `internal_tank_size` | main tank, reservoir |
| `range_boost` | Guardian FSD booster, ly |
| `cargo` | t carried the whole way |
| `use_supercharge` | `0` = "Normal", `1` = "With neutron boosts" |
| `supercharge_multiplier` / `injection_multiplier` | `4` (`6` for the SCO Mk II, as Spansh maps `…_overchargebooster_mkii`) / `2` |
| `is_supercharged`, `use_injections`, `use_injections_when_required`, `exclude_secondary`, `reserve_size` | `0` |
| `refuel_every_scoopable` | `0` |
| `algorithm` / `max_time` | `optimistic` / `60` (Spansh's defaults) |

Probed at two values (Sol → Maia, Mandalay SCO 5A): `cargo` 8 → 20 jumps, longest 22.49 ly; 100 → 23 jumps,
longest 19.46 ly. `use_supercharge` 0 → no neutron jump; 1 → 18 jumps incl. one 90 ly boost.
`refuel_every_scoopable` 1 → `must_refuel` on 14 rows (every scoopable star); 0 → 3 rows (only where needed).
`range_boost` 0 → 20 jumps, longest 22.49 ly; 10.5 → 13 jumps, longest 32.66 ly — a flat bonus in ly per
jump, the same figure the journal-based range adds. The other ship constants were sent at one value each
(this ship's); every one of the 20 jumps lies within ED Multitool's own range formula for the fuel on board at
that moment (tightest: 22.25 of 22.27 ly), so Spansh reads them in the units we send.

Result: `result.jumps[]`, one row per jump, first = the start (distance 0):
```json
{"name": "Tharwangu", "id64": 13865093965185, "x": …, "y": …, "z": …, "distance": 18.56,
 "distance_to_destination": 290.73, "fuel_in_tank": 32, "fuel_used": 3.13, "must_refuel": true,
 "is_scoopable": true, "has_neutron": false, "must_inject": 0}
```
- `has_neutron: true` = **supercharge at this star**: the jump of the NEXT row is the boosted one (Sol → Colonia
  with neutrons: all 183 marks sit right before the 183 jumps longer than a normal jump, and nowhere else).
- `is_scoopable` is right for the stars on the way (checked against Spansh's own system data: G, M, M =
  scoopable; L brown dwarf, neutron star = not), but the **start and destination always say `false`** — Maia
  (a B star) and Brestla (a red dwarf) included. ED Multitool treats both ends as "not known".
- The route starts with **full tanks**; `fuel_in_tank` is after arriving (and after refuelling).
- A long plot takes a while: Sol → Colonia took ~64 s (normal, 1,071 jumps) and ~63 s (neutrons, 784
  jumps); the normal reply is ~440 KB.
- Start = destination is not refused: Sol → Sol comes back as a two-row route of 0 ly. ED Multitool refuses it
  before asking.

Checked by hand on 2026-10-12 against spansh.co.uk's own results pages for the very form ED Multitool sends
(same job shape, Spansh's page reading it): normal Sol → Maia — 20 jumps, every name / distance / refuel mark
identical; with neutrons Trianguli Sector EL-Y c7 → Brestla — 6 jumps, supercharge at Jackson's Lighthouse,
refuel at Col 285 Sector ZT-X b15-1, all rows identical; with neutrons Sol → Colonia — 785 rows, 784 jumps,
183 neutron and 133 refuel marks, every row identical.

**Neutron plotter** `POST /route` (spansh.co.uk/plotter) — used briefly, **not used since 2026-10-12** (it lists
only the supercharge waypoints, so a trip without neutrons showed as start → destination). Kept for the
record: fields `from`, `to` (names), `range`, `efficiency` (default 60; 100 made the job fail with HTTP 500),
`supercharge_multiplier` (4 / 6); result `result.system_jumps[]` with `system`, `id64`, `distance_jumped`,
`jumps` (from the previous row; their sum is the trip's jump count, `result.total_jumps` is the number of legs)
and `neutron_star`. Its Sol → Colonia at 22.09 ly matched spansh.co.uk row for row (268 rows, 700 jumps).
- **Prime planets:** the riches job has no body-type filter, so with `planets` = prime we send
  `max_results` = 100 and filter subtypes (Earth-like / Water / Ammonia world, or `is_terraformable`) on our
  side. Probed live 2026-10-12, Sol → Colonia, min 1M mapping: 10 → 12 rows, 100 → 102 rows, both fine.

### Gear posts: system and station searches by name  (verified 2026-10-12)
The "Gear from the forum" card turns the place names of a forum post into systems and stations. Recorded
replies: `tools/smoke_spansh.py --gear --record` (`tests/data/spansh/gear_*.json`, trimmed).

- Pieces that may be systems go through the **autocomplete** above (any letter case; exact name only). A piece
  that reads like a station ("… Terminal", "… Port", "… Town") is never asked for as a system.
- `POST /systems/search`, JSON body (`Content-Type: application/json`):
  `{"filters": {"name": {"value": ["Keian Gu", …]}}, "sort": [], "size": <2 × names>, "page": 0}` → `200`
  `{"count", "from", "reference", "results": [{"id64", "name", "x", "y", "z", "stations": [ … ], "bodies": …}]}`.
  **The name filter is exact and letter case matters** ("shivis" finds nothing, "Shivis" does) — so we send the
  autocomplete's own spelling. A list of names is an OR; one call for all the systems of a post.
  Each `stations[]` row: `name`, `type` ("Ocellus Starport", "Outpost", "Planetary Outpost", settlements…),
  `distance_to_arrival` (ls), `has_large_pad`, `medium_pads`, `small_pads`, `is_planetary` (plus market and
  services, never read). Odyssey settlements are in the list.
- The Missions tab uses the same system search to place mission destinations: the journal names a mission's
  system only by name (no id64), in the game's own spelling, which is Spansh's; all open missions' systems go
  in one call (≤ 20 names), cached 24 h. A name with no row = "Spansh does not know" on the page. Rows are
  matched back to the asked names ignoring letter case only as a safeguard (the filter itself is exact).
- `POST /stations/search`, same body shape, `size` 50 → `results[]` with the station fields above plus
  `system_name`, `system_id64` (a number), `system_x/y/z`. Used only for station names no found system holds:
  one call per name (≤ 4 per post) with the post's spelling and each word capitalised, so a name's hits are
  all of them. Many stations share a name ("Hahn Terminal": 7).
- Optional `"reference_system": "<name>"` adds `distance` (ly) to every row — used only to check our own
  distances by hand (2026-10-12, from Trianguli Sector RT-Q a5-0: Tatamo 183.60, Maricoriang 229.38, Kuukese
  189.07 ly — identical to ED Multitool's).
- Replies are large (a station row carries its whole market: ~36 KB each); we keep only the fields above,
  cached 24 h by query.
- A search changes nothing at Spansh, so a JSON POST is retried like a GET (one `502` was seen live and the same
  query answered at once on retry); job submits stay un-retried on 502 / timeouts.

### Best trade: the trade router  (verified 2026-10-14)

`POST /trade/route` (spansh.co.uk/trade), form-encoded, a job like the tours: 202 `{"job"}` → `GET
/results/{job}` until it stops answering 202. One hop only (`max_hops=1`): Spansh picks what to buy at the
start station and the station that buys it for the most profit.

| field | our value |
|---|---|
| `system`, `station` | names; **both required** (`system` alone → 400 `{"error": "system and station are required"}`) |
| `max_hops` | `1` |
| `max_hop_distance` | ly, straight from the start system to the one you sell in |
| `starting_capital` / `max_cargo` | Cr to spend / t to fill (the page's fields, or your balance / the ship's hold) |
| `max_system_distance` | ls from the star, both stations |
| `max_price_age` | **seconds** (1 day = `86400`) |
| `requires_large_pad` | `1` when the ship needs L, else `0` (Spansh tells only large pads from the rest) |
| `allow_planetary` / `allow_player_owned` | planetary ports and settlements / fleet carriers: `1` or `0` |
| `allow_prohibited`, `unique`, `permit` | `0` |

The result echoes `parameters` (only the truthy ones, with `starting_station_id` instead of the names) and
`result` = a list of hops, `[]` when nothing pays within the limits. A hop: `source` / `destination` =
{`system`, `system_id64` (a number), `station`, `market_id`, `distance_to_arrival` (ls), `market_updated_at`
(**unix seconds**), `x`, `y`, `z`}; `distance` (ly); `commodities` = [{`name`, `amount` (t), `profit` (Cr/t),
`total_profit`, `source_commodity` / `destination_commodity` = {`buy_price`, `sell_price`, `supply`,
`demand`}}]; `total_profit`, `cumulative_profit`. No pad sizes, station types, factions or docking access.

Probed live (Sol, Abraham Lincoln; `tools/smoke_spansh.py --trade`): 400 t / 50 M Cr → Biowaste 400 t,
profit = sell − buy per t and total = profit × t; 20,000 Cr → the load shrinks to 183 t (what 20,000 Cr buys,
no second commodity); `max_hop_distance` 1 → `result: []`; an unknown station → 400 `{"error": "Could not
find station"}` at the submit. The same 400 came for a real station Spansh's station search knows (Jiushao
Vista, Cave Sector HR-W d1-31) even with a 30-day price age: the trade router keeps its own list of
markets.

**The station choice** (before a trade): `POST /systems/search` `{"filters": {"name": {"value": [name]}},
"sort": [], "size": 4, "page": 0}` — the exact name, letter case included ("sol" finds nothing). Each system
row carries `stations` = [{`name`, `type`, `has_market`, `distance_to_arrival`, `has_large_pad`,
`medium_pads`, `small_pads`, …}]; we list those with `has_market: true`, nearest to the star first. Some rows
carry only `name`, `distance_to_arrival` and `has_large_pad` (4 of 67 in Sol): not listed. Fleet carriers did
not appear in Sol's list. Size 4 keeps its cache apart from the gear lookups' (size 2), whose rows were cached
without `has_market`.

### Commodity markets: the station search with a market filter  (verified 2026-10-12)
The "Commodity" card. Recorded reply: `tools/smoke_spansh.py --market --record`
(`tests/data/spansh/market_sol_tritium.json`, trimmed). Every filter below was checked live at two values.

- `POST /stations/search`, JSON body:
  ```json
  {"filters": {
     "market": [{"name": "Tritium", "demand": {"value": [10, 999999999], "comparison": "<=>"}}],
     "market_updated_at": {"value": ["2026-10-09T16:00:00Z", "2026-10-13T16:00:00Z"], "comparison": "<=>"},
     "type": {"value": ["Asteroid base", "Coriolis Starport", …every type but "Drake-Class Carrier"]},
     "has_large_pad": {"value": true}},
   "sort": [{"distance": {"direction": "asc"}}],
   "reference_coords": {"x": 0, "y": 0, "z": 0}, "size": 25, "page": 0}
  ```
  → `{"count", "results": [station rows]}`, nearest first; `distance` (ly) from `reference_coords`.
- `market[].name` is the display name exactly as `GET /stations/field_values/market` lists it (the bundled
  list, `tools/make_commodities.py`). `supply` = stock (you buy), `demand` (you sell); the range applies to
  that commodity only (Tritium near Sol: ≥ 10 t → 5,188 stations, ≥ 5,000 t → 1,597, every row ≥ 5,000).
- `market_updated_at` filters the station's market time; `type` is an OR of station types. **A type
  `"comparison": "!="` is ignored** (carriers still came back), so carriers are left out by listing every
  other type (`GET /stations/field_values/type`). Only a large pad has a filter; medium is checked by us.
- Each row's `market[]` is the WHOLE market (~36 KB a station): we keep only the commodity asked
  (`buy_price`, `sell_price`, `supply`, `demand`) plus `controlling_minor_faction`, `carrier_docking_access`
  (`"All"`, `"Friends"`, `"Squadron Friends"`, … or missing), `market_updated_at`, pads, `system_x/y/z`.
- A carrier's `controlling_minor_faction` is `"FleetCarrier"`. A Powerplay **Stronghold Carrier** is a row
  named `"Stronghold Carrier"` of type `"Planetary Outpost"` whose `controlling_minor_faction` is the power
  ("Aisling Duval") — only pilots pledged to that power may dock.
- Cached 10 minutes (markets change): the `market_updated_at` window moves in 10-minute steps, so the same
  search again within them is the same request; the exact age is cut on our side. More pages (`page` 1, 2)
  are asked only when our own filters leave fewer than 10 rows; a later page failing keeps what was found.

### Selling the hold: the same search, best price first, within a distance  (verified 2026-10-14)
The "Sell cargo" card, one search per commodity carried. Recorded reply: `tools/smoke_spansh.py --sell
--record` (`tests/data/spansh/sell_sol_painite.json`, trimmed). Checked live: the order on every page, the
distance at 20 and 50 ly, demand ≥ the tonnes carried, large pads.

- The commodity-market body above, with `demand` from the tonnes you carry, plus
  `"distance": {"min": "0", "max": "50"}` (ly from `reference_coords`; strings, as Spansh's own page sends)
  and `"sort": [{"market_sell_price": [{"name": "Painite", "direction": "desc"}]}]`.
  → the best-paying stations first; `count` is the number within the distance (Painite ≥ 30 t near Sol:
  554 within 50 ly, 105 within 20).
- Each row still carries its whole market: we keep every commodity of the hold (not only the one searched),
  so the one station that pays most for the whole load is found among all the rows returned. A commodity at
  demand 0 there is not an offer.
- Cached 10 minutes like the market search; the cache key includes the commodities kept.

### Nearest station: the station search with no market condition  (verified 2026-10-13, services 2026-10-15)
The "Nearest service" card's only source. Recorded replies: `tools/smoke_spansh.py --stations --record`
(`tests/data/spansh/stations_sol_large.json`, `stations_sol_medium.json`, `stations_sol_services.json`,
`stations_sol_trader_raw.json`, trimmed). Each filter checked live at two values (large pad / none, settlements
in / out, carriers in / out, services, kinds).

- `POST /stations/search` with `{"filters": {"type": {"value": [...]}, "has_large_pad": {"value": true}},
  "sort": [{"distance": {"direction": "asc"}}], "reference_coords": {x, y, z}, "size": 25, "page": n}`.
  Types = the bundled `STATION_TYPES` (no carriers), minus `"Settlement"` / `"Surface Settlement"` when
  settlements are left out, plus `"Drake-Class Carrier"` when carriers are wanted. Medium pads are checked by us.
- **Order within a system is arbitrary** (only the system's distance is sorted): ED Multitool sorts by
  (system distance, `distance_to_arrival`) and asks the next page while a page still ends in the last system
  shown (Sol has more stations than one page).
- A row: the station fields of the market search plus `updated_at`; a megaship can have `medium_pads` /
  `small_pads` null (no pad known: never shown). Odyssey settlements often report `has_large_pad: true` with
  0 medium / small pads.
- Cross-checked 2026-10-13 from Bitjala (large pad, no settlements): the 10 shown = a direct query sorted the
  same way, 10/10, Rajgonta 6.47 ly.
- **Services**: `"services": [{"name": "Black Market"}, {"name": …}]` = stations with **every** one;
  `"services": {"value": [...]}` = **any one** of them (with or without a `"comparison"`), so the list-of-names
  form is the one used. Names as in a row's `services[].name` ("Material Trader", "Technology Broker",
  "Interstellar Factors Contact", "Universal Cartographics", "Vista Genomics", "Restock", …); an unknown name
  gives 0 results. Recorded: `stations_sol_services.json` (Black Market + Interstellar Factors, 25 of 25 have
  both). Cross-checked from 9 Tauri (medium pad): the 10 shown all have both in a direct query by name.
- **A trader's or broker's kind** = the station's primary economy (the game's rule): `"primary_economy":
  {"value": ["Extraction", "Refinery"]}` (raw), `["Industrial"]` (manufactured / human broker),
  `["High Tech", "Military"]` (encoded), `["High Tech"]` (Guardian broker). A value list is "any of"; checked
  live near Sol (raw traders 373, every row Extraction or Refinery; Guardian brokers 887, every row High Tech).
  A row carries `primary_economy` in Spansh's spelling (`"High Tech"` with a space). Two kinds that share no
  economy (a raw trader with a human broker) are answered without asking. Cross-checked from Maia (raw trader,
  medium pad): the 10 shown = a direct query sorted the same way, 10/10, Hinz Hub 196 ly.

### Powerplay: the system search with a power filter  (verified 2026-10-12)
The "Powerplay" card. Recorded replies: `tools/smoke_spansh.py --powerplay --record`
(`tests/data/spansh/power_sol_lyr_{held,others,acquire}.json`, trimmed). Each filter was checked live at two values.

- `POST /systems/search`, JSON body:
  ```json
  {"filters": {"controlling_power": {"value": ["Li Yong-Rui"]},
               "power_state": {"value": ["Exploited", "Fortified", "Stronghold"]}},
   "sort": [{"distance": {"direction": "asc"}}],
   "reference_coords": {"x": 0, "y": 0, "z": 0}, "size": 10, "page": 0}
  ```
  → `{"count", "results": [system rows]}`, nearest first. `count` is capped at 10 000.
- Power names: `GET /systems/field_values/controlling_power` (and `/power`) — the 12 powers, spelled as the
  journal spells them (`"A. Lavigny-Duval"`). States: `GET /systems/field_values/power_state` → `Exploited`,
  `Fortified`, `Stronghold`, `Unoccupied` (nothing else).
- "Held": `controlling_power` (from Sol: 2,185 systems; with `power_state` Fortified 429, every row Fortified).
  "Present": `power` (the row's `power[]` holds it) — includes systems another power holds (Sol: Jerome Archer)
  and Unoccupied ones (7,995 for Li Yong-Rui). The page asks it two ways, never mixed with the power's own:
  "others hold" = `power` + `controlling_power` = the 11 other powers + the three controlled states (a value
  list is "any of"; from Sol 3,206 for Li Yong-Rui, Sol first, held by Jerome Archer); "can acquire" = `power`
  + `power_state` Unoccupied (from Sol 8,013, Stein 2051 first). Recorded 2026-10-14.
- Row fields we keep: `id64`, `name`, `x/y/z`, `controlling_power` (null when Unoccupied), `power[]`,
  `power_state`, `power_state_control_progress` (a fraction: 1.44 = 144 %, past the state's band),
  `power_state_reinforcement` / `power_state_undermining` (merits this cycle), `updated_at`. An Unoccupied
  row leaves `controlling_power`, the progress and both merit fields out altogether (not null). A row also carries bodies, stations and factions: trimmed before caching.
- Cached 15 minutes.
- **How many systems a power holds** (the Commander tab): the same search, one per state
  (`power_state` = `Stronghold` / `Fortified` / `Exploited`), `reference_coords` 0,0,0 and `"size": 1`: only
  `count` is read. Li Yong-Rui on 2026-10-13: 241 + 429 + 1,515 = 2,185, the same as the "held" search.
  Then every one of the 12 powers held in any controlled state (all three states in one filter, size 1): 12 more
  searches, cached 15 minutes like the rest. 2026-10-13: Aisling Duval 3,038 (a direct query: 3,038) … Denton
  Patreus 720.

### The bodies of one system: the body search  (verified 2026-10-13)
The "This system" card (on every arrival). Recorded replies: `tools/smoke_spansh.py --arrival --record`
(`tests/data/spansh/bodies_{boelts,qawakana,plaa_eurk,unknown}.json`, trimmed; landmarks without coordinates).

- `POST /bodies/search`, JSON body:
  ```json
  {"filters": {"system_id64": {"value": 642232231025}}, "size": 100, "page": 0}
  ```
  → `{"count", "results": [body rows]}`; a system Spansh does not know → `count` 0, `results` []. A further
  page is asked only while a full page came back and `count` is not reached (no real system needs it).
- Row fields we read: `body_id` (the journal's BodyID), `name`, `type` (`Star` / `Planet`), `subtype` (EDSM-style
  names: `High metal content world`, `Earth-like world`, `K (Yellow-Orange) Star`…), `distance_to_arrival`,
  `estimated_scan_value`, `estimated_mapping_value` (also given for stars, where it means nothing — not read),
  `terraforming_state` (`Terraformable` / `Not terraformable` / `Terraformed`; there is no `is_terraformable` here,
  unlike the tour jobs), `is_landable`, `signals` (`[{"name": "Biological", "count": 6}, …]`), `genuses`
  (`[{"name": "Bacterial"}]`: codex keys, shown as the game's genus names), `landmarks` (every sighting:
  `{"type": "Frutexa", "subtype": "Frutexa Acus", "value": 7774700, "variant", "latitude", "longitude"}`, one
  entry per sighting, plus geysers and settlements with `value` 0). Species = landmarks with a value, one per
  `subtype`.
- Values: `estimated_*` follow one formula exactly (all 89 planets of the three recorded systems, to the
  credit): scan = ⌊max(500, k + k·0.56591828·mass^0.2)⌋ with mass in Earth masses; mapping =
  ⌊scan·3.3333333333⌋ + ⌊max(scan·0.3, 555)⌋. k: Earth-like 181,126; water world 64,831 (+116,295
  terraformable); ammonia world 96,932; metal-rich 21,790; high metal content and class II gas giant 9,654
  (+100,677 terraformable HMC); class I gas giant 1,656; anything else 300 (+93,328 terraformable). Stars: ⌊k +
  solar masses·k/66.25⌋, k 1,200 (22,628 neutron stars and black holes, 14,057 white dwarfs). Our own FSS
  scans are valued with it (`core/arrival.py`), so a body Spansh does not know yet gets the same number.
- Cached 1 hour (other explorers' scans reach Spansh within that).

## Ardent Insight — `https://api.ardent-insight.com/v2`  (verified 2026-10-12)
The commodity card's backup when Spansh fails. Recorded reply:
`tests/data/ardent/market_sol_tritium.json` (first 30 rows). Same client, pace and User-Agent as Spansh.

- `GET /system/address/{id64}/commodity/name/{symbol}/nearby/imports` (where you can sell) or `/exports`
  (where you can buy); `/system/name/{name}/…` takes the system's name instead (used only when the id64 is
  not known), params `maxDistance` (ly, default 100, max 500), `minVolume` (demand / stock ≥),
  `maxDaysAgo` (whole days; default 90), `sort=distance`, `fleetCarriers=false` to leave carriers out —
  **`true` means carriers ONLY**, so with carriers wanted the parameter is left out.
- `{symbol}` is the game's commodity symbol in lower case (`lowtemperaturediamond`, `opal` for Void Opal),
  never the display name (`low temperature diamonds` → `[]`). The pairs are bundled
  (`catalog/commodities.py`); a commodity without one has no backup search.
- Reply: a list (up to 1,000 rows) of `{stationName, stationType, maxLandingPadSize (1/2/3), systemAddress,
  systemName, systemX/Y/Z, distanceToArrival, buyPrice, sellPrice, stock, demand, updatedAt, distance}`.
  The **current system itself is left out**; no station faction; Stronghold Carriers come as
  `stationType: "StrongholdCarrier"` without their power (so never known to be yours), and carriers without
  their docking access (shown as open). `demand: 0` rows pass any `minVolume` (Ardent
  reads 0 as "no limit"); we leave them out, as Spansh's filter does.

## GitHub — `https://api.github.com`  (verified 2026-10-14)
The update check. Public, no token, no sign-in; the same polite client, pace and User-Agent.

- `GET /repos/{owner}/{repo}/releases/latest` → the newest **published** release (drafts and pre-releases are
  never returned). Fields used: `tag_name` (`v0.31.0`), `html_url` (the release page), `name`, `draft`,
  `prerelease`. Shape as seen on a public repository, kept in `tests/data/github/latest_release.json` with our own
  tag. A `404` (`{"message": "Not Found"}`) means no release is published yet, or the repository cannot be
  seen: "nothing newer", and the check says nothing.
- Unauthenticated limit: 60 calls an hour per address, answered with `403` / `429`; the client asks about once
  a day (reply cached 24 h), so the limit is never near. A failed ask is tried again an hour later.
- A tag counts only when it reads as `[v]MAJOR.MINOR[.PATCH][-prerelease]`; a link counts only when it begins
  `https://github.com/`.
