"""Regenerate `EDMultitool/edmultitool/catalog/engineers.py` — MANUAL ONLY, ~40 live calls (≤ 1/s).

Which engineers exist, their home system (id64) and their base (market id) come from EDCD's FDevIDs
`engineers.csv` (ids 3xxxxx = ship engineers, 4xxxxx = on-foot engineers); the system's name and
coordinates and the base's name and type come from Spansh's system record. What each ship engineer
improves, and up to which grade, is the game's own engineer screen re-typed by hand in SPECIALTIES below.
`--check` also compares SPECIALTIES with the EDCD coriolis-data blueprint tables and prints every
difference (those tables miss some engineers and copy the Sensors grades onto the cargo scanner, so a
difference is a prompt to look, not proof).

Usage (from repo root, after `. tools/env.ps1`):
    .venv/Scripts/python tools/make_engineers.py [--check]
    .venv/Scripts/python -m ruff format EDMultitool/edmultitool/catalog/engineers.py
"""

import csv
import io
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "EDMultitool"))

from edmultitool import USER_AGENT
from edmultitool.sources.http import PoliteClient, requests_transport

OUT = Path(__file__).resolve().parents[1] / "EDMultitool" / "edmultitool" / "catalog" / "engineers.py"
FDEV = "https://raw.githubusercontent.com/EDCD/FDevIDs/master/engineers.csv"
CORIOLIS = "https://raw.githubusercontent.com/EDCD/coriolis-data/master/modifications/modules.json"

# The module names ED Multitool shows (and the page's "improves" filter offers).
MODULES = (
    "Frame Shift Drive",
    "Thrusters",
    "Power Plant",
    "Power Distributor",
    "Shield Generator",
    "Shield Booster",
    "Shield Cell Bank",
    "Armour",
    "Hull Reinforcement",
    "Sensors",
    "Detailed Surface Scanner",
    "FSD Interdictor",
    "Life Support",
    "Fuel Scoop",
    "Refinery",
    "Auto Field-Maintenance Unit",
    "Beam Laser",
    "Burst Laser",
    "Pulse Laser",
    "Multi-cannon",
    "Cannon",
    "Fragment Cannon",
    "Rail Gun",
    "Plasma Accelerator",
    "Missile Rack",
    "Torpedo Pylon",
    "Mine Launcher",
    "Chaff Launcher",
    "Electronic Countermeasure",
    "Heat Sink Launcher",
    "Point Defence",
    "Frame Shift Wake Scanner",
    "Kill Warrant Scanner",
    "Manifest Scanner",
    "Collector Limpet",
    "Fuel Transfer Limpet",
    "Hatch Breaker Limpet",
    "Prospector Limpet",
)

# Ship engineer → {module: highest grade}. Game facts (each engineer's workshop screen).
SPECIALTIES: dict[str, dict[str, int]] = {
    "Felicity Farseer": {
        "Frame Shift Drive": 5,
        "Detailed Surface Scanner": 3,
        "Thrusters": 3,
        "Sensors": 3,
        "FSD Interdictor": 1,
        "Power Plant": 1,
        "Shield Booster": 1,
    },
    "Elvira Martuuk": {"Frame Shift Drive": 5, "Shield Generator": 3, "Thrusters": 2, "Shield Cell Bank": 1},
    "The Dweller": {"Power Distributor": 5, "Pulse Laser": 4, "Burst Laser": 3, "Beam Laser": 3},
    "Liz Ryder": {
        "Missile Rack": 5,
        "Torpedo Pylon": 5,
        "Mine Launcher": 3,
        "Hull Reinforcement": 1,
        "Armour": 1,
    },
    "Tod 'The Blaster' McQuinn": {"Multi-cannon": 5, "Rail Gun": 5, "Fragment Cannon": 3, "Cannon": 2},
    "Zacariah Nemo": {"Fragment Cannon": 5, "Multi-cannon": 3, "Plasma Accelerator": 2},
    "Lei Cheung": {"Shield Generator": 5, "Detailed Surface Scanner": 5, "Sensors": 5, "Shield Booster": 3},
    "Hera Tani": {"Power Plant": 5, "Detailed Surface Scanner": 5, "Power Distributor": 3, "Sensors": 3},
    "Juri Ishmaak": {
        "Mine Launcher": 5,
        "Detailed Surface Scanner": 5,
        "Sensors": 5,
        "Frame Shift Wake Scanner": 3,
        "Kill Warrant Scanner": 3,
        "Manifest Scanner": 3,
        "Torpedo Pylon": 3,
        "Missile Rack": 3,
    },
    "Selene Jean": {"Armour": 5, "Hull Reinforcement": 5},
    "Marco Qwent": {"Power Plant": 4, "Power Distributor": 3},
    "Ram Tah": {
        "Chaff Launcher": 5,
        "Electronic Countermeasure": 5,
        "Heat Sink Launcher": 5,
        "Point Defence": 5,
        "Collector Limpet": 4,
        "Fuel Transfer Limpet": 4,
        "Prospector Limpet": 4,
        "Hatch Breaker Limpet": 4,
    },
    "Broo Tarquin": {"Pulse Laser": 5, "Burst Laser": 5, "Beam Laser": 5},
    "Colonel Bris Dekker": {"FSD Interdictor": 4, "Frame Shift Drive": 3},
    "Didi Vatermann": {"Shield Booster": 5, "Shield Generator": 3},
    "Professor Palin": {"Thrusters": 5, "Frame Shift Drive": 3},
    "Lori Jameson": {
        "Detailed Surface Scanner": 5,
        "Sensors": 5,
        "Fuel Scoop": 4,
        "Life Support": 4,
        "Refinery": 4,
        "Auto Field-Maintenance Unit": 4,
        "Shield Cell Bank": 3,
        "Frame Shift Wake Scanner": 3,
        "Kill Warrant Scanner": 3,
        "Manifest Scanner": 3,
    },
    "Tiana Fortune": {
        "Collector Limpet": 5,
        "Hatch Breaker Limpet": 5,
        "Prospector Limpet": 5,
        "Fuel Transfer Limpet": 5,
        "Sensors": 5,
        "Detailed Surface Scanner": 3,
        "Frame Shift Wake Scanner": 5,
        "Kill Warrant Scanner": 5,
        "Manifest Scanner": 5,
        "FSD Interdictor": 3,
    },
    "Bill Turner": {
        "Plasma Accelerator": 5,
        "Detailed Surface Scanner": 5,
        "Sensors": 5,
        "Fuel Scoop": 3,
        "Life Support": 3,
        "Refinery": 3,
        "Auto Field-Maintenance Unit": 3,
        "Frame Shift Wake Scanner": 3,
        "Kill Warrant Scanner": 3,
        "Manifest Scanner": 3,
    },
    "The Sarge": {
        "Cannon": 5,
        "Collector Limpet": 5,
        "Fuel Transfer Limpet": 5,
        "Hatch Breaker Limpet": 5,
        "Prospector Limpet": 5,
        "Rail Gun": 3,
    },
    "Mel Brandon": {
        "Beam Laser": 5,
        "Burst Laser": 5,
        "Pulse Laser": 5,
        "Shield Generator": 5,
        "Shield Booster": 5,
        "Shield Cell Bank": 4,
        "Thrusters": 5,
        "Frame Shift Drive": 5,
        "FSD Interdictor": 5,
    },
    "Marsha Hicks": {
        "Cannon": 5,
        "Fragment Cannon": 5,
        "Multi-cannon": 5,
        "Fuel Scoop": 5,
        "Refinery": 5,
        "Collector Limpet": 5,
        "Fuel Transfer Limpet": 5,
        "Hatch Breaker Limpet": 5,
        "Prospector Limpet": 5,
    },
    "Etienne Dorn": {
        "Plasma Accelerator": 5,
        "Rail Gun": 5,
        "Power Plant": 5,
        "Power Distributor": 5,
        "Life Support": 5,
        "Sensors": 5,
        "Detailed Surface Scanner": 5,
        "Frame Shift Wake Scanner": 5,
        "Kill Warrant Scanner": 5,
        "Manifest Scanner": 5,
    },
    "Petra Olmanova": {
        "Armour": 5,
        "Hull Reinforcement": 5,
        "Auto Field-Maintenance Unit": 5,
        "Missile Rack": 5,
        "Torpedo Pylon": 5,
        "Mine Launcher": 5,
        "Chaff Launcher": 5,
        "Electronic Countermeasure": 5,
        "Heat Sink Launcher": 5,
        "Point Defence": 5,
    },
    "Chloe Sedesi": {"Thrusters": 5, "Frame Shift Drive": 3},
}

# coriolis-data module code → our module name (for --check only)
CODES = {
    "am": "Auto Field-Maintenance Unit",
    "bh": "Armour",
    "bl": "Burst Laser",
    "bsg": "Shield Generator",
    "psg": "Shield Generator",
    "sg": "Shield Generator",
    "c": "Cannon",
    "cc": "Collector Limpet",
    "ch": "Chaff Launcher",
    "cs": "Manifest Scanner",
    "ec": "Electronic Countermeasure",
    "fc": "Fragment Cannon",
    "fi": "FSD Interdictor",
    "fs": "Fuel Scoop",
    "fsd": "Frame Shift Drive",
    "fx": "Fuel Transfer Limpet",
    "hb": "Hatch Breaker Limpet",
    "hr": "Hull Reinforcement",
    "hs": "Heat Sink Launcher",
    "kw": "Kill Warrant Scanner",
    "ls": "Life Support",
    "mc": "Multi-cannon",
    "mr": "Missile Rack",
    "nl": "Mine Launcher",
    "pa": "Plasma Accelerator",
    "pc": "Prospector Limpet",
    "pd": "Power Distributor",
    "pl": "Pulse Laser",
    "po": "Point Defence",
    "pp": "Power Plant",
    "rf": "Refinery",
    "rg": "Rail Gun",
    "s": "Sensors",
    "sb": "Shield Booster",
    "scb": "Shield Cell Bank",
    "ss": "Detailed Surface Scanner",
    "t": "Thrusters",
    "tp": "Torpedo Pylon",
    "ul": "Beam Laser",
    "ws": "Frame Shift Wake Scanner",
}


def check(client: PoliteClient, cancel: threading.Event) -> None:
    modules = client.get(CORIOLIS, cancel).json()
    theirs: dict[str, dict[str, int]] = {}
    for code, name in CODES.items():
        for bp in modules.get(code, {}).get("blueprints", {}).values():
            for grade, g in bp["grades"].items():
                for eng in g.get("engineers") or []:
                    have = theirs.setdefault(eng, {})
                    have[name] = max(have.get(name, 0), int(grade))
    for eng in sorted(set(theirs) | set(SPECIALTIES)):
        mine, other = SPECIALTIES.get(eng, {}), theirs.get(eng, {})
        diff = {m: (mine.get(m), other.get(m)) for m in set(mine) | set(other) if mine.get(m) != other.get(m)}
        if diff:
            print(f"{eng}: (ours, coriolis) {dict(sorted(diff.items()))}")


def main() -> int:
    client = PoliteClient(requests_transport(), user_agent=USER_AGENT, cache_dir=None)
    cancel = threading.Event()
    bad = {m for s in SPECIALTIES.values() for m in s if m not in MODULES}
    if bad:
        print(f"unknown modules: {sorted(bad)}")
        return 1
    if "--check" in sys.argv:
        check(client, cancel)
    rows = list(csv.DictReader(io.StringIO(client.get(FDEV, cancel).body.decode("utf-8"))))
    out: list[str] = []
    for row in rows:
        eid, name = int(row["id"]), row["name"]
        address, market = int(row["system_address"]), int(row["market_id"])
        ship = eid < 400000
        if ship and name not in SPECIALTIES:
            print(f"no specialties typed for {name}")
            return 1
        record = client.get(f"https://spansh.co.uk/api/system/{address}", cancel).json()["record"]
        base = next((s for s in record.get("stations", []) if s.get("market_id") == market), None)
        base_name = base["name"] if base else ""
        base_type = base.get("type", "") if base else ""
        if not base:
            print(f"{name}: base {market} not in Spansh's {record['name']}")
        mods = SPECIALTIES.get(name, {})
        mods_text = ", ".join(
            f"{json.dumps(m)}: {g}" for m, g in sorted(mods.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        out.append(
            f"    Engineer({json.dumps(name)}, {'True' if ship else 'False'}, {json.dumps(record['name'])}, "
            f"{address}, ({record['x']}, {record['y']}, {record['z']}), {json.dumps(base_name)}, "
            f"{json.dumps(base_type)}, {{{mods_text}}}),"
        )
        print(f"{'ship' if ship else 'foot'} {name}: {record['name']} / {base_name} ({base_type})")
    lines = [
        '"""Every engineer: ship (25) and on-foot (13), their home system (id64 + coordinates), their base,',
        "and for the ship engineers what they improve and up to which grade.",
        "GENERATED by `tools/make_engineers.py` (EDCD FDevIDs ids, Spansh coordinates, specialties",
        'typed from the game) — do not edit by hand."""',
        "",
        "from dataclasses import dataclass, field",
        "",
        "",
        "@dataclass(frozen=True, slots=True)",
        "class Engineer:",
        "    name: str",
        "    ship: bool  # False = on-foot (suits and personal weapons)",
        "    system: str",
        "    system_id64: int",
        "    star_pos: tuple[float, float, float]",
        "    base: str",
        "    base_type: str",
        "    modules: dict[str, int] = field(default_factory=dict)  # module → highest grade (ship)",
        "",
        "",
        "MODULES: tuple[str, ...] = (",
        *(f"    {json.dumps(m)}," for m in MODULES),
        ")",
        "",
        "ENGINEERS: tuple[Engineer, ...] = (",
        *out,
        ")",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(out)} engineers written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
