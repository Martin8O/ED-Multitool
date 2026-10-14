# ED Multitool — architecture

ED Multitool is an [EDMarketConnector](https://github.com/EDCD/EDMarketConnector) (EDMC) plugin for Elite
Dangerous. It reads the player's live state from EDMC and the journal files, asks online services (Spansh,
Ardent Insight) for galaxy data, and shows the results in a local browser page plus a small panel inside
EDMC's window.

```
 Elite Dangerous ──journal──▶ EDMC ──hooks──▶ load.py (thin adapter, tk main thread)
                                               │  events            ▲ finished jobs (queue, drained by a
                                               ▼                    │  tk `after` timer every 250 ms)
                                        edmultitool.core  ◀────────▶  worker thread
                                        (pure Python: state,       │
                                         route model, values,      ▼
                                         filters, laden range)   edmultitool.sources (one HTTP client:
                                               │                  timeout, cache, backoff, 1 request/s)
                                               ▼                    ├─ spansh.py  (routes, searches)
                                        edmultitool.server          ├─ ardent.py  (commodity backup, services)
                                        (http.server, 127.0.0.1 only) └─ github.py  (update check)
                                               │ JSON state + actions
                                               ▼
                                        edmultitool/web  (static HTML/JS/CSS, no build step)
```

## Layout (repo)
| path | role |
|---|---|
| `EDMultitool/` | **the plugin folder EDMC loads**; for development, link it into EDMC's plugins folder (a directory junction works: `mklink /J`) |
| `EDMultitool/load.py` | EDMC adapter: hooks → core events; tk panel; settings tab; clipboard. The only file importing EDMC modules |
| `EDMultitool/edmultitool/plugin.py` | the plugin's state and actions, shared by EDMC and the page |
| `EDMultitool/edmultitool/core/` | pure logic — no tkinter, no EDMC imports; unit-tested |
| `EDMultitool/edmultitool/sources/` | Spansh / Ardent / GitHub adapters behind one polite HTTP client |
| `EDMultitool/edmultitool/worker.py` | the one background thread for online lookups; `*_lookup.py` = the jobs it runs |
| `EDMultitool/edmultitool/history.py` | read-only passes over the journal history (fleet, explored, career, earnings, missions, statistics) |
| `EDMultitool/edmultitool/explored.py` | the store of bodies mapped and species analysed (SQLite) |
| `EDMultitool/edmultitool/server/` | localhost HTTP server: serves `web/`, JSON state, user actions |
| `EDMultitool/edmultitool/web/` | the big window (plain HTML/JS/CSS); polls `GET /api/state` once a second |
| `EDMultitool/edmultitool/browser.py` | opens the page: Chromium `--app` window, else the default browser |
| `EDMultitool/edmultitool/catalog/` | bundled static data (engineers, commodities, ships and FSDs, species and genera, powers, gear ratings, mission names) |
| `EDMultitool/data/` | runtime user data (HTTP cache, `settings.json`, `ships.json`, `route.json`, `explored.db` = what you explored) — gitignored |
| `tests/` | pytest; recorded journal lines + API replies in `tests/data/`; no test calls a live service |
| `tools/` | gate, dev runner (core + server without EDMC, journal replay), live smoke checks, release zip, catalog builders |

## Seams and contracts
- **Journal → core:** `core.events` dataclasses built from journal dicts (jumps and arrivals, loadout and
  cargo, scans, missions, money, ranks and reputation, statistics, deaths…) and from EDMC's Status.json
  state. The only place journal field names appear.
- **Core → UI:** one `UiState` JSON shape served at `GET /api/state`; the web page only renders it.
- **Sources → core:** typed results (`ScoutStop`, `ScoutBody`, `MarketHit`, `PowerHit`…); raw API JSON
  never leaves `sources/`. The request shapes we rely on are recorded in `docs/api-notes.md`.
- **Threads:** EDMC hooks run on the tk main thread and must return fast. Network work runs on one worker
  thread; finished jobs go into a queue that the main thread empties with a tk `after` timer, so the worker
  never touches tk. The HTTP server runs on its own daemon threads; it reads a snapshot of the state and
  queues the page's actions for the main thread.

## Cross-cutting rules
- Server binds `127.0.0.1` only (exclusive bind, port 47615, OS-chosen fallback); rejects foreign Host/Origin
  headers and cross-site fetches; GET/HEAD for the page and the state, POST (JSON only, same origin) for the
  page's actions; every error is JSON; no CORS.
- No secrets needed (Spansh, Ardent and GitHub's release check are keyless). What leaves the machine: the
  systems searched from or for, the system you arrive in, your missions' destinations, and a daily version
  check. Never the commander name or account.
- Systems are matched by id64 (`SystemAddress`), never by name; routes use the jump range worked out from the
  loadout, cargo and fuel (full tank by default), never the journal's `MaxJumpRange`.
- Everything degrades gracefully: no journal yet, game closed, Spansh slow/down, multicrew (no position).
