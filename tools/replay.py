"""Replay Elite Dangerous journal files through edmultitool.core and report the state timeline.

Usage (from repo root, after `. tools/env.ps1`):
    .venv/Scripts/python tools/replay.py [JOURNAL_DIR] [--since YYYY-MM-DD] [--out FILE]
    .venv/Scripts/python tools/replay.py --export-loadouts tests/data/loadouts.json

Read-only on the journal folder. Output goes to --out (default: stdout); redirect big output to a file.
"""

import argparse
import glob
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "EDMultitool"))

from edmultitool.core.events import Arrived, LoadoutChanged, parse_event
from edmultitool.core.state import GameState, apply

DEFAULT_DIR = os.path.expandvars(r"%USERPROFILE%\Saved Games\Frontier Developments\Elite Dangerous")


def journal_files(folder: str, since: str | None) -> list[str]:
    files = sorted(glob.glob(os.path.join(folder, "Journal.*.log")))
    if since:
        files = [f for f in files if os.path.basename(f)[8:18] >= since]
    return files


def entries(files: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a half-written last line while the game runs
    return out


def replay(raw: list[dict[str, Any]], out: TextIO) -> dict[str, Any]:
    state = GameState()
    loadout_diffs: list[float] = []
    jumps = violations = 0
    worst_margin = float("inf")
    for e in raw:
        ev = parse_event(e)
        if ev is None:
            continue
        if (
            isinstance(ev, Arrived)
            and ev.kind == "FSDJump"
            and not ev.boosted
            and ev.jump_dist is not None
            and ev.fuel_level is not None
            and ev.fuel_used is not None
            and state.ship is not None
            and state.ship.fsd is not None
        ):
            # Range at the moment BEFORE the jump: fuel then = fuel after + fuel used.
            before = replace(state, fuel_main=ev.fuel_level + ev.fuel_used)
            rng = before.current_range() or 0.0
            margin = rng - ev.jump_dist
            jumps += 1
            worst_margin = min(worst_margin, margin)
            if margin < -0.01:
                violations += 1
                out.write(f"!! jump {ev.jump_dist:.2f} ly > computed range {rng:.2f} ly ({ev.system})\n")
        state = apply(state, ev)
        if isinstance(ev, LoadoutChanged):
            best = state.best_case_range()
            diff = abs(best - ev.game_max_jump_range) if best is not None else float("nan")
            loadout_diffs.append(diff)
            fsd = ev.fsd.item if ev.fsd else "?"
            out.write(
                f"{e.get('timestamp', '')} LOADOUT {ev.ship_type} {fsd} unladen={ev.unladen_mass:.1f} "
                f"game={ev.game_max_jump_range:.3f} ours={best or 0:.3f} diff={diff:.4f}\n"
            )
        elif isinstance(ev, Arrived):
            cur = state.current_range()
            full = state.full_tank_range()
            out.write(
                f"{e.get('timestamp', '')} {ev.kind:<11} {ev.system} id64={ev.address} "
                f"fuel={state.fuel_main} cargo={state.cargo} "
                f"range_now={cur if cur is None else round(cur, 2)} "
                f"range_full={full if full is None else round(full, 2)}\n"
            )
    summary = {
        "loadouts": len(loadout_diffs),
        "loadout_max_diff": max(loadout_diffs, default=0.0),
        "jumps_checked": jumps,
        "jump_violations": violations,
        "worst_jump_margin": None if jumps == 0 else round(worst_margin, 3),
        "final_system": state.system,
        "final_address": state.address,
        "final_star_pos": state.star_pos,
    }
    out.write("SUMMARY " + json.dumps(summary) + "\n")
    return summary


def export_loadouts(raw: list[dict[str, Any]], path: str) -> None:
    """Distinct Loadouts, stripped of anything personal (names, idents, IDs)."""
    seen: dict[str, dict[str, Any]] = {}
    for e in raw:
        if e.get("event") != "Loadout":
            continue
        fsd = next((m for m in e.get("Modules", []) if m.get("Slot") == "FrameShiftDrive"), None)
        boosters = [
            m["Item"] for m in e.get("Modules", []) if "guardianfsdbooster" in m.get("Item", "").lower()
        ]
        row = {
            "event": "Loadout",
            "Ship": e["Ship"],
            "UnladenMass": e["UnladenMass"],
            "FuelCapacity": e.get("FuelCapacity"),
            "CargoCapacity": e.get("CargoCapacity", 0),
            "MaxJumpRange": e["MaxJumpRange"],
            "Modules": [
                *([{"Slot": "FrameShiftDrive", "Item": fsd["Item"], **_eng(fsd)}] if fsd else []),
                *({"Slot": "Booster", "Item": b} for b in boosters),
            ],
        }
        key = json.dumps({k: v for k, v in row.items() if k != "MaxJumpRange"}, sort_keys=True)
        seen[key] = row
    Path(path).write_text(json.dumps(list(seen.values()), indent=1) + "\n", encoding="utf-8")
    print(f"exported {len(seen)} distinct loadouts to {path}")


def _eng(module: dict[str, Any]) -> dict[str, Any]:
    eng = module.get("Engineering")
    if not eng:
        return {}
    return {"Engineering": {"Modifiers": eng.get("Modifiers", [])}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default=DEFAULT_DIR)
    ap.add_argument("--since")
    ap.add_argument("--out")
    ap.add_argument("--export-loadouts")
    args = ap.parse_args()
    raw = entries(journal_files(args.folder, args.since))
    if args.export_loadouts:
        export_loadouts(raw, args.export_loadouts)
        return 0
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            summary = replay(raw, fh)
    else:
        summary = replay(raw, sys.stdout)
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
