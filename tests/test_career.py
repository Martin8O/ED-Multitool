"""The Commander tab: superpower reputation, navy ranks, the Powerplay career and their history."""

import json
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from edmultitool.catalog.powers import POWERS
from edmultitool.core.career import (
    NAVIES,
    RANK_TITLES,
    Career,
    UiHold,
    UiPoint,
    UiRankStep,
    cycle_start,
    hold_message,
    pp_rank,
    standing,
    thin,
    ui_career,
)
from edmultitool.core.events import (
    RANKS,
    MeritsEarned,
    PledgeChanged,
    PowerplayKnown,
    PowerRanked,
    ProgressMoved,
    RanksKnown,
    ReputationKnown,
    parse_career,
)
from edmultitool.core.rank_guide import RANK_GUIDE
from edmultitool.history import scan_career
from edmultitool.plugin import CAREER_JOB, QUEUED, ScoutPlugin
from edmultitool.sources import spansh
from edmultitool.sources.http import Request, Response
from edmultitool.worker import JobResult
from fakes import FakeTime, ScriptedTransport, make_client, reply

LYR = "Li Yong-Rui"
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)  # a Tuesday: the week began Thursday 8 Oct, 07:00 UTC

# shapes copied from real journals (2026-10-11 … 13)
REPUTATION = {
    "timestamp": "2026-10-13T08:47:37Z", "event": "Reputation",
    "Empire": 15.004400, "Federation": 50.588501, "Independent": 0.000000, "Alliance": 37.687099,
}  # fmt: skip
RANK = {
    "timestamp": "2026-10-13T08:47:37Z", "event": "Rank", "Combat": 0, "Trade": 4, "Explore": 5, "Soldier": 0,
    "Exobiologist": 2, "Empire": 0, "Federation": 0, "CQC": 0,
}  # fmt: skip
PROGRESS = {
    "timestamp": "2026-10-13T08:47:37Z", "event": "Progress", "Combat": 37, "Trade": 86, "Explore": 55,
    "Soldier": 13, "Exobiologist": 92, "Empire": 3, "Federation": 77, "CQC": 0,
}  # fmt: skip
POWERPLAY = {
    "timestamp": "2026-10-13T08:47:50Z", "event": "Powerplay", "Power": LYR, "Rank": 0, "Merits": 10584,
    "TimePledged": 162419,
}  # fmt: skip


def _merits(when: str, gained: int, total: int) -> dict[str, object]:
    return {
        "timestamp": when,
        "event": "PowerplayMerits",
        "Power": LYR,
        "MeritsGained": gained,
        "TotalMerits": total,
    }


def _fold(entries: list[dict[str, object]]) -> Career:
    c = Career()
    for e in entries:
        for ev in parse_career(e):
            c.add(ev)
    return c


# --- parsing ------------------------------------------------------------------------------------------------


def test_parse_the_login_entries() -> None:
    (rep,) = parse_career(REPUTATION)
    assert isinstance(rep, ReputationKnown)
    assert dict(rep.standings) == {
        "Empire": 15.0044,
        "Federation": 50.588501,
        "Independent": 0.0,
        "Alliance": 37.687099,
    }
    (rank,) = parse_career(RANK)
    assert isinstance(rank, RanksKnown) and not rank.progress and dict(rank.values)["Exobiologist"] == 2
    (progress,) = parse_career(PROGRESS)
    assert (
        isinstance(progress, RanksKnown) and progress.progress and dict(progress.values)["Federation"] == 77
    )
    assert parse_career(POWERPLAY) == [PowerplayKnown(LYR, 10584, 162419, "2026-10-13T08:47:50Z", 0)]


def test_parse_merits_and_pledge_changes() -> None:
    assert parse_career(_merits("t", 4, 10694)) == [MeritsEarned(LYR, 4, 10694, "t")]
    assert parse_career({"timestamp": "t", "event": "PowerplayJoin", "Power": LYR}) == [
        PledgeChanged(LYR, "t")
    ]
    defect = {"timestamp": "t", "event": "PowerplayDefect", "FromPower": LYR, "ToPower": "Aisling Duval"}
    assert parse_career(defect) == [PledgeChanged("Aisling Duval", "t")]
    assert parse_career({"timestamp": "t", "event": "PowerplayLeave", "Power": LYR}) == [
        PledgeChanged("", "t")
    ]
    assert parse_career({"timestamp": "t", "event": "Promotion", "Explore": 2}) == [
        RanksKnown((("Explore", 2),), False, "t")
    ]


def test_parse_refuses_broken_entries() -> None:
    assert parse_career({**REPUTATION, "timestamp": None}) == []  # the history needs the time
    assert parse_career({"timestamp": "t", "event": "Reputation"}) == []
    assert parse_career({"timestamp": "t", "event": "Powerplay", "Power": LYR}) == []  # no merits
    assert parse_career({"timestamp": "t", "event": "PowerplayMerits", "Power": ""}) == []
    assert parse_career({"timestamp": "t", "event": "Rank", "Combat": True}) == []


# --- the rules ----------------------------------------------------------------------------------------------


def test_standing_tiers() -> None:
    assert standing("Federation", 50.588501) == standing("Federation", 50.6)
    s = standing("Federation", 50.588501)
    assert (s.value, s.tier, s.next_tier, s.to_next, s.tier_pct) == (50.6, "Friendly", "Allied", 39.4, 28)
    assert (standing("x", 4.0).tier, standing("x", 3.9).tier) == ("Cordial", "Neutral")
    assert (standing("x", -90).tier, standing("x", -90.1).tier) == ("Unfriendly", "Hostile")
    top = standing("x", 100)
    assert (top.tier, top.next_tier, top.to_next, top.tier_pct) == ("Allied", "", 0.0, 100)
    assert standing("x", -250).value == -100.0


def test_powerplay_ranks_from_merits() -> None:
    assert pp_rank(0) == (1, 2000)
    assert pp_rank(1999) == (1, 2000)
    assert pp_rank(2000) == (2, 5000)
    assert pp_rank(10702) == (4, 15000)
    assert pp_rank(15000) == (5, 23000)
    assert pp_rank(775_000) == (100, 783_000)  # rank 100 at 775,000 (the published table)


def test_week_starts_thursday_0700_utc() -> None:
    assert cycle_start(NOW) == datetime(2026, 10, 8, 7, 0, tzinfo=UTC)
    assert cycle_start(datetime(2026, 10, 15, 6, 59, tzinfo=UTC)) == datetime(2026, 10, 8, 7, tzinfo=UTC)
    assert cycle_start(datetime(2026, 10, 15, 7, 0, tzinfo=UTC)) == datetime(2026, 10, 15, 7, tzinfo=UTC)


def test_the_games_total_wins_over_entries_out_of_order() -> None:
    # 2026-10-11 17:10: merits written within seconds come out of order; the game's next total (4261 + 196 =
    # 4457) carried on from the LAST one written, not the highest: the last total wins
    c = _fold(
        [
            {"timestamp": "2026-10-11T11:40:51Z", "event": "PowerplayJoin", "Power": LYR},
            _merits("2026-10-11T17:10:45Z", 6, 4247),
            _merits("2026-10-11T17:10:46Z", 3, 4244),
            _merits("2026-10-11T17:10:47Z", 1, 4273),
            _merits("2026-10-11T17:10:48Z", 2, 4261),
        ]
    )
    assert c.merits == 4261
    assert c.week_merits() == {"2026-10-08T07:00:00Z": 4261}  # pledged this week: from 0
    p = ui_career(c, NOW).pledge
    assert (p.power, p.merits, p.week, p.rank, p.since) == (LYR, 4261, 4261, 2, "2026-10-11T11:40:51Z")


def test_weeks_from_the_rise_of_the_total() -> None:
    c = _fold(
        [
            {**POWERPLAY, "timestamp": "2026-09-30T10:00:00Z", "Merits": 1000},  # the week of 24 Sep
            _merits("2026-10-02T10:00:00Z", 500, 1500),  # the week of 1 Oct
            _merits("2026-10-12T10:00:00Z", 30, 1530),  # this week
            _merits("2026-10-12T10:00:01Z", 20, 1550),
        ]
    )
    assert c.week_merits() == {
        "2026-09-24T07:00:00Z": 0,  # only a login: no earlier total, no merits earned
        "2026-10-01T07:00:00Z": 500,
        "2026-10-08T07:00:00Z": 50,
    }
    weeks = ui_career(c, NOW).weeks
    assert [(w.start[:10], w.merits) for w in weeks] == [
        ("2026-09-24", 0),
        ("2026-10-01", 500),
        ("2026-10-08", 50),
    ]


def test_leaving_resets_and_defecting_starts_from_zero() -> None:
    c = _fold([POWERPLAY, {"timestamp": "2026-10-13T09:00:00Z", "event": "PowerplayLeave", "Power": LYR}])
    p = ui_career(c, NOW).pledge
    assert (p.power, p.known, p.merits, p.rank, p.since) == ("", True, None, None, "")
    assert ui_career(c, NOW).weeks == ()
    c = _fold(
        [
            POWERPLAY,
            {
                "timestamp": "2026-10-13T09:00:00Z",
                "event": "PowerplayDefect",
                "FromPower": LYR,
                "ToPower": "Nakato Kaine",
            },
            {**_merits("2026-10-13T09:10:00Z", 40, 40), "Power": "Nakato Kaine"},
        ]
    )
    p = ui_career(c, NOW).pledge
    assert (p.power, p.merits, p.week, p.since) == ("Nakato Kaine", 40, 40, "2026-10-13T09:00:00Z")


def test_ranks_and_promotion() -> None:
    c = _fold([RANK, PROGRESS])
    v = ui_career(c, NOW)
    assert [r.kind for r in v.ranks] == list(RANKS)  # every rank the game keeps, in its order
    navies = [(r.kind, r.rank, r.top, r.title, r.next_title, r.progress) for r in v.ranks if r.kind in NAVIES]
    assert navies == [
        ("Empire", 0, 14, "None", "Outsider", 3),
        ("Federation", 0, 14, "None", "Recruit", 77),
    ]
    c.add(RanksKnown((("Federation", 1),), False, "2026-10-14T10:00:00Z"))  # Promotion
    v = ui_career(c, NOW)
    fed = next(r for r in v.ranks if r.kind == "Federation")
    assert (fed.title, fed.progress) == ("Recruit", 0)
    assert [p.day for p in v.navy_history] == ["2026-10-13", "2026-10-14"]
    assert v.navy_history[0].values == (0.77, 0.03) and v.navy_history[1].values == (1.0, 0.03)
    c.add(RanksKnown((("Empire", 14), ("Combat", 13), ("CQC", 8)), False, "2026-10-15T10:00:00Z"))
    tops = {r.kind: (r.title, r.next_title, r.top) for r in ui_career(c, NOW).ranks}
    assert tops["Empire"] == ("King", "", 14)
    assert tops["Combat"] == ("Elite V", "", 13)
    assert tops["CQC"] == ("Elite", "", 8)


def test_a_promotion_or_a_sale_leaves_the_percent_unknown_until_the_next_login() -> None:
    sale = {"timestamp": "2026-10-14T10:00:00Z", "event": "SellOrganicData", "MarketID": 1, "BioData": []}
    assert parse_career(sale) == [ProgressMoved("Exobiologist", "2026-10-14T10:00:00Z")]
    explore = {"timestamp": "2026-10-14T10:00:00Z", "event": "MultiSellExplorationData", "Discovered": []}
    assert parse_career(explore) == [ProgressMoved("Explore", "2026-10-14T10:00:00Z")]
    trade = {"timestamp": "2026-10-14T10:00:00Z", "event": "MarketSell"}
    assert parse_career(trade) == [ProgressMoved("Trade", "2026-10-14T10:00:00Z")]
    c = _fold([RANK, PROGRESS])
    assert not any(r.stale for r in ui_career(c, NOW).ranks)
    c.add(ProgressMoved("Explore", "2026-10-14T10:00:00Z"))
    c.add(RanksKnown((("Exobiologist", 3),), False, "2026-10-14T10:01:00Z"))  # Promotion
    ranks = {r.kind: r for r in ui_career(c, NOW).ranks}
    assert ranks["Explore"].stale and ranks["Exobiologist"].stale and ranks["Exobiologist"].progress == 0
    assert not ranks["Trade"].stale
    # the next login's Progress tells the real percents again
    c.add(RanksKnown((("Explore", 40), ("Exobiologist", 90)), True, "2026-10-15T10:00:00Z"))
    ranks = {r.kind: r for r in ui_career(c, NOW).ranks}
    assert (ranks["Exobiologist"].stale, ranks["Exobiologist"].progress) == (False, 90)
    assert not ranks["Explore"].stale
    # the top rank has nothing left to fill
    c.add(RanksKnown((("Combat", 13),), False, "2026-10-15T11:00:00Z"))
    assert not next(r for r in ui_career(c, NOW).ranks if r.kind == "Combat").stale


def test_every_rank_carries_its_ladder() -> None:
    assert set(RANK_GUIDE) == set(RANKS)
    for kind, guide in RANK_GUIDE.items():
        assert guide.how and all(0 < i < len(RANK_TITLES[kind]) for i in guide.unlocks)
    c = _fold([RANK, PROGRESS])
    ranks = {r.kind: r for r in ui_career(c, NOW).ranks}
    fed = ranks["Federation"]
    assert [s.title for s in fed.ladder] == list(RANK_TITLES["Federation"])
    assert fed.ladder[4] == UiRankStep("Petty Officer", "Sol permit")
    assert fed.ladder[12].unlocks == "Federal Corvette" and fed.ladder[1].unlocks == ""
    assert "promotion mission" in fed.how and fed.note
    assert ranks["Empire"].ladder[4].unlocks == "Achenar permit"
    assert ranks["Combat"].ladder[8].unlocks.startswith("Shinrarta Dezhra permit")
    assert not any(s.unlocks for s in ranks["CQC"].ladder)


def test_rank_titles_cover_every_rank() -> None:
    assert set(RANK_TITLES) == set(RANKS)
    for kind in ("Combat", "Trade", "Explore", "Soldier", "Exobiologist"):
        assert len(RANK_TITLES[kind]) == 14 and RANK_TITLES[kind][8] == "Elite"
    assert (RANK_TITLES["Trade"][4], RANK_TITLES["Explore"][5], RANK_TITLES["Exobiologist"][2]) == (
        "Merchant",
        "Pathfinder",
        "Compiler",
    )
    assert len(RANK_TITLES["CQC"]) == 9 and len(RANK_TITLES["Empire"]) == 15


def test_history_one_point_per_day_and_thinned() -> None:
    c = _fold(
        [
            {**REPUTATION, "timestamp": "2026-10-12T08:00:00Z", "Federation": 40.0},
            {**REPUTATION, "timestamp": "2026-10-12T20:00:00Z", "Federation": 45.0},
            REPUTATION,
        ]
    )
    v = ui_career(c, NOW)
    assert [(p.day, p.values[0]) for p in v.standing_history] == [("2026-10-12", 45.0), ("2026-10-13", 50.6)]
    assert v.standing_history[-1].values == (
        50.6,
        15.0,
        37.7,
        0.0,
    )  # Federation, Empire, Alliance, Independent
    assert (v.since, v.known) == ("2026-10-12", True)
    points = [UiPoint(f"d{i}", (float(i),)) for i in range(1000)]
    kept = thin(points, 300)
    assert len(kept) == 300 and kept[0] == points[0] and kept[-1] == points[-1]


def test_nothing_known() -> None:
    v = ui_career(Career(), NOW)
    assert (v.known, v.standings, v.ranks, v.weeks, v.pledge.known) == (False, (), (), (), False)
    assert v.pledge.week_start == "2026-10-08T07:00:00Z"


def test_hold_shows_only_for_the_power_pledged() -> None:
    c = _fold([POWERPLAY])
    counts = (("Stronghold", 120), ("Fortified", 429), ("Exploited", 1636))
    hold = UiHold(LYR, "done", hold_message(LYR, counts), counts, "x")
    assert ui_career(c, NOW, hold).hold.message == "Li Yong-Rui holds 2,185 systems"
    assert ui_career(c, NOW, UiHold("Aisling Duval", "done")).hold == UiHold()
    assert hold_message(LYR, (("Exploited", 10_000),)) == "Li Yong-Rui holds 10,000 or more systems"


# --- the history pass and the plugin ------------------------------------------------------------------------


def _journal(tmp_path: Path) -> Path:
    d = tmp_path / "journals"
    d.mkdir()
    lines = [
        {"timestamp": "2026-10-11T11:40:51Z", "event": "PowerplayJoin", "Power": LYR},
        _merits("2026-10-11T17:10:45Z", 600, 600),
        {**REPUTATION, "timestamp": "2026-10-11T18:00:00Z", "Federation": 48.0},
        RANK, PROGRESS, REPUTATION, POWERPLAY,
        _merits("2026-10-13T09:47:04Z", 4, 10694),
    ]  # fmt: skip
    text = "\n".join(json.dumps(e) for e in lines) + "\n" + '{"timestamp":"2026-10-13T09:47:'  # half a line
    (d / "Journal.2026-10-13T084700.01.log").write_text(text, encoding="utf-8")
    return d


def test_history_pass(tmp_path: Path) -> None:
    c = scan_career(_journal(tmp_path), threading.Event())
    assert (c.power, c.merits, c.last, c.first) == (
        LYR,
        10694,
        "2026-10-13T09:47:04Z",
        "2026-10-11T11:40:51Z",
    )
    assert sorted(c.standing_days) == ["2026-10-11", "2026-10-13"]


def _route(request: Request) -> Response:
    if request.url.endswith("/systems/search") and request.json_body is not None:
        return reply("power_sol_lyr_held")
    return Response(500, b"{}")


@pytest.fixture
def plugin(tmp_path: Path) -> Iterator[tuple[ScoutPlugin, ScriptedTransport]]:
    p = ScoutPlugin(tmp_path / "plugin", _journal(tmp_path))
    p.now = lambda: NOW
    t = ScriptedTransport(_route, repeat_last=True)
    p.client = make_client(t, FakeTime())
    p.start_worker()
    yield p, t
    p.stop_worker()


def _pump_until(p: ScoutPlugin, done: object, timeout: float = 5) -> None:
    assert callable(done)
    deadline = time.monotonic() + timeout
    while not done() and time.monotonic() < deadline:
        p.pump()
        time.sleep(0.01)
    assert done()


def test_plugin_reads_the_history_and_counts_the_power(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, t = plugin
    _pump_until(p, lambda: p._career_live is None)
    v = p.ui.career
    assert (v.pledge.power, v.pledge.merits, v.pledge.week, v.pledge.rank) == (
        LYR,
        10694,
        10694,
        0,
    )  # the game says 0: introductory missions not done
    assert [s.tier for s in v.standings] == ["Friendly", "Cordial", "Friendly", "Neutral"]
    assert p.api_post("/api/career", {}) == (202, {"accepted": "career"})
    _pump_until(p, lambda: p.hold.status == "done")
    # the recorded reply says 2,185 for every state: three searches, one per state, one row each
    searches = [json.loads(r.json_body or "{}") for r in t.requests if r.url.endswith("/systems/search")]
    assert [(q["filters"]["power_state"]["value"], q["size"]) for q in searches[:3]] == [
        (["Stronghold"], 1), (["Fortified"], 1), (["Exploited"], 1)
    ]  # fmt: skip
    # then every power, held in any controlled state: one size-1 search each
    assert [q["filters"]["controlling_power"]["value"][0] for q in searches[3:]] == list(POWERS)
    assert {(tuple(q["filters"]["power_state"]["value"]), q["size"]) for q in searches[3:]} == {
        (("Exploited", "Fortified", "Stronghold"), 1)
    }
    state = json.loads(p.snapshot)["state"]["career"]
    assert state["hold"]["counts"] == [["Stronghold", 2185], ["Fortified", 2185], ["Exploited", 2185]]
    assert state["hold"]["powers"] == [[name, 2185] for name in sorted(POWERS)]  # equal counts: by name
    assert state["pledge"]["since"] == "2026-10-11T11:40:51Z"
    # a count stands a while: shown the tab again, nothing is asked
    asked = len(t.requests)
    p.api_post("/api/career", {})
    p.pump()
    assert len(t.requests) == asked


def test_live_entries_while_the_pass_runs_are_not_lost_or_doubled(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p.now = lambda: NOW
    p.on_journal(_merits("2026-10-13T09:47:04Z", 4, 10694), None)  # the pass has it too
    p.on_journal(_merits("2026-10-13T10:00:00Z", 6, 10700), None)  # newer than the pass
    assert p.career.merits == 10700  # shown at once
    found = scan_career(_journal(tmp_path), threading.Event())
    p._finish(JobResult(1, CAREER_JOB, found))
    assert p.career.merits == 10700 and p.career.week_merits() == {"2026-10-08T07:00:00Z": 10700}
    assert p._career_live is None
    p.on_journal(_merits("2026-10-13T11:00:00Z", 2, 10702), None)
    assert p.career.merits == 10702


def test_hold_needs_a_pledge_and_reports_failure(plugin: tuple[ScoutPlugin, ScriptedTransport]) -> None:
    p, t = plugin
    t.steps = [Response(500, b"{}")]
    _pump_until(p, lambda: p._career_live is None)
    p.api_post("/api/career", {})
    _pump_until(p, lambda: p.hold.status == "error", timeout=30)
    assert p.hold.message == "Spansh: the site is down or overloaded (HTTP 500); try again later."
    p.on_journal({"timestamp": "2026-10-13T12:00:00Z", "event": "PowerplayLeave", "Power": LYR}, None)
    p.api_post("/api/career", {})
    p.pump()
    assert p.ui.career.hold == UiHold()  # not pledged: nothing to count, nothing shown


def test_after_a_defect_the_weeks_start_at_the_new_pledge() -> None:
    c = _fold(
        [
            {**POWERPLAY, "timestamp": "2026-09-30T10:00:00Z", "Merits": 1000},
            _merits("2026-10-02T10:00:00Z", 500, 1500),
            {
                "timestamp": "2026-10-12T09:00:00Z",
                "event": "PowerplayDefect",
                "FromPower": LYR,
                "ToPower": "Nakato Kaine",
            },
            {**_merits("2026-10-12T10:00:00Z", 30, 30), "Power": "Nakato Kaine"},
        ]
    )
    assert [(w.start[:10], w.merits) for w in ui_career(c, NOW).weeks] == [("2026-10-08", 30)]
    assert pp_rank(-5) == (1, 2000)  # a broken total never takes the page down


def test_a_queued_count_says_so_until_the_route_search_ends(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path)
    p.hold = UiHold(LYR, "running", QUEUED + "counting the systems Li Yong-Rui holds…")
    p._unqueue()
    assert p.hold.message == "Counting the systems Li Yong-Rui holds…"


def test_the_games_own_powerplay_rank_wins() -> None:
    """Real journals: PowerplayRank 0 at the pledge, every login's Powerplay entry Rank 0 with 10,584 merits —
    rank 0 until the power's introductory missions are done, whatever the merits."""
    join: dict[str, object] = {"timestamp": "2026-10-11T11:40:51Z", "event": "PowerplayJoin", "Power": LYR}
    ranked = {"timestamp": "2026-10-11T11:40:51Z", "event": "PowerplayRank", "Power": LYR, "Rank": 0}
    assert parse_career(ranked) == [PowerRanked(LYR, 0, "2026-10-11T11:40:51Z")]
    c = _fold([ranked, join, _merits("2026-10-12T10:00:00Z", 10584, 10584)])  # the journal's own order
    p = ui_career(c, NOW).pledge
    assert (p.rank, p.next_rank_merits) == (0, None)
    c = _fold([ranked, join, {**POWERPLAY, "Rank": 3, "Merits": 6000}])
    p = ui_career(c, NOW).pledge
    assert (p.rank, p.next_rank_merits) == (3, 9000)  # rank 4 needs 9,000 in all
    # a defect: the new power's rank 0 comes just before the Defect, in the same second
    c.add(PowerRanked("Aisling Duval", 0, "2026-10-14T10:00:00Z"))
    assert ui_career(c, NOW).pledge.rank == 3  # still the old power's until the defect
    c.add(PledgeChanged("Aisling Duval", "2026-10-14T10:00:00Z"))
    assert (ui_career(c, NOW).pledge.power, ui_career(c, NOW).pledge.rank) == ("Aisling Duval", 0)
    c.add(PledgeChanged("Zemina Torval", "2026-10-15T10:00:00Z"))  # no rank written with it: from merits
    assert ui_career(c, NOW).pledge.rank == 1


def test_every_power_by_the_systems_it_holds() -> None:
    held = {name: 100 * i for i, name in enumerate(POWERS)}

    def answer(request: Request) -> Response:
        power = json.loads(request.json_body or "{}")["filters"]["controlling_power"]["value"][0]
        return Response(200, json.dumps({"count": held[power], "results": []}).encode())

    t = ScriptedTransport(answer, repeat_last=True)
    got = spansh.powers_held(make_client(t, FakeTime()), threading.Event())
    assert got == tuple(sorted(held.items(), key=lambda pc: -pc[1]))
    assert len(t.requests) == 12


def test_a_failed_count_of_every_power_keeps_your_powers_count(tmp_path: Path) -> None:
    def answer(request: Request) -> Response:
        states = json.loads(request.json_body or "{}")["filters"]["power_state"]["value"]
        return reply("power_sol_lyr_held") if len(states) == 1 else Response(400, b"{}")

    p = ScoutPlugin(tmp_path / "plugin", _journal(tmp_path))
    p.now = lambda: NOW
    p.client = make_client(ScriptedTransport(answer, repeat_last=True), FakeTime(), max_retries=0)
    p.start_worker()
    try:
        _pump_until(p, lambda: p._career_live is None)
        p.api_post("/api/career", {})
        _pump_until(p, lambda: p.hold.status in ("done", "error"))
        assert (p.hold.status, p.hold.powers) == ("done", ())
        assert p.hold.counts == (("Stronghold", 2185), ("Fortified", 2185), ("Exploited", 2185))
    finally:
        p.stop_worker()
