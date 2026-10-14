"""Balance and legal standing: credits, legal state, notoriety, unpaid fines and bounties."""

import json
import threading
from pathlib import Path

from edmultitool.core.commander import Commander, Debt, apply_commander, ui_commander
from edmultitool.core.events import (
    CreditsKnown,
    CrimeCommitted,
    DebtPaid,
    Died,
    LegalStateKnown,
    NotorietyKnown,
    parse_commander,
)
from edmultitool.history import scan_commander
from edmultitool.plugin import COMMANDER_JOB, ScoutPlugin
from edmultitool.worker import JobResult


def _crime(when: str, faction: str, fine: int = 0, bounty: int = 0) -> dict[str, object]:
    e: dict[str, object] = {"timestamp": when, "event": "CommitCrime", "CrimeType": "x", "Faction": faction}
    if fine:
        e["Fine"] = fine
    if bounty:
        e["Bounty"] = bounty
    return e


def _fold(entries: list[dict[str, object]]) -> Commander:
    c = Commander()
    for e in entries:
        for ev in parse_commander(e):
            c = apply_commander(c, ev)
    return c


# --- parsing (shapes copied from real journals) ----------------------------------------------------------


def test_status_gives_balance_and_legal_state() -> None:
    status = {"event": "Status", "Flags": 0, "LegalState": "Clean", "Balance": 250600258}
    assert parse_commander(status) == [CreditsKnown(250600258), LegalStateKnown("Clean")]
    assert parse_commander({"event": "Status", "Flags": 0}) == []


def test_login_gives_credits_and_statistics_notoriety() -> None:
    assert parse_commander({"event": "LoadGame", "Credits": 1234, "Loan": 0}) == [CreditsKnown(1234)]
    stats = {"event": "Statistics", "Crime": {"Notoriety": 2, "Fines": 4, "Total_Fines": 1700}}
    assert parse_commander(stats) == [NotorietyKnown(2)]


def test_crimes_and_payments() -> None:
    assert parse_commander(_crime("t1", "Perez Ring Brewery", fine=400)) == [
        CrimeCommitted("Perez Ring Brewery", 400, 0, "t1")
    ]
    assert parse_commander(_crime("t1", "X")) == []  # neither a fine nor a bounty: nothing owed
    paid = {"timestamp": "t2", "event": "PayFines", "Amount": 100, "AllFines": False, "Faction": "P"}
    assert parse_commander(paid) == [DebtPaid("P", False, "t2")]
    every = {"timestamp": "t3", "event": "PayFines", "Amount": 100, "AllFines": True, "Faction": "P"}
    assert parse_commander(every) == [DebtPaid("", False, "t3")]
    bounties = {"timestamp": "t4", "event": "PayBounties", "Amount": 1250, "AllFines": False, "Faction": "W"}
    assert parse_commander(bounties) == [DebtPaid("W", True, "t4")]
    assert parse_commander({"timestamp": "t5", "event": "PayLegacyFines", "Amount": 9}) == [
        DebtPaid("", True, "t5")
    ]
    assert parse_commander({"timestamp": "t6", "event": "Died"}) == [Died("t6")]


# --- the rules -------------------------------------------------------------------------------------------


def test_paying_bounties_settles_that_factions_fines_too() -> None:
    # 2026-09-28: fine 400 + bounty 600 from one faction, then PayBounties 1250 (= 1000 + 25 % broker)
    c = _fold(
        [
            _crime("a", "Workers of Gateway for Equality", fine=400),
            _crime("b", "Workers of Gateway for Equality", bounty=600),
            {
                "timestamp": "c",
                "event": "PayBounties",
                "AllFines": False,
                "Faction": "Workers of Gateway for Equality",
            },
        ]
    )
    assert c.debts == ()


def test_paying_fines_leaves_bounties_and_other_factions() -> None:
    c = _fold(
        [
            _crime("a", "A", fine=100, bounty=200),
            _crime("b", "B", fine=50),
            {"timestamp": "c", "event": "PayFines", "AllFines": False, "Faction": "A"},
        ]
    )
    assert c.debts == (Debt("A", 0, 200), Debt("B", 50, 0))
    c = _fold(
        [_crime("a", "A", fine=100), _crime("b", "B", fine=50), {"event": "PayFines", "AllFines": True}]
    )
    assert c.debts == ()


def test_crimes_before_a_destruction_no_longer_count() -> None:
    # 2026-10-12: fine + bounty from Putama Imperial Society, destroyed by its station the same second
    c = _fold(
        [
            _crime("2026-10-12T14:45:09Z", "Putama Imperial Society", fine=400),
            _crime("2026-10-12T14:46:29Z", "Putama Imperial Society", bounty=600),
            {"timestamp": "2026-10-12T14:46:29Z", "event": "Died", "KillerName": "Lucifer Landing"},
        ]
    )
    assert c.debts == ()
    assert ui_commander(c).warnings == ()


def test_same_faction_adds_up() -> None:
    c = _fold([_crime("a", "A", fine=100), _crime("b", "A", bounty=600), _crime("c", "A", fine=50)])
    assert c.debts == (Debt("A", 150, 600),)


# --- what the page shows ---------------------------------------------------------------------------------


def test_credits_in_millions_and_no_warnings_when_clean() -> None:
    ui = ui_commander(Commander(credits=250600258, legal="Clean", notoriety=0))
    assert ui.credits == 250.6
    assert ui.warnings == ()
    assert ui_commander(Commander()).credits is None


def test_warnings_say_what_and_how_bad() -> None:
    c = Commander(legal="Wanted", notoriety=3, debts=(Debt("A", 400, 0), Debt("B", 0, 600)))
    ui = ui_commander(c)
    assert [(w.level, w.text) for w in ui.warnings] == [
        ("danger", "Wanted here"),
        ("warn", "Notoriety 3"),
        ("danger", "Unpaid: 600 Cr bounties · 400 Cr fines"),
    ]
    assert "A: fine 400 Cr" in ui.warnings[2].detail and "B: bounty 600 Cr" in ui.warnings[2].detail
    only_fines = ui_commander(Commander(debts=(Debt("A", 400, 0),))).warnings
    assert [(w.level, w.text) for w in only_fines] == [("warn", "Unpaid: 400 Cr fines")]
    assert ui_commander(Commander(notoriety=5)).warnings[0].level == "danger"
    for quiet in ("Clean", "Allied", "Thargoid", ""):
        assert ui_commander(Commander(legal=quiet)).warnings == ()
    for state, text in (
        ("IllegalCargo", "Illegal cargo"),
        ("Speeding", "Speeding"),
        ("Warrant", "Warrant here"),
    ):
        assert ui_commander(Commander(legal=state)).warnings[0].text == text


# --- history pass and the plugin ---------------------------------------------------------------------------


def _write(folder: Path, name: str, entries: list[dict[str, object]]) -> None:
    (folder / name).write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def test_history_pass_finds_what_is_still_unpaid(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "Journal.2026-10-01T100000.01.log",
        [
            {"timestamp": "2026-10-01T10:00:00Z", "event": "LoadGame", "Credits": 1000},
            {"timestamp": "2026-10-01T10:00:01Z", "event": "Statistics", "Crime": {"Notoriety": 1}},
            _crime("2026-10-01T10:05:00Z", "Old", fine=100),
            {"timestamp": "2026-10-01T10:06:00Z", "event": "Died"},
            _crime("2026-10-01T10:07:00Z", "Kept", bounty=500),
        ],
    )
    c, last = scan_commander(tmp_path, threading.Event())
    assert c == Commander(credits=1000, legal="", notoriety=1, debts=(Debt("Kept", 0, 500),))
    assert last == "2026-10-01T10:07:00Z"


def test_live_entries_seen_by_the_pass_are_not_counted_twice(tmp_path: Path) -> None:
    plugin = ScoutPlugin(tmp_path)
    plugin._commander_live = []  # the pass is running
    seen = _crime("2026-10-13T10:00:00Z", "A", fine=100)
    newer = _crime("2026-10-13T10:00:05Z", "A", bounty=200)
    plugin.on_journal(seen, None)
    plugin.on_journal(newer, None)
    plugin.on_dashboard({"Flags": 0, "Balance": 5_000_000, "LegalState": "Wanted"})
    found = Commander(credits=1, notoriety=0, debts=(Debt("A", 100, 0),))  # the pass read `seen` only
    plugin._finish(JobResult(1, COMMANDER_JOB, value=(found, "2026-10-13T10:00:00Z")))
    assert plugin.commander == Commander(
        credits=5_000_000, legal="Wanted", notoriety=0, debts=(Debt("A", 100, 200),)
    )
    ui = plugin.ui.commander
    assert ui.credits == 5.0
    assert [w.text for w in ui.warnings] == ["Wanted here", "Unpaid: 200 Cr bounties · 100 Cr fines"]


def test_balance_follows_status_also_on_foot(tmp_path: Path) -> None:
    plugin = ScoutPlugin(tmp_path)
    on_foot = {"Flags": 0, "Flags2": 16, "Balance": 7_340_000, "LegalState": "Clean"}  # Flags2 bit 4: on foot
    assert plugin.on_dashboard(on_foot) is False  # the EDMC panel shows no balance: no redraw needed
    assert plugin.ui.commander.credits == 7.3  # …but the page has it
