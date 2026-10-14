"""The game's lifetime statistics: parsing, the fold, the Stats tab's state and the journal pass."""

import json
import threading
from pathlib import Path
from typing import Any

from edmultitool.core.events import parse_statistics
from edmultitool.core.stats import HEADLINES, Stats, UiStatBar, ui_stats
from edmultitool.history import scan_stats
from edmultitool.plugin import STATS_JOB, ScoutPlugin
from edmultitool.worker import JobResult


def _entry(when: str, **over: Any) -> dict[str, Any]:
    """A Statistics entry shaped like the game's (trimmed, made-up figures), numbers overridable per group."""
    e: dict[str, Any] = {
        "timestamp": when,
        "event": "Statistics",
        "Bank_Account": {"Current_Wealth": 300000000, "Spent_On_Outfitting": 60000000,
                         "Spent_On_Fuel": 20000, "Spent_On_Ships": 0, "MercCoins_Current": 0},
        "Exploration": {"Systems_Visited": 300, "Exploration_Profits": 25000000,
                        "Total_Hyperspace_Distance": 5000, "Total_Hyperspace_Jumps": 400,
                        "Time_Played": 216000, "OnFoot_Distance_Travelled": 20000},
        "Exobiology": {"Organic_Data_Profits": 200000000, "Organic_Data": 25},
        "Passengers": {"Passengers_Missions_Tourist_vip_delivered": 50},
        "Squadron": {"Squadron_Bank_Credits_Deposited": 0},
        "Crew": {"NpcCrew_TotalWages": 0, "NpcCrew_Hired": 1},
    }  # fmt: skip
    for group, fields in over.items():
        e[group] = {**e.get(group, {}), **fields}
    return e


def _stats(*entries: dict[str, Any]) -> Stats:
    s = Stats()
    for e in entries:
        parsed = parse_statistics(e)
        assert parsed is not None
        s.add(parsed)
    return s


def test_parse_names_every_number_ours() -> None:
    entry = _entry("2026-03-10T12:00:00Z", Odd_New_Group={"Some_Thing_Profits": 5, "Flag": True, "Name": "x"})
    s = parse_statistics(entry)
    assert s is not None and s.when == "2026-03-10T12:00:00Z"
    by_key = {v.key: v for v in s.values}
    assert by_key["wealth"].group == "overview" and by_key["wealth"].unit == "cr"
    assert (by_key["jumps"].value, by_key["distance"].unit) == (400.0, "ly")
    assert by_key["time_played"].unit == "s" and by_key["explore.on_foot"].unit == "m"
    assert by_key["spent_outfitting"].group == "spent"
    # not named by us: labelled from the game's own words, unit guessed, kept under its group
    tourist = by_key["passengers.passengers_missions_tourist_vip_delivered"]
    assert (tourist.label, tourist.unit) == ("Tourist VIP delivered", "")
    new = by_key["odd_new_group.some_thing_profits"]
    assert (new.group, new.label, new.unit, new.value) == ("odd_new_group", "Some thing profits", "cr", 5.0)
    assert "odd_new_group.flag" not in by_key and "odd_new_group.name" not in by_key  # not numbers
    assert parse_statistics({"timestamp": "t", "event": "LoadGame"}) is None
    assert parse_statistics({"event": "Statistics"}) is None  # no time


def test_units_guessed_for_fields_we_do_not_name() -> None:
    fields = {
        "Multicrew": {"Multicrew_Time_Total": 1, "Multicrew_Fines_Total": 1, "Multicrew_Credits_Total": 1},
        "FLEETCARRIER": {"FLEETCARRIER_TRADESPEND_TOTAL": 1, "FLEETCARRIER_MAINTENANCE_TOTAL": 1,
                         "FLEETCARRIER_TOTAL_JUMPS": 1, "FLEETCARRIER_DISTANCE_TRAVELLED": "4170 LY"},
        "Squadron": {"Squadron_Bank_Commodities_Deposited_Num": 1,
                     "Squadron_Bank_Commodities_Deposited_Value": 1},
        "CQC": {"CQC_KD": 0.4, "CQC_WL": 1.5, "CQC_Credits_Earned": 1},
        "Combat": {"ConflictZone_High_Wins": 1},
        "Bank_Account": {"MercCoins_Total_Earned": 1},
    }  # fmt: skip
    s = parse_statistics({"timestamp": "2026-03-10T12:00:00Z", "event": "Statistics", **fields})
    assert s is not None
    got = {v.key: (v.label, v.unit, v.value) for v in s.values}
    assert got["multicrew.multicrew_time_total"][1] == "s"
    assert got["multicrew.fines"][1] == "cr"
    assert got["carrier.fleetcarrier_tradespend_total"] == ("Tradespend total", "cr", 1.0)
    assert got["carrier.fleetcarrier_maintenance_total"][1] == "cr"
    assert got["carrier.fleetcarrier_total_jumps"] == ("Total jumps", "", 1.0)
    assert got["carrier.fleetcarrier_distance_travelled"] == (
        "Distance travelled",
        "ly",
        4170.0,
    )  # written as text
    assert got["squadron.squadron_bank_commodities_deposited_num"] == (
        "Bank commodities deposited (number)",
        "",
        1.0,
    )
    assert got["squadron.squadron_bank_commodities_deposited_value"][1] == "cr"
    assert got["cqc.kd"] == ("Kills per death", "ratio", 0.4)
    assert got["cqc.cqc_credits_earned"] == ("CQC credits earned", "cr", 1.0)
    assert got["combat.conflictzone_high_wins"][0] == "Conflict zone high wins"
    assert got["bank.merccoins_total_earned"] == ("Merc coins total earned", "", 1.0)  # not credits


def test_ui_stats_totals_derived_week_and_bars() -> None:
    old = _entry("2026-03-01T20:00:00Z", Exploration={"Systems_Visited": 150, "Total_Hyperspace_Jumps": 200})
    recent = _entry("2026-03-07T10:00:00Z", Exploration={"Systems_Visited": 290})  # under a week before
    stats = _stats(_entry("2026-03-10T12:00:00Z"), recent, old, old)  # out of order, one login twice
    assert [when[:10] for when, _ in stats.logins] == ["2026-03-01", "2026-03-07", "2026-03-10"]
    u = ui_stats(stats)
    assert (u.count, u.first[:10], u.at[:10], u.week_from[:10]) == (
        3,
        "2026-03-01",
        "2026-03-10",
        "2026-03-01",
    )
    heads = {h.key: h for h in u.headlines}
    assert heads["systems_visited"].week == 150  # 300 - 150: from the newest login at least a week earlier
    assert [p.value for p in heads["systems_visited"].points] == [150, 290, 300]
    assert heads["wealth"].week == 0
    cards = {c.key: c for c in u.cards}
    assert [c.key for c in u.cards][:3] == ["overview", "explore", "exobio"]
    overview = {r.key: r for r in cards["overview"].rows}
    earned = 25000000 + 200000000
    assert overview["earned"].value == earned and overview["earned"].derived
    assert overview["per_hour"].value == earned / 60  # 216,000 s = 60 h
    explore = {r.key: r for r in cards["explore"].rows}
    assert explore["avg_jump"].value == 12.5
    assert {r.key: r.value for r in cards["exobio"].rows}["avg_sample"] == 8000000
    spent = cards["spent"]
    assert [r.key for r in spent.rows] == [
        "spent_outfitting",
        "spent_fuel",
    ] and spent.zeros == 2  # ships, wages
    assert [r.label for r in cards["crew"].rows] == ["Hired"]  # its wages are spending
    assert u.empty == ("Squadron", "Other currencies")  # nothing but zeros
    assert u.profits == (UiStatBar("Exobiology", 200000000), UiStatBar("Exploration data", 25000000))
    assert u.spending == (UiStatBar("Outfitting", 60000000), UiStatBar("Fuel", 20000))


def test_no_week_old_login_no_change_and_times_without_a_zone() -> None:
    u = ui_stats(_stats(_entry("2026-03-09T08:00:00Z"), _entry("2026-03-10T08:00:00Z")))
    assert u.week_from == "" and all(h.week is None for h in u.headlines)
    assert ui_stats(Stats()).known is False
    # a hand-made journal mixing times with and without a zone: no crash, the zoneless one taken as UTC
    mixed = ui_stats(_stats(_entry("2026-03-01T08:00:00"), _entry("2026-03-10T08:00:00Z")))
    assert mixed.week_from == "2026-03-01T08:00:00"


def test_old_logins_keep_only_the_headline_totals() -> None:
    entries = [_entry(f"2026-0{m}-01T08:00:00Z", Exploration={"Systems_Visited": m}) for m in range(1, 7)]
    stats = _stats(*entries)
    sizes = [len(values) for _, values in stats.logins]
    assert all(size <= len(HEADLINES) for size in sizes[:-2]) and sizes[-1] > len(HEADLINES)
    heads = {h.key: h for h in ui_stats(stats).headlines}
    assert [p.value for p in heads["systems_visited"].points] == [1, 2, 3, 4, 5, 6]


def _journal(folder: Path, name: str, entries: list[dict[str, Any]]) -> None:
    (folder / name).write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def test_scan_reads_every_login_and_the_plugin_adds_live_ones(tmp_path: Path) -> None:
    journals = tmp_path / "journals"
    journals.mkdir()
    _journal(journals, "Journal.2026-03-01T200000.01.log", [
        {"timestamp": "2026-03-01T20:00:00Z", "event": "LoadGame", "Credits": 5},
        _entry("2026-03-01T20:00:05Z"),
    ])  # fmt: skip
    _journal(journals, "Journal.2026-03-10T120000.01.log", [_entry("2026-03-10T12:00:00Z"), {"event": "x"}])
    stats = scan_stats(journals, threading.Event())
    assert [when for when, _ in stats.logins] == ["2026-03-01T20:00:05Z", "2026-03-10T12:00:00Z"]

    p = ScoutPlugin(tmp_path / "data")
    p.on_journal(_entry("2026-03-11T09:00:00Z", Exploration={"Systems_Visited": 400}), None)
    assert p.ui.stats.count == 1 and p.ui.stats.headlines[1].value == 400


def test_the_pass_and_live_logins_meet(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path / "data")
    p.on_journal(_entry("2026-03-10T12:00:00Z"), None)  # read live (the pass reads it too)
    p.on_journal(_entry("2026-03-11T09:00:00Z", Exploration={"Systems_Visited": 400}), None)  # live only
    assert p.ui.stats.count == 2
    passed = _stats(_entry("2026-03-01T20:00:05Z"), _entry("2026-03-10T12:00:00Z"))
    p._finish(JobResult(1, STATS_JOB, passed))
    p.on_journal({"timestamp": "2026-03-11T09:01:00Z", "event": "Music", "MusicTrack": "x"}, None)
    u = p.ui.stats
    assert (u.count, u.first[:10], u.at[:10]) == (3, "2026-03-01", "2026-03-11")  # one login read twice: once
    assert {h.key: h.value for h in u.headlines}[
        "systems_visited"
    ] == 400  # the newest still names the totals
