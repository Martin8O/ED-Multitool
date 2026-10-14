"""Route building: merge by id64, order along the from→to line, every threshold, the Spansh queries."""

from dataclasses import replace

import pytest

from edmultitool.core.route import build_route, min_jumps, scout_queries
from edmultitool.core.scout import BioSpecies, ScoutBody, ScoutKind, ScoutQuery, ScoutResult, ScoutSystem
from edmultitool.core.settings import ScoutPrefs
from edmultitool.core.view import ui_route

START = ScoutSystem(1, "Start", 0, 0.0, 0.0, 0.0, ())
DEST = ScoutSystem(2, "Dest", 9, 100.0, 0.0, 0.0, ())
OFF = ScoutPrefs(to_system="Dest", min_scan=0, min_mapping=0, min_species=0, min_bio_body=0)


def body(id64: int, name: str, scan: int = 0, mapping: int = 0, species: tuple[int, ...] = ()) -> ScoutBody:
    return ScoutBody(
        id64, name, "Planet", "High metal content world", 100.0 + id64 % 7, scan, mapping, False,
        tuple(BioSpecies("Genus", f"Genus S{v}", v, 3) for v in species),
    )  # fmt: skip


def system(id64: int, name: str, x: float, y: float, *bodies: ScoutBody) -> ScoutSystem:
    return ScoutSystem(id64, name, 4, x, y, 0.0, bodies)


def result(kind: ScoutKind, *systems: ScoutSystem, cached: bool = False) -> ScoutResult:
    return ScoutResult(kind, ScoutQuery("Start", "Dest", 20.0), (START, *systems, DEST), from_cache=cached)


def test_systems_are_ordered_along_the_line_not_by_search() -> None:
    riches = result("riches", system(10, "Late", 80, 5, body(101, "Late 1", 10, 10)))
    bio = result(
        "bio",
        system(11, "Early", 20, -3, body(111, "Early 1", species=(5,))),
        system(12, "Middle", 50, 0, body(121, "Middle 1", species=(5,))),
    )
    route = build_route([riches, bio], OFF)
    assert [s.name for s in route.stops] == ["Early", "Middle", "Late"]
    early, middle, late = route.stops
    assert early.along_ly == pytest.approx(20) and early.off_line_ly == pytest.approx(3)
    assert early.leg_ly == pytest.approx((20**2 + 3**2) ** 0.5)
    assert middle.leg_ly == pytest.approx((30**2 + 3**2) ** 0.5)
    assert late.leg_ly == pytest.approx((30**2 + 5**2) ** 0.5)
    assert route.final_leg_ly == pytest.approx((20**2 + 5**2) ** 0.5)
    assert (route.from_name, route.to_name, route.total_ly, route.range_ly) == ("Start", "Dest", 100.0, 20.0)


def test_same_system_from_both_searches_is_one_stop_and_shared_body_carries_both() -> None:
    both = system(10, "Both", 40, 0, body(101, "Both 1", 300, 900), body(102, "Both 2", 5, 50))
    riches = result("riches", both)
    bio = result("bio", system(10, "Both", 40, 0, body(101, "Both 1", 300, 900, species=(7, 3))))
    (stop,) = build_route([riches, bio], OFF).stops
    assert [b.id64 for b in stop.bodies] == [101, 102]  # by distance to arrival (103 ls, 104 ls)
    shared = next(b for b in stop.bodies if b.id64 == 101)
    assert shared.scan_hit and shared.bio_value == 10
    assert (stop.scan_value, stop.mapping_value, stop.bio_value) == (305, 950, 10)


def test_scan_thresholds_both_apply() -> None:
    riches = result(
        "riches",
        system(
            10,
            "A",
            30,
            0,
            body(101, "A 1", 300, 900),
            body(102, "A 2", 100, 2000),
            body(103, "A 3", 400, 200),
        ),
    )
    prefs = replace(OFF, min_scan=300, min_mapping=500)
    (stop,) = build_route([riches], prefs).stops
    assert [b.id64 for b in stop.bodies] == [101]  # 102 fails scan, 103 fails mapping


def test_species_threshold_drops_cheap_species_before_the_body_total() -> None:
    bio = result(
        "bio",
        system(10, "A", 30, 0, body(101, "A 1", species=(9, 1, 1)), body(102, "A 2", species=(4, 4, 4))),
    )
    prefs = replace(OFF, min_species=2, min_bio_body=10)
    (stop,) = build_route([bio], prefs).stops
    # A 1: 9 counted (1 + 1 dropped) < 10 → out; A 2: 12 → in
    assert [b.id64 for b in stop.bodies] == [102]
    assert [s.value for s in stop.bodies[0].species] == [4, 4, 4]
    prefs = replace(OFF, min_species=5, min_bio_body=0)
    (stop,) = build_route([bio], prefs).stops
    assert [(b.id64, b.bio_value) for b in stop.bodies] == [(101, 9)]


def test_stop_with_no_counted_body_is_dropped_and_ends_are_never_stops() -> None:
    riches = result(
        "riches",
        system(10, "Poor", 30, 0, body(101, "Poor 1", 1, 1)),
        system(1, "Start", 0, 0, body(9, "Start 1", 999, 999)),  # Spansh listing the start mid-tour
    )
    route = build_route([riches], replace(OFF, min_scan=10))
    assert route.stops == ()
    assert route.final_leg_ly == pytest.approx(100)


def test_from_cache_only_when_every_search_was_cached() -> None:
    a, b = result("riches", cached=True), result("bio", cached=False)
    assert not build_route([a, b], OFF).from_cache
    assert build_route([a, replace(b, from_cache=True)], OFF).from_cache


def test_build_route_refuses_empty_input() -> None:
    with pytest.raises(ValueError):
        build_route([], OFF)
    with pytest.raises(ValueError):
        build_route([ScoutResult("bio", ScoutQuery("a", "b", 1.0), ())], OFF)


@pytest.mark.parametrize(("dist", "rng", "jumps"), [(0, 20, 0), (20, 20, 1), (20.01, 20, 2), (59.9, 20, 3)])
def test_min_jumps(dist: float, rng: float, jumps: int) -> None:
    assert min_jumps(dist, rng) == jumps


def test_queries_follow_the_form() -> None:
    prefs = ScoutPrefs(to_system="Colonia", radius_ly=30, max_distance_ls=20_000, max_stops=7,
                       min_scan=200_000, min_mapping=1_500_000, min_species=2_000_000,
                       min_bio_body=9_000_000)  # fmt: skip
    (rk, rq), (bk, bq) = scout_queries(prefs, "Sol", 21.5)
    assert (rk, bk) == ("riches", "bio")
    for q in (rq, bq):
        assert (q.from_system, q.to_system, q.range_ly, q.radius_ly, q.max_results, q.max_distance_ls) == (
            "Sol", "Colonia", 21.5, 30, 7, 20_000,
        )  # fmt: skip
    assert (rq.min_value, rq.use_mapping_value) == (1_500_000, True)
    assert (bq.min_value, bq.use_mapping_value) == (2_000_000, False)  # Spansh's bio minimum is per species
    # mapping threshold off → Spansh filters on scan value instead
    ((_, rq),) = scout_queries(replace(prefs, min_mapping=0, bio=False), "Sol", 21.5)
    assert (rq.min_value, rq.use_mapping_value) == (200_000, False)
    ((kind, _),) = scout_queries(replace(prefs, riches=False), "Sol", 21.5)
    assert kind == "bio"


def test_ui_route_rounds_and_shortens_names() -> None:
    eol = system(10**18 + 7, "Eol Prou BZ-A d138", 33.333, 4, body(101, "Eol Prou BZ-A d138 A 2", 1, 2))
    riches = result("riches", eol)
    ui = ui_route(build_route([riches], OFF), "2026-10-12T10:00:00+00:00")
    (stop,) = ui.stops
    assert stop.id64 == "1000000000000000007"  # exact, as text
    assert stop.bodies[0].short == "A 2"
    assert stop.leg_ly == round((33.333**2 + 16) ** 0.5, 2)
    assert stop.leg_jumps == 2 and ui.final_leg_jumps == 4
