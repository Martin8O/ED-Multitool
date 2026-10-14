import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from edmultitool.core.events import Arrived, CargoChanged, CrewChanged, LoadoutChanged, parse_event, parse_fsd
from edmultitool.core.jumprange import best_case_range, jump_range
from edmultitool.core.state import GameState, apply

DATA = Path(__file__).parent / "data"
LOADOUTS: list[dict[str, Any]] = json.loads((DATA / "loadouts.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("raw", LOADOUTS, ids=lambda r: f"{r['Modules'][0]['Item']}@{r['UnladenMass']}")
def test_best_case_matches_game_max_jump_range(raw: dict[str, Any]) -> None:
    ev = parse_event(raw)
    assert isinstance(ev, LoadoutChanged) and ev.fsd is not None
    ours = best_case_range(ev.fsd, unladen_mass=ev.unladen_mass, guardian_boost_ly=ev.guardian_boost_ly)
    assert ours == pytest.approx(ev.game_max_jump_range, abs=0.01)


def test_parse_fsd_item_names() -> None:
    sco = parse_fsd("int_hyperdrive_overcharge_size5_class5")
    std = parse_fsd("Int_Hyperdrive_Size2_Class1")
    assert sco is not None and (sco.overcharge, sco.size, sco.rating) == (True, 5, "A")
    assert std is not None and (std.overcharge, std.size, std.rating) == (False, 2, "E")
    assert parse_fsd("int_guardianfsdbooster_size5") is None
    assert parse_fsd("int_hyperdrive_size9_class5") is None


def test_engineering_modifiers_override_constants() -> None:
    eng = {
        "Modifiers": [{"Label": "FSDOptimalMass", "Value": 1500.0}, {"Label": "MaxFuelPerJump", "Value": 5.5}]
    }
    fsd = parse_fsd("int_hyperdrive_size5_class5", eng)
    assert fsd is not None and fsd.engineered
    assert (fsd.spec.opt_mass, fsd.spec.max_fuel) == (1500.0, 5.5)


def test_cargo_and_fuel_reduce_range() -> None:
    fsd = parse_fsd("int_hyperdrive_overcharge_size5_class5")
    assert fsd is not None
    best = best_case_range(fsd, unladen_mass=572.8)
    full = jump_range(fsd, unladen_mass=572.8, fuel_main=32.0, fuel_reservoir=0.75)
    laden = jump_range(fsd, unladen_mass=572.8, cargo=8, fuel_main=32.0, fuel_reservoir=0.75)
    assert best > full > laden > 0
    assert jump_range(fsd, unladen_mass=572.8, fuel_main=0.0) == 0.0


def test_guardian_booster_adds_flat_bonus() -> None:
    raw = dict(LOADOUTS[0])
    raw["Modules"] = [*raw["Modules"], {"Slot": "Slot01", "Item": "int_guardianfsdbooster_size5"}]
    ev = parse_event(raw)
    assert isinstance(ev, LoadoutChanged) and ev.guardian_boost_ly == 10.5


def _sample() -> list[dict[str, Any]]:
    lines = (DATA / "journal_sample.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_replay_sample_no_real_jump_exceeds_computed_range() -> None:
    state = GameState()
    jumps = 0
    for raw in _sample():
        ev = parse_event(raw)
        if ev is None:
            continue
        if isinstance(ev, Arrived) and ev.kind == "FSDJump" and not ev.boosted:
            assert ev.jump_dist is not None and ev.fuel_level is not None and ev.fuel_used is not None
            before = replace(state, fuel_main=ev.fuel_level + ev.fuel_used)
            rng = before.current_range()
            assert rng is not None and ev.jump_dist <= rng + 0.01, (ev.system, ev.jump_dist, rng)
            jumps += 1
        state = apply(state, ev)
    assert jumps == 9
    last = [r for r in _sample() if r["event"] == "FSDJump"][-1]
    assert state.system == last["StarSystem"]
    assert state.address == last["SystemAddress"]
    assert state.star_pos == tuple(last["StarPos"])


def test_srv_cargo_is_ignored_and_ship_cargo_counts() -> None:
    assert parse_event({"event": "Cargo", "Vessel": "SRV", "Count": 2}) is None
    state = apply(GameState(), CargoChanged(8))
    assert state.cargo == 8


def test_multicrew_hides_position_and_range() -> None:
    state = GameState()
    for raw in _sample():
        ev = parse_event(raw)
        if ev is not None:
            state = apply(state, ev)
    crew = apply(state, CrewChanged(in_other_ship=True))
    assert not crew.position_known and crew.system is None
    assert crew.current_range() is None
    back = apply(crew, CrewChanged(in_other_ship=False))
    assert not back.position_known  # stays unknown until the next Location/FSDJump
    assert back.current_range() is not None


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"event": "FSDJump"},
        {"event": "FSDJump", "StarSystem": "X", "SystemAddress": "notint", "StarPos": [0, 0, 0]},
        {"event": "Loadout", "Ship": "x"},
        {"event": "Status", "Fuel": "broken"},
        {"event": "Location", "StarSystem": "X", "SystemAddress": 1, "StarPos": [0, 0]},
    ],
)
def test_malformed_entries_are_ignored_not_raised(raw: dict[str, Any]) -> None:
    assert parse_event(raw) is None


def test_status_fuel_and_refuel_delta() -> None:
    status = parse_event({"event": "Status", "Fuel": {"FuelMain": 10.0, "FuelReservoir": 0.5}})
    assert status is not None
    state = apply(GameState(), status)
    assert (state.fuel_main, state.fuel_reservoir) == (10.0, 0.5)
    refuel = parse_event({"event": "RefuelAll", "Amount": 5.0})
    assert refuel is not None
    assert apply(state, refuel).fuel_main == 15.0


def test_switching_ship_drops_old_fuel_and_cargo() -> None:
    first = parse_event({**LOADOUTS[0], "ShipID": 1})
    second = parse_event({**LOADOUTS[-1], "ShipID": 2})
    assert isinstance(first, LoadoutChanged) and isinstance(second, LoadoutChanged)
    loadgame = parse_event({"event": "LoadGame", "FuelLevel": 0.7})
    assert loadgame is not None
    state = apply(apply(apply(GameState(), first), CargoChanged(4)), loadgame)
    assert (state.cargo, state.fuel_main) == (4, 0.7)
    same = apply(state, first)  # re-outfitting the same ship keeps what we know
    assert (same.cargo, same.fuel_main) == (4, 0.7)
    swapped = apply(state, second)
    assert (swapped.cargo, swapped.fuel_main) == (0, None)
    assert swapped.current_range() == swapped.full_tank_range()


def test_multicrew_location_does_not_become_our_position() -> None:
    raw = {"event": "Location", "StarSystem": "Sol", "SystemAddress": 10477373803, "StarPos": [0, 0, 0]}
    crew = parse_event({**raw, "Multicrew": True})
    assert crew is not None
    assert not apply(GameState(), crew).position_known
    own = parse_event(raw)
    assert own is not None and apply(GameState(), own).system == "Sol"


def test_crew_in_another_ship_keeps_our_ship_and_fuel() -> None:
    own = parse_event({**LOADOUTS[0], "ShipID": 1})
    host = parse_event({**LOADOUTS[-1], "ShipID": 99})
    host_fuel = parse_event({"event": "Status", "Fuel": {"FuelMain": 0.5, "FuelReservoir": 0.1}})
    loaded = parse_event({"event": "LoadGame", "FuelLevel": 1.5, "ShipID": 1})
    assert isinstance(own, LoadoutChanged) and isinstance(host, LoadoutChanged)
    assert host_fuel is not None and loaded is not None
    state = apply(apply(GameState(), own), loaded)
    crew = apply(apply(apply(state, CrewChanged(in_other_ship=True)), host), host_fuel)
    assert crew.ship == own and crew.fuel_main == 1.5  # a Sidewinder holds 2 t
    back = apply(crew, CrewChanged(in_other_ship=False))
    assert back.ship == own and back.current_range() == state.current_range()


@pytest.mark.parametrize("flags2", [1 << 0, 1 << 1, 1 << 2], ids=["on-foot", "taxi", "multicrew"])
def test_status_fuel_not_of_our_ship_is_ignored(flags2: int) -> None:
    assert parse_event({"event": "Status", "Flags2": flags2, "Fuel": {"FuelMain": 1.0}}) is None


def test_taxi_jump_moves_us_but_not_our_fuel() -> None:
    raw = {
        "event": "FSDJump", "StarSystem": "Sol", "SystemAddress": 10477373803, "StarPos": [0, 0, 0],
        "Taxi": True, "JumpDist": 9.0, "FuelUsed": 1.0, "FuelLevel": 3.0,
    }  # fmt: skip
    ev = parse_event(raw)
    assert isinstance(ev, Arrived) and ev.fuel_level is None
    state = apply(GameState(fuel_main=12.0), ev)
    assert (state.system, state.fuel_main) == ("Sol", 12.0)


def test_reported_fuel_never_exceeds_the_tank() -> None:
    ship = parse_event({**LOADOUTS[0], "ShipID": 1})
    assert isinstance(ship, LoadoutChanged)
    over = parse_event({"event": "Status", "Fuel": {"FuelMain": ship.fuel_capacity_main + 50}})
    assert over is not None
    assert apply(apply(GameState(), ship), over).fuel_main == ship.fuel_capacity_main
    jump = {
        "event": "FSDJump",
        "StarSystem": "Sol",
        "SystemAddress": 1,
        "StarPos": [0, 0, 0],
        "FuelLevel": 99.0,
    }
    arrived = parse_event(jump)
    assert arrived is not None
    assert apply(apply(GameState(), ship), arrived).fuel_main == ship.fuel_capacity_main
    loaded = parse_event({"event": "LoadGame", "FuelLevel": 99.0, "ShipID": 1})  # LoadGame before Loadout
    assert loaded is not None
    assert apply(apply(GameState(), loaded), ship).fuel_main == ship.fuel_capacity_main


TIGHT_JUMPS: list[dict[str, Any]] = json.loads((DATA / "tight_jumps.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("jump", TIGHT_JUMPS, ids=lambda j: f"{j['fsd_item']}-{j['JumpDist']}")
def test_laden_formula_is_tight_on_real_max_range_jumps(jump: dict[str, Any]) -> None:
    # The six real jumps closest to the ship's limit (from real journals): each must fit under the
    # computed laden range AND sit close to it — a formula that is merely "big enough" would fail here.
    fsd = parse_fsd(jump["fsd_item"])
    assert fsd is not None
    rng = jump_range(
        fsd,
        unladen_mass=jump["UnladenMass"],
        cargo=jump["cargo"],
        fuel_main=jump["fuel_main_before"],
        fuel_reservoir=jump["FuelCapacity"]["Reserve"],
    )
    assert -0.01 <= rng - jump["JumpDist"] < 0.2


def test_login_fuel_survives_the_loadout_of_that_ship() -> None:
    # Login order in the journal: LoadGame (fuel of ship 2) → Location → Loadout (ship 2).
    old_ship = parse_event({**LOADOUTS[0], "ShipID": 1})
    new_ship = parse_event({**LOADOUTS[-1], "ShipID": 2})
    login = parse_event({"event": "LoadGame", "FuelLevel": 12.5, "ShipID": 2})
    assert old_ship is not None and new_ship is not None and login is not None
    state = apply(apply(apply(GameState(), old_ship), login), new_ship)
    assert state.fuel_main == 12.5
