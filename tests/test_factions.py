"""The minor factions you have a standing with — parsed, folded, shown, and the hostile warning."""

import json
import os
import threading
from pathlib import Path
from typing import Any

from edmultitool.core.events import Arrived, FactionSeen, MissionEnded, parse_event, parse_mission
from edmultitool.core.factions import Factions, state_words, ui_factions
from edmultitool.history import scan_standings
from edmultitool.plugin import STANDINGS_JOB, ScoutPlugin
from edmultitool.worker import JobResult

SOL_ID = 10477373803
LHS_ID = 1458376315610


def _faction(
    name: str, rep: float, state: str = "None", active: tuple[str, ...] = (), inf: float = 0.2
) -> dict[str, Any]:
    f: dict[str, Any] = {
        "Name": name, "FactionState": state, "Government": "Corporate", "Influence": inf,
        "Allegiance": "Independent", "Happiness": "$Faction_HappinessBand2;", "MyReputation": rep,
    }  # fmt: skip
    if active:
        f["ActiveStates"] = [{"State": s} for s in active]
    return f


def _jump(
    t: str, system: str, address: int, pos: list[float], owner: str, *factions: dict[str, Any]
) -> dict[str, Any]:
    return {
        "timestamp": t, "event": "FSDJump", "StarSystem": system, "SystemAddress": address, "StarPos": pos,
        "SystemFaction": {"Name": owner}, "Factions": list(factions),
    }  # fmt: skip


def _done(t: str, faction: str, mission_id: int = 1) -> dict[str, Any]:
    return {
        "timestamp": t, "event": "MissionCompleted", "Faction": faction, "MissionID": mission_id,
        "Reward": 10, "FactionEffects": [{"Faction": faction, "Reputation": "+"}],
    }  # fmt: skip


JUMPS = [
    _jump(
        "2026-10-01T10:00:00Z", "Sol", SOL_ID, [0, 0, 0], "Mother Gaia",
        _faction("Mother Gaia", -95.5, "CivilWar", ("CivilWar", "Boom"), 0.4),
        _faction("Sol Workers' Party", 0.0),
        _faction("Perez Ring Brewery", 12.0),
    ),
    _jump(
        "2026-10-02T10:00:00Z", "LHS 5217a", LHS_ID, [30, 0, 40], "Perez Ring Brewery",
        _faction("Perez Ring Brewery", 100.0, "Expansion", ("Expansion",), 0.143707),
        _faction("Dark Echo", 0.0),
    ),
]  # fmt: skip


def _fold(entries: list[dict[str, Any]]) -> Factions:
    f = Factions()
    for e in entries:
        ev = parse_event(e)
        if isinstance(ev, Arrived):
            f.arrived(ev, e["timestamp"])
        m = parse_mission(e)
        if isinstance(m, MissionEnded):
            f.mission(m)
    return f


# --- the journal -------------------------------------------------------------------------------------------
def test_an_arrival_carries_each_factions_state_and_the_systems_owner() -> None:
    ev = parse_event(JUMPS[0])
    assert isinstance(ev, Arrived) and ev.system_faction == "Mother Gaia"
    assert ev.factions[0] == ("Mother Gaia", -95.5)  # the old pairs stay as they were
    assert ev.faction_details[0] == FactionSeen("Mother Gaia", -95.5, "CivilWar", ("CivilWar", "Boom"), 0.4)
    assert ev.faction_details[1].state == "" and ev.faction_details[1].active == ()  # "None" = no state


def test_a_faction_without_a_reputation_is_left_out() -> None:
    e = _jump("2026-10-01T10:00:00Z", "Sol", SOL_ID, [0, 0, 0], "X", {"Name": "X", "FactionState": "Boom"})
    ev = parse_event(e)
    assert isinstance(ev, Arrived) and ev.faction_details == () and ev.factions == ()


def test_a_completed_mission_names_who_gave_it() -> None:
    assert parse_mission(_done("2026-10-01T11:00:00Z", "Lave Radio Network")) == MissionEnded(
        1, "completed", "2026-10-01T11:00:00Z", "Lave Radio Network"
    )


def test_state_words() -> None:
    assert [state_words(s) for s in ("CivilWar", "Boom", "PirateAttack", "NaturalDisaster")] == [
        "Civil war", "Boom", "Pirate attack", "Natural disaster",
    ]  # fmt: skip


# --- the fold and what the page shows ----------------------------------------------------------------------
def test_the_card_lists_standings_best_first_and_counts_the_zeros() -> None:
    f = _fold(
        [
            *JUMPS,
            _done("2026-10-02T11:00:00Z", "Perez Ring Brewery"),
            _done("2026-10-02T12:00:00Z", "Perez Ring Brewery", 2),
        ]
    )
    ui = ui_factions(f, (30, 0, 40), LHS_ID)
    assert ui.known and [r.name for r in ui.rows] == ["Perez Ring Brewery", "Mother Gaia"]
    assert ui.neutral == 2  # Sol Workers' Party and Dark Echo: met at exactly 0
    perez, gaia = ui.rows
    assert (perez.standing.tier, perez.standing.value, perez.missions) == ("Allied", 100.0, 2)
    assert perez.system == "LHS 5217a" and perez.seen == "2026-10-02T10:00:00Z" and perez.controls
    assert perez.state == "Expansion" and perez.influence == 14 and perez.places == 2
    assert (perez.nearest, perez.nearest_ly) == ("LHS 5217a", 0.0)  # you are there
    assert gaia.hostile and gaia.standing.tier == "Hostile" and gaia.state == "Civil war, Boom"
    assert (gaia.nearest, gaia.nearest_ly) == ("Sol", 50.0)
    assert not perez.hostile
    assert ui.warning == ""  # nobody at LHS 5217a holds you hostile


def test_nearest_is_unknown_without_a_position() -> None:
    row = ui_factions(_fold(JUMPS), None, None).rows[0]
    assert (row.nearest, row.nearest_ly) == ("LHS 5217a", None)


def test_a_mission_for_a_faction_at_zero_lists_it() -> None:
    f = _fold([*JUMPS, _done("2026-10-02T11:00:00Z", "Dark Echo")])
    ui = ui_factions(f, None, None)
    assert "Dark Echo" in [r.name for r in ui.rows] and ui.neutral == 1


def test_only_completed_missions_count() -> None:
    f = _fold(JUMPS)
    f.mission(MissionEnded(5, "failed", "2026-10-03T00:00:00Z", "Perez Ring Brewery"))
    f.mission(MissionEnded(6, "abandoned", "2026-10-03T00:00:00Z", "Perez Ring Brewery"))
    assert f.records["perez ring brewery"].missions == 0


def test_hostile_here_names_the_controlling_faction_first() -> None:
    f = _fold(JUMPS)
    f.arrived(
        Arrived("FSDJump", "Sol", SOL_ID, (0, 0, 0), faction_details=(
            FactionSeen("Mother Gaia", -95.5), FactionSeen("Aardvark Front", -100.0),
        ), system_faction="Mother Gaia"),
        "2026-10-03T00:00:00Z",
    )  # fmt: skip
    ui = ui_factions(f, (0, 0, 0), SOL_ID)
    assert ui.warning == (
        "Hostile to you here: Mother Gaia (controls the system), Aardvark Front. "
        "Their stations refuse you, their ships may attack."
    )
    assert ui_factions(f, (30, 0, 40), LHS_ID).warning == ""  # elsewhere: no line


def test_a_faction_that_left_the_system_warns_no_more() -> None:
    f = _fold(JUMPS[:1])
    assert ui_factions(f, (0, 0, 0), SOL_ID).warning.startswith("Hostile to you here: Mother Gaia")
    f.arrived(_arrived_in_sol_without_gaia(), "2026-11-01T00:00:00Z")  # it retreated from Sol since
    assert ui_factions(f, (0, 0, 0), SOL_ID).warning == ""
    assert f.records["mother gaia"].system == "Sol"  # still where you last met it


def _arrived_in_sol_without_gaia() -> Arrived:
    return Arrived(
        "FSDJump", "Sol", SOL_ID, (0, 0, 0), faction_details=(FactionSeen("Sol Workers' Party", 0.0),)
    )


def test_unfriendly_is_no_warning() -> None:
    f = Factions()
    f.arrived(Arrived("FSDJump", "Sol", SOL_ID, (0, 0, 0), faction_details=(FactionSeen("X", -60.0),)), "t")
    assert ui_factions(f, None, SOL_ID).warning == ""


# --- the history pass and the hand-over --------------------------------------------------------------------
def _journal(tmp_path: Path) -> Path:
    d = tmp_path / "journal"
    d.mkdir()
    old = d / "Journal.2026-10-01T100000.01.log"
    new = d / "Journal.2026-10-02T100000.01.log"
    old.write_text(
        json.dumps(JUMPS[0]) + "\n" + json.dumps(_done("2026-10-01T11:00:00Z", "Perez Ring Brewery")) + "\n"
    )
    new.write_text(
        json.dumps(JUMPS[1])
        + "\n"
        + json.dumps(_done("2026-10-02T11:00:00Z", "Perez Ring Brewery", 2))
        + "\n{half"
    )
    os.utime(old, (1_000, 1_000))
    os.utime(new, (2_000, 2_000))
    return d


def test_the_history_pass_reads_standings_places_and_missions(tmp_path: Path) -> None:
    f, power = scan_standings(_journal(tmp_path), threading.Event())
    assert power is None
    perez = f.records["perez ring brewery"]
    assert (perez.reputation, perez.missions, len(perez.places), perez.system) == (100.0, 2, 2, "LHS 5217a")
    assert f.last == "2026-10-02T11:00:00Z"


def test_live_entries_while_the_pass_runs_are_not_lost_or_doubled(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p._standing_live = []  # as when the pass has been started
    p._refresh()
    assert not p.ui.factions.known  # the card says it is still reading
    p.on_journal(_done("2026-10-02T11:00:00Z", "Perez Ring Brewery", 2), None)  # the pass has it too
    p.on_journal(_done("2026-10-02T12:00:00Z", "Perez Ring Brewery", 3), None)  # newer than the pass
    found = scan_standings(_journal(tmp_path), threading.Event())
    p._finish(JobResult(1, STANDINGS_JOB, found))
    assert p._standing_live is None
    assert p.factions.records["perez ring brewery"].missions == 3
    p._refresh()
    assert p.ui.factions.known and p.ui.factions.rows[0].missions == 3
    p.on_journal(_done("2026-10-02T13:00:00Z", "Perez Ring Brewery", 4), None)
    assert p.factions.records["perez ring brewery"].missions == 4


def test_the_warning_shows_while_the_pass_still_reads(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p._standing_live = []  # as when the pass has been started
    p.on_journal(JUMPS[0], None)
    p._refresh()
    assert not p.ui.factions.known and p.ui.panel.warning.startswith("Hostile to you here: Mother Gaia")


def test_the_panel_warns_on_arriving_where_you_are_hostile(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p.on_journal(JUMPS[0], None)
    p._refresh()
    assert p.ui.panel.warning == (
        "Hostile to you here: Mother Gaia (controls the system). "
        "Its stations refuse you, its ships may attack."
    )
    assert p.ui.factions.warning == p.ui.panel.warning
    p.on_journal(JUMPS[1], None)
    p._refresh()
    assert p.ui.panel.warning == ""
    assert p._standings().of("Mother Gaia") == "hostile"  # the stations searches see the same standing


def test_a_failed_pass_keeps_what_came_live(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p._standing_live = []
    p.on_journal(JUMPS[0], None)
    p._finish(JobResult(1, STANDINGS_JOB, error="the journals could not be read"))
    assert p._standing_live is None and p.factions.records["mother gaia"].reputation == -95.5
    p._refresh()
    assert p.ui.factions.known and p.ui.panel.warning.startswith("Hostile to you here: Mother Gaia")
