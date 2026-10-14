"""Deaths on the Stats tab: who killed you, what coming back cost, the journal pass and the live journal."""

import json
import threading
from pathlib import Path
from typing import Any

from edmultitool.core.deaths import Deaths, UiDeath, UiKiller, ui_deaths
from edmultitool.core.events import Died, Killer, Resurrected, parse_death, parse_event
from edmultitool.core.stats import ui_stats
from edmultitool.history import scan_stats
from edmultitool.plugin import STATS_JOB, ScoutPlugin
from edmultitool.worker import JobResult

# shaped like the game's entries (made-up names)
PILOT = {"timestamp": "2026-09-26T15:20:27Z", "event": "Died", "KillerName": "Jo Pilot",
         "KillerShip": "federation_dropship_mkii", "KillerRank": "Competent"}  # fmt: skip
STATION = {"timestamp": "2026-09-29T11:36:55Z", "event": "Died", "KillerName": "Some Enterprise",
           "KillerShip": "outpostindustrial"}  # fmt: skip
WING = {"timestamp": "2026-10-01T10:00:00Z", "event": "Died", "Killers": [
    {"Name": "Cmdr One", "Ship": "krait_mkii", "Rank": "Dangerous"},
    {"Name": "$npc_name_decorate:#name=Two;", "Name_Localised": "Two", "Ship": "Anaconda", "Rank": "Elite"},
    {"Name": "Three", "Ship": "some_new_ship", "Rank": "Expert"}]}  # fmt: skip
NOBODY = {"timestamp": "2026-10-02T10:00:00Z", "event": "Died"}


def _back(when: str, option: str = "rebuy", cost: int = 0, bankrupt: bool = False) -> dict[str, Any]:
    return {"timestamp": when, "event": "Resurrect", "Option": option, "Cost": cost, "Bankrupt": bankrupt}


def test_parse_who_killed_you_and_the_resurrection() -> None:
    assert parse_death(PILOT) == Died(
        PILOT["timestamp"], (Killer("Jo Pilot", "federation_dropship_mkii", "Competent"),)
    )
    assert parse_death(STATION) == Died(
        STATION["timestamp"], (Killer("Some Enterprise", "outpostindustrial", ""),)
    )
    wing = parse_death(WING)
    assert isinstance(wing, Died)
    assert [k.name for k in wing.killers] == [
        "Cmdr One",
        "Two",
        "Three",
    ]  # the localised name when there is one
    assert wing.killers[1].ship == "anaconda"
    assert parse_death(NOBODY) == Died(NOBODY["timestamp"])
    assert parse_death(_back("t", "Rebuy", 250318)) == Resurrected("rebuy", 250318, False, "t")
    assert parse_death(_back("t", "rebuy", 9, True)) == Resurrected("rebuy", 9, True, "t")
    # the explore rules get the same death (one parser)
    assert parse_event(PILOT) == parse_death(PILOT)
    # broken or other entries never raise
    assert parse_death({"event": "Died"}) is None  # no time
    assert parse_death({"timestamp": "t", "event": "Resurrect"}) == Resurrected("", 0, False, "t")
    assert parse_death({"timestamp": "t", "event": "Died", "Killers": "x", "KillerName": 5}) == Died("t")
    assert parse_death({"timestamp": "t", "event": "Died", "Killers": [None, {"Rank": "Elite"}]}) == Died("t")
    assert parse_death({"timestamp": "t", "event": "Music"}) is None


def _deaths(*entries: dict[str, Any]) -> Deaths:
    d = Deaths()
    for e in entries:
        event = parse_death(e)
        assert event is not None
        d.add(event)
    return d


def test_each_death_with_what_coming_back_cost_newest_first() -> None:
    d = _deaths(
        PILOT, _back("2026-09-26T15:21:32Z", "free"),
        STATION, _back("2026-09-29T11:38:17Z", "rebuy", 250318),
        WING, _back("2026-10-01T10:01:00Z", "rebuy", 5000000, bankrupt=True),
        NOBODY,  # no resurrection read yet (the game is on the rebuy screen)
    )  # fmt: skip
    u = ui_deaths(d)
    assert (u.count, u.paid) == (4, 250318)  # a bankrupt rebuy is not paid
    assert [r.when[:10] for r in u.rows] == ["2026-10-02", "2026-10-01", "2026-09-29", "2026-09-26"]
    assert u.rows[0] == UiDeath(NOBODY["timestamp"], (), "", 0)
    assert u.rows[1].outcome == "bankrupt" and u.rows[1].cost == 0
    assert u.rows[1].killers == (
        UiKiller("Cmdr One", "Krait Mk II", "Dangerous", False),
        UiKiller("Two", "Anaconda", "Elite", False),
        UiKiller("Three", "Some New Ship", "Expert", False),  # a ship we do not know yet: its own name
    )
    station = UiKiller("Some Enterprise", "Industrial Outpost", "", True)
    assert u.rows[2] == UiDeath(STATION["timestamp"], (station,), "rebuy", 250318)
    assert u.rows[3].killers == (UiKiller("Jo Pilot", "Federal Assault Ship", "Competent", False),)
    assert (u.rows[3].outcome, u.rows[3].cost) == ("free", 0)


def test_station_types_we_do_not_list_still_read_as_stations() -> None:
    d = _deaths({"timestamp": "t", "event": "Died", "KillerName": "X", "KillerShip": "outpostfuturistic"})
    assert ui_deaths(d).rows[0].killers == (UiKiller("X", "Outpostfuturistic", "", True),)
    assert ui_deaths(_deaths({"timestamp": "t", "event": "Died", "KillerShip": "coriolis"})).rows[
        0
    ].killers == (UiKiller("", "Coriolis Starport", "", True),)


def test_a_death_read_twice_is_kept_once_and_a_late_resurrection_lands() -> None:
    d = _deaths(PILOT, PILOT)
    other = _deaths(PILOT, _back("2026-09-26T15:21:32Z", "rebuy", 7))
    k = d.key
    d.merge(other)
    assert d.key != k
    u = ui_deaths(d)
    assert (u.count, u.paid, u.rows[0].outcome) == (1, 7, "rebuy")


def _journal(folder: Path, name: str, entries: list[dict[str, Any]]) -> None:
    (folder / name).write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def test_the_pass_reads_deaths_and_the_plugin_adds_live_ones(tmp_path: Path) -> None:
    journals = tmp_path / "journals"
    journals.mkdir()
    _journal(journals, "Journal.2026-09-26T150000.01.log", [PILOT, _back("2026-09-26T15:21:32Z", "free")])
    _journal(
        journals,
        "Journal.2026-09-29T110000.01.log",
        [STATION, _back("2026-09-29T11:38:17Z", "rebuy", 250318)],
    )
    passed = scan_stats(journals, threading.Event())
    assert ui_stats(passed).deaths.count == 2  # no Statistics entry needed

    p = ScoutPlugin(tmp_path / "data")
    p.on_journal(STATION, None)  # read live too
    p.on_journal(NOBODY, None)
    p.on_journal(_back("2026-10-02T10:01:00Z", "rebuy", 1000), None)
    assert (p.ui.stats.deaths.count, p.ui.stats.deaths.paid) == (2, 1000)
    p._finish(JobResult(1, STATS_JOB, passed))
    p.on_journal({"timestamp": "2026-10-02T10:02:00Z", "event": "Music", "MusicTrack": "x"}, None)
    u = p.ui.stats.deaths
    assert (u.count, u.paid) == (3, 251318)  # the station death read twice: once
    assert [r.outcome for r in u.rows] == ["rebuy", "rebuy", "free"]


def test_a_death_without_a_resurrection_does_not_take_the_next_ones() -> None:
    d = _deaths(PILOT, STATION, _back("2026-09-29T11:38:17Z", "rebuy", 250318))  # the first one: no Resurrect
    u = ui_deaths(d)
    assert [(r.outcome, r.cost) for r in u.rows] == [("rebuy", 250318), ("", 0)]
    dodec = _deaths({"timestamp": "t", "event": "Died", "KillerName": "X", "KillerShip": "Dodec"})
    assert ui_deaths(dodec).rows[0].killers == (UiKiller("X", "Dodec Starport", "", True),)
