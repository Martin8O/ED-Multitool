import importlib.util
import json
import threading
import time
import tkinter as tk
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from tkinter import ttk
from types import ModuleType
from typing import Any

import pytest

from edmultitool.catalog.ships import ship_display_name
from edmultitool.core.settings import Settings, load_settings, normalise
from edmultitool.plugin import ScoutPlugin

DATA = Path(__file__).parent / "data"
PLUGIN_DIR = Path(__file__).resolve().parents[1] / "EDMultitool"
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never route 127.0.0.1 via a proxy


def _sample() -> list[dict[str, Any]]:
    lines = (DATA / "journal_sample.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _edmc_state(**over: Any) -> dict[str, Any]:
    """Shaped like EDMC's monitor.state after a Loadout + Location (Modules keyed by slot)."""
    loadout = json.loads((DATA / "loadouts.json").read_text(encoding="utf-8"))[-1]
    st: dict[str, Any] = {
        "ShipType": loadout["Ship"].lower(),
        "ShipID": 7,
        "ShipName": "Test Ship",
        "UnladenMass": loadout["UnladenMass"],
        "FuelCapacity": loadout["FuelCapacity"],
        "CargoCapacity": loadout["CargoCapacity"],
        "MaxJumpRange": loadout["MaxJumpRange"],
        "Modules": {m["Slot"]: m for m in loadout["Modules"]},
        "SystemName": "Sol",
        "SystemAddress": 10477373803,
        "StarPos": (0.0, 0.0, 0.0),
        "Cargo": {"gold": 3, "silver": 1},
        "Captain": None,
    }
    st.update(over)
    return st


def test_startup_mid_session_fills_panel_from_edmc_state(tmp_path: Path) -> None:
    plugin = ScoutPlugin(tmp_path)
    assert plugin.text.scout == "waiting for the game…"
    assert plugin.on_journal({"event": "StartUp"}, _edmc_state())
    assert plugin.text.scout == "Sol"
    assert plugin.state.cargo == 4
    expected_ship = ship_display_name(_edmc_state()["ShipType"])
    assert plugin.text.ship.startswith(f"{expected_ship} · FSD ")
    assert plugin.text.range.startswith("now ") and plugin.text.range.endswith(" ly")


def test_startup_while_crew_in_another_ship(tmp_path: Path) -> None:
    plugin = ScoutPlugin(tmp_path)
    plugin.on_journal({"event": "StartUp"}, _edmc_state(Captain="Someone Else", SystemName=None))
    assert plugin.text.scout == "position unknown (crew in another ship)"
    assert plugin.text.range == "—"


def test_journal_replay_updates_panel_and_debug_dump(tmp_path: Path) -> None:
    plugin = ScoutPlugin(tmp_path)
    for entry in _sample():
        plugin.on_journal(entry, None)
    last = [e for e in _sample() if e["event"] == "FSDJump"][-1]
    assert plugin.text.scout == last["StarSystem"]
    dump = json.loads((tmp_path / "data" / "debug" / "panel.json").read_text(encoding="utf-8"))
    assert dump["panel"]["scout"] == last["StarSystem"]
    assert dump["state"]["address"] == last["SystemAddress"]
    assert dump["state"]["ship"] == "Lynx Highliner"
    assert abs(dump["state"]["range_best"] - dump["state"]["game_max_jump_range"]) < 0.01
    assert dump["panel"]["routing"].startswith(f"{dump['state']['range_full']:.2f} ly (full tank")


def test_dashboard_only_redraws_on_change(tmp_path: Path) -> None:
    plugin = ScoutPlugin(tmp_path)
    for entry in _sample():
        plugin.on_journal(entry, None)
    status = {
        "timestamp": "x",
        "event": "Status",
        "Flags": 0,
        "Fuel": {"FuelMain": 10.0, "FuelReservoir": 0.5},
    }
    assert plugin.on_dashboard(status) is True
    assert plugin.on_dashboard(status) is False
    assert plugin.on_dashboard({"event": "Status", "Flags": 0}) is False  # no fuel block → nothing to do


def test_settings_persist_and_change_routing_text(tmp_path: Path) -> None:
    plugin = ScoutPlugin(tmp_path)
    for entry in _sample():
        plugin.on_journal(entry, None)
    full = plugin.state.full_tank_range()
    assert full is not None
    assert plugin.set_settings(Settings(route_basis="full", margin_pct=10))
    assert plugin.text.routing == f"{full * 0.9:.2f} ly (full tank, -10 %)"
    reloaded = ScoutPlugin(tmp_path)
    assert reloaded.settings == Settings(route_basis="full", margin_pct=10)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({}, Settings()),
        ({"route_basis": "now", "margin_pct": 5}, Settings("now", 5.0)),
        ({"route_basis": "warp", "margin_pct": "lots"}, Settings()),
        ({"margin_pct": 999}, Settings(margin_pct=50.0)),
        ({"margin_pct": -3}, Settings(margin_pct=0.0)),
        ({"margin_pct": True}, Settings()),
    ],
)
def test_settings_normalise(raw: dict[str, object], expected: Settings) -> None:
    assert normalise(raw) == expected


def test_corrupt_settings_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_settings(path) == Settings()


# --- the real tk wiring in load.py -------------------------------------------------------------------


@pytest.fixture
def load_module() -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("edmultitool_load_panel", PLUGIN_DIR / "load.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    module.plugin_stop()  # idempotent; keeps a failed test from leaving the page server running


@pytest.fixture(scope="module")
def tk_root() -> Iterator[tk.Tk]:
    # one Tk for the module: on Windows a second Tk() in the same process sometimes fails to find tk.tcl
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # no display available
        pytest.skip(f"tk unavailable: {exc}")
    root.withdraw()
    yield root
    root.destroy()


def test_load_py_panel_shows_live_values(load_module: ModuleType, tk_root: tk.Tk, tmp_path: Path) -> None:
    assert load_module.plugin_start3(str(tmp_path)) == "ED Multitool"
    frame = load_module.plugin_app(tk_root)
    texts = {k: w["text"] for k, w in load_module._Ui.values.items()}
    assert texts["scout"] == "waiting for the game…"
    for entry in _sample():
        load_module.journal_entry("cmdr", False, None, None, entry, {})
    last = [e for e in _sample() if e["event"] == "FSDJump"][-1]
    assert load_module._Ui.values["scout"]["text"] == last["StarSystem"]
    assert load_module._Ui.values["ship"]["text"].startswith("Lynx Highliner · FSD 5A SCO")
    note = load_module._Ui.note
    labels = [w["text"] for w in frame.grid_slaves(column=0) if isinstance(w, tk.Label) and w is not note]
    assert sorted(labels) == sorted(
        ["System:", "Ship:", "Range:", "Routing:", "Next:", "Here:", "Earned:", "Missions:"]
    )
    (row,) = [w for w in frame.grid_slaves(column=0) if isinstance(w, tk.Frame)]
    button, copy = row.pack_slaves()
    assert isinstance(button, ttk.Button) and isinstance(copy, ttk.Button)
    assert button["text"] == "Open ED Multitool"
    assert copy["text"] == "Copy next" and copy.instate(["disabled"])  # no route to follow yet
    frame.grid()  # as EDMC places the plugin's frame in its window
    tk_root.update_idletasks()
    assert copy.winfo_x() >= button.winfo_x() + button.winfo_width()  # side by side, never on top
    assert not button.instate(["disabled"])  # the page server is up, so the button works

    # The page serves exactly what the panel shows.
    url = load_module._Ui.plugin.server_url
    with _NO_PROXY.open(f"{url}api/state", timeout=5) as resp:
        page = json.loads(resp.read())["state"]["panel"]
    shown = {k: w["text"] for k, w in load_module._Ui.values.items()}
    ui = load_module._Ui
    lines = {"note": note["text"], "update": ui.update["text"], "warning": ui.warning["text"]}
    assert page == {**shown, **lines}
    assert not load_module._Ui.warning.winfo_manager()  # nobody in the sample holds you hostile: no line
    # the note line shows only when there is something to say (EDMC running holds port 47615: it says so)
    assert bool(note.winfo_manager()) == bool(note["text"])

    load_module.plugin_stop()
    assert load_module._Ui.plugin.server_url is None
    with pytest.raises(OSError):
        _NO_PROXY.open(f"{url}api/state", timeout=2)


def test_load_py_collects_background_jobs_on_the_tk_thread(
    load_module: ModuleType, tk_root: tk.Tk, tmp_path: Path
) -> None:
    load_module.plugin_start3(str(tmp_path))
    load_module.plugin_app(tk_root)
    plugin = load_module._Ui.plugin
    assert plugin.worker.alive and load_module._Ui.pump_after is not None

    plugin.worker.submit("probe", lambda _c: threading.current_thread().name)
    deadline = time.monotonic() + 5
    while plugin.jobs_done < 2 and time.monotonic() < deadline:  # the startup cache prune + our probe
        tk_root.update()
        time.sleep(0.02)
    assert plugin.jobs_done == 2
    assert (plugin.last_job.name, plugin.last_job.value) == ("probe", "EDMultitool worker")
    dump = json.loads((tmp_path / "data" / "debug" / "panel.json").read_text(encoding="utf-8"))
    assert dump["worker"] == {
        "alive": True,
        "jobs_done": 2,
        "last_job": {"name": "probe", "ok": True, "cancelled": False, "error": None},
    }

    # A failing batch keeps the timer alive.
    def broken() -> bool:
        raise RuntimeError("bad batch")

    plugin.pump = broken
    with pytest.raises(RuntimeError):
        load_module._pump()
    assert load_module._Ui.pump_after is not None

    load_module.plugin_stop()
    assert load_module._Ui.pump_after is None
    assert not plugin.worker.alive
    assert not any(t.name == "EDMultitool worker" for t in threading.enumerate())
