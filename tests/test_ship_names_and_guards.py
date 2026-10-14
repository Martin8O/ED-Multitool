import json
from pathlib import Path
from typing import Any

import pytest

from edmultitool.core.events import GameLoaded, ShipNamed, parse_event
from edmultitool.core.settings import Settings, settings_from_form
from edmultitool.core.state import GameState, apply
from edmultitool.plugin import ScoutPlugin

DATA = Path(__file__).parent / "data"
LOADOUTS: list[dict[str, Any]] = json.loads((DATA / "loadouts.json").read_text(encoding="utf-8"))
UNKNOWN_SHIP = {**LOADOUTS[0], "Ship": "brandnewship_mk9"}


def _fold(*entries: dict[str, Any]) -> GameState:
    state = GameState()
    for raw in entries:
        ev = parse_event(raw)
        if ev is not None:
            state = apply(state, ev)
    return state


def test_unknown_ship_falls_back_to_tidied_symbol() -> None:
    assert _fold(UNKNOWN_SHIP).ship_display == "Brandnewship Mk9"


def test_loadgame_teaches_the_localised_ship_name() -> None:
    login = {
        "event": "LoadGame",
        "Ship": "BrandNewShip_Mk9",
        "Ship_Localised": "Star Wanderer",
        "FuelLevel": 1.5,
    }
    ev = parse_event(login)
    assert isinstance(ev, GameLoaded) and ev.ship == ShipNamed("brandnewship_mk9", "Star Wanderer")
    state = _fold(login, UNKNOWN_SHIP)
    assert state.ship_display == "Star Wanderer"
    assert state.fuel_main == 1.5  # below this small tank, so not clamped


@pytest.mark.parametrize("event", ["ShipyardSwap", "ShipyardNew"])
def test_shipyard_events_teach_names_and_beat_the_static_table(event: str) -> None:
    sidewinder = next(r for r in LOADOUTS if r["Ship"] == "sidewinder")
    renamed = {"event": event, "ShipType": "SideWinder", "ShipType_Localised": "Sidewinder (localised)"}
    assert _fold(renamed, sidewinder).ship_display == "Sidewinder (localised)"


def test_srv_and_fighter_loadouts_are_not_our_ship() -> None:
    for ship in ("testbuggy", "combat_multicrew_srv_01_buggy", "independent_fighter"):
        assert parse_event({**LOADOUTS[0], "Ship": ship}) is None


def test_status_fuel_ignored_in_srv_or_fighter() -> None:
    fuel = {"FuelMain": 1.0, "FuelReservoir": 0.2}
    assert parse_event({"event": "Status", "Flags": 1 << 26, "Fuel": fuel}) is None
    assert parse_event({"event": "Status", "Flags": 1 << 25, "Fuel": fuel}) is None
    assert parse_event({"event": "Status", "Flags": 0, "Fuel": fuel}) is not None


@pytest.mark.parametrize(
    ("label", "margin", "expected"),
    [
        ("current fuel & cargo", "12,5", Settings("now", 12.5)),
        ("best case (empty)", " 3 ", Settings("best", 3.0)),
        ("full tank", "abc", Settings("full", 7.0)),  # garbage keeps current margin
        ("full tank", "nan", Settings("full", 7.0)),
        ("full tank", "1e999", Settings("full", 50.0)),
        ("full tank", "-4", Settings("full", 0.0)),
        ("no such label", "", Settings("now", 7.0)),  # unknown label keeps current basis
    ],
)
def test_settings_from_form(label: str, margin: str, expected: Settings) -> None:
    assert settings_from_form(label, margin, Settings("now", 7.0)) == expected


def test_dashboard_dumps_are_throttled_journal_dumps_are_not(tmp_path: Path) -> None:
    plugin = ScoutPlugin(tmp_path)
    plugin.on_journal(LOADOUTS[-1], None)
    dump = tmp_path / "data" / "debug" / "panel.json"
    first = dump.read_text(encoding="utf-8")
    assert plugin.on_dashboard({"Flags": 0, "Fuel": {"FuelMain": 1.0, "FuelReservoir": 0.3}})
    assert dump.read_text(encoding="utf-8") == first  # within the interval: panel changed, dump did not
    assert plugin.on_journal({"event": "Cargo", "Vessel": "Ship", "Count": 2}, None)
    assert json.loads(dump.read_text(encoding="utf-8"))["state"]["cargo"] == 2
