"""Run ED Multitool's core + page server without EDMC, fed by a replayed journal.

Usage (from repo root, after `. tools/env.ps1`):
    .venv/Scripts/python tools/devrun.py                       # sample journal, all at once, serve
    .venv/Scripts/python tools/devrun.py --journal DIR --since 2026-10-10
    .venv/Scripts/python tools/devrun.py --step 4 --jumps 5    # last 5 jumps replayed every 4 s
    .venv/Scripts/python tools/devrun.py --journal FILE --lose-loadouts --history DIR

--lose-loadouts replays what EDMC does when its own ship-loadout export fails: `Loadout` entries never
reach the plugin, but every entry carries EDMC's cumulative state, which already holds the ship.
--history DIR lets the plugin learn the fleet and what you explored from that journal folder (read-only),
as in EDMC.
--fly 3 --fly-points 1,3,off,2,6,end: once the page shows a route, arrive (synthetic FSDJump) in those
points of it every 3 s — index into start / stops or waypoints / destination, "off" = a system off the
route, "end" = the destination; one `FLY` line each.

Lines with "event": "Status" in the replayed journal go where EDMC sends Status.json (balance, legal state,
fuel), so a hand-made journal can show the page's warnings.

--fail connect|timeout|429|503|maintenance: every online question fails that way (no network used), to
see what the page says when Spansh / Ardent are down, slow, overloaded or answer a maintenance page.
--release v9.0.0: GitHub's latest release is that tag (answered here, "none" = 404): the update strip.

Prints `URL <url>` first, then one `JUMP` line per stepped jump (wall-clock time + seq) so a page check
can measure how fast the page follows. Runtime data goes to .devrun/ (never EDMultitool/data).
Read-only on the journal. Stops on Ctrl+C or after --exit-after seconds.
The worker runs with the real network: a route search or name suggestion on the page calls Spansh.
"""

import argparse
import glob
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "EDMultitool"))

from edmultitool.core.follow import points
from edmultitool.core.view import UiRoute
from edmultitool.plugin import ScoutPlugin
from edmultitool.server import DEFAULT_PORT
from edmultitool.sources.http import NetworkError, Request, Response, Transport

FAIL_MODES = ("connect", "timeout", "429", "503", "maintenance")


def failing_transport(mode: str) -> Transport:
    """Spansh and Ardent as they look when down / slow / overloaded / under maintenance."""

    def send(request: Request, timeout: float) -> Response:
        where = f"{request.method} {request.url}"
        if mode == "connect":
            raise NetworkError(f"{where}: HTTPSConnectionPool: Max retries exceeded (connection refused)")
        if mode == "timeout":
            time.sleep(min(timeout, 2.0))
            raise NetworkError(
                f"{where}: HTTPSConnectionPool: Read timed out. (read timeout=20)", timed_out=True
            )
        if mode == "maintenance":
            return Response(200, b"<html><body><h1>Down for maintenance</h1></body></html>")
        return Response(int(mode), b"", {"retry-after": "1"} if mode == "429" else {})

    return send


ROOT = Path(__file__).resolve().parents[1]


def release_transport(tag: str, otherwise: Transport | None) -> Transport:
    """GitHub's "latest release" answered with a recorded reply carrying `tag` ("none" = 404, as before
    a first release); every other question goes on to `otherwise` (the real network when None)."""
    from edmultitool.sources import github
    from edmultitool.sources.http import requests_transport

    doc = json.loads((ROOT / "tests" / "data" / "github" / "latest_release.json").read_text(encoding="utf-8"))
    body = {**doc["body"], "tag_name": tag, "html_url": doc["body"]["html_url"].rsplit("/", 1)[0] + "/" + tag}
    rest = otherwise or requests_transport()

    def send(request: Request, timeout: float) -> Response:
        if request.url != github.URL:
            return rest(request, timeout)
        if tag == "none":
            return Response(404, b'{"message": "Not Found"}')
        return Response(200, json.dumps(body).encode("utf-8"))

    return send


JUMP_EVENTS = ("FSDJump", "CarrierJump")


def read_journal(path: Path, since: str | None) -> list[dict[str, Any]]:
    """A .jsonl/.log file, or a journal folder (Journal.*.log, oldest first)."""
    files = [str(path)]
    if path.is_dir():
        files = sorted(glob.glob(os.path.join(path, "Journal.*.log")))
        if since:
            files = [f for f in files if os.path.basename(f)[8:18] >= since]
    out: list[dict[str, Any]] = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a half-written last line while the game runs
                if isinstance(entry, dict):
                    out.append(entry)
    return out


def edmc_state_after(st: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """The ship keys of EDMC's cumulative `state` after `entry` (EDMC 6.1.2 monitor.py)."""
    name = entry.get("event")
    if name == "Loadout":
        ship = str(entry.get("Ship", "")).lower()
        if "fighter" in ship or "buggy" in ship:
            return st
        fuel = entry.get("FuelCapacity") or {}
        return {
            **st,
            "ShipID": entry.get("ShipID"),
            "ShipIdent": entry.get("ShipIdent"),
            "ShipName": entry.get("ShipName")
            if str(entry.get("ShipName", "")).strip()
            else st.get("ShipName"),
            "ShipType": ship,
            "UnladenMass": entry.get("UnladenMass"),
            "CargoCapacity": entry.get("CargoCapacity"),
            "MaxJumpRange": entry.get("MaxJumpRange"),
            "FuelCapacity": {"Main": fuel.get("Main"), "Reserve": fuel.get("Reserve")},
            "Modules": {m["Slot"]: m for m in entry.get("Modules", []) if "Slot" in m},
        }
    if name in ("ShipyardSwap", "ShipyardBuy"):
        ship_id = entry.get("ShipID") if name == "ShipyardSwap" else None
        cleared = {"ShipName": None, "ShipIdent": None, "Modules": None}
        return {**st, "ShipID": ship_id, "ShipType": str(entry.get("ShipType", "")).lower(), **cleared}
    return st


def cargo_state(journal: Path, entry: dict[str, Any]) -> dict[str, Any] | None:
    """EDMC reads Cargo.json for every ship `Cargo` entry that does not list the hold; a replay can only do
    that for the entry the file was written with (it holds the hold as it is now)."""
    folder = journal if journal.is_dir() else journal.parent
    try:
        doc = json.loads((folder / "Cargo.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    same = isinstance(doc, dict) and doc.get("timestamp") == entry.get("timestamp")
    return {"CargoJSON": doc} if same else None


def fly(plugin: ScoutPlugin, route: UiRoute, point: str) -> None:
    """Arrive in one point of the shown route (or off it) with a synthetic FSDJump."""
    ids = points(route)
    names = (
        [w.name for w in route.waypoints]
        if route.mode != "tour"
        else [route.from_name, *(s.name for s in route.stops), route.to_name]
    )
    if point == "off":
        name, address = "Devrun Off Route", 1
    else:
        i = len(ids) - 1 if point == "end" else min(int(point), len(ids) - 1)
        name, address = names[i], int(ids[i] or 2)
    when = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {"timestamp": when, "event": "FSDJump", "StarSystem": name, "SystemAddress": address,
             "StarPos": [0.0, 0.0, 0.0], "JumpDist": 10.0, "FuelUsed": 0.5, "FuelLevel": 20.0}  # fmt: skip
    plugin.on_journal(entry, None)
    f = plugin.ui.follow
    where = f"next={f.next_index}/{f.count} {f.next_name!r}"
    print(f"FLY {point} -> {name!r}: {where} panel={f.text!r}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--journal", type=Path, default=ROOT / "tests" / "data" / "journal_sample.jsonl")
    ap.add_argument("--since", help="with a journal folder: only files from this date (YYYY-MM-DD)")
    ap.add_argument(
        "--port", type=int, default=DEFAULT_PORT + 1, help="preferred port (EDMC uses the default)"
    )
    ap.add_argument(
        "--step", type=float, default=0.0, help="seconds between the last --jumps jumps (0 = none)"
    )
    ap.add_argument("--jumps", type=int, default=5, help="how many final jumps to replay stepwise")
    ap.add_argument(
        "--exit-after", type=float, default=0.0, help="stop after N seconds (0 = run until Ctrl+C)"
    )
    ap.add_argument("--history", type=Path, help="journal folder the fleet is learned from (read-only)")
    ap.add_argument(
        "--lose-loadouts", action="store_true", help="drop Loadout entries like a failed EDMC export"
    )
    ap.add_argument("--fly", type=float, default=0.0, help="seconds between --fly-points arrivals (0 = off)")
    ap.add_argument("--fly-points", default="1,3,off,2,6,end", help="route points to arrive in, in order")
    ap.add_argument("--fail", choices=FAIL_MODES, help="every online question fails this way (no network)")
    ap.add_argument(
        "--release", metavar="TAG", help="the latest GitHub release is TAG (e.g. v9.0.0; none = 404)"
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    plugin_dir = ROOT / ".devrun"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    plugin = ScoutPlugin(plugin_dir, journal_dir=args.history)
    edmc: dict[str, Any] = {}

    def feed(entry: dict[str, Any]) -> None:
        nonlocal edmc
        if entry.get("event") == "Status":  # a Status.json snapshot in the replay: EDMC's dashboard hook
            plugin.on_dashboard(entry)
            return
        if not args.lose_loadouts:
            cargo = cargo_state(args.journal, entry) if entry.get("event") == "Cargo" else None
            plugin.on_journal(entry, cargo)
            return
        edmc = edmc_state_after(edmc, entry)
        if entry.get("event") != "Loadout":
            plugin.on_journal(entry, edmc)

    entries = read_journal(args.journal, args.since)
    jump_idx = [i for i, e in enumerate(entries) if e.get("event") in JUMP_EVENTS]
    stepped = jump_idx[-args.jumps :] if args.step > 0 and args.jumps > 0 else []
    if args.step > 0 and len(stepped) < args.jumps:
        print(f"NOTE only {len(stepped)} jumps in the journal; stepping those", flush=True)
    cursor = stepped[0] if stepped else len(entries)
    for entry in entries[:cursor]:
        feed(entry)

    # real network: the page's route searches and suggestions go to Spansh — unless --fail
    transport = failing_transport(args.fail) if args.fail else None
    plugin.start_worker(release_transport(args.release, transport) if args.release else transport)
    plugin._want_update()  # the first check, as the 30-second tick would make it
    url = plugin.start_server(args.port)
    if url is None:
        print("ERROR could not start the server", flush=True)
        return 1
    print(f"URL {url}", flush=True)
    print(f"STATE seq={plugin.seq} system={plugin.ui.system!r}", flush=True)
    print(f"SHIP {plugin.ui.panel.ship!r} range {plugin.ui.panel.range!r}", flush=True)
    started = time.monotonic()

    try:
        for jump in stepped:
            until = time.monotonic() + args.step
            while time.monotonic() < until:  # collect finished lookups as EDMC's timer does
                time.sleep(0.2)
                plugin.pump()
            for entry in entries[cursor : jump + 1]:  # everything up to and including this jump
                feed(entry)
            cursor = jump + 1
            print(f"JUMP t={time.time():.3f} seq={plugin.seq} system={plugin.ui.system!r}", flush=True)
        for entry in entries[cursor:]:  # whatever follows the last stepped jump, at once
            feed(entry)
        flights = [p.strip() for p in args.fly_points.split(",") if p.strip()] if args.fly > 0 else []
        next_flight = 0.0
        while not args.exit_after or time.monotonic() - started < args.exit_after:
            time.sleep(0.2)
            plugin.pump()  # the main-thread timer EDMC's tk loop runs in load.py
            route = plugin.scout.route
            if flights and route is not None and time.monotonic() >= next_flight:
                next_flight = time.monotonic() + args.fly
                fly(plugin, route, flights.pop(0))

    except KeyboardInterrupt:
        pass
    finally:
        plugin.stop_server()
        plugin.stop_worker()
        plugin.close()
        print("STOPPED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
