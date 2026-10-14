"""The Earnings tab: credits earned and spent per session / day / week, and the data carried unsold."""

import json
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from edmultitool.catalog.species import SPECIES_VALUES, species_value
from edmultitool.core.arrival import honk_value, sale_value
from edmultitool.core.earnings import Earnings, UiBalance, ui_earnings
from edmultitool.core.events import (
    BodyScanned,
    DataMapped,
    DataScanned,
    MoneyMoved,
    parse_money,
)
from edmultitool.history import scan_earnings
from edmultitool.plugin import EARNINGS_JOB, ScoutPlugin
from edmultitool.worker import JobResult

CEST = timezone(timedelta(hours=2))
NOW = datetime(2026, 10, 13, 18, 0, tzinfo=CEST)  # a Tuesday: the week began Monday 12 Oct 00:00 CEST


def _e(t: str, event: str, **fields: Any) -> dict[str, Any]:
    return {"timestamp": t, "event": event, **fields}


def _fold(*entries: dict[str, Any]) -> Earnings:
    e = Earnings()
    for entry in entries:
        for ev in parse_money(entry):
            e.add(ev)
    return e


def _row(rows: tuple[Any, ...], kind: str) -> tuple[int, int, int]:
    r = next(r for r in rows if r.kind == kind)
    return r.session, r.today, r.week


# --- reading the journal ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "earned", "kind", "amount"),
    [
        (
            _e("T", "MarketSell", Count=8, SellPrice=53933, TotalSale=431464, AvgPricePaid=50110),
            True,
            "trade",
            30584,
        ),
        (
            _e("T", "MarketSell", Count=10, SellPrice=900, TotalSale=9000),
            True,
            "trade",
            9000,
        ),  # mined: no price paid
        (
            _e("T", "MultiSellExplorationData", Discovered=[], BaseValue=50, Bonus=6, TotalEarnings=56),
            True,
            "exploration",
            56,
        ),
        (_e("T", "RedeemVoucher", Type="bounty", Amount=203000), True, "bounties", 203000),
        (_e("T", "RedeemVoucher", Type="CombatBond", Amount=5000), True, "bounties", 5000),
        (_e("T", "RedeemVoucher", Type="codex", Amount=2500), True, "other", 2500),
        (_e("T", "MissionCompleted", Reward=334124), True, "missions", 334124),
        (_e("T", "CommunityGoalReward", Reward=55000000), True, "other", 55000000),
        (_e("T", "Repair", Cost=32917), False, "repairs", 32917),
        (_e("T", "RefuelAll", Cost=404), False, "fuel", 404),
        (_e("T", "BuyAmmo", Cost=160), False, "rearm", 160),
        (_e("T", "SellDrones", TotalSale=300), False, "rearm", -300),
        (_e("T", "ModuleBuy", BuyPrice=16179531, SellPrice=1000000), False, "ships", 15179531),
        (_e("T", "ShipyardBuy", ShipPrice=500000, SellPrice=40000), False, "ships", 460000),
        (_e("T", "ShipyardSell", ShipPrice=40000), False, "ships", -40000),
        (_e("T", "PayBounties", Amount=1250), False, "fines", 1250),
        (_e("T", "Resurrect", Option="rebuy", Cost=2855049), False, "rebuy", 2855049),
        (_e("T", "BuyMicroResources", Count=2, Price=50000), False, "other", 50000),
        (_e("T", "BuyMicroResources", Count=2, Price=25000, TotalCost=50000), False, "other", 50000),
    ],
)
def test_each_entry_moves_its_money(entry: dict[str, Any], earned: bool, kind: str, amount: int) -> None:
    moves = [m for m in parse_money(entry) if isinstance(m, MoneyMoved)]
    assert moves == [MoneyMoved(earned, kind, amount, "T")]


def test_organic_sale_counts_value_and_bonus_and_a_donation_is_spent() -> None:
    sale = _e("T", "SellOrganicData", BioData=[
        {"Species_Localised": "Stratum Tectonicas", "Value": 19010800, "Bonus": 0},
        {"Species_Localised": "Tussock Ignis", "Value": 1849000, "Bonus": 7396000},
    ])  # fmt: skip
    assert [m for m in parse_money(sale) if isinstance(m, MoneyMoved)] == [
        MoneyMoved(True, "exobiology", 28255800, "T")
    ]
    donation = _e("T", "MissionCompleted", Reward=0, Donated=1000000)
    assert parse_money(donation) == [MoneyMoved(False, "other", 1000000, "T")]


def test_nothing_without_a_time_or_an_amount() -> None:
    assert parse_money({"event": "RepairAll", "Cost": 10}) == []
    assert parse_money(_e("T", "RepairAll", Cost=0)) == []
    assert parse_money(_e("T", "Music", MusicTrack="x")) == []


def test_nav_beacon_data_is_not_carried() -> None:
    scan = _e("T", "Scan", ScanType="NavBeaconDetail", BodyName="A 1", BodyID=1, StarSystem="A",
              SystemAddress=5, PlanetClass="Icy body", MassEM=0.1)  # fmt: skip
    assert parse_money(scan) == []
    assert isinstance(parse_money({**scan, "ScanType": "Detailed"})[0], DataScanned)


# --- the windows ------------------------------------------------------------------------------------------


def test_session_today_and_week_windows() -> None:
    e = _fold(
        _e("2026-10-11T21:59:59Z", "RepairAll", Cost=1),  # Sunday 23:59 local: last week
        _e("2026-10-11T22:00:00Z", "RepairAll", Cost=10),  # Monday 00:00 local: this week
        _e("2026-10-12T21:00:00Z", "LoadGame", Credits=5),  # the game started Monday 23:00 local
        _e("2026-10-12T21:30:00Z", "RepairAll", Cost=100),  # session, not today
        _e("2026-10-12T22:00:00Z", "RepairAll", Cost=1000),  # today 00:00 local
        _e("2026-10-13T12:00:00Z", "MissionCompleted", Reward=5000),
    )
    ui = ui_earnings(e, NOW)
    assert _row(ui.spent, "repairs") == (1100, 1000, 1110)
    assert _row(ui.spent, "total") == (1100, 1000, 1110)
    assert _row(ui.earned, "missions") == (5000, 5000, 5000)
    assert (ui.net.session, ui.net.today, ui.net.week) == (3900, 4000, 3890)
    assert ui.week_start == "2026-10-12T00:00:00+02:00"
    assert ui.running and ui.session_hours == 15.0  # to the newest entry, not to the clock
    assert ui.per_hour == round(5000 / 15)
    assert ui.text == "session +0.0 M · today +0.0 M"


def test_a_session_that_began_last_week_still_sums_from_its_start() -> None:
    e = _fold(
        _e("2026-10-11T20:00:00Z", "LoadGame"),
        _e("2026-10-11T21:00:00Z", "MissionCompleted", Reward=7_000_000),  # Sunday local
        _e("2026-10-12T09:00:00Z", "MissionCompleted", Reward=1_000_000),
        _e("2026-10-12T10:00:00Z", "Shutdown"),
    )
    ui = ui_earnings(e, NOW)
    assert _row(ui.earned, "missions") == (8_000_000, 0, 1_000_000)
    assert not ui.running and ui.session_hours == 14.0
    assert ui.text == "session +8.0 M · today +0.0 M"


def test_short_session_has_no_rate() -> None:
    ui = ui_earnings(
        _fold(_e("2026-10-13T10:00:00Z", "LoadGame"), _e("2026-10-13T10:03:00Z", "RepairAll", Cost=5)), NOW
    )
    assert ui.per_hour is None and ui.session_hours == 0.05


def test_not_read_yet_has_no_panel_text() -> None:
    assert ui_earnings(Earnings(), NOW, known=False).text == "—"


# --- data carried unsold ----------------------------------------------------------------------------------


def _scan(t: str, system: str, body: int, **fields: Any) -> dict[str, Any]:
    base = {"ScanType": "Detailed", "BodyName": f"{system} {body}", "BodyID": body, "StarSystem": system,
            "SystemAddress": hash(system) & 0xFFFFFF, "WasDiscovered": True, "WasMapped": True}  # fmt: skip
    return _e(t, "Scan", **{**base, **fields})


def test_scans_are_carried_until_their_system_is_sold_and_lost_on_death() -> None:
    hmc = {"PlanetClass": "High metal content body", "MassEM": 0.5}
    e = _fold(
        _scan("2026-10-13T10:00:00Z", "Alpha", 1, **hmc),
        _scan("2026-10-13T10:01:00Z", "Alpha", 1, **hmc),  # scanned again: one body
        _scan("2026-10-13T10:02:00Z", "Beta", 2, StarType="M", StellarMass=0.3),
        _e(
            "2026-10-13T10:03:00Z",
            "SAAScanComplete",
            SystemAddress=hash("Alpha") & 0xFFFFFF,
            BodyID=1,
            ProbesUsed=5,
            EfficiencyTarget=6,
        ),
    )
    ui = ui_earnings(e, NOW)
    assert (ui.data_bodies, ui.data_systems, ui.data_since) == (2, 2, "2026-10-13T10:00:00Z")
    alpha = next(b for b in e.bodies.values() if b.system == "Alpha")
    assert alpha.mapped and alpha.efficient
    assert ui.data_value == sum(b.value() for b in e.bodies.values())
    sold = [{"SystemName": "Alpha", "NumBodies": 1}]
    for ev in parse_money(
        _e("2026-10-13T11:00:00Z", "MultiSellExplorationData", Discovered=sold, TotalEarnings=1)
    ):
        e.add(ev)
    assert {b.system for b in e.bodies.values()} == {"Beta"}
    beta = ui_earnings(e, NOW).data_value
    for ev in parse_money(_e("2026-10-13T12:00:00Z", "Died")):
        e.add(ev)
    ui = ui_earnings(e, NOW)
    assert (ui.data_bodies, ui.data_value, ui.died, ui.lost) == (0, 0, "2026-10-13T12:00:00Z", beta)


def test_species_carried_until_sold_one_for_one() -> None:
    def analysed(t: str, name: str) -> dict[str, Any]:
        return _e(t, "ScanOrganic", ScanType="Analyse", Species_Localised=name, SystemAddress=1, Body=2)

    e = _fold(
        analysed("2026-10-13T10:00:00Z", "Stratum Tectonicas"),
        analysed("2026-10-13T10:10:00Z", "Stratum Tectonicas"),
        analysed("2026-10-13T10:20:00Z", "Tussock Cultro"),
        _e(
            "2026-10-13T10:21:00Z",
            "ScanOrganic",
            ScanType="Sample",
            Species_Localised="Bacterium Vesicula",
            SystemAddress=1,
            Body=3,
        ),
        analysed("2026-10-13T10:30:00Z", "Unheard Of"),
        _e(
            "2026-10-13T11:00:00Z",
            "SellOrganicData",
            BioData=[{"Species_Localised": "stratum tectonicas", "Value": 19010800, "Bonus": 0}],
        ),
    )
    ui = ui_earnings(e, NOW)
    assert [(s.name, s.value) for s in ui.bio_species] == [
        ("Unheard Of", None),
        ("Tussock Cultro", 1_766_600),
        ("Stratum Tectonicas", 19_010_800),
    ]
    assert ui.bio_value == 20_777_400 and ui.bio_since == "2026-10-13T10:10:00Z"
    assert ui.text == "today +19.0 M · unsold 20.8 M"


def test_species_catalog_looks_up_any_case() -> None:
    assert species_value("FONTICULUA FLUCTUS") == 20_000_000
    assert species_value("Nothing Such") is None
    assert len(SPECIES_VALUES) == 118 and all(v > 0 for v in SPECIES_VALUES.values())


def _body(star: str = "", planet: str = "", mass: float = 0.0, *, terra: bool = False,
          discovered: bool = True, mapped: bool = True) -> BodyScanned:  # fmt: skip
    return BodyScanned(1, 1, "x", star, planet, mass, terra, False, 0.0, discovered, mapped)


def test_sale_value_bonuses() -> None:
    hmc = _body(planet="High metal content body", mass=0.5)
    base = sale_value(hmc, False, False)
    assert base == round(9_654 + 9_654 * 0.56591828 * 0.5**0.2)
    first = sale_value(_body(planet="High metal content body", mass=0.5, discovered=False), False, False)
    assert first == round(base * 2.6)
    mapped = sale_value(hmc, True, False)
    raw = (9_654 + 9_654 * 0.56591828 * 0.5**0.2) * 3.3333333333
    assert mapped == round(raw + max(raw * 0.3, 555))
    efficient = (9_654 + 9_654 * 0.56591828 * 0.5**0.2) * 3.3333333333 * 1.25
    assert sale_value(hmc, True, True) == round(efficient + efficient * 0.3)
    first_mapped = _body(planet="High metal content body", mass=0.5, mapped=False)
    assert sale_value(first_mapped, True, False) > mapped
    assert sale_value(_body(star="M", mass=0.3), False, False) == 1205


# --- history pass and hand-over ---------------------------------------------------------------------------


def _journal(tmp_path: Path, entries: list[dict[str, Any]]) -> Path:
    folder = tmp_path / "journal"
    folder.mkdir(exist_ok=True)
    (folder / "Journal.2026-10-13T100000.01.log").write_text(
        "\n".join(json.dumps(x) for x in entries) + "\n", encoding="utf-8"
    )
    return folder


def test_live_entries_while_the_pass_runs_are_not_lost_or_doubled(tmp_path: Path) -> None:
    same_second = "2026-10-13T10:05:00Z"
    history = [
        _e("2026-10-13T10:00:00Z", "LoadGame"),
        _e(same_second, "RefuelAll", Cost=100),
        _e(same_second, "RefuelAll", Cost=100),
    ]
    p = ScoutPlugin(tmp_path / "plugin")
    p.now = lambda: NOW.astimezone(UTC)
    for entry in history[1:]:  # EDMC delivered these while the pass was reading them too
        p.on_journal(entry, None)
    p.on_journal(_e(same_second, "RefuelAll", Cost=100), None)  # a third in that second: the pass missed it
    p.on_journal(_e("2026-10-13T10:06:00Z", "RepairAll", Cost=7), None)
    assert not p.ui.earnings.known  # the page says the journals are being read
    found = scan_earnings(_journal(tmp_path, history), threading.Event())
    p._finish(JobResult(1, EARNINGS_JOB, found))
    assert p._earnings_live is None
    p.on_journal(_e("2026-10-13T10:07:00Z", "Music", MusicTrack="x"), None)
    ui = p.ui.earnings
    assert ui.known and _row(ui.spent, "fuel") == (300, 300, 300)
    assert _row(ui.spent, "repairs") == (7, 7, 7)
    assert ui.session_start == "2026-10-13T10:00:00Z" and ui.session_hours == round(7 / 60, 2)
    assert json.loads(p.snapshot)["state"]["panel"]["earned"] == ui.text


def test_history_pass_reads_carried_data_across_files(tmp_path: Path) -> None:
    folder = _journal(tmp_path, [
        _scan("2026-10-13T10:00:00Z", "Alpha", 1, StarType="K", StellarMass=0.7),
        _e("2026-10-13T10:05:00Z", "ScanOrganic", ScanType="Analyse", Species_Localised="Bacterium Volu",
           SystemAddress=1, Body=2),
    ])  # fmt: skip
    e = scan_earnings(folder, threading.Event())
    assert len(e.bodies) == 1 and [n for n, _ in e.species] == ["Bacterium Volu"]
    assert e.latest == "2026-10-13T10:05:00Z"
    assert e.moves == [] and e.session_start == ""


def test_a_system_sale_counts_every_body_the_honk_found() -> None:
    """A real sale: Wolfsegen sold for 6 201 Cr — its T dwarf (1 201) and one icy body (500) scanned, the
    discovery scan counting 10 bodies: the 9 besides the arrival star add 500 each."""
    address = 22961431923568
    common = {"StarSystem": "Wolfsegen", "SystemAddress": address, "WasDiscovered": True, "WasMapped": True}
    star = _e("2026-09-26T10:00:00Z", "Scan", ScanType="AutoScan", BodyName="Wolfsegen", BodyID=0,
              DistanceFromArrivalLS=0.0, StarType="T", StellarMass=0.0625, **common)  # fmt: skip
    honk = _e("2026-09-26T10:01:00Z", "FSSDiscoveryScan", BodyCount=10, SystemName="Wolfsegen",
              SystemAddress=address)  # fmt: skip
    icy = _e("2026-09-26T10:02:00Z", "Scan", ScanType="Detailed", BodyName="Wolfsegen 3", BodyID=4,
             DistanceFromArrivalLS=453.2, PlanetClass="Icy body", MassEM=0.046174, **common)  # fmt: skip
    assert _fold(star, honk, icy).data_value() == 6201
    # without the honk the other bodies are not known: only the scanned ones count
    assert _fold(star, icy).data_value() == 1201 + 500 + 500


def test_honk_value_of_a_body() -> None:
    m_star = _body(star="M", mass=0.3867)
    assert honk_value(m_star) == round((1200 + 0.3867 * 1200 / 66.25) / 3)  # no 500 minimum for a star
    assert honk_value(_body(planet="Icy body", mass=0.05)) == 500
    assert honk_value(_body(planet="Icy body", mass=0.05, discovered=False)) == 1300
    assert honk_value(None) == 500


def test_today_after_a_clock_change_starts_at_that_midnight() -> None:
    cet = timezone(timedelta(hours=1))

    def local(t: datetime) -> datetime:  # summer time ends 25 Oct 2026 at 03:00
        return t.replace(tzinfo=CEST if t < datetime(2026, 10, 25, 3) else cet)

    now = datetime(2026, 10, 25, 12, 0, tzinfo=cet)
    e = _fold(_e("2026-10-24T22:30:00Z", "RepairAll", Cost=10))  # 00:30 local, still summer time
    ui = ui_earnings(e, now, local=local)
    assert _row(ui.spent, "repairs") == (0, 10, 10)
    assert ui.week_start == "2026-10-19T00:00:00+02:00"


def test_a_system_sold_and_visited_again_is_not_unsold_again() -> None:
    star = _scan("2026-10-13T10:00:00Z", "Home", 0, StarType="G", StellarMass=1.0, DistanceFromArrivalLS=0.0)
    honk = _e("2026-10-13T10:00:30Z", "FSSDiscoveryScan", BodyCount=5, SystemName="Home",
              SystemAddress=hash("Home") & 0xFFFFFF)  # fmt: skip
    sale = _e("2026-10-13T11:00:00Z", "SellExplorationData", Systems=["Home"], TotalEarnings=3000)
    e = _fold(
        star,
        honk,
        sale,
        {**star, "timestamp": "2026-10-14T10:00:00Z"},
        {**honk, "timestamp": "2026-10-14T10:00:30Z"},
    )
    assert e.bodies == {} and e.data_value() == 0
    planet = _scan(
        "2026-10-14T10:05:00Z", "Home", 3, PlanetClass="Icy body", MassEM=0.1, DistanceFromArrivalLS=500.0
    )
    e.add(parse_money(planet)[0])  # a body never sold before still counts
    assert (
        len(e.bodies) == 1
        and e.data_value() == sale_value(e.bodies[next(iter(e.bodies))].scan, False, False) + 500
    )


def test_more_entries_that_move_money() -> None:
    assert parse_money(_e("T", "ModuleBuyAndStore", BuyPrice=900)) == [MoneyMoved(False, "ships", 900, "T")]
    assert parse_money(_e("T", "MissionFailed", Fine=5000)) == [MoneyMoved(False, "fines", 5000, "T")]
    assert parse_money(_e("T", "EngineerContribution", Type="Credits", Quantity=100000)) == [
        MoneyMoved(False, "other", 100000, "T")
    ]
    assert parse_money(_e("T", "EngineerContribution", Type="Commodity", Quantity=10)) == []


def test_history_pass_folds_in_time_order_whatever_the_file_times(tmp_path: Path) -> None:
    import os

    folder = tmp_path / "journal"
    folder.mkdir()
    old = folder / "Journal.2026-10-01T100000.01.log"
    new = folder / "Journal.2026-10-13T100000.01.log"
    old.write_text(
        json.dumps(_e("2026-10-01T10:00:00Z", "MissionCompleted", Reward=1)) + "\n", encoding="utf-8"
    )
    new.write_text(
        "\n".join(json.dumps(x) for x in [
            _e("2026-10-13T10:00:00Z", "LoadGame"),
            _e("2026-10-13T10:01:00Z", "MissionCompleted", Reward=50),
            _e("2026-10-13T12:00:00Z", "Music", MusicTrack="x"),
        ]) + "\n", encoding="utf-8",
    )  # fmt: skip
    os.utime(old, (2_000_000_000, 2_000_000_000))  # the old file looks newest (a copied folder)
    e = scan_earnings(folder, threading.Event())
    assert [m.amount for m in e.moves] == [1, 50]
    ui = ui_earnings(e, NOW)
    assert _row(ui.earned, "missions") == (50, 50, 50)
    assert ui.session_hours == 2.0  # to the file's last entry, a Music line


def test_mapping_a_body_whose_scan_was_sold_counts_the_mapping_only() -> None:
    world = {"PlanetClass": "Earthlike body", "MassEM": 0.8, "DistanceFromArrivalLS": 900.0}
    address = hash("Eden") & 0xFFFFFF
    e = _fold(
        _scan("2026-10-12T10:00:00Z", "Eden", 4, **world),
        _e("2026-10-12T11:00:00Z", "SellExplorationData", Systems=["Eden"], TotalEarnings=1),
        # back later: mapped, and the game writes the body's scan again in that same second
        _e(
            "2026-10-13T10:00:00Z",
            "SAAScanComplete",
            SystemAddress=address,
            BodyID=4,
            ProbesUsed=9,
            EfficiencyTarget=8,
        ),
        _scan("2026-10-13T10:00:00Z", "Eden", 4, **world),
    )
    (body,) = e.bodies.values()
    full, scan_only = sale_value(body.scan, True, False), sale_value(body.scan, False, False)
    assert body.mapping_only and e.data_value() == full - scan_only
    e.add(
        parse_money(_e("2026-10-13T11:00:00Z", "SellExplorationData", Systems=["Eden"], TotalEarnings=1))[1]
    )
    e.add(parse_money(_scan("2026-10-14T10:00:00Z", "Eden", 4, **world))[0])
    e.add(DataMapped(address, 4, True, "2026-10-14T10:01:00Z"))
    assert e.bodies == {}  # scan and mapping both sold: nothing left


def test_balance_follows_every_credit_between_logins() -> None:
    e = _fold(
        _e("2026-10-13T08:00:00Z", "RepairAll", Cost=5),  # before any login: no balance known yet
        _e("2026-10-13T09:00:00Z", "LoadGame", Credits=1_000_000),
        _e("2026-10-13T09:10:00Z", "MarketBuy", Count=10, BuyPrice=1000, TotalCost=10_000),
        _e(
            "2026-10-13T09:20:00Z",
            "MarketSell",
            Count=10,
            SellPrice=1500,
            TotalSale=15_000,
            AvgPricePaid=1000,
        ),
        _e("2026-10-13T09:30:00Z", "RefuelAll", Cost=400),
        _e("2026-10-13T10:00:00Z", "LoadGame", Credits=1_004_000),  # exact again (here: 600 off)
    )
    assert e.balance == [
        ("2026-10-13T09:00:00Z", 1_000_000),
        ("2026-10-13T09:10:00Z", 990_000),
        ("2026-10-13T09:20:00Z", 995_000),  # the trade profit
        ("2026-10-13T09:20:00Z", 1_005_000),  # the price paid, back
        ("2026-10-13T09:30:00Z", 1_004_600),
        ("2026-10-13T10:00:00Z", 1_004_000),
    ]
    e.live = ("2026-10-13T10:00:00Z", 1_010_000)  # a status in the same second as the login: older news
    assert len(ui_earnings(e, NOW).balance) == 6
    e.live = ("2026-10-13T10:05:00Z", 1_004_000)  # nothing new
    assert len(ui_earnings(e, NOW).balance) == 6
    e.live = ("2026-10-13T10:05:00Z", 1_010_000)
    assert ui_earnings(e, NOW).balance[-1] == UiBalance("2026-10-13T10:05:00Z", 1_010_000)


def test_balance_points_are_thinned_keeping_both_ends() -> None:
    e = Earnings()
    e.add(parse_money(_e("2026-10-01T00:00:00Z", "LoadGame", Credits=0))[1])
    for i in range(1000):
        e.add(MoneyMoved(True, "missions", 1, f"2026-10-02T00:{i // 60:02d}:{i % 60:02d}Z"))
    points = ui_earnings(e, NOW).balance
    assert len(points) == 300 and points[0].credits == 0 and points[-1].credits == 1000


def test_live_balance_comes_from_the_status_only(tmp_path: Path) -> None:
    p = ScoutPlugin(tmp_path / "plugin")
    p.on_journal(_e("2026-10-13T10:00:00Z", "LoadGame", Credits=5_000_000), None)
    assert p.earnings.live is None  # the login's figure is a balance point, not a live one
    p.on_dashboard({"timestamp": "2026-10-13T10:01:00Z", "Balance": 5_100_000, "Flags": 0})
    assert p.earnings.live == ("2026-10-13T10:01:00Z", 5_100_000)
